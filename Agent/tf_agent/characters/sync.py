"""Import character folders into the DB: a new version only when profile or canonical image changes."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.characters.brief import build_brief
from tf_agent.characters.profile import CharacterError, Profile, parse_profile
from tf_agent.tools.normalize import canonical_id
from tf_db.models import Character, CharacterVersion, Seed


@dataclass(frozen=True)
class SyncResult:
    slug: str
    version: int
    changed: bool


@dataclass
class LoadedCharacter:
    character_id: uuid.UUID
    version_id: uuid.UUID
    slug: str
    profile: Profile
    brief: str
    canonical_image_path: str
    seeds: list[str]


def _seed_urls(folder: Path, profile: Profile) -> list[str]:
    urls = [str(u).strip() for u in profile.front.get("seeds") or [] if str(u).strip()]
    seeds_file = folder / "seeds" / "seeds.txt"
    if seeds_file.exists():
        urls += [ln.strip() for ln in seeds_file.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    return list(dict.fromkeys(urls))


async def sync_characters(characters_dir: Path, sessionmaker: async_sessionmaker[AsyncSession]) -> list[SyncResult]:
    results = []
    for folder in sorted(p for p in Path(characters_dir).iterdir() if (p / "profile.md").is_file()):
        text = (folder / "profile.md").read_text()
        profile = parse_profile(text)
        image = folder / (profile.canonical_image or "")
        if not profile.canonical_image or not image.is_file():
            raise CharacterError(f"{profile.slug}: canonical image {image} not found")
        digest = hashlib.sha256(text.encode() + hashlib.sha256(image.read_bytes()).digest()).hexdigest()
        async with sessionmaker() as s:
            stmt = pg_insert(Character).values(slug=profile.slug, name=profile.name, folder_path=str(folder))
            stmt = stmt.on_conflict_do_update(index_elements=[Character.slug],
                                              set_={"name": profile.name, "folder_path": str(folder)})
            await s.execute(stmt)
            char = (await s.execute(select(Character).where(Character.slug == profile.slug))).scalar_one()
            latest = (await s.execute(select(CharacterVersion).where(CharacterVersion.character_id == char.id)
                                      .order_by(CharacterVersion.version.desc()).limit(1))).scalar_one_or_none()
            changed = latest is None or latest.content_hash != digest
            version = (latest.version if latest else 0) + (1 if changed else 0)
            if changed:
                s.add(CharacterVersion(character_id=char.id, version=version, profile_md=text,
                                       front_matter=profile.front, canonical_image_path=str(image.resolve()),
                                       content_hash=digest))
            for url in _seed_urls(folder, profile):
                await s.execute(pg_insert(Seed).values(character_id=char.id, url=url, canonical_id=canonical_id(url),
                                                       source="profile").on_conflict_do_nothing())
            await s.commit()
        results.append(SyncResult(profile.slug, version, changed))
    return results


async def load_character(sessionmaker: async_sessionmaker[AsyncSession], slug: str) -> LoadedCharacter:
    async with sessionmaker() as s:
        char = (await s.execute(select(Character).where(Character.slug == slug))).scalar_one_or_none()
        if char is None:
            raise CharacterError(f"unknown character {slug!r}: run `tf sync-characters` first")
        version = (await s.execute(select(CharacterVersion).where(CharacterVersion.character_id == char.id)
                                   .order_by(CharacterVersion.version.desc()).limit(1))).scalar_one()
        seeds = (await s.execute(select(Seed.url).where(Seed.character_id == char.id)
                                 .order_by(func.coalesce(Seed.created_at, func.now()), Seed.url))).scalars().all()
    profile = parse_profile(version.profile_md)
    return LoadedCharacter(character_id=char.id, version_id=version.id, slug=slug, profile=profile,
                           brief=build_brief(profile), canonical_image_path=version.canonical_image_path,
                           seeds=list(seeds))
