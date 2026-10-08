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
