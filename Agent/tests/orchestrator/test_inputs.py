"""Plan 5 Task 5: the run reads the character, studies trend URLs and takes the owner's target videos."""
import dataclasses

import pytest
from sqlalchemy import select, update

from tf_agent.characters.folders import sync_characters
from tf_agent.orchestrator.blackboard import Blackboard
from tf_agent.orchestrator.run import InputError
from tf_db.models import Finding, Run, Target

from .test_run import SETTINGS, World, build

TREND = "https://www.tiktok.com/@ai.star/video/7400000000000000001"
TARGET = "https://www.youtube.com/shorts/TestShort01"


def settings(**kw):
    return dataclasses.replace(SETTINGS, rounds=1, **kw)


async def types_of(sm, run_id):
    return [e["type"] for e in await Blackboard(sm).events_after(run_id, 0, limit=5000)]


async def test_read_study_and_targets_flow_into_the_run(db_sessionmaker, tmp_path):
    world = World()
    orch = await build(db_sessionmaker, tmp_path, world)
    run_id = await orch.create_run("testy", settings(trend_urls=[TREND], targets=[{"url": TARGET, "character": "testy"}],
                                                     freshness="day"))
    assert (await orch.execute(run_id)).state == "review_ready"
    async with db_sessionmaker() as s:
        run = await s.get(Run, run_id)
        owner = (await s.execute(select(Finding).where(Finding.run_id == run_id, Finding.source == "owner"))
                 ).scalars().all()
    assert run.character_read["look"] == "Robot butler in a black tailcoat"
    assert run.inputs["trend_studies"][TREND]["format"] == "slow-motion walk-in to a beat drop"
    assert [f.canonical_id for f in owner] == ["youtube:TestShort01"] and owner[0].status == "analyzed"
    prompt = world.master_prompts[0]
    assert "Robot butler in a black tailcoat" in prompt and "slow-motion walk-in to a beat drop" in prompt
    types = await types_of(db_sessionmaker, run_id)
    assert types.index("character.read") < types.index("trend.studied") < types.index("plan.created")
    assert orch.tools.recent == "day"


async def test_invalid_url_is_rejected_by_name(db_sessionmaker, tmp_path):
    orch = await build(db_sessionmaker, tmp_path, World())
    with pytest.raises(InputError, match="https://example.com/x"):
        await orch.create_run("testy", settings(trend_urls=["https://example.com/x"]))
    with pytest.raises(InputError, match="nobody"):
        await orch.create_run("testy", settings(targets=[{"url": TARGET, "character": "nobody"}]))


async def test_targets_for_another_character_wait_for_its_next_run(db_sessionmaker, tmp_path):
    orch = await build(db_sessionmaker, tmp_path, World())
    other = tmp_path / "chars" / "Robo Two"
    other.mkdir()
    (other / "robo two.png").write_bytes(b"png")
    await sync_characters(tmp_path / "chars", db_sessionmaker)
    first = await orch.create_run("testy", settings(targets=[{"url": TARGET, "character": "robo-two"}]))
    await orch.execute(first)
    async with db_sessionmaker() as s:
        t = (await s.execute(select(Target))).scalar_one()
        assert t.status == "pending" and t.run_id is None
    second = await orch.create_run("robo-two", settings())
    await orch.execute(second)
    async with db_sessionmaker() as s:
        t = (await s.execute(select(Target))).scalar_one()
        owner = (await s.execute(select(Finding).where(Finding.run_id == second, Finding.source == "owner"))
                 ).scalars().all()
    assert t.status == "used" and t.run_id == second and len(owner) == 1


async def test_unreachable_targets_are_rejected_and_the_run_continues(db_sessionmaker, tmp_path):
    """Review Focus 5."""
    orch = await build(db_sessionmaker, tmp_path, World())
    run_id = await orch.create_run("testy", settings(targets=[
        {"url": "https://www.tiktok.com/@gone/video/7400000000000000009?private=1", "character": "testy"},
        {"url": "https://www.instagram.com/reel/TESTREEL002/", "character": "testy"}]))
    assert (await orch.execute(run_id)).state == "review_ready"
    events = await Blackboard(db_sessionmaker).events_after(run_id, 0, limit=5000)
    reasons = [e["payload"]["reason"] for e in events if e["type"] == "candidate.rejected"]
    assert any("private or removed" in r for r in reasons) and any("media unavailable" in r for r in reasons)


async def test_resume_does_not_read_the_character_again(db_sessionmaker, tmp_path):
    world = World()
    orch = await build(db_sessionmaker, tmp_path, world)
    run_id = await orch.create_run("testy", settings())
    await orch.execute(run_id)
    async with db_sessionmaker() as s:
        await s.execute(update(Run).where(Run.id == run_id).values(state="interrupted", stop_reason=None))
        await s.commit()
    await orch.execute(run_id)
    assert world.reads == 1


async def test_ratings_refresh_the_taste_profile_before_the_read(db_sessionmaker, tmp_path):
    from tf_agent.learning.learner import Learner
    from tf_db.models import TasteProfile, TrendCluster

    world = World()
    orch = await build(db_sessionmaker, tmp_path, world)
    orch.learner = Learner(db_sessionmaker, orch.roles)
    first = await orch.create_run("testy", settings())
    await orch.execute(first)
    async with db_sessionmaker() as s:
        cluster = TrendCluster(run_id=first, member_count=0, rank=99)
        s.add(cluster)
        await s.commit()
        cid = cluster.id
    await orch.learner.rate(cid, "up", "more of this")
    second = await orch.create_run("testy", settings())
    await orch.execute(second)
    async with db_sessionmaker() as s:
        assert (await s.execute(select(TasteProfile.version))).scalars().all() == [1]
    types = await types_of(db_sessionmaker, second)
    assert types.index("agent.thought") < types.index("character.read")
    events = await Blackboard(db_sessionmaker).events_after(second, 0, limit=5000)
    assert any(e["type"] == "agent.finished" and e["payload"]["agent"]["role"] == "learner" for e in events)


async def test_phases_announce_once_and_trend_study_shows_up_as_an_agent(db_sessionmaker, tmp_path):
    from tf_agent.learning.learner import Learner

    orch = await build(db_sessionmaker, tmp_path, World())
    orch.learner = Learner(db_sessionmaker, orch.roles)
    run_id = await orch.create_run("testy", settings(trend_urls=[TREND]))
    await orch.execute(run_id)
    events = await Blackboard(db_sessionmaker).events_after(run_id, 0, limit=5000)
    states = [e["payload"]["state"] for e in events if e["type"] == "run.state"]
    assert states.count("reading_character") == 1
    kinds = [(e["type"], (e["payload"].get("agent") or {}).get("role")) for e in events]
    assert kinds.index(("agent.started", "seed_study")) < kinds.index(("trend.studied", "seed_study"))



# ---- Plan 8: references live in the owner's list, studied once per character ----
async def test_references_are_studied_once_and_reused_by_later_runs(db_sessionmaker, tmp_path):
    from tf_agent.manual import ManualVideos

    world = World()
    orch = await build(db_sessionmaker, tmp_path, world)
    first = await orch.create_run("testy", settings(trend_urls=[TREND], freshness="day"))
    await orch.execute(first)
    assert world.studies == 1
    (card,) = await ManualVideos(db_sessionmaker).list("testy")
    assert card["is_reference"] and card["study"]["format"] == "slow-motion walk-in to a beat drop"
    second = await orch.create_run("testy", settings(freshness="day"))  # no links given: the list is used
    await orch.execute(second)
    assert world.studies == 1  # reused, not studied again
    async with db_sessionmaker() as s:
        run = await s.get(Run, second)
    assert run.inputs["trend_studies"][TREND]["format"] == "slow-motion walk-in to a beat drop"
    assert "The owner's reference videos (1 studied" in world.master_prompts[-1]


def test_the_planner_gets_a_summary_and_every_reference_unjudged():
    from tf_agent.orchestrator.run import STUDY_DETAIL, reference_lines

    studies = [{"format": f"f{i}", "trend_type": "dance" if i % 2 else "skit", "niche": "office", "tags": ["desk"],
                "fit_score": i % 11} for i in range(30)]
    lines = reference_lines(studies)
    assert "dance (15)" in lines[1] and "skit (15)" in lines[1] and "office (30)" in lines[2]
    detail = lines[lines.index("Each reference:") + 1:]
    assert len(detail) == STUDY_DETAIL and detail[0].startswith("- skit | f0")
    assert not any("/10" in ln for ln in lines) and "Never question them" in lines[0]
