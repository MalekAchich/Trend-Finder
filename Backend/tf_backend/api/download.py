"""GET /api/download/{canonical_id}?kind=video|audio: the owner's copy of a found or manually chosen video."""
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select

from tf_agent.tools.normalize import canonical_url
from tf_agent.tools.types import CANONICAL_ID_PATTERN, ToolFailure
from tf_backend.api.deps import ctx
from tf_backend.app_context import AppContext
from tf_db.models import ManualVideo, SocialPost, Video

router = APIRouter(prefix="/download", tags=["download"])
MEDIA_TYPES = {".mp4": "video/mp4", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".webm": "video/webm"}


@router.get("/{cid}")
async def download(cid: str, kind: Literal["video", "audio"] = "video", c: AppContext = Depends(ctx)) -> FileResponse:
    if not re.match(CANONICAL_ID_PATTERN, cid):
        raise HTTPException(404, "unknown video")
    if c.downloads is None:
        raise HTTPException(503, "downloads aren't available in this process")
    async with c.sessionmaker() as s:
        video = await s.get(Video, cid)
        manual = None if video else (await s.execute(
            select(ManualVideo).where(ManualVideo.canonical_id == cid))).scalar_one_or_none()
        own = None if video or manual else (await s.execute(
            select(SocialPost).where(SocialPost.canonical_id == cid).limit(1))).scalar_one_or_none()
    if video is None and manual is None and own is None:  # only videos the app knows: never an arbitrary URL
        raise HTTPException(404, "unknown video")
    url = canonical_url(cid, video.url if video else manual.url if manual else own.url)
    try:
        path = await c.downloads.get(cid, url, kind)
    except ToolFailure as e:
        raise HTTPException(502, f"Couldn't download it: {e.error.message}") from None
    creator = re.sub(r"[^\w.-]", "", (video.creator_handle if video else None) or "")
    platform, _, vid = cid.partition(":")
    name = "-".join(p for p in (platform, creator, vid) if p) + ("-sound" if kind == "audio" else "") + path.suffix
    return FileResponse(path, filename=name, media_type=MEDIA_TYPES.get(path.suffix, "application/octet-stream"))
