"""Plan 5 Task 2: a character is the images in its folder; the reader role turns them into the agents' brief."""
from pathlib import Path

import pytest
from sqlalchemy import select

from tf_agent.characters.folders import CharacterError, discover, load_character, main_image, sync_characters
from tf_agent.characters.read import CharacterRead, render_brief
from tf_db.models import CharacterVersion

READ = {"look": "Curly dark hair, moustache, tan 1970s suit, brown shirt", "vibe": "calm, sincere, out of time",
        "performance_angle": "treats modern trends with total gravity", "possible_niches": ["man out of time",
        "deadpan professional", "retro lifestyle"], "kling_constraints": "full body, one person, slow moves",
        "avoid": "fast spins, props in hands"}


def tree(root: Path) -> Path:
    """Mirrors the real folder: spaces and case in names, a stray profile.md, an empty folder."""
    t = root / "Tekashi67"
    t.mkdir(parents=True)
    for name in ("Tekashi67 face.png", "Tekashi67 body.png", "Tekashi67.png"):
        (t / name).write_bytes(name.encode())
    n = root / "Nicolaiz"
    n.mkdir()
    (n / "Nicolaiz face views.png").write_bytes(b"f")
    (n / "Nicolaiz body views.PNG").write_bytes(b"b")
    (n / "profile.md").write_text("ignored")
    (root / "Empty Folder").mkdir()
    (root / "notes.txt").write_text("not a character")
    return root


def test_discover_reads_image_folders_only(tmp_path):
    chars = {c.slug: c for c in discover(tree(tmp_path))}
    assert set(chars) == {"tekashi67", "nicolaiz"}
    t = chars["tekashi67"]
    assert t.name == "Tekashi67" and Path(t.images[0]).name == "Tekashi67.png" and len(t.images) == 3
    assert all(not p.endswith(".md") for p in chars["nicolaiz"].images)


def test_main_image_rule():
    assert Path(main_image("Tekashi67", ["/a/Tekashi67 body.png", "/a/Tekashi67.png"])).name == "Tekashi67.png"
    assert Path(main_image("Nicolaiz", ["/a/Nicolaiz face views.png", "/a/Studio shot.png"])).name == "Studio shot.png"
    assert Path(main_image("X", ["/a/x face.png", "/a/x body.png"])).name == "x body.png"


def test_slug_handles_spaces():
    root_names = ["Mona Lisa"]
    assert [c.slug for c in discover_names(root_names)] == ["mona-lisa"]


def discover_names(names):
    import tempfile
    d = Path(tempfile.mkdtemp())
    for n in names:
        (d / n).mkdir()
        (d / n / "a.jpg").write_bytes(b"x")
    return discover(d)


async def test_sync_versions_only_when_images_change(tmp_path, db_sessionmaker):
    root = tree(tmp_path)
    first = {r.slug: r for r in await sync_characters(root, db_sessionmaker)}
    assert first["tekashi67"].version == 1 and first["tekashi67"].changed
    again = {r.slug: r for r in await sync_characters(root, db_sessionmaker)}
    assert again["tekashi67"].version == 1 and not again["tekashi67"].changed
    (root / "Tekashi67" / "Tekashi67.png").write_bytes(b"new pixels")
    third = {r.slug: r for r in await sync_characters(root, db_sessionmaker)}
    assert third["tekashi67"].version == 2 and not third["nicolaiz"].changed
    async with db_sessionmaker() as s:
        v = (await s.execute(select(CharacterVersion).order_by(CharacterVersion.version.desc()))).scalars().first()
        assert Path(v.images[0]).name == "Tekashi67.png" and v.canonical_image_path == v.images[0]


async def test_load_character(tmp_path, db_sessionmaker):
    await sync_characters(tree(tmp_path), db_sessionmaker)
    ch = await load_character(db_sessionmaker, "tekashi67")
    assert ch.name == "Tekashi67" and len(ch.images) == 3 and ch.canonical_image_path == ch.images[0]
    with pytest.raises(CharacterError):
        await load_character(db_sessionmaker, "nobody")


def test_render_brief_uses_read_and_taste_within_budget():
    read = CharacterRead(**READ)
    brief = render_brief("Nicolaiz", read, "## Loves\n- calm duties\n" + "x" * 5000)
    assert brief.startswith("# Character: Nicolaiz") and "man out of time" in brief and "tan 1970s suit" in brief
    assert "Loves" in brief and len(brief) <= 2400
    assert "OPEN" not in render_brief("N", read, None)


async def test_reader_sees_up_to_four_images_in_order(tmp_path):
    from tf_agent.models.fake import FakeAdapter, text_response
    from tf_agent.roles.prompts import PromptLibrary
    from tf_agent.roles.runners import Roles
    from tf_agent.testing import make_client

    imgs = []
    for i in range(5):
        p = tmp_path / f"{i}.png"
        p.write_bytes(b"x")
        imgs.append(str(p))
    a = FakeAdapter("a", [text_response("a", structured=READ)])
    client, _, _ = make_client({"a": a})
    out = await Roles(client, PromptLibrary()).read_character("Nicolaiz", imgs, "## Loves\n- x", ["more retro"])
    assert out.result.possible_niches[0] == "man out of time"
    msg = a.requests[0].messages[0]
    assert [i.path for i in msg.images()] == imgs[:4]
    assert "more retro" in msg.text() and "Loves" in msg.text()
