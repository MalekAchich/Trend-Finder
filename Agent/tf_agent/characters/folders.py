"""A character is the images in its folder (`CHARACTERS_DIR/<Name>/*.png|jpg|webp`); nothing else is read.
The folder stays the source of truth: the app's add / rename / add-images edit the folder, then sync it."""
from __future__ import annotations

import hashlib
import io
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_db.models import Character, CharacterVersion

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
SECONDARY_WORDS = ("face", "body")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_IMAGES = 12
IMAGE_FORMATS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}


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
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
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
            await _follow_renamed_folder(s, c, digest)
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


async def _follow_renamed_folder(s: AsyncSession, c: FolderCharacter, digest: str) -> None:
    """A folder renamed by hand keeps its character (runs, ratings, targets): same images, old folder gone."""
    if (await s.execute(select(Character.id).where(Character.slug == c.slug))).first():
        return
    for char in (await s.execute(select(Character))).scalars():
        if Path(char.folder_path).is_dir():
            continue
        latest = (await s.execute(select(CharacterVersion.content_hash).where(CharacterVersion.character_id == char.id)
                                  .order_by(CharacterVersion.version.desc()).limit(1))).scalar_one_or_none()
        if latest == digest:
            char.slug, char.name, char.folder_path = c.slug, c.name, str(c.folder)
            await s.flush()
            return


def check_name(name: str) -> str:
    name = " ".join(name.split())
    if not NAME_RE.match(name) or not slugify(name):
        raise CharacterError("use 1-40 letters, digits, spaces, dots, dashes or underscores")
    return name


def check_images(images: list[tuple[str, bytes]]) -> list[tuple[str, bytes, str]]:
    """(file name, bytes) -> (clean stem, bytes, extension); only real PNG / JPEG / WebP images."""
    from PIL import Image

    if not images:
        raise CharacterError("add at least one image")
    if len(images) > MAX_IMAGES:
        raise CharacterError(f"at most {MAX_IMAGES} images at a time")
    out = []
    for filename, data in images:
        if len(data) > MAX_IMAGE_BYTES:
            raise CharacterError(f"{filename} is over {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
        try:
            with Image.open(io.BytesIO(data)) as im:
                fmt = im.format
                im.verify()
        except Exception:
            raise CharacterError(f"{filename} isn't an image") from None
        if fmt not in IMAGE_FORMATS:
            raise CharacterError(f"{filename}: use PNG, JPEG or WebP")
        stem = re.sub(r"[^\w .-]", "", Path(filename).stem).strip(" .") or "image"
        out.append((stem[:60], data, IMAGE_FORMATS[fmt]))
    return out


def _free_path(folder: Path, stem: str, ext: str) -> Path:
    path, n = folder / f"{stem}{ext}", 2
    while path.exists():
        path, n = folder / f"{stem} {n}{ext}", n + 1
    return path


def _taken(characters_dir: Path, slug: str, but: Path | None = None) -> bool:
    return any(slugify(p.name) == slug and (but is None or p.resolve() != but.resolve())
               for p in Path(characters_dir).iterdir() if p.is_dir())


async def _character(sm: async_sessionmaker[AsyncSession], slug: str) -> Character:
    async with sm() as s:
        char = (await s.execute(select(Character).where(Character.slug == slug))).scalar_one_or_none()
    if char is None or not Path(char.folder_path).is_dir():
        raise CharacterError(f"unknown character {slug!r}")
    return char


async def create_character(characters_dir: Path, sm: async_sessionmaker[AsyncSession], name: str,
                           images: list[tuple[str, bytes]]) -> str:
    """A new folder named after the character; the first image becomes the main one (named like the folder)."""
    name, files = check_name(name), check_images(images)
    root = Path(characters_dir)
    root.mkdir(parents=True, exist_ok=True)
    if _taken(root, slugify(name)):
        raise CharacterError(f"there's already a character called {name}")
    staging = root / f".{slugify(name)}.partial"  # hidden: never synced half-written
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        for n, (stem, data, ext) in enumerate(files):
            _free_path(staging, name if n == 0 else stem, ext).write_bytes(data)
        staging.rename(root / name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    await sync_characters(root, sm)
    return slugify(name)


async def add_images(characters_dir: Path, sm: async_sessionmaker[AsyncSession], slug: str,
                     images: list[tuple[str, bytes]]) -> None:
    char, files = await _character(sm, slug), check_images(images)
    for stem, data, ext in files:
        _free_path(Path(char.folder_path), stem, ext).write_bytes(data)
    await sync_characters(characters_dir, sm)


async def rename_character(characters_dir: Path, sm: async_sessionmaker[AsyncSession], slug: str,
                           new_name: str) -> str:
    """Renames the folder (and its main image) and moves the character's row with it, so its history stays."""
    char, name = await _character(sm, slug), check_name(new_name)
    folder = Path(char.folder_path)
    new_slug = slugify(name)
    if name == folder.name:
        return slug
    if _taken(characters_dir, new_slug, but=folder):
        raise CharacterError(f"there's already a character called {name}")
    for f in folder.iterdir():
        if f.is_file() and f.stem.lower() == folder.name.lower() and f.suffix.lower() in IMAGE_SUFFIXES:
            f.rename(f.with_name(name + f.suffix))  # the main image is the one named like the folder
    target = folder.with_name(name)
    folder.rename(target)
    async with sm() as s:
        row = await s.get(Character, char.id)
        assert row is not None
        row.slug, row.name, row.folder_path = new_slug, name, str(target.resolve())
        await s.commit()
    await sync_characters(characters_dir, sm)
    return new_slug


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
