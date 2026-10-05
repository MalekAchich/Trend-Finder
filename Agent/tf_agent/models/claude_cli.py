"""Claude adapter: the official `claude -p` CLI as an isolated subprocess (D-29, D-30, D-36).

Ported login/status/model-discovery logic from Docs/Code docs/claude-codex-auth-reference.md §3.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from tf_agent.models.errors import (
    AuthRequired,
    MalformedResponse,
    ProviderError,
    TransientProviderError,
    UsageLimited,
)
from tf_agent.models.types import (
    CompletionRequest,
    CompletionResponse,
    ImagePart,
    ModelInfo,
    ProviderHealth,
    ToolCall,
    ToolSpec,
    Usage,
)

PROVIDER = "claude"
URL_RE = re.compile(r"https?://[^\s)>\"]+")
MODEL_RE = re.compile(r"^claude-(haiku|sonnet|opus)-(\d+)-(\d+)(?:-(\d{8}))?$")
CLAUDE_ALIASES = ("opus", "sonnet", "haiku")
STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
MAX_SYSTEM_ARG_BYTES = 64_000

LIMIT_RE = re.compile(r"usage limit|rate limit|hit your limit|limit reached|too many requests", re.I)
AUTH_RE = re.compile(r"not logged in|run /login|invalid api key|authenticat|oauth token|unauthorized", re.I)
TRANSIENT_RE = re.compile(r"overloaded|internal server error|bad gateway|service unavailable|timed? ?out|"
                          r"connection (reset|refused|error)", re.I)
RESET_EPOCH_RE = re.compile(r"\|(\d{10})\b")


def clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}


def isolation_flags(empty_mcp_config: Path) -> list[str]:
    return ["-p", "--output-format", "json", "--permission-mode", "dontAsk", "--tools", "",
            "--no-session-persistence", "--strict-mcp-config", "--mcp-config", str(empty_mcp_config),
            "--setting-sources", ""]


def step_schema(tools: list[ToolSpec]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "thought": {"type": "string"},
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


def render_prompt(req: CompletionRequest, image_ref: Callable[[ImagePart], str]) -> str:
    out = ["<conversation>"]
    for m in req.messages:
        if m.role == "user":
            out.append("[user]")
            out.append(m.text())
            out.extend(image_ref(img) for img in m.images())
        elif m.role == "assistant":
            out.append("[assistant]")
            if m.text():
                out.append(m.text())
            if m.tool_calls:
                out.append(json.dumps({"calls": [{"id": c.id, "name": c.name, "arguments": c.arguments}
                                                 for c in m.tool_calls]}, ensure_ascii=False))
        else:
            out.append(f"[tool result id={m.tool_call_id} name={m.tool_name}]")
            out.append(m.text())
    out.append("</conversation>")
    if req.tools:
        out.append("<tools>")
        out.extend(json.dumps({"name": t.name, "description": t.description, "parameters": t.parameters},
                              ensure_ascii=False) for t in req.tools)
        out.append("</tools>")
        out.append('Decide your next step. Reply ONLY with JSON matching the schema: one or more tool calls in '
                   '"calls", each with the tool "name" and "arguments" matching that tool\'s parameters.')
    elif req.output_schema is not None:
        out.append("Reply ONLY with JSON matching the required schema.")
    return "\n".join(out)


def classify_failure(text: str, api_status: Any) -> ProviderError:
    t = (text or "").strip()[:800]
    try:
        status = int(api_status) if api_status is not None else None
    except (TypeError, ValueError):
        status = None
    if status == 429 or LIMIT_RE.search(t):
        m = RESET_EPOCH_RE.search(t)
        return UsageLimited(PROVIDER, t or "usage limited", float(m.group(1)) if m else None)
    if status in (401, 403) or AUTH_RE.search(t):
        return AuthRequired(PROVIDER, t or "authentication required: run `tf login claude`")
    if (status or 0) >= 500 or TRANSIENT_RE.search(t):
        return TransientProviderError(PROVIDER, t or "transient failure")
    return ProviderError(PROVIDER, t or "unknown claude CLI failure")


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

    def discover_models(self) -> list[str]:
        try:
            usage = json.loads(self.stats_path.read_text()).get("modelUsage", {})
        except Exception:
            usage = {}
        latest: dict[str, tuple[tuple[int, int, int], str]] = {}
        for model_id in usage:
            m = MODEL_RE.match(model_id)
            if m:
                key = (int(m[2]), int(m[3]), int(m[4] or 0))
                if m[1] not in latest or key > latest[m[1]][0]:
                    latest[m[1]] = (key, model_id)
        return [v[1] for v in latest.values()]


class ClaudeCLIAdapter:
    name = PROVIDER

    def __init__(self, auth: ClaudeCliAuth, runtime_dir: Path | None = None) -> None:
        self.auth = auth
        self.runtime_dir = Path(runtime_dir or Path(tempfile.gettempdir()) / "tf-claude").resolve()
        if " " in str(self.runtime_dir):
            raise ValueError("runtime_dir must not contain spaces (Claude @-mentions break on spaces)")
        self.cwd = self.runtime_dir / "cwd"
        self.image_dir = self.runtime_dir / "images"
        self.mcp_config = self.runtime_dir / "empty-mcp.json"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        if not self.mcp_config.exists():
            self.mcp_config.write_text('{"mcpServers": {}}')

    def _image_ref(self, img: ImagePart) -> str:
        src = Path(img.path).resolve()
        if " " not in str(src):
            return f"@{src}"
        data = src.read_bytes()
        dest = self.image_dir / f"{hashlib.sha256(data).hexdigest()[:16]}{src.suffix or '.jpg'}"
        if not dest.exists():
            dest.write_bytes(data)
        return f"@{dest}"

    async def _run(self, cmd: list[str], stdin_text: str, timeout_s: float) -> dict[str, Any]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=self.cwd, env=clean_env())
        except (FileNotFoundError, PermissionError) as e:
            raise AuthRequired(PROVIDER, f"claude CLI not found at {self.auth.bin!r}") from e
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin_text.encode()), timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise TransientProviderError(PROVIDER, f"claude -p timed out after {timeout_s}s") from None
        stdout = out.decode(errors="replace").strip()
        stderr = err.decode(errors="replace").strip()
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            failure = classify_failure(f"{stdout}\n{stderr}", None)
            if proc.returncode != 0 and type(failure) is ProviderError:
                raise TransientProviderError(PROVIDER, f"claude -p exited {proc.returncode}: {stderr[:300]}")
            if proc.returncode != 0:
                raise failure
            raise MalformedResponse(PROVIDER, "claude -p did not return JSON") from None
        if envelope.get("is_error") or envelope.get("subtype") not in (None, "success"):
            raise classify_failure(f"{envelope.get('result') or ''} {stderr}", envelope.get("api_error_status"))
        return envelope

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        prompt = render_prompt(req, self._image_ref)
        system = req.system
        if len(system.encode()) > MAX_SYSTEM_ARG_BYTES:
            prompt = f"<system>\n{system}\n</system>\n{prompt}"
            system = "Follow the instructions in the <system> block of the user message."
        cmd = [self.auth.bin, *isolation_flags(self.mcp_config), "--model", req.model, "--system-prompt", system]
        if req.tools:
            cmd += ["--json-schema", json.dumps(step_schema(req.tools))]
        elif req.output_schema is not None:
            cmd += ["--json-schema", json.dumps(req.output_schema)]
        envelope = await self._run(cmd, prompt, req.timeout_s)

        raw_usage = envelope.get("usage") or {}
        usage = Usage(
            input_tokens=sum(int(raw_usage.get(k) or 0) for k in
                             ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
            output_tokens=raw_usage.get("output_tokens"),
        )
        model = next(iter(envelope.get("modelUsage") or {}), req.model)
        structured = envelope.get("structured_output")

        if req.tools:
            calls_raw = structured.get("calls") if isinstance(structured, dict) else None
            calls = [ToolCall(f"call_{uuid.uuid4().hex[:12]}", c["name"], c.get("arguments") or {})
                     for c in (calls_raw or []) if isinstance(c, dict) and c.get("name")]
            if not calls:
                raise MalformedResponse(PROVIDER, "tool step returned no calls")
            return CompletionResponse(provider=PROVIDER, model=model, text=str(structured.get("thought") or ""),
                                      tool_calls=calls, usage=usage)
        if req.output_schema is not None:
            if not isinstance(structured, dict):
                try:
                    structured = json.loads(envelope.get("result") or "")
                except json.JSONDecodeError:
                    raise MalformedResponse(PROVIDER, "structured output missing") from None
            return CompletionResponse(provider=PROVIDER, model=model, text=json.dumps(structured),
                                      structured=structured, usage=usage)
        return CompletionResponse(provider=PROVIDER, model=model, text=str(envelope.get("result") or ""),
                                  usage=usage)

    async def list_models(self) -> list[ModelInfo]:
        infos = [ModelInfo(PROVIDER, alias, display_name=f"latest {alias}", priority=i + 1)
                 for i, alias in enumerate(CLAUDE_ALIASES)]
        infos += [ModelInfo(PROVIDER, mid, display_name=mid, priority=10)
                  for mid in await asyncio.to_thread(self.auth.discover_models)]
        return infos

    async def health(self) -> ProviderHealth:
        st = await asyncio.to_thread(self.auth.status)
        if not st["available"]:
            return ProviderHealth(PROVIDER, False, f"claude CLI not found at {self.auth.bin!r}")
        if not st["connected"]:
            return ProviderHealth(PROVIDER, False, "not logged in: run `tf login claude`")
        return ProviderHealth(PROVIDER, True, "connected", account=f"{st['email']} ({st['subscription']})")
