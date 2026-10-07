"""GET /api/preview/{canonical_id}: plays a video on hover (Instagram and X are relayed; nothing is stored)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from tf_agent.tools.types import CANONICAL_ID_PATTERN, ToolFailure
from tf_backend.api.deps import ctx
from tf_backend.app_context import AppContext
from tf_backend.previews import RELAYED

router = APIRouter(prefix="/preview", tags=["preview"])
PASS_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges")


@router.get("/{cid}")
async def preview(cid: str, request: Request, c: AppContext = Depends(ctx)) -> StreamingResponse:
    import re

    if not re.match(CANONICAL_ID_PATTERN, cid) or cid.split(":", 1)[0] not in RELAYED:
        raise HTTPException(404, "no relayed preview for this video")
    if c.previews is None:
        raise HTTPException(503, "previews aren't available in this process")
    try:
        resp, body = await c.previews.open(cid, request.headers.get("range"))
    except ToolFailure as e:
        raise HTTPException(404, e.error.message) from None
    headers = {k: v for k, v in resp.headers.items() if k.lower() in PASS_HEADERS}
    headers["cache-control"] = "no-store"
    return StreamingResponse(body, status_code=resp.status_code, headers=headers)
