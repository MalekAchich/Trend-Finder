"""Provider status, model lists and subscription logins (Docs/Code docs/02-model-layer.md)."""
import asyncio
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from tf_agent.models.chatgpt_auth import ChatGptAuthError
from tf_backend.services import Services

CLIENT_HEADER = "x-trendfinder-client"


def require_client_header(request: Request) -> None:
    """Mutating calls need a custom header: browsers can't send one cross-site without a CORS preflight,
    so a random web page can't log the owner out or start logins."""
    if not request.headers.get(CLIENT_HEADER):
        raise HTTPException(403, f"missing {CLIENT_HEADER} header")


router = APIRouter(prefix="/providers", tags=["providers"])


def _sv(request: Request) -> Services:
    return request.app.state.services


def _require(sv: Services, provider: str) -> None:
    if provider not in sv.adapters:
        raise HTTPException(404, f"unknown provider {provider!r}")


@router.get("")
async def list_providers(request: Request) -> list[dict[str, Any]]:
    sv = _sv(request)
    out = []
    for name, adapter in sv.adapters.items():
        h = await adapter.health()
        st = sv.governor.status(name)
        out.append({"provider": name, "connected": h.connected, "detail": h.detail, "account": h.account,
                    "status": st.status, "cooling_until": st.cooling_until, "used_percent": st.used_percent,
                    "last_error": st.last_error})
    return out


@router.get("/{provider}/models")
async def list_models(provider: str, request: Request) -> list[dict[str, Any]]:
    sv = _sv(request)
    _require(sv, provider)
    models = sv.registry.models(provider) or await sv.registry.refresh_provider(provider)
    return [asdict(m) for m in models]


class ChatGptLoginStart(BaseModel):
    device_code: bool = False


@router.post("/chatgpt/login/start", dependencies=[Depends(require_client_header)])
async def chatgpt_login_start(body: ChatGptLoginStart, request: Request) -> dict[str, Any]:
    sv = _sv(request)
    try:
        if body.device_code:
            return await sv.chatgpt_auth.device_login_start()
        return {"auth_url": await sv.chatgpt_auth.browser_login_start()}
    except ChatGptAuthError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/claude/login/start", dependencies=[Depends(require_client_header)])
async def claude_login_start(request: Request) -> dict[str, Any]:
    return await _sv(request).claude_auth.login_start()


class ClaudeLoginComplete(BaseModel):
    code: str


@router.post("/claude/login/complete", dependencies=[Depends(require_client_header)])
async def claude_login_complete(body: ClaudeLoginComplete, request: Request) -> dict[str, bool]:
    sv = _sv(request)
    try:
        connected = await sv.claude_auth.login_complete(body.code)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    await sv.governor.mark_ok("claude")
    return {"connected": connected}


@router.post("/{provider}/logout", dependencies=[Depends(require_client_header)])
async def logout(provider: str, request: Request) -> dict[str, bool]:
    sv = _sv(request)
    _require(sv, provider)
    auth = sv.claude_auth if provider == "claude" else sv.chatgpt_auth
    await asyncio.to_thread(auth.logout)
    return {"ok": True}
