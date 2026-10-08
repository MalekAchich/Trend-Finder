"""A lookalike run end to end on fakes: seeds from the owner's list, discovery, the analyst only on the picks,
and the owner's score bar."""
import dataclasses

from sqlalchemy import select

from tf_db.models import Finding, ManualVideo

from .test_lookalike import FakeSources, vid
from .test_run import SETTINGS, World, build


def lookalike(**kw):
    return dataclasses.replace(SETTINGS, mode="lookalike", freshness="any", **kw)


async def seeded(db_sessionmaker, tmp_path, min_score):
    world = World()
    orch = await build(db_sessionmaker, tmp_path, world)
    seed = vid(1, handle="creator_a", plays=2_000_000)
    await orch.store.upsert_videos([seed])
    async with db_sessionmaker() as s:
        s.add(ManualVideo(url=seed.url, canonical_id=seed.canonical_id, platform="instagram", status="ready"))
        await s.commit()
    src = FakeSources()
    orch.tools.platform_tools = lambda seen_filter=None: src
    run_id = await orch.create_run("testy", lookalike(min_score=min_score))
    return orch, run_id, src, world


async def test_a_lookalike_run_judges_only_the_fastest_growing_finds(db_sessionmaker, tmp_path):
    orch, run_id, src, world = await seeded(db_sessionmaker, tmp_path, min_score=0)
    out = await orch.execute(run_id)
    assert out.state == "review_ready"
    async with db_sessionmaker() as s:
        found = (await s.execute(select(Finding).where(Finding.run_id == run_id))).scalars().all()
    ids = {f.canonical_id for f in found}
    assert "instagram:TESTREEL001" not in ids  # the owner's own reference is never "found"
    assert "instagram:TESTREEL010" in ids and "instagram:TESTREEL011" not in ids  # 1,000 plays: not judged
    assert world.rounds_planned == 0  # no planner in this mode
    assert all((f.why or "").startswith(("new from @", "found searching")) for f in found)  # says where it came from


async def test_finds_under_the_owners_bar_are_dropped(db_sessionmaker, tmp_path):
    orch, run_id, _, _ = await seeded(db_sessionmaker, tmp_path, min_score=99)
    await orch.execute(run_id)
    async with db_sessionmaker() as s:
        statuses = {f.status for f in (await s.execute(select(Finding).where(Finding.run_id == run_id))).scalars()}
    assert statuses and statuses <= {"below_bar", "filtered_feasibility", "failed"}


async def test_a_run_keeps_digging_with_the_closest_finds_until_nothing_new_is_left(db_sessionmaker, tmp_path):
    from tf_db.models import Run

    orch, run_id, src, _ = await seeded(db_sessionmaker, tmp_path, min_score=0)  # min_kept 10, only 4 exist
    out = await orch.execute(run_id)
    assert out.stop_reason == "kept 4: nothing new left that looks like your references"
    assert ("instagram_search", "ai deadpan professional") in src.calls  # round 2: the theme of the closest finds
    assert src.calls.count(("instagram_creator", "creator_a")) == 1  # a creator is followed once per run
    async with db_sessionmaker() as s:
        run = await s.get(Run, run_id)
    assert [r["round"] for r in run.inputs["lookalike_rounds"]] == [1, 2]


async def test_a_run_ends_as_soon_as_enough_videos_pass_the_bar(db_sessionmaker, tmp_path):
    world = World()
    orch = await build(db_sessionmaker, tmp_path, world)
    seed = vid(1, handle="creator_a", plays=2_000_000)
    await orch.store.upsert_videos([seed])
    async with db_sessionmaker() as s:
        s.add(ManualVideo(url=seed.url, canonical_id=seed.canonical_id, platform="instagram", status="ready"))
        await s.commit()
    src = FakeSources()
    orch.tools.platform_tools = lambda seen_filter=None: src
    run_id = await orch.create_run("testy", lookalike(min_score=0, min_kept=2))
    out = await orch.execute(run_id)
    assert out.stop_reason == "found 4 videos at or above your bar"
    assert not any(q == "ai deadpan professional" for _, q in src.calls)  # no second round needed


async def test_stopping_a_run_still_shows_the_second_opinion_the_owners_references(db_sessionmaker, tmp_path):
    from sqlalchemy import update

    from tf_db.models import Run

    orch, run_id, _, _ = await seeded(db_sessionmaker, tmp_path, min_score=75)
    seen = []

    class Curator:
        async def curate(self, run_id, character, weights=None, min_score=0.0):
            seen.append((character.brief, min_score))

    orch.curator = Curator()
    async with db_sessionmaker() as s:
        await s.execute(update(Run).where(Run.id == run_id).values(
            state="running", inputs={"trend_studies": {"https://www.instagram.com/reel/TESTREEL001/": {
                "format": "AI muscleman dam stunt", "trend_type": "stunt", "niche": "ai spectacle", "tags": ["ai"]}}}))
        await s.commit()
    await orch.stop_and_curate(run_id)
    (brief, bar), = seen
    assert "The owner's reference videos (1 studied)" in brief and "AI muscleman dam stunt" in brief and bar == 75


async def test_every_judge_sees_how_our_own_channels_did(db_sessionmaker, tmp_path):
    from datetime import UTC, datetime

    from tf_db.models import Run, SocialChannel, SocialChannelSnapshot

    orch, run_id, _, _ = await seeded(db_sessionmaker, tmp_path, min_score=75)
    async with db_sessionmaker() as s:
        run = await s.get(Run, run_id)
        ch = SocialChannel(character_id=run.character_id, platform="tiktok", handle="made_up_channel")
        s.add(ch)
        await s.flush()
        s.add(SocialChannelSnapshot(channel_id=ch.id, taken_at=datetime.now(UTC), followers=12, posts=1))
        await s.commit()
    brief = (await orch._judge_brief(await orch._character(run), run)).brief
    assert "Your own channels (facts" in brief and "TikTok @made_up_channel: 12 followers" in brief
