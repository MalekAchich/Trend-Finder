"""Manually chosen videos: the owner's permanent list of references (intel) and targets, with cards."""
import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from tf_agent.manual import MAX_ADD, ManualVideos
from tf_agent.orchestrator.run import InputError
from tf_backend.api.deps import ctx, require_client_header
from tf_backend.app_context import AppContext

router = APIRouter(prefix="/manual-videos", tags=["manual videos"])
CHECKS_AT_ONCE = 3


def _service(c: AppContext) -> ManualVideos:
    if c.manual is None:
        raise HTTPException(503, "manually chosen videos aren't available in this process")
    return c.manual


def _id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(404, "unknown video") from None


def _check_later(c: AppContext, ids: list[uuid.UUID], fresh: bool = False) -> None:
    """Each new link is looked up in the background; its card fills in (or shows the problem) when done."""
    service = _service(c)
    slots: asyncio.Semaphore = c.extras.setdefault("manual_check_slots", asyncio.Semaphore(CHECKS_AT_ONCE))
    tasks: set[asyncio.Task[Any]] = c.extras.setdefault("manual_checks", set())

    async def one(video_id: uuid.UUID) -> None:
        async with slots:
            await service.check(video_id, fresh=fresh)

    for video_id in ids:
        task = asyncio.create_task(one(video_id))
        tasks.add(task)
        task.add_done_callback(tasks.discard)


@router.get("")
async def list_videos(character: str | None = None, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    try:
        return {"items": await _service(c).list(character)}
    except InputError as e:
        raise HTTPException(404, str(e)) from e


class AddIn(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=MAX_ADD)
    reference: bool = True
    target: str | None = Field(None, max_length=64)


@router.post("", status_code=201, dependencies=[Depends(require_client_header)])
async def add_videos(body: AddIn, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    try:
        ids = await _service(c).add(body.urls, reference=body.reference, target=body.target)
    except InputError as e:
        raise HTTPException(422, str(e)) from e
    _check_later(c, ids)
    return {"added": len(ids)}


class RolesIn(BaseModel):
    is_reference: bool | None = None
    target: str | None = Field(None, max_length=64)


@router.patch("/{video_id}", dependencies=[Depends(require_client_header)])
async def set_roles(video_id: str, body: RolesIn, c: AppContext = Depends(ctx)) -> dict[str, str]:
    kw: dict[str, Any] = {"is_reference": body.is_reference}
    if "target" in body.model_fields_set:
        kw["target"] = body.target
    try:
        await _service(c).set_roles(_id(video_id), **kw)
    except KeyError:
        raise HTTPException(404, "unknown video") from None
    except InputError as e:
        raise HTTPException(422, str(e)) from e
    return {"ok": "saved"}


@router.post("/{video_id}/check", status_code=202, dependencies=[Depends(require_client_header)])
async def recheck(video_id: str, c: AppContext = Depends(ctx)) -> dict[str, str]:
    """Look a video up again (for example after connecting the account it needed)."""
    vid = _id(video_id)
    if not any(v["id"] == str(vid) for v in await _service(c).list()):
        raise HTTPException(404, "unknown video")
    await _service(c)._update(vid, status="checking", problem=None)
    _check_later(c, [vid], fresh=True)
    return {"state": "checking"}


@router.delete("/{video_id}", dependencies=[Depends(require_client_header)])
async def remove(video_id: str, c: AppContext = Depends(ctx)) -> dict[str, str]:
    try:
        await _service(c).remove(_id(video_id))
    except KeyError:
        raise HTTPException(404, "unknown video") from None
    return {"ok": "removed"}
