"""Settings, Accounts & keys: the YouTube API key and the scraping-account logins (TikTok, Instagram, X).

Secrets go in and never come out: responses carry only whether something is set, a masked hint and dates. Logins
open a real browser window on this machine; the owner logs in there and only the session is kept.
"""
import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from tf_agent.credentials import KEYS, SESSIONS
from tf_agent.tools.types import ToolFailure
from tf_backend.api.deps import ctx, require_client_header
from tf_backend.app_context import AppContext

router = APIRouter(tags=["settings"])


def _browser(c: AppContext) -> Any:
    if c.browser is None:
        raise HTTPException(503, "accounts aren't available in this process")
    return c.browser


def _items(c: AppContext) -> list[dict[str, Any]]:
    browser = _browser(c)
    items = browser.creds.describe()
    for item in items:
        item["connecting"] = browser.connecting.get(item["id"]) if item["kind"] == "session" else None
    return items


def _item(c: AppContext, item_id: str) -> dict[str, Any]:
    return next(i for i in _items(c) if i["id"] == item_id)


@router.get("/settings/accounts")
async def list_accounts(c: AppContext = Depends(ctx)) -> dict[str, Any]:
    return {"items": _items(c)}


class KeyIn(BaseModel):
    value: str = Field(max_length=512)


@router.put("/settings/accounts/{item_id}", dependencies=[Depends(require_client_header)])
async def set_key(item_id: str, body: KeyIn, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    if item_id not in KEYS:
        raise HTTPException(422, f"{item_id} isn't a key that can be typed in")
    value = body.value.strip()
    if not value:
        raise HTTPException(422, "the key is empty")
    if item_id == "youtube_api_key" and c.youtube_api is not None:
        try:
            await c.youtube_api.check_key(value)
        except ToolFailure as e:  # never echo the value back
            raise HTTPException(422, e.error.message) from None
    _browser(c).creds.set(item_id, value)
    return _item(c, item_id)


@router.delete("/settings/accounts/{item_id}", dependencies=[Depends(require_client_header)])
async def remove(item_id: str, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    if item_id not in KEYS and item_id not in SESSIONS:
        raise HTTPException(404, f"unknown item {item_id}")
    _browser(c).creds.delete(item_id)
    return _item(c, item_id)


@router.post("/settings/accounts/{platform}/connect", status_code=202,
             dependencies=[Depends(require_client_header)])
async def connect(platform: str, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    if platform not in SESSIONS:
        raise HTTPException(422, f"{platform} has no account to connect")
    browser = _browser(c)
    if (browser.connecting.get(platform) or {}).get("state") == "waiting":
        raise HTTPException(409, "a login window for this account is already open")
    browser.connecting[platform] = {"state": "waiting", "message": "Opening the login window…"}
    task = asyncio.create_task(browser.connect(platform))
    tasks: set[asyncio.Task[Any]] = c.extras.setdefault("connect_tasks", set())
    tasks.add(task)  # keep a reference until it ends
    task.add_done_callback(tasks.discard)
    return {"state": "waiting"}
