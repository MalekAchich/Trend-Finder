"""`tf` command line (Docs/Code docs/08-infrastructure-and-repo.md)."""
import asyncio
import subprocess
import time
import webbrowser
from pathlib import Path

import typer
from alembic import command
from alembic.config import Config

import tf_db
from tf_agent.config import AppSettings
from tf_agent.models.chatgpt_auth import ChatGptAuth
from tf_agent.models.claude_cli import ClaudeCliAuth, clean_env
from tf_backend.doctor import default_probes, run_checks
from tf_backend.services import build_services, close_services

app = typer.Typer(no_args_is_help=True, help="Trend Finder command line")
login_app = typer.Typer(no_args_is_help=True, help="Log in to a model provider")


@app.callback()
def main() -> None:
    """Trend Finder command line."""


app.add_typer(login_app, name="login")


def alembic_config() -> Config:
    return Config(str(Path(tf_db.__file__).resolve().parent.parent / "alembic.ini"))


@app.command()
def version() -> None:
    """Print the app version."""
    from tf_backend import __version__

    typer.echo(__version__)


@app.command()
def migrate() -> None:
    """Upgrade the database to the latest migration."""
    command.upgrade(alembic_config(), "head")
    typer.echo("database is at the latest migration")


@app.command()
def doctor() -> None:
    """Check providers, database, ffmpeg and disk space."""
    checks = run_checks(default_probes(AppSettings()))
    for c in checks:
        typer.echo(f"[{c.level:<4}] {c.name:<9} {c.detail}")
    if any(c.level == "FAIL" for c in checks):
        raise typer.Exit(1)


@login_app.command("chatgpt")
def login_chatgpt(device: bool = typer.Option(False, "--device", help="Use device-code login instead of a local callback")) -> None:
    """Log in with your ChatGPT subscription."""
    asyncio.run(_login_chatgpt(device))


async def _login_chatgpt(device: bool) -> None:
    auth = ChatGptAuth(AppSettings().chatgpt_auth_file)
    started = time.time()
    if device:
        info = await auth.device_login_start()
        typer.echo(f"Open {info['verification_url']} and enter the code: {info['user_code']}")
    else:
        url = await auth.browser_login_start()
        typer.echo(f"Opening your browser. If it doesn't open, visit:\n{url}")
        webbrowser.open(url)
    try:
        ok = await auth.wait_connected(since=started, timeout_s=900)
    finally:
        await auth.stop_callback_server()
    if not ok:
        typer.echo("Login timed out.")
        raise typer.Exit(1)
    st = auth.status()
    typer.echo(f"ChatGPT connected: {st['email']} ({st['plan']})")


@login_app.command("claude")
def login_claude() -> None:
    """Log in with your Claude subscription (runs `claude auth login`)."""
    settings = AppSettings()
    st = ClaudeCliAuth(settings.claude_bin).status()
    if st["connected"]:
        typer.echo(f"Claude already connected: {st['email']} ({st['subscription']})")
        return
    raise typer.Exit(subprocess.call([settings.claude_bin, "auth", "login", "--claudeai"], env=clean_env()))


@app.command()
def models() -> None:
    """List the models each provider currently offers."""
    asyncio.run(_models())


async def _models() -> None:
    sv = await build_services(with_db=False)
    try:
        for provider, infos in (await sv.registry.refresh()).items():
            typer.echo(f"{provider}:")
            for m in sorted(infos, key=lambda m: m.priority):
                flag = " (hidden)" if m.hidden else ""
                typer.echo(f"  {m.model_id:<28} {m.display_name or ''}{flag}")
            for alias in ("best", "fast"):
                typer.echo(f"  {alias} -> {sv.registry.resolve(provider, alias)}")
    finally:
        await close_services(sv)


from tf_agent.tools.types import ToolFailure  # noqa: E402

PLATFORM_SEARCH = {"tiktok": "tiktok_search", "instagram": "instagram_search", "shorts": "shorts_search",
                   "x": "x_search"}


@app.command()
def search(
    platform: str = typer.Argument(..., help="tiktok, instagram, shorts or x"),
    query: str = typer.Argument(..., help="search words"),
    max_results: int = typer.Option(10, "--max", min=1, max=30),
    recent: str | None = typer.Option(None, help="day, week, month or year"),
) -> None:
    """Discover videos on one platform (connected accounts and the YouTube key are used when set)."""
    if recent not in (None, "day", "week", "month", "year"):
        typer.echo("--recent must be day, week, month or year", err=True)
        raise typer.Exit(2)
    if platform not in PLATFORM_SEARCH:
        typer.echo("platform must be one of: tiktok, instagram, shorts, x", err=True)
        raise typer.Exit(2)
    asyncio.run(_search(platform, query, max_results, recent))


async def _search(platform: str, query: str, max_results: int, recent: str | None) -> None:
    from tf_agent.tools.agent_tools import compact_discovery
    from tf_agent.tools.factory import build_tool_stack
    from tf_db.session import make_engine, make_sessionmaker

    settings = AppSettings()
    engine = make_engine(settings.database_url)
    try:
        stack = build_tool_stack(settings, make_sessionmaker(engine))
        try:
            res = await getattr(stack.platforms, PLATFORM_SEARCH[platform])(query, max_results, recent)
        except ToolFailure as e:
            typer.echo(f"search failed: {e.error.code}: {e.error.message}", err=True)
            raise typer.Exit(1) from None
        typer.echo(compact_discovery(res.to_dict()))
    finally:
        await engine.dispose()


@app.command()
def analyze(url: str = typer.Argument(..., help="TikTok, Instagram, YouTube or X video URL")) -> None:
    """Download and analyse one video: contact sheet, pose-based Kling feasibility, transcript."""
    asyncio.run(_analyze(url))


async def _analyze(url: str) -> None:
    from tf_agent.pipeline.analyze import thread_runner
    from tf_agent.pipeline.pose import PoseAnalyzer
    from tf_agent.pipeline.transcript import Transcriber
    from tf_agent.tools.factory import build_analyzer, build_tool_stack
    from tf_db.session import make_engine, make_sessionmaker

    settings = AppSettings()
    engine = make_engine(settings.database_url)
    try:
        stack = build_tool_stack(settings, make_sessionmaker(engine))
        try:
            item = await stack.platforms.get_video(url)
        except ToolFailure as e:
            typer.echo(f"cannot read that video: {e.error.code}: {e.error.message}", err=True)
            raise typer.Exit(1) from None
        runner = thread_runner(PoseAnalyzer(), Transcriber(settings.whisper_model))
        try:
            r = await build_analyzer(settings, stack, runner).analyze(item)
        finally:
            runner.close()
    finally:
        await engine.dispose()
    typer.echo(f"{r.canonical_id}: feasibility={r.feasibility} filtered={r.filtered_reason or 'no'}")
    if r.best_clean_segment:
        typer.echo(f"  best clean segment: {r.best_clean_segment['start_s']:.1f}s → {r.best_clean_segment['end_s']:.1f}s")
    if r.pose:
        typer.echo(f"  single person {r.pose['single_person_ratio']:.0%}, body visible {r.pose['body_visibility']:.0%},"
                   f" camera motion {r.camera_motion}, cuts {len(r.cuts)}")
    if r.transcript and r.transcript.get("text"):
        typer.echo(f"  transcript ({r.transcript['language']}): {r.transcript['text'][:160]}")
    if r.contact_sheet_path:
        typer.echo(f"  contact sheet: {r.contact_sheet_path}")


def _parse_run_id(raw: str):
    import uuid

    try:
        return uuid.UUID(raw)
    except ValueError:
        typer.echo("that is not a valid run id (use `tf runs` to list them)", err=True)
        raise typer.Exit(2) from None


def _event_line(e: dict) -> str | None:
    p = e["payload"]
    kind = e["type"]
    who = (p.get("agent") or {}).get("role", "")
    if kind == "run.state":
        extra = f" — {p['stop_reason']}" if p.get("stop_reason") else ""
        return f"● run {p['state']}{extra}"
    if kind == "character.read":
        return f"◆ read the character: {p['read']['vibe']} | niches: {', '.join(p['read']['possible_niches'])}"
    if kind == "trend.studied":
        return f"◆ studied {p['url']}: {p['study']['format'][:120]}"
    if kind == "plan.created":
        return (f"◆ round {p['round']}: {len(p['tasks'])} tasks ({p['explore']} explore / {p['exploit']} exploit). "
                f"{p['reasoning'][:160]}")
    if kind == "agent.thought":
        return f"  {who}: {p['text'][:160]}"
    if kind == "agent.finished":
        if p.get("failed"):
            return f"  ✗ {who} failed: {str(p['failed'])[:120]}"
        return f"  ✓ {who}: {p['accepted']} accepted, {p['rejected']} rejected, {p['leads']} leads ({p.get('provider')})"
    if kind == "video.saved":
        v = p["video"]
        return f"  ★ saved {v['url']} (score {v['score']:.0f})" if v.get("score") is not None else f"  ★ saved {v['url']}"
    if kind in ("candidate.rejected", "error", "provider.switched"):
        return f"  · {kind}: {p.get('reason') or p.get('message') or (p.get('from') + ' → ' + p.get('to'))}"
    if kind == "round.finished":
        return f"◆ round {p['round']} done: {p['new_analyzed']} analyzed"
    return None


async def _follow(orchestrator, run_id, stop) -> None:
    from tf_agent.orchestrator.blackboard import EventCursor

    cursor = EventCursor(orchestrator.blackboard, run_id)
    while True:
        for e in await cursor.next_batch():
            line = _event_line(e)
            if line:
                typer.echo(line)
        if stop.is_set():
            return
        await asyncio.sleep(2)


async def _execute(runtime, run_id) -> None:
    stop = asyncio.Event()
    follower = asyncio.create_task(_follow(runtime.orchestrator, run_id, stop))
    try:
        outcome = await runtime.orchestrator.execute(run_id)
    finally:
        stop.set()
        await follower
    typer.echo(f"\nrun {run_id}: {outcome.state} — {outcome.stop_reason} ({outcome.findings_analyzed} analyzed)")
    await _print_trends(runtime.services.sessionmaker, run_id, 10)


from tf_agent.orchestrator.run import RunSettings as _RunSettings  # noqa: E402

_DEFAULT = _RunSettings()


@app.command()
def run(
    slug: str = typer.Argument(..., help="character slug (folder name, lower-case), e.g. nicolaiz"),
    rounds: int = typer.Option(_DEFAULT.rounds, min=1, max=20, help="safety cap on rounds"),
    tasks: int = typer.Option(_DEFAULT.tasks_per_round, min=1, max=16, help="tasks per round"),
    target: int = typer.Option(_DEFAULT.target_findings, min=1, help="stop once this many findings score ≥ --good"),
    good: float = typer.Option(_DEFAULT.good_score, help="score that counts toward the target"),
    platforms: str = typer.Option(",".join(_DEFAULT.platforms)),
    freshness: str = typer.Option(_DEFAULT.freshness, help="when videos must be posted: day, week, month or any"),
    minutes: float = typer.Option(_DEFAULT.wall_clock_s / 60, help="wall-clock limit"),
    workers: int = typer.Option(8, min=1, max=16),
    trend_url: list[str] = typer.Option([], "--trend-url", help="a trending AI-influencer video to study (repeat)"),
    target_url: list[str] = typer.Option([], "--target", help="a video you found: URL or URL=character (repeat)"),
) -> None:
    """Start a multi-agent trend-finding run for a character."""
    targets = []
    for raw in target_url:
        url, _, who = raw.partition("=")
        targets.append({"url": url.strip(), "character": (who or slug).strip()})
    asyncio.run(_run(slug, rounds, tasks, target, good, platforms, freshness, minutes, workers, trend_url, targets))


async def _run(slug, rounds, tasks, target, good, platforms, freshness, minutes, workers, trend_urls, targets) -> None:
    from tf_agent.characters.folders import sync_characters
    from tf_agent.orchestrator.run import InputError, RunSettings
    from tf_backend.runtime import build_runtime

    runtime = await build_runtime()
    try:
        await sync_characters(runtime.services.settings.characters_dir, runtime.services.sessionmaker)
        settings = RunSettings(platforms=[p.strip() for p in platforms.split(",") if p.strip()], rounds=rounds,
                               tasks_per_round=tasks, target_findings=target, good_score=good,
                               wall_clock_s=minutes * 60, workers=workers, freshness=freshness,
                               trend_urls=list(trend_urls), targets=targets)
        try:
            run_id = await runtime.orchestrator.create_run(slug, settings)
        except InputError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(2) from None
        typer.echo(f"run {run_id} started (resume with `tf resume {run_id}` if interrupted)")
        await _execute(runtime, run_id)
    finally:
        await runtime.close()


@app.command()
def resume(run_id: str) -> None:
    """Resume an interrupted run where it left off."""
    rid = _parse_run_id(run_id)

    async def go() -> None:
        from tf_backend.runtime import build_runtime

        runtime = await build_runtime()
        try:
            await _execute(runtime, rid)
        finally:
            await runtime.close()

    asyncio.run(go())


@app.command()
def runs(limit: int = typer.Option(10, min=1, max=100)) -> None:
    """List recent runs."""
    async def go() -> None:
        from sqlalchemy import select

        from tf_db.models import Character, Run
        from tf_db.session import make_engine, make_sessionmaker

        engine = make_engine(AppSettings().database_url)
        try:
            async with make_sessionmaker(engine)() as s:
                rows = (await s.execute(select(Run, Character.slug).join(Character, Character.id == Run.character_id)
                                        .order_by(Run.started_at.desc()).limit(limit))).all()
        finally:
            await engine.dispose()
        for r, slug in rows:
            typer.echo(f"{r.id}  {slug:<12} {r.state:<13} round {r.current_round}  {r.started_at:%Y-%m-%d %H:%M}  "
                       f"{r.stop_reason or ''}")

    asyncio.run(go())


async def _print_trends(sessionmaker, run_id, top: int) -> None:
    from sqlalchemy import select

    from tf_db.models import Finding, FindingScore, TrendCluster, Video, VideoAnalysis

    async with sessionmaker() as s:
        rows = (await s.execute(
            select(TrendCluster, FindingScore, Video, VideoAnalysis)
            .join(Finding, Finding.id == TrendCluster.best_finding_id)
            .join(FindingScore, FindingScore.finding_id == Finding.id)
            .join(Video, Video.canonical_id == Finding.canonical_id)
            .outerjoin(VideoAnalysis, (VideoAnalysis.canonical_id == Finding.canonical_id)
                       & (VideoAnalysis.pipeline_version == "1"))
            .where(TrendCluster.run_id == run_id).order_by(TrendCluster.rank).limit(top))).all()
    if not rows:
        typer.echo("no trend cards for this run")
        return
    for c, sc, v, a in rows:
        flag = "  ⚠ models disagree" if sc.disagreement else ""
        typer.echo(f"\n#{c.rank}  overall {c.overall:.0f}  (fit {sc.fit or 0:.0f} · kling {sc.feasibility or 0:.0f} · "
                   f"momentum {sc.momentum or 0:.0f} · fresh {sc.freshness or 0:.0f}){flag}")
        typer.echo(f"    {v.url}   [{c.member_count} version(s)] {c.label or ''}")
        if sc.adaptation_idea:
            typer.echo(f"    idea: {sc.adaptation_idea[:220]}")
        if a is not None and a.best_clean_segment:
            seg = a.best_clean_segment
            typer.echo(f"    motion window: {seg['start_s']:.1f}s → {seg['end_s']:.1f}s")


@app.command()
def trends(run_id: str, top: int = typer.Option(20, min=1, max=100)) -> None:
    """Show a run's ranked trend cards."""
    rid = _parse_run_id(run_id)

    async def go() -> None:
        from tf_db.session import make_engine, make_sessionmaker

        engine = make_engine(AppSettings().database_url)
        try:
            await _print_trends(make_sessionmaker(engine), rid, top)
        finally:
            await engine.dispose()

    asyncio.run(go())


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "Frontend" / "dist"
LOOPBACK = ("127.0.0.1", "localhost", "::1")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Interface to bind"),
    port: int = typer.Option(8000, help="Port"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the web app in the browser"),
    allow_remote: bool = typer.Option(False, "--allow-remote", help="Allow binding a non-loopback interface"),
) -> None:
    """Run the API and the web app (unfinished runs resume automatically)."""
    import uvicorn

    if host not in LOOPBACK and not allow_remote:
        typer.echo(f"Refusing to bind {host}: the app has no login, so anyone on the network could use your "
                   "subscriptions. Pass --allow-remote if you really mean it.", err=True)
        raise typer.Exit(2)
    if not (FRONTEND_DIST / "index.html").exists():
        typer.echo("The web app isn't built yet; the API still works. Build it with: cd Frontend && npm run build",
                   err=True)
    if allow_remote:
        import os

        os.environ["TF_ALLOWED_HOSTS"] = "*"
    url = f"http://{'127.0.0.1' if host in LOOPBACK else host}:{port}"
    typer.echo(f"Trend Finder at {url}  (Ctrl+C to stop)")
    if open_browser:
        import threading

        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    # live-run SSE streams never end on their own; without a deadline Ctrl+C waits for every open tab
    uvicorn.run("tf_backend.main:app", host=host, port=port, log_level="info", timeout_graceful_shutdown=5)
