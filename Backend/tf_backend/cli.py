"""`tf` command line (Docs/Code docs/08-infrastructure-and-repo.md)."""
import asyncio
import subprocess
import time
import webbrowser
from pathlib import Path

import typer
from alembic import command
from alembic.config import Config
from pydantic import BaseModel

import tf_db
from tf_agent.config import AppSettings
from tf_agent.loop.agent import AgentBudget, AgentEvent, run_agent
from tf_agent.loop.tools import Tool
from tf_agent.models.chatgpt_auth import ChatGptAuth
from tf_agent.models.claude_cli import ClaudeCliAuth, clean_env
from tf_backend.doctor import default_probes, run_checks
from tf_backend.services import build_services, close_services

app = typer.Typer(no_args_is_help=True, help="Trend Finder command line")
login_app = typer.Typer(no_args_is_help=True, help="Log in to a model provider")
seed_app = typer.Typer(no_args_is_help=True, help="Manage a character's seed videos")


@app.callback()
def main() -> None:
    """Trend Finder command line."""


app.add_typer(login_app, name="login")
app.add_typer(seed_app, name="seed")


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


class _AddParams(BaseModel):
    a: int
    b: int


class _DemoResult(BaseModel):
    answer: int
    explanation: str


@app.command("demo-agent")
def demo_agent(
    provider: str = typer.Option("claude", help="claude or chatgpt"),
    question: str = typer.Option("What is (17 + 25) + 8? Use the add tool for every addition."),
) -> None:
    """Run a tiny tool-using agent end-to-end on one provider."""
    asyncio.run(_demo(provider, question))


async def _demo(provider: str, question: str) -> None:
    sv = await build_services(with_db=False)

    async def add(p: _AddParams) -> int:
        return p.a + p.b

    async def show(ev: AgentEvent) -> None:
        typer.echo(f"  step {ev.step} [{ev.kind}] {ev.detail}")

    try:
        await sv.registry.refresh_provider(provider)
        res = await run_agent(
            client=sv.client, role="demo", provider=provider, result_model=_DemoResult,
            system="You are a careful assistant. Use the add tool for every addition, then call submit_result.",
            task=question, tools=[Tool("add", "Add two integers and return the sum.", _AddParams, add)],
            budget=AgentBudget(max_steps=8), on_event=show)
    finally:
        await close_services(sv)
    typer.echo(f"{res.status} via {res.provider}/{res.model} in {res.steps} steps: {res.result or res.error}")
    if res.status != "succeeded":
        raise typer.Exit(1)


from tf_agent.tools.types import ToolFailure  # noqa: E402

PLATFORM_SEARCH = {"tiktok": "tiktok_search", "instagram": "instagram_search", "shorts": "shorts_search"}


@app.command()
def search(
    platform: str = typer.Argument(..., help="tiktok, instagram or shorts"),
    query: str = typer.Argument(..., help="search words"),
    max_results: int = typer.Option(10, "--max", min=1, max=30),
    recent: str | None = typer.Option(None, help="day, week, month or year"),
) -> None:
    """Discover videos on one platform (no account needed; Instagram is discovery-only)."""
    if recent not in (None, "day", "week", "month", "year"):
        typer.echo("--recent must be day, week, month or year", err=True)
        raise typer.Exit(2)
    if platform not in PLATFORM_SEARCH:
        typer.echo("platform must be one of: tiktok, instagram, shorts", err=True)
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
def analyze(url: str = typer.Argument(..., help="TikTok, Instagram or YouTube video URL")) -> None:
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


@app.command("sync-characters")
def sync_characters_cmd() -> None:
    """Import/refresh characters from the characters folder (new version only when something changed)."""
    asyncio.run(_sync_characters())


async def _sync_characters() -> None:
    from tf_agent.characters.sync import sync_characters
    from tf_db.session import make_engine, make_sessionmaker

    settings = AppSettings()
    engine = make_engine(settings.database_url)
    try:
        for r in await sync_characters(settings.characters_dir, make_sessionmaker(engine)):
            typer.echo(f"{r.slug}: version {r.version}{' (updated)' if r.changed else ' (unchanged)'}")
    finally:
        await engine.dispose()


@seed_app.command("add")
def seed_add(slug: str, url: str) -> None:
    """Add a seed video (an example of the direction you want) to a character."""
    asyncio.run(_seed_add(slug, url))


async def _seed_add(slug: str, url: str) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from tf_agent.characters.sync import load_character
    from tf_agent.tools.normalize import canonical_id
    from tf_db.models import Seed
    from tf_db.session import make_engine, make_sessionmaker

    settings = AppSettings()
    engine = make_engine(settings.database_url)
    try:
        sm = make_sessionmaker(engine)
        ch = await load_character(sm, slug)
        async with sm() as s:
            await s.execute(pg_insert(Seed).values(character_id=ch.character_id, url=url, canonical_id=canonical_id(url),
                                                   source="cli").on_conflict_do_nothing())
            await s.commit()
    finally:
        await engine.dispose()
    typer.echo(f"seed added to {slug}: {url}")


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
    if kind == "run.state":
        extra = f" — {p['stop_reason']}" if p.get("stop_reason") else ""
        return f"● run {p['state']}{extra}"
    if kind == "plan.created":
        return f"◆ round {p['round']}: {p['tasks']} tasks ({p['explore']} explore / {p['exploit']} exploit). {p['summary'][:160]}"
    if kind == "task.finished":
        if p.get("failed"):
            return f"  ✗ {p['type']} failed: {str(p['failed'])[:120]}"
        return f"  ✓ {p['type']}: {len(p.get('accepted', []))} accepted, {len(p.get('rejected', []))} rejected, {p.get('leads', 0)} leads ({p.get('provider')})"
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


@app.command()
def run(
    slug: str = typer.Argument(..., help="character slug, e.g. nicolaiz"),
    rounds: int = typer.Option(3, min=1, max=10),
    tasks: int = typer.Option(12, min=1, max=16, help="tasks per round"),
    target: int = typer.Option(20, min=1, help="stop once this many findings score ≥ --good"),
    good: float = typer.Option(60.0, help="score that counts toward the target"),
    platforms: str = typer.Option("tiktok,youtube,instagram"),
    minutes: float = typer.Option(60.0, help="wall-clock limit"),
    workers: int = typer.Option(8, min=1, max=16),
) -> None:
    """Start a multi-agent trend-finding run for a character."""
    asyncio.run(_run(slug, rounds, tasks, target, good, platforms, minutes, workers))


async def _run(slug, rounds, tasks, target, good, platforms, minutes, workers) -> None:
    from tf_agent.orchestrator.run import RunSettings
    from tf_backend.runtime import build_runtime

    runtime = await build_runtime()
    try:
        settings = RunSettings(platforms=[p.strip() for p in platforms.split(",") if p.strip()], rounds=rounds,
                               tasks_per_round=tasks, target_findings=target, good_score=good,
                               wall_clock_s=minutes * 60, workers=workers)
        run_id = await runtime.orchestrator.create_run(slug, settings)
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
            typer.echo(f"    motion window: {seg['start_s']:.1f}s → {seg['end_s']:.1f}s   sheet: {a.contact_sheet_path}")


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
