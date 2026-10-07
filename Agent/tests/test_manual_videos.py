"""Plan 8: the owner's manually chosen videos: a permanent list of references and targets."""
import io
import uuid

import pytest
from PIL import Image
from sqlalchemy import select

from tf_agent.manual import ManualVideos
from tf_agent.orchestrator.run import InputError
from tf_agent.tools.types import Creator, Metrics, ToolFailure, VideoItem
from tf_db.models import Finding, FindingScore, ManualVideo, ReferenceStudy, Target

from .orchestrator.test_blackboard import make_run

TT = "https://www.tiktok.com/@creator_a/video/1000000000000000001"
YT = "https://www.youtube.com/shorts/TestShort01"
PRIVATE = "https://www.instagram.com/reel/TESTREEL001/"


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (720, 1280), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


async def fake_get_video(url):
    from tf_agent.tools.normalize import canonical_id, platform_of

    if url == PRIVATE:
        raise ToolFailure("login_required", "instagram: this reel is private")
    cid = canonical_id(url)
    return VideoItem(canonical_id=cid, platform=platform_of(url), url=url, caption="made-up clip",
                     creator=Creator(handle="creator_a"), metrics=Metrics(views=12_000), duration_s=14,
                     thumbnail_url="https://cover.invalid/t.jpg")


async def fake_fetch(url):
    return png()


def service(sm, tmp_path):
    return ManualVideos(sm, get_video=fake_get_video, thumbs_dir=tmp_path / "thumbs", fetch_image=fake_fetch)


async def test_adding_checks_each_link_and_keeps_a_small_thumbnail(db_sessionmaker, tmp_path):
    mv = service(db_sessionmaker, tmp_path)
    ids = await mv.add([TT, YT, PRIVATE], reference=True)
    for i in ids:
        await mv.check(i)
    cards = {c["canonical_id"]: c for c in await mv.list()}
    tt = cards["tiktok:1000000000000000001"]
    assert tt["status"] == "ready" and tt["creator"] == "creator_a" and tt["views"] == 12_000 and tt["is_reference"]
    thumb = tmp_path / "thumbs" / tt["thumbnail_url"].rsplit("/", 1)[1]
    assert thumb.is_file() and thumb.stat().st_size <= 40_000 and Image.open(thumb).width == 360
    ig = cards["instagram:TESTREEL001"]
    assert ig["status"] == "problem" and "private" in ig["problem"]


async def test_bad_links_are_refused_together(db_sessionmaker, tmp_path):
    mv = service(db_sessionmaker, tmp_path)
    with pytest.raises(InputError) as ei:
        await mv.add([TT, "https://example.invalid/clip"], reference=True)
    assert "example.invalid" in str(ei.value)
    async with db_sessionmaker() as s:
        assert (await s.execute(select(ManualVideo))).first() is None


async def test_the_same_video_twice_only_updates_its_roles(db_sessionmaker, tmp_path):
    char_id, *_ = await make_run(db_sessionmaker, n_tasks=0)
    mv = service(db_sessionmaker, tmp_path)
    (first,) = await mv.add([TT], reference=True)
    again = await mv.add([TT + "?is_from_webapp=1"], reference=False, target="nicolaiz")
    assert again == [] or again == [first]
    (card,) = await mv.list()
    assert card["is_reference"] and card["target"]["slug"] == "nicolaiz" and card["target"]["state"] == "waiting"
    async with db_sessionmaker() as s:
        t = (await s.execute(select(Target))).scalar_one()
    assert (t.character_id, t.canonical_id, t.status) == (char_id, "tiktok:1000000000000000001", "pending")


async def test_roles_can_change_and_keep_targets_in_sync(db_sessionmaker, tmp_path):
    await make_run(db_sessionmaker, n_tasks=0)
    mv = service(db_sessionmaker, tmp_path)
    (vid,) = await mv.add([YT], reference=False, target="nicolaiz")
    await mv.set_roles(vid, is_reference=True, target=None)
    (card,) = await mv.list()
    assert card["is_reference"] and card["target"] is None
    async with db_sessionmaker() as s:
        assert (await s.execute(select(Target))).first() is None  # a pending target goes with the role
    with pytest.raises(InputError):
        await mv.set_roles(vid, is_reference=False, target=None)  # it must stay useful for something
    with pytest.raises(InputError):
        await mv.set_roles(vid, target="nobody")


async def test_a_used_target_shows_its_score_and_studies_show_per_character(db_sessionmaker, tmp_path):
    char_id, run_id, _ = await make_run(db_sessionmaker, n_tasks=0)
    mv = service(db_sessionmaker, tmp_path)
    (vid,) = await mv.add([YT], reference=True, target="nicolaiz")
    await mv.check(vid)
    async with db_sessionmaker() as s:
        await s.execute(Target.__table__.update().values(status="used", run_id=run_id))
        f = Finding(run_id=run_id, canonical_id="youtube:TestShort01", source="owner", status="analyzed")
        s.add(f)
        await s.flush()
        s.add(FindingScore(finding_id=f.id, overall=81.4))
        s.add(ReferenceStudy(manual_video_id=vid, character_id=char_id,
                             study={"format": "slow walk-in", "niche": "office comedy", "tags": ["office", "deadpan"],
                                    "trend_type": "skit", "fit_score": 8}))
        await s.commit()
    (card,) = await mv.list("nicolaiz")
    assert card["target"]["state"] == "analysed" and card["target"]["score"] == 81.4
    assert card["study"]["niche"] == "office comedy" and card["study"]["tags"] == ["office", "deadpan"]
    (plain,) = await mv.list()
    assert plain["study"]["niche"] == "office comedy"  # without a character: the latest study


async def test_removing_drops_it_and_its_pending_target(db_sessionmaker, tmp_path):
    await make_run(db_sessionmaker, n_tasks=0)
    mv = service(db_sessionmaker, tmp_path)
    (vid,) = await mv.add([YT], reference=True, target="nicolaiz")
    await mv.remove(vid)
    assert await mv.list() == []
    async with db_sessionmaker() as s:
        assert (await s.execute(select(Target))).first() is None
    with pytest.raises(KeyError):
        await mv.remove(uuid.uuid4())


async def test_references_for_a_character_say_which_need_studying(db_sessionmaker, tmp_path):
    char_id, *_ = await make_run(db_sessionmaker, n_tasks=0)
    mv = service(db_sessionmaker, tmp_path)
    a, b = await mv.add([TT, YT], reference=True)
    await mv.add([PRIVATE], reference=True)
    async with db_sessionmaker() as s:
        s.add(ReferenceStudy(manual_video_id=a, character_id=char_id, study={"format": "x"}))
        await s.execute(ManualVideo.__table__.update().where(ManualVideo.url == PRIVATE).values(status="problem"))
        await s.commit()
    refs = await mv.references_for(char_id)
    assert {(r.url, r.study is not None) for r in refs} == {(TT, True), (YT, False)}  # problems are skipped
