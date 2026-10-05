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
from tf_agent.models.claude_cli import ClaudeCliAuth
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
    raise typer.Exit(subprocess.call([settings.claude_bin, "auth", "login", "--claudeai"]))


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
