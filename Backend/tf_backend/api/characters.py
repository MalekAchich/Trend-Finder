"""Characters: list, detail (brief, seeds, taste profile, directions), sync, taste-profile edits, seeds."""
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tf_agent.characters.brief import build_brief
from tf_agent.characters.profile import CharacterError, parse_profile
from tf_agent.characters.sync import sync_characters
from tf_agent.tools.normalize import canonical_id
from tf_backend.api.deps import ctx, require_client_header
from tf_backend.app_context import AppContext
from tf_db.models import Character, CharacterVersion, Direction, Run, Seed, TasteProfile

router = APIRouter(prefix="/characters", tags=["characters"])


def image_url(c: AppContext, path: str | None) -> str | None:
    if not path:
        return None
    try:
        rel = Path(path).resolve().relative_to(c.characters_dir.resolve())
    except ValueError:
        return None
    return f"/api/media/characters/{rel.as_posix()}"


async def _character(c: AppContext, slug: str) -> tuple[Character, CharacterVersion]:
    async with c.sessionmaker() as s:
        ch = (await s.execute(select(Character).where(Character.slug == slug))).scalar_one_or_none()
        if ch is None:
            raise HTTPException(404, f"unknown character {slug!r}")
        version = (await s.execute(select(CharacterVersion).where(CharacterVersion.character_id == ch.id)
                                   .order_by(CharacterVersion.version.desc()).limit(1))).scalar_one()
    return ch, version


@router.get("")
async def list_characters(c: AppContext = Depends(ctx)) -> list[dict[str, Any]]:
    async with c.sessionmaker() as s:
        chars = (await s.execute(select(Character).order_by(Character.slug))).scalars().all()
        out = []
        for ch in chars:
            v = (await s.execute(select(CharacterVersion).where(CharacterVersion.character_id == ch.id)
                                 .order_by(CharacterVersion.version.desc()).limit(1))).scalar_one()
            runs = (await s.execute(select(func.count()).select_from(Run).where(Run.character_id == ch.id))).scalar_one()
            try:
                niche_open = parse_profile(v.profile_md).niche_open
            except CharacterError:
                niche_open = None
            out.append({"slug": ch.slug, "name": ch.name, "version": v.version, "runs": runs,
                        "niche_open": niche_open, "canonical_image_url": image_url(c, v.canonical_image_path)})
    return out


@router.get("/{slug}")
async def character_detail(slug: str, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    ch, v = await _character(c, slug)
    try:
        profile = parse_profile(v.profile_md)
        brief, sections, niche_open = build_brief(profile), profile.sections, profile.niche_open
    except CharacterError as e:
        brief, sections, niche_open = f"(profile could not be parsed: {e})", {}, None
    async with c.sessionmaker() as s:
        seeds = (await s.execute(select(Seed).where(Seed.character_id == ch.id).order_by(Seed.created_at))).scalars().all()
        taste = (await s.execute(select(TasteProfile).where(TasteProfile.character_id == ch.id)
                                 .order_by(TasteProfile.version.desc()).limit(1))).scalar_one_or_none()
        dirs = (await s.execute(select(Direction).where(Direction.character_id == ch.id))).scalars().all()
    return {
        "slug": ch.slug, "name": ch.name, "version": v.version, "brief": brief, "sections": sections,
        "niche_open": niche_open, "canonical_image_url": image_url(c, v.canonical_image_path),
        "seeds": [{"url": x.url, "canonical_id": x.canonical_id, "source": x.source, "study": x.study} for x in seeds],
        "taste_profile": None if taste is None else {"version": taste.version, "body_md": taste.body_md,
                                                     "author": taste.author, "created_at": taste.created_at},
        "directions": sorted(({"key": d.key, "label": d.label, "niche": d.niche, "alpha": d.alpha, "beta": d.beta}
                              for d in dirs), key=lambda d: (d["alpha"] - d["beta"], d["alpha"]), reverse=True),
    }


@router.post("/sync", dependencies=[Depends(require_client_header)])
async def sync(c: AppContext = Depends(ctx)) -> list[dict[str, Any]]:
    try:
        results = await sync_characters(c.characters_dir, c.sessionmaker)
    except CharacterError as e:
        raise HTTPException(422, str(e)) from e
    return [{"slug": r.slug, "version": r.version, "changed": r.changed} for r in results]


class TasteEdit(BaseModel):
    body_md: str = Field(min_length=1, max_length=8000)


@router.put("/{slug}/taste-profile", dependencies=[Depends(require_client_header)])
async def edit_taste(slug: str, body: TasteEdit, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    ch, _ = await _character(c, slug)
    async with c.sessionmaker() as s:
        version = (await s.execute(select(func.coalesce(func.max(TasteProfile.version), 0)).where(
            TasteProfile.character_id == ch.id))).scalar_one() + 1
        s.add(TasteProfile(character_id=ch.id, version=version, body_md=body.body_md, author="owner"))
        await s.commit()
    return {"version": version}


class SeedIn(BaseModel):
    url: str = Field(min_length=10, max_length=500)


@router.post("/{slug}/seeds", dependencies=[Depends(require_client_header)])
async def add_seed(slug: str, body: SeedIn, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    ch, _ = await _character(c, slug)
    cid = canonical_id(body.url)
    if cid is None:
        raise HTTPException(422, "not a TikTok, Instagram or YouTube video URL")
    async with c.sessionmaker() as s:
        await s.execute(pg_insert(Seed).values(character_id=ch.id, url=body.url, canonical_id=cid, source="ui")
                        .on_conflict_do_nothing())
        await s.commit()
    return {"url": body.url, "canonical_id": cid}
