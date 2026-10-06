"""A character is the images in its folder (`CHARACTERS_DIR/<Name>/*.png|jpg|webp`); nothing else is read."""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_db.models import Character, CharacterVersion

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
SECONDARY_WORDS = ("face", "body")


class CharacterError(Exception):
    pass


@dataclass(frozen=True)
class FolderCharacter:
    slug: str
    name: str
    folder: Path
    images: list[str]  # absolute paths, main image first


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
    name: str
    images: list[str]
    canonical_image_path: str
    brief: str = ""  # rendered from the run's character read (orchestrator); name only until then

    def __post_init__(self) -> None:
        if not self.brief:
            self.brief = f"# Character: {self.name}"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def main_image(name: str, images: list[str]) -> str:
    """The image named like the folder; else the first that isn't a face/body sheet; else the first."""
    ordered = sorted(images, key=lambda p: Path(p).name.lower())
    for p in ordered:
        if Path(p).stem.lower() == name.lower():
            return p
    for p in ordered:
        if not any(w in Path(p).stem.lower() for w in SECONDARY_WORDS):
            return p
    return ordered[0]


def discover(characters_dir: Path) -> list[FolderCharacter]:
    root = Path(characters_dir)
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        images = [str(p.resolve()) for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
        if not images:
            continue
        main = main_image(folder.name, images)
        rest = sorted((p for p in images if p != main), key=lambda p: Path(p).name.lower())
        out.append(FolderCharacter(slugify(folder.name), folder.name, folder.resolve(), [main, *rest]))
    return out


def _images_hash(images: list[str]) -> str:
    h = hashlib.sha256()
    for p in sorted(images, key=lambda x: Path(x).name):
        h.update(Path(p).name.encode())
        h.update(hashlib.sha256(Path(p).read_bytes()).digest())
    return h.hexdigest()


async def sync_characters(characters_dir: Path, sessionmaker: async_sessionmaker[AsyncSession]) -> list[SyncResult]:
    results = []
    for c in discover(characters_dir):
        digest = _images_hash(c.images)
        async with sessionmaker() as s:
            stmt = pg_insert(Character).values(slug=c.slug, name=c.name, folder_path=str(c.folder))
            await s.execute(stmt.on_conflict_do_update(index_elements=[Character.slug],
                                                       set_={"name": c.name, "folder_path": str(c.folder)}))
            char = (await s.execute(select(Character).where(Character.slug == c.slug))).scalar_one()
            latest = (await s.execute(select(CharacterVersion).where(CharacterVersion.character_id == char.id)
                                      .order_by(CharacterVersion.version.desc()).limit(1))).scalar_one_or_none()
            changed = latest is None or latest.content_hash != digest
            version = (latest.version if latest else 0) + (1 if changed else 0)
            if changed:
                s.add(CharacterVersion(character_id=char.id, version=version, images=c.images,
                                       canonical_image_path=c.images[0], content_hash=digest))
            await s.commit()
        results.append(SyncResult(c.slug, version, changed))
    return results


async def load_character(sessionmaker: async_sessionmaker[AsyncSession], slug: str) -> LoadedCharacter:
    async with sessionmaker() as s:
        char = (await s.execute(select(Character).where(Character.slug == slug))).scalar_one_or_none()
        if char is None:
            raise CharacterError(f"unknown character {slug!r}: add a folder with images to the characters folder")
        version = (await s.execute(select(CharacterVersion).where(CharacterVersion.character_id == char.id)
                                   .order_by(CharacterVersion.version.desc()).limit(1))).scalar_one()
    images = list(version.images or [version.canonical_image_path])
    return LoadedCharacter(character_id=char.id, version_id=version.id, slug=slug, name=char.name, images=images,
                           canonical_image_path=version.canonical_image_path)
