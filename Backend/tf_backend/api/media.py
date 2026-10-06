"""Serves video thumbnails and character images: only from their own roots (no traversal, no other files)."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from tf_backend.api.deps import ctx
from tf_backend.app_context import AppContext

router = APIRouter(prefix="/media", tags=["media"])
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@router.get("/{kind}/{path:path}")
async def media(kind: str, path: str, c: AppContext = Depends(ctx)) -> FileResponse:
    roots = {"thumbs": c.media_dir / "thumbs", "characters": c.characters_dir}
    root = roots.get(kind)
    if root is None:
        raise HTTPException(404, "not found")
    root = root.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file() or target.suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(404, "not found")
    return FileResponse(target, headers={"Cache-Control": "private, max-age=86400"})
