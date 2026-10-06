"""Characters are the image folders in CHARACTERS_DIR (synced on every read) and the runs made for them."""
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from tf_agent.characters.folders import sync_characters
from tf_backend.api.deps import ctx
from tf_backend.app_context import AppContext
from tf_db.models import Character, CharacterVersion, Finding, Run, RunFeedback, TrendCluster

router = APIRouter(prefix="/characters", tags=["characters"])


def image_url(c: AppContext, path: str | None) -> str | None:
    if not path:
        return None
    try:
        rel = Path(path).resolve().relative_to(c.characters_dir.resolve())
    except ValueError:
        return None
    return f"/api/media/characters/{rel.as_posix()}"


async def character_card(c: AppContext, ch: Character) -> dict[str, Any]:
    async with c.sessionmaker() as s:
        v = (await s.execute(select(CharacterVersion).where(CharacterVersion.character_id == ch.id)
                             .order_by(CharacterVersion.version.desc()).limit(1))).scalar_one()
    return {"slug": ch.slug, "name": ch.name, "image_url": image_url(c, v.canonical_image_path)}


@router.get("")
async def list_characters(c: AppContext = Depends(ctx)) -> list[dict[str, Any]]:
    await sync_characters(c.characters_dir, c.sessionmaker)
    present = {p.name for p in c.characters_dir.iterdir() if p.is_dir()} if c.characters_dir.is_dir() else set()
    async with c.sessionmaker() as s:
        chars = (await s.execute(select(Character).order_by(Character.name))).scalars().all()
        out = []
        for ch in chars:
            if Path(ch.folder_path).name not in present:  # a folder the owner removed
                continue
            v = (await s.execute(select(CharacterVersion).where(CharacterVersion.character_id == ch.id)
                                 .order_by(CharacterVersion.version.desc()).limit(1))).scalar_one()
            runs = (await s.execute(select(func.count()).select_from(Run).where(Run.character_id == ch.id))).scalar_one()
            out.append({"slug": ch.slug, "name": ch.name, "image_url": image_url(c, v.canonical_image_path),
                        "images": [u for u in (image_url(c, p) for p in v.images or []) if u], "runs": runs})
    return out


@router.get("/{slug}/runs")
async def character_runs(slug: str, c: AppContext = Depends(ctx)) -> list[dict[str, Any]]:
    async with c.sessionmaker() as s:
        ch = (await s.execute(select(Character).where(Character.slug == slug))).scalar_one_or_none()
        if ch is None:
            raise HTTPException(404, f"unknown character {slug!r}")
        runs = (await s.execute(select(Run, RunFeedback.satisfaction).outerjoin(RunFeedback, RunFeedback.run_id == Run.id)
                                .where(Run.character_id == ch.id).order_by(Run.started_at.desc()).limit(100))).all()
        out = []
        for r, satisfaction in runs:
            clusters = (await s.execute(select(func.count()).select_from(TrendCluster).where(
                TrendCluster.run_id == r.id))).scalar_one()
            videos = clusters or (await s.execute(select(func.count()).select_from(Finding).where(
                Finding.run_id == r.id, Finding.status == "analyzed"))).scalar_one()
            out.append({"id": str(r.id), "state": r.state, "stop_reason": r.stop_reason, "started_at": r.started_at,
                        "finished_at": r.finished_at, "videos": videos, "satisfaction": satisfaction,
                        "active": c.runs.is_active(r.id)})
    return out
