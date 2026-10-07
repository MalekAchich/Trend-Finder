"""Claude adapter: the official `claude -p` CLI as an isolated subprocess (D-29, D-30, D-36).

Ported login/status/model-discovery logic from Docs/Code docs/claude-codex-auth-reference.md §3.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from tf_agent.models.errors import (
    ContentRefused,
    AuthRequired,
    InvalidRequest,
    ModelUnavailable,
    MalformedResponse,
    ProviderError,
    TransientProviderError,
    UsageLimited,
)
from tf_agent.models.types import (
    CompletionRequest,
    CompletionResponse,
    ImagePart,
    Message,
    ModelInfo,
    ProviderHealth,
    RateInfo,
    RateWindow,
    ToolCall,
    ToolSpec,
    Usage,
)

log = logging.getLogger(__name__)
PROVIDER = "claude"
URL_RE = re.compile(r"https?://[^\s)>\"]+")
CLAUDE_ALIASES = ("fable", "opus", "sonnet", "haiku")  # what `claude --model` accepts for "latest X"
# Variables that would move Claude calls off the owner's subscription (API key, proxy, cloud providers).
STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_OAUTH_TOKEN")
_LIVE_MENTION_RE = re.compile(r"@(?=[/~.])")
MAX_SYSTEM_ARG_BYTES = 64_000

LIMIT_RE = re.compile(r"usage limit|rate limit|hit your limit|limit reached|too many requests", re.I)
AUTH_RE = re.compile(r"not logged in|run /login|invalid api key|authenticat|oauth token|unauthorized", re.I)
TRANSIENT_RE = re.compile(r"overloaded|internal server error|bad gateway|service unavailable|timed? ?out|"
                          r"connection (reset|refused|error)", re.I)
RESET_EPOCH_RE = re.compile(r"\|(\d{10})\b")


def clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}


# `claude -p` silently drops @-attached images above ~256 KB (measured: 233 KB seen, 278 KB not), and the model
# then answers as if it had looked. Anything bigger is re-encoded to fit.
CLAUDE_IMAGE_MAX_BYTES = 240_000
CLAUDE_IMAGE_MAX_EDGE = 1568  # the API downsizes past this anyway


def _fit_image(src: Path) -> bytes:
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((CLAUDE_IMAGE_MAX_EDGE, CLAUDE_IMAGE_MAX_EDGE))
        while True:
            for quality in (85, 75, 65, 55, 45):
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=quality, optimize=True)
                if buf.tell() <= CLAUDE_IMAGE_MAX_BYTES:
                    return buf.getvalue()
            im = im.resize((max(1, int(im.width * 0.8)), max(1, int(im.height * 0.8))), Image.LANCZOS)


def defuse(text: str) -> str:
    """Neutralise `@/…`, `@~…`, `@.…` in untrusted text so the CLI never inlines local files (only image refs may)."""
    return _LIVE_MENTION_RE.sub("@\u200b", text)


def isolation_flags(empty_mcp_config: Path, persist: bool = False) -> list[str]:
    # stream-json (+ --verbose) also reports the concrete model and the subscription's usage windows.
    # Sessions are saved only for agent conversations (persist=True), which resume them step after step.
    return ["-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk", "--tools", "",
            *([] if persist else ["--no-session-persistence"]), "--strict-mcp-config", "--mcp-config",
            str(empty_mcp_config), "--setting-sources", ""]


def step_schema(tools: list[ToolSpec]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "note": {"type": "string", "description": "one-line progress note for the owner: what you check next"},
            "calls": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": [t.name for t in tools]},
                        "arguments": {"type": "object"},
                    },
                    "required": ["name", "arguments"],
                },
            },
        },
        "required": ["calls"],
    }


def _render_messages(messages: list[Message], image_ref: Callable[[ImagePart], str]) -> list[str]:
    out: list[str] = []
    for m in messages:
        if m.role == "user":
            out.append("[user]")
            out.append(defuse(m.text()))
            out.extend(image_ref(img) for img in m.images())
        elif m.role == "assistant":
            out.append("[assistant]")
            if m.text():
                out.append(defuse(m.text()))
            if m.tool_calls:
                out.append(defuse(json.dumps({"calls": [{"id": c.id, "name": c.name, "arguments": c.arguments}
                                                        for c in m.tool_calls]}, ensure_ascii=False)))
        else:
            out.append(f"[tool result id={m.tool_call_id} name={m.tool_name}]")
            out.append(defuse(m.text()))
    return out


def _render_tail(req: CompletionRequest, with_tools: bool = True) -> list[str]:
    out: list[str] = []
    if req.tools:
        if with_tools:
            out.append("<tools>")
            out.extend(defuse(json.dumps({"name": t.name, "description": t.description, "parameters": t.parameters},
                                         ensure_ascii=False)) for t in req.tools)
            out.append("</tools>")
        out.append('Decide your next step. Reply ONLY with JSON matching the schema: one or more tool calls in '
                   '"calls", each with the tool "name" and "arguments" matching that tool\'s parameters.')
    elif req.output_schema is not None:
        out.append("Reply ONLY with JSON matching the required schema.")
    return out


def render_prompt(req: CompletionRequest, image_ref: Callable[[ImagePart], str]) -> str:
    return "\n".join(["<conversation>", *_render_messages(req.messages, image_ref), "</conversation>",
                      *_render_tail(req)])


def render_delta(req: CompletionRequest, new: list[Message], image_ref: Callable[[ImagePart], str],
                 tools_changed: bool) -> str:
    """A resumed session already holds everything before `new` (and Claude's own replies and thinking)."""
    return "\n".join(["<conversation continues>", *_render_messages(new, image_ref), "</conversation continues>",
                      *_render_tail(req, with_tools=tools_changed)])


def _fingerprint(m: Message) -> str:
    raw = json.dumps([m.role, m.text(), [i.path for i in m.images()], m.tool_call_id,
                      [[c.name, c.arguments] for c in m.tool_calls]], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass
class _Session:
    """One agent conversation's CLI session: what it already holds, so each step sends only what's new."""
    sid: str
    model: str
    system: str
    seen: list[str]  # fingerprints of the request messages the session holds (its own reply follows them)
    tools: tuple[str, ...]


SESSION_TTL_S = 24 * 3600


CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
OUT_OF_CREDITS_RE = re.compile(r"out of usage credits", re.I)
WINDOW_MINUTES = {"five_hour": 300, "seven_day": 10080}
MODEL_NAME_RE = re.compile(r"^claude-([a-z]+)-(\d+)-(\d+)(?:-\d{8})?$")
RESOLVE_TTL_S = 24 * 3600
USAGE_TTL_S = 600  # Settings refreshes Claude's usage at most every 10 minutes


def display_name(model_id: str) -> str:
    m = MODEL_NAME_RE.match(model_id)
    return f"{m[1].title()} {m[2]}.{m[3]}" if m else model_id


def _envelope_from(stdout: str) -> dict[str, Any]:
    """Accepts stream-json lines (init, rate_limit_event, …, result) or a single json envelope."""
    events = [json.loads(line) for line in stdout.splitlines() if line.strip().startswith("{")]
    if not events:
        raise json.JSONDecodeError("no JSON output", stdout, 0)
    envelope = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if envelope is None:
        if len(events) == 1:
            return events[0]
        raise json.JSONDecodeError("no result event", stdout, 0)
    envelope = dict(envelope)
    for e in events:
        if e.get("type") == "system" and e.get("subtype") == "init" and e.get("model"):
            envelope["_model"] = e["model"]
        if e.get("type") == "rate_limit_event":
            envelope["_rate"] = e.get("rate_limit_info") or {}
    return envelope


def _rate_from(info: dict[str, Any] | None) -> RateInfo | None:
    windows = []
    for name, w in ((info or {}).get("unifiedWindows") or {}).items():
        if isinstance(w, dict) and w.get("utilization") is not None:
            resets = w.get("resetsAt")
            windows.append(RateWindow(name, round(float(w["utilization"]) * 100, 1), WINDOW_MINUTES.get(name),
                                      float(resets) if resets else None))
    if not windows:
        return None
    top = max(windows, key=lambda w: w.used_percent)
    return RateInfo(used_percent=top.used_percent, window_minutes=top.window_minutes, resets_at=top.resets_at,
                    windows=tuple(windows))
REFUSAL_RE = re.compile(r"safeguards flagged|usage policy|acceptable use policy|\banthropic\.com/legal/aup", re.I)


def classify_failure(text: str, api_status: Any) -> ProviderError:
    t = (text or "").strip()[:800]
    try:
        status = int(api_status) if api_status is not None else None
    except (TypeError, ValueError):
        status = None
    if REFUSAL_RE.search(t):
        return ContentRefused(PROVIDER, t)
    if status == 429 or LIMIT_RE.search(t):
        m = RESET_EPOCH_RE.search(t)
        return UsageLimited(PROVIDER, t or "usage limited", float(m.group(1)) if m else None)
    if status in (401, 403) or AUTH_RE.search(t):
        return AuthRequired(PROVIDER, t or "authentication required: run `tf login claude`")
    if (status or 0) >= 500 or TRANSIENT_RE.search(t):
        return TransientProviderError(PROVIDER, t or "transient failure")
    return ProviderError(PROVIDER, t or "unknown claude CLI failure")


async def _kill_group(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await proc.wait()


class ClaudeCliAuth:
    def __init__(self, bin: str = "claude", log_path: Path | None = None, stats_path: Path | None = None) -> None:
        self.bin = shutil.which(bin) or bin
        self.log_path = log_path or Path(tempfile.gettempdir()) / "tf-claude" / "login.log"
        self.stats_path = stats_path or Path.home() / ".claude" / "stats-cache.json"
        self._proc: subprocess.Popen[str] | None = None

    def available(self) -> bool:
        return Path(self.bin).is_file() and os.access(self.bin, os.X_OK)

    def status(self) -> dict[str, Any]:
        if not self.available():
            return {"available": False, "connected": False, "email": None, "subscription": None}
        try:
            r = subprocess.run([self.bin, "auth", "status"], capture_output=True, text=True, timeout=15,
                               stdin=subprocess.DEVNULL, env=clean_env())
            d = json.loads(r.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            d = {}
        return {"available": True, "connected": bool(d.get("loggedIn")), "email": d.get("email"),
                "subscription": d.get("subscriptionType")}

    async def login_start(self, email: str | None = None) -> dict[str, Any]:
        if self._proc and self._proc.poll() is None:
            return {"started": True, "pending": True, "auth_url": None}
        cmd = [self.bin, "auth", "login", "--claudeai"] + (["--email", email] if email else [])
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("w") as log:
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                          text=True, start_new_session=True, env=clean_env())
        for _ in range(10):  # the CLI prints the auth URL shortly after start
            await asyncio.sleep(0.4)
            m = URL_RE.search(self.log_path.read_text(errors="ignore"))
            if m:
                return {"started": True, "auth_url": m.group(0)}
        return {"started": True, "auth_url": None}

    async def login_complete(self, raw: str) -> bool:
        code = raw.strip()
        if code.startswith("http"):
            code = (parse_qs(urlparse(code).query).get("code") or [code])[0]
        p = self._proc
        if not p or p.poll() is not None or not p.stdin:
            raise RuntimeError("no pending Claude login: start it again")
        p.stdin.write(code + "\n")
        p.stdin.flush()
        p.stdin.close()
        for _ in range(30):
            await asyncio.sleep(1)
            if (await asyncio.to_thread(self.status))["connected"]:
                self._proc = None
                return True
            if p.poll() is not None:
                break
        self._proc = None
        raise RuntimeError(self.log_path.read_text(errors="ignore")[-2000:])

    def logout(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None
        subprocess.run([self.bin, "auth", "logout"], capture_output=True, text=True, timeout=20,
                       stdin=subprocess.DEVNULL, env=clean_env())


class ClaudeCLIAdapter:
    name = PROVIDER

    def __init__(self, auth: ClaudeCliAuth, runtime_dir: Path | None = None, claude_home: Path | None = None) -> None:
        self.auth = auth
        self._sessions: dict[str, _Session] = {}
        self.runtime_dir = Path(runtime_dir or Path(tempfile.gettempdir()) / "tf-claude").resolve()
        if " " in str(self.runtime_dir):
            raise ValueError("runtime_dir must not contain spaces (Claude @-mentions break on spaces)")
        self.cwd = self.runtime_dir / "cwd"
        self.image_dir = self.runtime_dir / "images"
        self.mcp_config = self.runtime_dir / "empty-mcp.json"
        self._rate_at = 0.0
        self.last_rate: RateInfo | None = self._load_rate()  # the latest usage windows the CLI reported
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        if not self.mcp_config.exists():
            self.mcp_config.write_text('{"mcpServers": {}}')
        home = claude_home or Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        # the CLI saves a session under projects/<cwd with every non-alphanumeric char as "-">/<id>.jsonl
        self.sessions_dir = home / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(self.cwd))
        self._sweep_sessions()

    def _sweep_sessions(self) -> None:
        """Sessions left by a crash (only our private cwd's folder, never the owner's own Claude projects)."""
        if not self.sessions_dir.is_dir():
            return
        cutoff = time.time() - SESSION_TTL_S
        for f in self.sessions_dir.glob("*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass

    def _drop_session(self, conversation: str) -> None:
        sess = self._sessions.pop(conversation, None)
        if sess is not None:
            (self.sessions_dir / f"{sess.sid}.jsonl").unlink(missing_ok=True)
            shutil.rmtree(self.sessions_dir / sess.sid, ignore_errors=True)

    def end_conversation(self, conversation: str) -> None:
        self._drop_session(conversation)

    def _image_ref(self, img: ImagePart) -> str:
        src = Path(img.path).resolve()
        if not src.is_file():
            raise InvalidRequest(PROVIDER, f"image not found: {img.path}")
        small = src.stat().st_size <= CLAUDE_IMAGE_MAX_BYTES
        if small and " " not in str(src):
            return f"@{src}"
        data = src.read_bytes()
        digest = hashlib.sha256(data).hexdigest()[:16]
        dest = self.image_dir / (f"{digest}{src.suffix or '.jpg'}" if small else f"{digest}-fit.jpg")
        if not dest.exists():
            tmp = dest.with_suffix(f".{uuid.uuid4().hex}.tmp")
            tmp.write_bytes(data if small else _fit_image(src))
            tmp.replace(dest)
        return f"@{dest}"

    async def _run(self, cmd: list[str], stdin_text: str, timeout_s: float) -> dict[str, Any]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=self.cwd, env=clean_env(), start_new_session=True)
        except (FileNotFoundError, PermissionError) as e:
            raise AuthRequired(PROVIDER, f"claude CLI not found at {self.auth.bin!r}") from e
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin_text.encode()), timeout_s)
        except TimeoutError:
            await _kill_group(proc)
            raise TransientProviderError(PROVIDER, f"claude -p timed out after {timeout_s}s") from None
        except BaseException:  # cancellation: never leave a CLI (and its children) spending usage
            await asyncio.shield(_kill_group(proc))
            raise
        stdout = out.decode(errors="replace").strip()
        stderr = err.decode(errors="replace").strip()
        try:
            envelope = _envelope_from(stdout)
        except json.JSONDecodeError:
            failure = classify_failure(f"{stdout}\n{stderr}", None)
            if proc.returncode != 0 and type(failure) is ProviderError:
                raise TransientProviderError(PROVIDER, f"claude -p exited {proc.returncode}: {stderr[:300]}")
            if proc.returncode != 0:
                raise failure
            raise MalformedResponse(PROVIDER, "claude -p did not return JSON") from None
        rate = _rate_from(envelope.get("_rate"))
        if rate is not None:
            self.last_rate, self._rate_at = rate, time.time()
            self._save_rate(envelope.get("_rate"))
        if OUT_OF_CREDITS_RE.search(str(envelope.get("result") or "")):  # a model the plan lacks, not a usage limit
            raise ModelUnavailable(PROVIDER, str(envelope.get("result"))[:300])
        if envelope.get("is_error") or envelope.get("subtype") not in (None, "success"):
            raise classify_failure(f"{envelope.get('result') or ''} {stderr}", envelope.get("api_error_status"))
        return envelope

    def _session_args(self, req: CompletionRequest, system: str) -> tuple[list[str], str | None, _Session | None]:
        """(--session-id/--resume args, a delta prompt when resuming, the session to record after success)."""
        conv = req.conversation
        if conv is None:
            return [], None, None
        fps = [_fingerprint(m) for m in req.messages]
        tools = tuple(t.name for t in req.tools)
        sess = self._sessions.get(conv)
        n = len(sess.seen) if sess else 0
        if (sess is not None and sess.model == req.model and sess.system == system and len(fps) > n + 1
                and fps[:n] == sess.seen and req.messages[n].role == "assistant"):
            delta = render_delta(req, req.messages[n + 1:], self._image_ref, tools != sess.tools)
            return ["--resume", sess.sid], delta, _Session(sess.sid, req.model, system, fps, tools)
        self._drop_session(conv)  # first step, or the history changed under it (compaction): start clean
        sid = str(uuid.uuid4())
        return ["--session-id", sid], None, _Session(sid, req.model, system, fps, tools)

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        prompt = render_prompt(req, self._image_ref)
        system = req.system
        if len(system.encode()) > MAX_SYSTEM_ARG_BYTES:
            prompt = f"<system>\n{system}\n</system>\n{prompt}"
            system = "Follow the instructions in the <system> block of the user message."
        session_args, delta, session = self._session_args(req, system)
        if delta is not None:
            prompt = delta
        cmd = [self.auth.bin, *isolation_flags(self.mcp_config, persist=session is not None), *session_args,
               "--model", req.model, "--system-prompt", system]
        if req.reasoning_effort in CLAUDE_EFFORTS:
            cmd += ["--effort", req.reasoning_effort]
        if req.tools:
            cmd += ["--json-schema", json.dumps(step_schema(req.tools))]
        elif req.output_schema is not None:
            cmd += ["--json-schema", json.dumps(req.output_schema)]
        try:
            envelope = await self._run(cmd, prompt, req.timeout_s)
            resp = self._response(req, envelope)
        except BaseException:
            if req.conversation is not None:
                self._drop_session(req.conversation)  # unknown what the session recorded: next step starts clean
            raise
        if session is not None and req.conversation is not None:
            self._sessions[req.conversation] = session
        return resp

    def _response(self, req: CompletionRequest, envelope: dict[str, Any]) -> CompletionResponse:
        raw_usage = envelope.get("usage") or {}
        usage = Usage(
            input_tokens=sum(int(raw_usage.get(k) or 0) for k in
                             ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
            output_tokens=raw_usage.get("output_tokens"),
        )
        model = envelope.get("_model") or next(iter(envelope.get("modelUsage") or {}), req.model)
        if req.model in CLAUDE_ALIASES and model != req.model:
            self._remember(req.model, model, None)
        rate = _rate_from(envelope.get("_rate"))
        structured = envelope.get("structured_output")

        if req.tools:
            calls_raw = structured.get("calls") if isinstance(structured, dict) else None
            calls = [ToolCall(f"call_{uuid.uuid4().hex[:12]}", c["name"], c.get("arguments") or {})
                     for c in (calls_raw or []) if isinstance(c, dict) and c.get("name")]
            if not calls:
                raise MalformedResponse(PROVIDER, "tool step returned no calls")
            return CompletionResponse(provider=PROVIDER, model=model,
                                      text=str(structured.get("note") or structured.get("thought") or ""),
                                      tool_calls=calls, usage=usage, rate=rate)
        if req.output_schema is not None:
            if not isinstance(structured, dict):
                try:
                    structured = json.loads(envelope.get("result") or "")
                except json.JSONDecodeError:
                    raise MalformedResponse(PROVIDER, "structured output missing") from None
            return CompletionResponse(provider=PROVIDER, model=model, text=json.dumps(structured),
                                      structured=structured, usage=usage, rate=rate)
        return CompletionResponse(provider=PROVIDER, model=model, text=str(envelope.get("result") or ""),
                                  usage=usage, rate=rate)

    # ---- the last usage windows the CLI reported, kept across restarts ----
    def _save_rate(self, info: dict[str, Any] | None) -> None:
        try:
            tmp = self.runtime_dir / f"last-usage.{uuid.uuid4().hex}.tmp"
            tmp.write_text(json.dumps({"info": info or {}, "at": time.time()}))
            tmp.replace(self.runtime_dir / "last-usage.json")
        except OSError as e:
            log.info("could not save claude usage: %s", e)

    def _load_rate(self) -> RateInfo | None:
        try:
            saved = json.loads((self.runtime_dir / "last-usage.json").read_text())
        except (OSError, json.JSONDecodeError):
            return None
        self._rate_at = float(saved.get("at") or 0)
        return _rate_from(saved.get("info"))

    async def usage(self) -> RateInfo | None:
        """Claude has no usage endpoint: reuse the last reported windows, refreshed by a tiny call when stale."""
        if self.last_rate is not None and time.time() - self._rate_at < USAGE_TTL_S:
            return self.last_rate
        cmd = [self.auth.bin, *isolation_flags(self.mcp_config), "--model", "haiku",
               "--system-prompt", "Reply with the single word ok."]
        try:
            await self._run(cmd, "ok?", 60)
        except ProviderError as e:
            log.info("claude usage refresh failed: %s", e)
        return self.last_rate

    # ---- which concrete model each alias means right now (asked from the CLI itself, cached for a day) ----
    @property
    def resolved_path(self) -> Path:
        return self.runtime_dir / "resolved-models.json"

    def _resolved(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.resolved_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _remember(self, alias: str, model: str | None, unavailable: str | None) -> None:
        data = self._resolved()
        data[alias] = {"model": model, "unavailable": unavailable, "at": time.time()}
        tmp = self.resolved_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(self.resolved_path)

    async def _probe(self, alias: str) -> None:
        cmd = [self.auth.bin, *isolation_flags(self.mcp_config), "--model", alias,
               "--system-prompt", "Reply with the single word ok."]
        try:
            envelope = await self._run(cmd, "ok?", 90)
        except ModelUnavailable:
            self._remember(alias, None, "not included in your plan")
            return
        except ProviderError as e:  # offline or limited: try again next time
            log.info("could not resolve claude alias %s: %s", alias, e)
            return
        self._remember(alias, envelope.get("_model") or next(iter(envelope.get("modelUsage") or {}), None), None)

    async def list_models(self) -> list[ModelInfo]:
        known = self._resolved()
        stale = [a for a in CLAUDE_ALIASES
                 if a not in known or time.time() - float(known[a].get("at") or 0) > RESOLVE_TTL_S]
        if stale:
            await asyncio.gather(*(self._probe(a) for a in stale))
            known = self._resolved()
        out = []
        for i, alias in enumerate(CLAUDE_ALIASES):
            info = known.get(alias) or {}
            concrete = info.get("model")
            out.append(ModelInfo(PROVIDER, alias, display_name=display_name(concrete) if concrete else f"Latest {alias}",
                                 priority=i + 1, unavailable=info.get("unavailable")))
        return out

    async def health(self) -> ProviderHealth:
        st = await asyncio.to_thread(self.auth.status)
        if not st["available"]:
            return ProviderHealth(PROVIDER, False, f"claude CLI not found at {self.auth.bin!r}")
        if not st["connected"]:
            return ProviderHealth(PROVIDER, False, "not logged in: run `tf login claude`")
        return ProviderHealth(PROVIDER, True, "connected", account=f"{st['email']} ({st['subscription']})")
