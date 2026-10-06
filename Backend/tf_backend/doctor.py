"""Environment checks for `tf doctor`."""
import asyncio
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from tf_agent.config import AppSettings
from tf_agent.models.chatgpt_auth import ChatGptAuth
from tf_agent.models.claude_cli import ClaudeCliAuth
from tf_db.session import ping


@dataclass(frozen=True)
class Check:
    name: str
    level: Literal["OK", "WARN", "FAIL"]
    detail: str


Probe = Callable[[], Check]


def run_checks(probes: dict[str, Probe]) -> list[Check]:
    out = []
    for name, probe in probes.items():
        try:
            out.append(probe())
        except Exception as e:
            out.append(Check(name, "FAIL", f"{type(e).__name__}: {e}"))
    return out


def search_check(response: dict) -> Check:
    """SearXNG's free engines get rate-limited or CAPTCHA'd; with all of them blocked, discovery finds nothing."""
    results = len(response.get("results") or [])
    blocked = ", ".join(f"{e[0]} ({e[1]})" for e in response.get("unresponsive_engines") or [])
    if results == 0:
        return Check("search", "FAIL", f"no results; blocked engines: {blocked or 'none reported'}. Wait an hour or "
                                       "enable more engines in config/searxng/settings.yml")
    return Check("search", "OK", f"{results} results" + (f"; blocked for now: {blocked}" if blocked else ""))


def disk_check(free_gb: float) -> Check:
    if free_gb < 5:
        return Check("disk", "FAIL", f"only {free_gb:.1f} GB free; media needs ~5 GB")
    if free_gb < 10:
        return Check("disk", "WARN", f"{free_gb:.1f} GB free; consider freeing space")
    return Check("disk", "OK", f"{free_gb:.1f} GB free")


def default_probes(settings: AppSettings) -> dict[str, Probe]:
    def claude() -> Check:
        st = ClaudeCliAuth(settings.claude_bin).status()
        if not st["available"]:
            return Check("claude", "FAIL", f"claude CLI not found ({settings.claude_bin})")
        if not st["connected"]:
            return Check("claude", "FAIL", "not logged in: run `tf login claude`")
        return Check("claude", "OK", f"{st['email']} ({st['subscription']})")

    def chatgpt() -> Check:
        st = ChatGptAuth(settings.chatgpt_auth_file).status()
        if not st["connected"]:
            return Check("chatgpt", "WARN", "not logged in: run `tf login chatgpt`")
        return Check("chatgpt", "OK", f"{st['email']} ({st['plan']})")

    def database() -> Check:
        if asyncio.run(ping(settings.database_url)):
            return Check("database", "OK", "reachable")
        return Check("database", "FAIL", "unreachable: docker compose -f Database/docker-compose.yml up -d")

    def ffmpeg() -> Check:
        missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
        return Check("ffmpeg", "FAIL", f"missing: {', '.join(missing)}") if missing else Check("ffmpeg", "OK", "found")

    def disk() -> Check:
        return disk_check(shutil.disk_usage(".").free / 1e9)

    def search() -> Check:
        import httpx

        r = httpx.get(f"{settings.searxng_url}/search", params={"q": "site:tiktok.com/@ dance", "format": "json"},
                      timeout=20)
        r.raise_for_status()
        return search_check(r.json())

    return {"claude": claude, "chatgpt": chatgpt, "database": database, "search": search, "ffmpeg": ffmpeg,
            "disk": disk}
