# Claude & Codex (ChatGPT) Subscription Auth: Reference

Extracted from Operis V2.0 so it can be reused in another project. It covers two ways a backend can call
models with a user's **subscription** instead of an API key:

| Provider | Mechanism | Where tokens live |
|---|---|---|
| **ChatGPT / Codex** | Native OAuth 2.0 + PKCE against `auth.openai.com` (same client as Codex CLI), then direct HTTP calls to `chatgpt.com/backend-api/codex/responses` | Our own `auth.json` (chmod 600) |
| **Claude** | Drives the official `claude` CLI (`claude auth login --claudeai`), then shells out to `claude -p` for each model call | Managed by the Claude CLI itself (`~/.claude/`) |

---

## 1. Where it lives in Operis

| Concern | File |
|---|---|
| ChatGPT OAuth (PKCE, device code, callback server, refresh, headers, model list) | `backend/app/services/chatgpt_auth_portal.py` |
| Claude CLI login / status / logout / model catalog | `backend/app/services/claude_code_auth.py` |
| Admin HTTP routes (`/admin/ai-models/...`) | `backend/app/api/routes/ai_models.py` |
| Choosing the active provider and model, plus validation | `backend/app/services/ai_model_selection.py` |
| Provider enum (`chatgpt_auth`, `claude_code_oauth`) and `chatgpt_model_id` column | `backend/app/db/models/auth.py` (lines 22–23, 220) |
| **Model calls**: `ChatGptAuthDecisionProvider` (Responses API over SSE) | `ai-agent/providers.py:599` |
| **Model calls**: `ClaudeCodeOAuthDecisionProvider` (`claude -p` subprocess) | `ai-agent/providers.py:871` |
| Where a provider gets picked at runtime | `backend/app/services/incident_agent.py:903`, `backend/app/services/ticket_ai_prefill.py:163` |

### Routes

```
GET  /admin/ai-models/chatgpt/status
POST /admin/ai-models/chatgpt/login/start        body: {"device_code": bool}
GET  /admin/ai-models/chatgpt/oauth/callback     (public; OAuth redirect target)
GET  /admin/ai-models/chatgpt/models
POST /admin/ai-models/chatgpt/logout

GET  /admin/ai-models/claude/status
POST /admin/ai-models/claude/login/start
POST /admin/ai-models/claude/login/complete      body: {"code": "..."}  (code or full callback URL)
GET  /admin/ai-models/claude/models
POST /admin/ai-models/claude/logout
```

---

## 2. ChatGPT / Codex: how it works

### Constants

```python
ISSUER          = "https://auth.openai.com"
CLIENT_ID       = "app_EMoamEEZ73f0CkXaXp7hrann"          # public Codex CLI client id
CODEX_BASE_URL  = "https://chatgpt.com/backend-api/codex"
CALLBACK_PORT   = 1455                                     # Codex CLI's registered loopback port
REDIRECT_URI    = "http://localhost:1455/auth/callback"
SCOPES          = "openid profile email offline_access api.connectors.read api.connectors.invoke"
REFRESH_SKEW    = 90  # seconds before JWT exp to refresh
```

> ⚠️ The client id is registered for `http://localhost:1455/auth/callback`. Operis also builds a
> `BACKEND_PUBLIC_URL/.../chatgpt/oauth/callback` redirect when that env var is set, but OpenAI will most
> likely reject a redirect URI that isn't registered for this client. For a remote server, use the
> **device-code flow** instead.

### Flow A: browser (PKCE + loopback callback)

1. Generate PKCE: `verifier = b64url(64 random bytes)`, `challenge = b64url(sha256(verifier))` (strip `=`).
2. Generate `state = token_urlsafe(32)` and store `{state: verifier}` in memory.
3. Start a tiny TCP/HTTP server on `127.0.0.1:1455` that handles `GET /auth/callback?code=&state=`.
4. Send the user to:
   ```
   {ISSUER}/oauth/authorize?response_type=code&client_id=...&redirect_uri=...&scope=...
     &code_challenge=...&code_challenge_method=S256&id_token_add_organizations=true
     &codex_cli_simplified_flow=true&state=...&originator=codex_cli_rs
   ```
5. In the callback, pop `state` to get `verifier`, then exchange the code (**form-encoded**):
   ```
   POST {ISSUER}/oauth/token
   grant_type=authorization_code&code=...&redirect_uri=<same as step 4>&client_id=...&code_verifier=...
   ```
   → `{id_token, access_token, refresh_token}`

### Flow B: device code (headless / remote servers)

1. `POST {ISSUER}/api/accounts/deviceauth/usercode` JSON `{"client_id": CLIENT_ID}`
   → `{user_code, device_auth_id, interval}`
2. Show the user `{ISSUER}/codex/device` and the `user_code`.
3. Poll `POST {ISSUER}/api/accounts/deviceauth/token` JSON `{device_auth_id, user_code}` every `interval` seconds.
   A **403/404 means still pending**. Give up after 15 minutes. Success → `{authorization_code, code_verifier}`
   (the server hands back the verifier).
4. Exchange the code exactly as in Flow A, but with `redirect_uri = {ISSUER}/deviceauth/callback`.

### Token claims (decode the JWT payload, no signature check needed for display)

```python
raw     = jwt_payload(id_token) or jwt_payload(access_token)
auth    = raw["https://api.openai.com/auth"]
profile = raw["https://api.openai.com/profile"]
email        = raw.get("email") or profile.get("email")
plan_type    = auth["chatgpt_plan_type"]         # plus / pro / team ...
account_id   = auth["chatgpt_account_id"]        # REQUIRED for API calls
is_fedramp   = auth.get("chatgpt_account_is_fedramp")
```

### Refresh (note: **JSON** body, unlike the code exchange)

```
POST {ISSUER}/oauth/token
{"client_id": CLIENT_ID, "grant_type": "refresh_token", "refresh_token": "..."}
```
Refresh when `exp - now <= 90s`. Keep the old refresh_token if the response doesn't include a new one.
Guard refresh with a lock so concurrent requests don't double-refresh.

### Calling models

Headers:
```
authorization:      Bearer <access_token>
chatgpt-account-id: <account_id>
content-type:       application/json
accept:             text/event-stream
x-openai-fedramp:   true              # only if claim says so
```

List models: `GET {CODEX_BASE_URL}/models?client_version=<v>`. Operis tries `1.0.0`, `2.0.0`, `0.99.0`
and keeps whichever returns the most models. A model is hidden when `visibility != "list"` or
`supported_in_api is False`. Cache the last good list to disk as a fallback.

Inference: `POST {CODEX_BASE_URL}/responses` (OpenAI **Responses API** shape, `stream: true`, `store: false`):
```json
{
  "model": "<slug>",
  "instructions": "<system prompt>",
  "input": [{"type":"message","role":"user","content":[{"type":"input_text","text":"..."}]}],
  "tools": [], "tool_choice": "none", "parallel_tool_calls": false,
  "reasoning": {"effort": "medium"}, "store": false, "stream": true, "include": [],
  "text": {"verbosity": "medium"}
}
```
SSE events to handle: `response.output_text.delta` (stream text), `response.output_item.done`
(collect `message` text and `function_call` items for tool use), `response.completed`, `error`.
Structured output: `"text": {"format": {"type":"json_schema","strict":true,"name":"...","schema":{...}}}`.
Tools use the Responses format: `{"type":"function","name","description","parameters","strict":false}`.
Tool results go back as `{"type":"function_call_output","call_id","output"}`.

---

## 3. Claude: how it works

Operis doesn't speak OAuth to Anthropic directly. It runs the official **Claude Code CLI** (`claude`),
which owns the credentials.

| Step | Command / behavior |
|---|---|
| Start login | `Popen(["claude","auth","login","--claudeai"] (+ ["--email", e]), stdin=PIPE, stdout=logfile, start_new_session=True)` |
| Get auth URL | Poll the log file (10 × 0.4s) and regex `https?://[^\s)>"]+` to find the URL. Show it to the user |
| Complete login | User pastes the code (or the full callback URL; pull `?code=` out of it). Write `code + "\n"` to the process's **stdin**, close stdin |
| Confirm | Poll `claude auth status` (JSON) once a second for up to 30s until `loggedIn: true` |
| Status | `claude auth status` → JSON with `loggedIn`, `email`, `subscriptionType` |
| Logout | Kill any pending login process, then run `claude auth logout` |
| Model list | No API. Operis reads `~/.claude/stats-cache.json` → `modelUsage` keys that match `^claude-(haiku\|sonnet\|opus)-\d+-\d+(-\d{8})?$`, keeps the newest per family, and falls back to the aliases `sonnet`, `haiku`, `opus` |
| Selected model | Stored in a small JSON state file (`{"model_id": ...}`). An alias resolves to the newest discovered full id |

Inference (`ai-agent/providers.py:875`):
```
claude -p --model <id> --system-prompt <sys> --output-format text \
       --permission-mode dontAsk --tools "" --no-session-persistence \
       [--json-schema '<schema json>'] "<user prompt>"
```
- `--tools ""` turns off the CLI's own tools, so it acts as a plain LLM.
- `--json-schema` enforces structured output.
- **Images**: decode the data URL to a temp file and reference it in the prompt as `@/tmp/file.png`.
- **Tool calling** is emulated. The prompt holds the tool list and the conversation as JSON, and the model
  must return `{"type":"tool_call","tool_name","arguments"}` or `{"type":"final","report"}`. Operis
  converts that into OpenAI-style `tool_calls`.

---

## 4. Standalone portable implementation (Python 3.11+, httpx)

Framework-free rewrite of the logic above. Drop it in and wire it to your own routes.

```python
"""subscription_auth.py: ChatGPT (Codex) OAuth and Claude CLI auth, framework-agnostic."""
from __future__ import annotations

import asyncio, base64, hashlib, json, os, re, secrets, shutil, subprocess, time
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

# ───────────────────────────── ChatGPT / Codex ─────────────────────────────

ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CALLBACK_PORT = 1455
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/auth/callback"
SCOPES = "openid profile email offline_access api.connectors.read api.connectors.invoke"


class ChatGptAuthError(RuntimeError):
    pass


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def jwt_payload(token: str | None) -> dict[str, Any]:
    try:
        part = (token or "").split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def chatgpt_claims(id_token: str | None, access_token: str | None) -> dict[str, Any]:
    raw = jwt_payload(id_token) or jwt_payload(access_token)
    auth = raw.get("https://api.openai.com/auth") or {}
    profile = raw.get("https://api.openai.com/profile") or {}
    return {
        "email": raw.get("email") or profile.get("email"),
        "plan_type": auth.get("chatgpt_plan_type"),
        "account_id": auth.get("chatgpt_account_id"),
        "is_fedramp": bool(auth.get("chatgpt_account_is_fedramp")),
        "exp": raw.get("exp"),
    }


class ChatGptAuth:
    def __init__(self, auth_file: Path):
        self.auth_file = auth_file
        self._pending: dict[str, str] = {}          # state -> code_verifier
        self._lock = asyncio.Lock()
        self._server: asyncio.AbstractServer | None = None

    # ---- storage ----
    def load(self) -> dict | None:
        try:
            return json.loads(self.auth_file.read_text())
        except Exception:
            return None

    def _save(self, data: dict) -> None:
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.auth_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.auth_file)

    def _persist(self, tokens: dict) -> dict:
        claims = chatgpt_claims(tokens.get("id_token"), tokens.get("access_token"))
        data = {
            "id_token": tokens.get("id_token"),
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "account_id": claims["account_id"],
            "claims": claims,
            "updated_at": time.time(),
        }
        self._save(data)
        return data

    # ---- token endpoint ----
    async def _exchange(self, code: str, verifier: str, redirect_uri: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{ISSUER}/oauth/token", data={       # form-encoded
                "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                "client_id": CLIENT_ID, "code_verifier": verifier,
            })
        if r.status_code >= 400:
            raise ChatGptAuthError(f"token exchange failed {r.status_code}: {r.text[:500]}")
        return self._persist(r.json())

    async def ensure_fresh(self, force: bool = False) -> dict:
        async with self._lock:
            data = self.load()
            if not data or not data.get("refresh_token"):
                raise ChatGptAuthError("not connected")
            exp = jwt_payload(data["access_token"]).get("exp")
            if not force and (not exp or exp - time.time() > 90):
                return data
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(f"{ISSUER}/oauth/token", json={   # JSON body for refresh
                    "client_id": CLIENT_ID, "grant_type": "refresh_token",
                    "refresh_token": data["refresh_token"],
                })
            if r.status_code >= 400:
                raise ChatGptAuthError(f"refresh failed {r.status_code}: {r.text[:500]}")
            new = r.json()
            return self._persist({
                "id_token": new.get("id_token") or data.get("id_token"),
                "access_token": new.get("access_token") or data["access_token"],
                "refresh_token": new.get("refresh_token") or data["refresh_token"],
            })

    # ---- Flow A: browser + loopback ----
    async def browser_login_start(self) -> str:
        await self._ensure_callback_server()
        verifier = _b64url(secrets.token_bytes(64))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        state = secrets.token_urlsafe(32)
        self._pending[state] = verifier
        return f"{ISSUER}/oauth/authorize?" + urlencode({
            "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
            "scope": SCOPES, "code_challenge": challenge, "code_challenge_method": "S256",
            "id_token_add_organizations": "true", "codex_cli_simplified_flow": "true",
            "state": state, "originator": "codex_cli_rs",
        })

    async def handle_callback(self, code: str | None, state: str | None, error: str | None = None) -> dict:
        if error:
            raise ChatGptAuthError(error)
        verifier = self._pending.pop(state or "", None)
        if not code or not verifier:
            raise ChatGptAuthError("invalid or expired state")
        return await self._exchange(code, verifier, REDIRECT_URI)

    async def _ensure_callback_server(self) -> None:
        if self._server and self._server.is_serving():
            return

        async def on_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            ok = True
            try:
                head = (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)).decode(errors="replace")
                target = urlparse(head.split()[1])
                if target.path != "/auth/callback":
                    raise ChatGptAuthError("bad path")
                q = parse_qs(target.query)
                await self.handle_callback((q.get("code") or [None])[0], (q.get("state") or [None])[0],
                                           (q.get("error") or [None])[0])
            except Exception:
                ok = False
            body = (b"<h2>Login complete. You can close this tab.</h2><script>setTimeout(()=>close(),1200)</script>"
                    if ok else b"<h2>Login failed. Retry from the app.</h2>")
            writer.write(b"HTTP/1.1 " + (b"200 OK" if ok else b"400 Bad Request") +
                         b"\r\nContent-Type: text/html\r\nContent-Length: " + str(len(body)).encode() +
                         b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain(); writer.close()

        self._server = await asyncio.start_server(on_conn, "127.0.0.1", CALLBACK_PORT)

    # ---- Flow B: device code ----
    async def device_login_start(self) -> dict:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{ISSUER}/api/accounts/deviceauth/usercode", json={"client_id": CLIENT_ID})
        r.raise_for_status()
        d = r.json()
        info = {
            "verification_url": f"{ISSUER}/codex/device",
            "user_code": d.get("user_code") or d.get("usercode"),
            "device_auth_id": d["device_auth_id"],
            "interval": max(1, int(d.get("interval") or 5)),
        }
        asyncio.create_task(self._device_poll(info))   # finish in background
        return {"verification_url": info["verification_url"], "user_code": info["user_code"]}

    async def _device_poll(self, info: dict, timeout_s: int = 900) -> None:
        deadline = time.time() + timeout_s
        async with httpx.AsyncClient(timeout=30) as c:
            while time.time() < deadline:
                r = await c.post(f"{ISSUER}/api/accounts/deviceauth/token",
                                 json={"device_auth_id": info["device_auth_id"], "user_code": info["user_code"]})
                if r.status_code in (403, 404):          # still pending
                    await asyncio.sleep(info["interval"]); continue
                r.raise_for_status()
                d = r.json()
                await self._exchange(d["authorization_code"], d["code_verifier"], f"{ISSUER}/deviceauth/callback")
                return
        raise ChatGptAuthError("device login timed out")

    # ---- API usage ----
    async def headers(self, accept: str = "text/event-stream") -> dict[str, str]:
        d = await self.ensure_fresh()
        if not d.get("account_id"):
            raise ChatGptAuthError("missing chatgpt account id")
        h = {"authorization": f"Bearer {d['access_token']}", "chatgpt-account-id": d["account_id"],
             "content-type": "application/json", "accept": accept}
        if d["claims"].get("is_fedramp"):
            h["x-openai-fedramp"] = "true"
        return h

    async def list_models(self) -> list[dict]:
        h = await self.headers("application/json")
        best: list[dict] = []
        async with httpx.AsyncClient(timeout=30) as c:
            for v in ("1.0.0", "2.0.0", "0.99.0"):
                r = await c.get(f"{CODEX_BASE_URL}/models", params={"client_version": v}, headers=h)
                if r.status_code < 400:
                    ms = r.json().get("models") or r.json().get("data") or []
                    if len(ms) > len(best):
                        best = ms
        return [{"id": m.get("slug") or m.get("id"), "name": m.get("display_name"),
                 "hidden": (m.get("visibility") or "list") != "list" or m.get("supported_in_api") is False,
                 "priority": m.get("priority") or 0} for m in best]

    async def stream_text(self, model: str, system: str, user: str) -> AsyncIterator[str]:
        payload = {
            "model": model, "instructions": system,
            "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": user}]}],
            "tools": [], "tool_choice": "none", "parallel_tool_calls": False,
            "reasoning": {"effort": "medium"}, "store": False, "stream": True, "include": [],
            "text": {"verbosity": "medium"},
        }
        async with httpx.AsyncClient(timeout=180) as c:
            async with c.stream("POST", f"{CODEX_BASE_URL}/responses", headers=await self.headers(), json=payload) as r:
                if r.status_code >= 400:
                    raise ChatGptAuthError(f"responses {r.status_code}: {(await r.aread())[:500]!r}")
                async for line in r.aiter_lines():
                    if not line.startswith("data: ") or line[6:] == "[DONE]":
                        continue
                    ev = json.loads(line[6:])
                    if ev.get("type") == "response.output_text.delta":
                        yield ev.get("delta", "")
                    elif ev.get("type") == "error":
                        raise ChatGptAuthError(json.dumps(ev))


# ───────────────────────────── Claude (via official CLI) ─────────────────────────────

URL_RE = re.compile(r"https?://[^\s)>\"]+")
MODEL_RE = re.compile(r"^claude-(haiku|sonnet|opus)-(\d+)-(\d+)(?:-(\d{8}))?$")


class ClaudeCliAuth:
    def __init__(self, log_path: Path):
        self.bin = shutil.which("claude")
        self.log_path = log_path
        self._proc: subprocess.Popen[str] | None = None

    def status(self) -> dict:
        if not self.bin:
            return {"available": False, "connected": False}
        r = subprocess.run([self.bin, "auth", "status"], capture_output=True, text=True, timeout=15)
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError:
            d = {}
        return {"available": True, "connected": bool(d.get("loggedIn")),
                "email": d.get("email"), "subscription": d.get("subscriptionType")}

    async def login_start(self, email: str | None = None) -> dict:
        if self._proc and self._proc.poll() is None:
            return {"started": True, "pending": True}
        cmd = [self.bin, "auth", "login", "--claudeai"] + (["--email", email] if email else [])
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("w") as log:
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                          text=True, start_new_session=True)
        for _ in range(10):                       # scrape the auth URL the CLI prints
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
            raise RuntimeError("no pending login")
        p.stdin.write(code + "\n"); p.stdin.flush(); p.stdin.close()
        for _ in range(30):
            await asyncio.sleep(1)
            if self.status()["connected"]:
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
        subprocess.run([self.bin, "auth", "logout"], capture_output=True, text=True, timeout=20)

    def discover_models(self) -> list[str]:
        try:
            usage = json.loads((Path.home() / ".claude" / "stats-cache.json").read_text()).get("modelUsage", {})
        except Exception:
            usage = {}
        latest: dict[str, tuple] = {}
        for mid in usage:
            m = MODEL_RE.match(mid)
            if m:
                key = (int(m[2]), int(m[3]), int(m[4] or 0))
                if m[1] not in latest or key > latest[m[1]][0]:
                    latest[m[1]] = (key, mid)
        return [v[1] for v in latest.values()] or ["sonnet", "haiku", "opus"]

    async def complete(self, model: str, system: str, user: str, schema: dict | None = None) -> str:
        cmd = [self.bin, "-p", "--model", model, "--system-prompt", system, "--output-format", "text",
               "--permission-mode", "dontAsk", "--tools", "", "--no-session-persistence"]
        if schema:
            cmd += ["--json-schema", json.dumps(schema)]
        proc = await asyncio.create_subprocess_exec(*cmd, user, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"claude failed: {(err or out).decode(errors='replace')[:800]}")
        return out.decode(errors="replace").strip()
```

---

## 5. Gotchas learned in Operis

- **Code exchange is form-encoded, refresh is JSON.** Mixing them up returns a 400.
- **`chatgpt-account-id` header is mandatory.** It comes from the `https://api.openai.com/auth` JWT claim.
- Device-code polling returns **403/404 while pending**. That's normal, not an error.
- The device flow's code exchange uses `redirect_uri = https://auth.openai.com/deviceauth/callback`.
- The loopback callback must bind `127.0.0.1:1455`. If something else holds the port (e.g. a running
  Codex CLI login), the browser flow fails, so fall back to device code.
- The `/models` response varies by `client_version`. Try several values and keep the largest list.
- The Claude CLI login is **interactive on stdin**. Keep the `Popen` handle alive between the "start" and
  "complete" HTTP requests (in-process singleton). It won't survive a backend restart or multiple workers.
- Claude's `--tools ""` is what stops the CLI from acting as an agent. Without it, it can run its own tools.
- Both providers hold state in-process (pending logins, PKCE verifiers), so run a **single worker**
  or move that state to Redis/DB.
- **ToS:** both approaches reuse first-party CLI clients to drive consumer subscriptions from a server.
  Check the current OpenAI and Anthropic terms before shipping this in a product.
