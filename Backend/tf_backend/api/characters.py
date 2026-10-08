"""Characters are the image folders in CHARACTERS_DIR (synced on every read) and the runs made for them."""
from pathlib import Path
from typing import Any

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from tf_agent.characters.folders import sync_characters
from tf_backend.api.deps import ctx
from tf_backend.app_context import AppContext
from tf_db.models import Character, CharacterVersion, Finding, Run, RunFeedback, TasteProfile, TrendCluster

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


@router.get("/{slug}")
async def character_detail(slug: str, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    async with c.sessionmaker() as s:
        ch = (await s.execute(select(Character).where(Character.slug == slug))).scalar_one_or_none()
        if ch is None:
            raise HTTPException(404, f"unknown character {slug!r}")
        v = (await s.execute(select(CharacterVersion).where(CharacterVersion.character_id == ch.id)
                             .order_by(CharacterVersion.version.desc()).limit(1))).scalar_one()
        read = (await s.execute(select(Run.character_read).where(Run.character_id == ch.id,
                                                                 Run.character_read.is_not(None))
                                .order_by(Run.started_at.desc()).limit(1))).scalar_one_or_none()
        taste = (await s.execute(select(TasteProfile.body_md).where(TasteProfile.character_id == ch.id)
                                 .order_by(TasteProfile.version.desc()).limit(1))).scalar_one_or_none()
        runs = (await s.execute(select(func.count()).select_from(Run).where(Run.character_id == ch.id))).scalar_one()
    videos = await _character_videos(c, ch.id, None)
    return {"slug": ch.slug, "name": ch.name, "image_url": image_url(c, v.canonical_image_path),
            "images": [u for u in (image_url(c, p) for p in v.images or []) if u], "latest_read": read,
            "taste_md": taste, "runs": runs, "videos_found": len(videos)}


async def _character_videos(c: AppContext, character_id, days: int | None) -> list[dict[str, Any]]:
    from tf_backend.api.videos import videos_for_run

    async with c.sessionmaker() as s:
        run_ids = (await s.execute(select(Run.id).where(Run.character_id == character_id)
                                   .order_by(Run.started_at.desc()).limit(200))).scalars().all()
        out = []
        for rid in run_ids:
            out += await videos_for_run(s, rid)
    if days is not None:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        out = [v for v in out if v.get("found_at") and v["found_at"] >= cutoff]
    return sorted(out, key=lambda v: v.get("found_at") or "", reverse=True)


@router.get("/{slug}/videos")
async def character_videos(slug: str, days: int | None = Query(None, ge=1, le=3650),
                           c: AppContext = Depends(ctx)) -> list[dict[str, Any]]:
    async with c.sessionmaker() as s:
        ch = (await s.execute(select(Character).where(Character.slug == slug))).scalar_one_or_none()
    if ch is None:
        raise HTTPException(404, f"unknown character {slug!r}")
    return await _character_videos(c, ch.id, days)


@router.get("/{slug}/runs")
async def character_runs(slug: str, c: AppContext = Depends(ctx)) -> list[dict[str, Any]]:
    async with c.sessionmaker() as s:
        ch = (await s.execute(select(Character).where(Character.slug == slug))).scalar_one_or_none()
        if ch is None:
            raise HTTPException(404, f"unknown character {slug!r}")
        runs = (await s.execute(select(Run, RunFeedback.satisfaction, RunFeedback.note).outerjoin(RunFeedback, RunFeedback.run_id == Run.id)
                                .where(Run.character_id == ch.id).order_by(Run.started_at.desc()).limit(100))).all()
        out = []
        for r, satisfaction, note in runs:
            clusters = (await s.execute(select(func.count()).select_from(TrendCluster).where(
                TrendCluster.run_id == r.id))).scalar_one()
            videos = clusters or (await s.execute(select(func.count()).select_from(Finding).where(
                Finding.run_id == r.id, Finding.status == "analyzed", Finding.source != "owner"))).scalar_one()
            out.append({"id": str(r.id), "state": r.state, "stop_reason": r.stop_reason, "started_at": r.started_at,
                        "finished_at": r.finished_at, "videos": videos, "satisfaction": satisfaction, "run_note": note,
                        "active": c.runs.is_active(r.id)})
    return out
