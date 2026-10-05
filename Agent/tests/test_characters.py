from pathlib import Path

import pytest
from sqlalchemy import select

from tf_agent.characters.brief import build_brief
from tf_agent.characters.profile import CharacterError, parse_profile
from tf_agent.characters.sync import load_character, sync_characters
from tf_db.models import CharacterVersion, Seed

REAL = Path(__file__).resolve().parents[3] / "AI Influencers Characters" / "Nicolaiz" / "profile.md"

PROFILE = """---
name: Testy
slug: testy
canonical_image: testy.png
seeds:
  - https://www.tiktok.com/@a/video/7000000000000000001
---

## Persona
A calm robot butler.

## Niche / context: OPEN
Candidates: kitchens, gyms.

## Kling constraints
Full body, one person.
"""


def make_char(root: Path, profile: str = PROFILE, image: bool = True) -> Path:
    folder = root / "Testy"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "profile.md").write_text(profile)
    if image:
        (folder / "testy.png").write_bytes(b"\x89PNG fake")
    return folder


def test_parse_real_nicolaiz_profile():
    p = parse_profile(REAL.read_text())
    assert p.name == "Nicolaiz" and p.slug == "nicolaiz" and p.niche_open is True
    assert {"persona", "energy & charisma", "kling constraints", "do / don't"} <= set(p.sections)
    assert p.canonical_image == "Nicolaiz.png"


def test_brief_is_compact_and_complete():
    brief = build_brief(parse_profile(REAL.read_text()))
    assert len(brief) <= 2400
    for needle in ("Nicolaiz", "deadpan", "OPEN", "Kling", "Don't"):
        assert needle.lower() in brief.lower()


def test_profile_without_front_matter_is_rejected():
    with pytest.raises(CharacterError, match="front matter"):
        parse_profile("## Persona\nno yaml here")


async def test_sync_versions_only_on_change(tmp_path, db_sessionmaker):
    folder = make_char(tmp_path)
    first = await sync_characters(tmp_path, db_sessionmaker)
    again = await sync_characters(tmp_path, db_sessionmaker)
    (folder / "profile.md").write_text(PROFILE.replace("calm robot butler", "nervous robot butler"))
    third = await sync_characters(tmp_path, db_sessionmaker)
    assert [(r.slug, r.version, r.changed) for r in (first + again + third)] == [
        ("testy", 1, True), ("testy", 1, False), ("testy", 2, True)]
    async with db_sessionmaker() as s:
        assert len((await s.execute(select(CharacterVersion))).scalars().all()) == 2


async def test_missing_canonical_image_is_a_clear_error(tmp_path, db_sessionmaker):
    make_char(tmp_path, image=False)
    with pytest.raises(CharacterError, match="canonical image"):
        await sync_characters(tmp_path, db_sessionmaker)


async def test_seeds_from_front_matter_and_seeds_file(tmp_path, db_sessionmaker):
    folder = make_char(tmp_path)
    (folder / "seeds").mkdir()
    (folder / "seeds" / "seeds.txt").write_text("# my picks\nhttps://www.youtube.com/shorts/OUZbZ8cz4j8\n\n")
    await sync_characters(tmp_path, db_sessionmaker)
    await sync_characters(tmp_path, db_sessionmaker)
    async with db_sessionmaker() as s:
        seeds = sorted((r.canonical_id, r.source) for r in (await s.execute(select(Seed))).scalars())
    assert seeds == [("tiktok:7000000000000000001", "profile"), ("youtube:OUZbZ8cz4j8", "profile")]


async def test_load_character_bundles_brief_image_and_seeds(tmp_path, db_sessionmaker):
    make_char(tmp_path)
    await sync_characters(tmp_path, db_sessionmaker)
    ch = await load_character(db_sessionmaker, "testy")
    assert ch.slug == "testy" and ch.profile.niche_open
    assert ch.canonical_image_path.endswith("testy.png") and "robot butler" in ch.brief
    assert ch.seeds == ["https://www.tiktok.com/@a/video/7000000000000000001"]
    with pytest.raises(CharacterError, match="unknown character"):
        await load_character(db_sessionmaker, "nobody")
