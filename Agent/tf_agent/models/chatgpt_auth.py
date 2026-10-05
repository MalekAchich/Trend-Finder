"""ChatGPT (Codex backend) subscription OAuth: PKCE browser flow + device-code flow.

Ported from the owner's Operis reference: Docs/Code docs/claude-codex-auth-reference.md.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # public Codex CLI client id
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CALLBACK_PORT = 1455  # the loopback port registered for this client id
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/auth/callback"
DEVICE_REDIRECT_URI = f"{ISSUER}/deviceauth/callback"
SCOPES = "openid profile email offline_access api.connectors.read api.connectors.invoke"
REFRESH_SKEW_S = 90

ClientFactory = Callable[[], httpx.AsyncClient]


class ChatGptAuthError(RuntimeError):
    pass


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


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
    def __init__(
        self,
        auth_file: Path,
        client_factory: ClientFactory | None = None,
        callback_port: int = CALLBACK_PORT,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.auth_file = Path(auth_file)
        self.callback_port = callback_port
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=30))
        self._clock = clock
        self._pending: dict[str, str] = {}  # state -> PKCE verifier
        self._lock = asyncio.Lock()
        self._server: asyncio.AbstractServer | None = None
        self._device_task: asyncio.Task[None] | None = None

    # ---- storage ----
    def load(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.auth_file.read_text())
        except Exception:
            return None

    def _save(self, data: dict[str, Any]) -> None:
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.auth_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.auth_file)

    def _persist(self, tokens: dict[str, Any]) -> dict[str, Any]:
        claims = chatgpt_claims(tokens.get("id_token"), tokens.get("access_token"))
        data = {
            "id_token": tokens.get("id_token"),
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "account_id": claims["account_id"],
            "claims": claims,
            "updated_at": self._clock(),
        }
        self._save(data)
        return data

    def status(self) -> dict[str, Any]:
        data = self.load()
        if not data or not data.get("refresh_token"):
            return {"connected": False, "email": None, "plan": None}
        claims = data.get("claims") or {}
        return {"connected": True, "email": claims.get("email"), "plan": claims.get("plan_type")}

    def logout(self) -> None:
        self.auth_file.unlink(missing_ok=True)

    # ---- token endpoint ----
    async def _exchange(self, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
        async with self._client_factory() as client:
            r = await client.post(f"{ISSUER}/oauth/token", data={  # form-encoded (auth reference §5)
                "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                "client_id": CLIENT_ID, "code_verifier": verifier,
            })
        if r.status_code >= 400:
            raise ChatGptAuthError(f"token exchange failed ({r.status_code})")
        return self._persist(r.json())

    async def ensure_fresh(self, force: bool = False) -> dict[str, Any]:
        async with self._lock:
            data = self.load()
            if not data or not data.get("refresh_token"):
                raise ChatGptAuthError("not connected: run `tf login chatgpt`")
            exp = jwt_payload(data["access_token"]).get("exp")
            if not force and (not exp or exp - self._clock() > REFRESH_SKEW_S):
                return data
            async with self._client_factory() as client:
                r = await client.post(f"{ISSUER}/oauth/token", json={  # JSON body for refresh
                    "client_id": CLIENT_ID, "grant_type": "refresh_token",
                    "refresh_token": data["refresh_token"],
                })
            if r.status_code >= 400:
                raise ChatGptAuthError(f"token refresh rejected ({r.status_code}): run `tf login chatgpt` again")
            new = r.json()
            return self._persist({
                "id_token": new.get("id_token") or data.get("id_token"),
                "access_token": new.get("access_token") or data["access_token"],
                "refresh_token": new.get("refresh_token") or data["refresh_token"],
            })

    async def headers(self, accept: str = "text/event-stream") -> dict[str, str]:
        data = await self.ensure_fresh()
        if not data.get("account_id"):
            raise ChatGptAuthError("missing chatgpt account id in token claims")
        h = {
            "authorization": f"Bearer {data['access_token']}",
            "chatgpt-account-id": data["account_id"],
            "content-type": "application/json",
            "accept": accept,
        }
        if (data.get("claims") or {}).get("is_fedramp"):
            h["x-openai-fedramp"] = "true"
        return h

    # ---- Flow A: browser + loopback callback ----
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

    async def handle_callback(self, code: str | None, state: str | None, error: str | None = None) -> dict[str, Any]:
        if error:
            raise ChatGptAuthError(f"login refused: {error}")
        verifier = self._pending.pop(state or "", None)
        if not code or not verifier:
            raise ChatGptAuthError("invalid or expired login state; start the login again")
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
            writer.write(b"HTTP/1.1 " + (b"200 OK" if ok else b"400 Bad Request")
                         + b"\r\nContent-Type: text/html\r\nContent-Length: " + str(len(body)).encode()
                         + b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
            writer.close()

        try:
            self._server = await asyncio.start_server(on_conn, "127.0.0.1", self.callback_port)
        except OSError as e:
            raise ChatGptAuthError(
                f"port {self.callback_port} is busy ({e.strerror}); is a Codex CLI login running? "
                "Use `tf login chatgpt --device` instead."
            ) from e

    async def stop_callback_server(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ---- Flow B: device code ----
    async def device_login_start(self) -> dict[str, str]:
        async with self._client_factory() as client:
            r = await client.post(f"{ISSUER}/api/accounts/deviceauth/usercode", json={"client_id": CLIENT_ID})
        if r.status_code >= 400:
            raise ChatGptAuthError(f"device login could not start ({r.status_code})")
        d = r.json()
        info = {
            "verification_url": f"{ISSUER}/codex/device",
            "user_code": d.get("user_code") or d.get("usercode"),
            "device_auth_id": d["device_auth_id"],
            "interval": max(1, int(d.get("interval") or 5)),
        }
        self._device_task = asyncio.create_task(self._device_poll(info))
        return {"verification_url": info["verification_url"], "user_code": info["user_code"]}

    async def _device_poll(self, info: dict[str, Any], timeout_s: int = 900) -> None:
        deadline = self._clock() + timeout_s
        async with self._client_factory() as client:
            while self._clock() < deadline:
                r = await client.post(f"{ISSUER}/api/accounts/deviceauth/token",
                                      json={"device_auth_id": info["device_auth_id"], "user_code": info["user_code"]})
                if r.status_code in (403, 404):  # still pending (auth reference §5)
                    await asyncio.sleep(info["interval"])
                    continue
                if r.status_code >= 400:
                    raise ChatGptAuthError(f"device login failed ({r.status_code})")
                d = r.json()
                await self._exchange(d["authorization_code"], d["code_verifier"], DEVICE_REDIRECT_URI)
                return
        raise ChatGptAuthError("device login timed out")

    async def wait_connected(self, since: float, timeout_s: float = 900, interval_s: float = 0.25) -> bool:
        """Wait until a login newer than `since` has been persisted."""
        deadline = self._clock() + timeout_s
        while self._clock() < deadline:
            data = self.load()
            if data and data.get("refresh_token") and float(data.get("updated_at") or 0) >= since:
                return True
            task = self._device_task
            if task is not None and task.done() and task.exception() is not None:
                raise ChatGptAuthError(str(task.exception()))
            await asyncio.sleep(interval_s)
        return False
