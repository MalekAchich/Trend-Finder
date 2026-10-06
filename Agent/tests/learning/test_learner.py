"""Plan 5 Task 6: ratings save one card at a time; the taste profile is refreshed at the next run's start."""
import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from tf_agent.learning.learner import FeedbackError, Learner
from tf_agent.models.fake import FakeAdapter, text_response
from tf_agent.roles.runners import Roles
from tf_agent.testing import create_test_run, make_client
from tf_db.models import (
    CardFeedback,
    Direction,
    Finding,
    FindingScore,
    Run,
    RunFeedback,
    SeenItem,
    TasteProfile,
    TrendCluster,
    TrendMember,
    Video,
)

LEARNED = {"taste_profile_md": "## Loves\n- mundane duties done with ceremony\n## Avoid\n- group dances",
           "retrospective": ["gym direction worked"], "primary_niche": "man out of time"}


async def world(sm, script=(), state="review_ready", n_cards=1):
    char_id, run_id, (task_id,) = await create_test_run(sm)
    clusters = []
    async with sm() as s:
        d = Direction(character_id=char_id, key="gym", label="Gym culture", niche="man out of time")
        s.add(d)
        await s.flush()
        for i in range(n_cards):
            s.add(Video(canonical_id=f"tiktok:{i + 1}", platform="tiktok", url="u"))
            await s.flush()
            f = Finding(run_id=run_id, task_id=task_id, canonical_id=f"tiktok:{i + 1}", direction_id=d.id,
                        status="analyzed")
            s.add(f)
            await s.flush()
            s.add(FindingScore(finding_id=f.id, fit=70, feasibility=80, momentum=50, freshness=90, overall=70,
                               niche_guess="man out of time"))
            c = TrendCluster(run_id=run_id, best_finding_id=f.id, member_count=1, rank=i + 1, overall=70, label="gym")
            s.add(c)
            await s.flush()
            s.add(TrendMember(cluster_id=c.id, finding_id=f.id))
            clusters.append(c.id)
        await s.execute(update(Run).where(Run.id == run_id).values(state=state))
        await s.commit()
        ids = SimpleNamespace(char=char_id, run=run_id, direction=d.id, cluster=clusters[0], clusters=clusters)
    client, _, _ = make_client({"a": FakeAdapter("a", list(script))})
    return ids, Learner(sm, Roles(client))


async def direction(sm, did):
    async with sm() as s:
        d = await s.get(Direction, did)
        return d.alpha, d.beta


async def test_rating_up_down_none_counts_only_the_final_state(db_sessionmaker):
    """Review Focus 4."""
    ids, learner = await world(db_sessionmaker)
    await learner.rate(ids.cluster, "up", "great")
    assert await direction(db_sessionmaker, ids.direction) == (1, 0)
    await learner.rate(ids.cluster, "down", "changed my mind")
    assert await direction(db_sessionmaker, ids.direction) == (0, 1)
    await learner.rate(ids.cluster, None, None)
    assert await direction(db_sessionmaker, ids.direction) == (0, 0)
    async with db_sessionmaker() as s:
        assert (await s.execute(select(CardFeedback))).scalars().all() == []


async def test_concurrent_ratings_on_one_run_serialise(db_sessionmaker):
    ids, learner = await world(db_sessionmaker, n_cards=4)
    await asyncio.gather(*(learner.rate(c, "up", None) for c in ids.clusters))
    assert await direction(db_sessionmaker, ids.direction) == (4, 0)


async def test_rating_marks_videos_seen_and_works_mid_run(db_sessionmaker):
    ids, learner = await world(db_sessionmaker, state="running")
    await learner.rate(ids.cluster, "down", None)
    async with db_sessionmaker() as s:
        seen = (await s.execute(select(SeenItem))).scalars().all()
    assert [(x.canonical_id, x.rated) for x in seen] == [("tiktok:1", True)]


async def test_unknown_card_is_rejected(db_sessionmaker):
    import uuid

    _, learner = await world(db_sessionmaker)
    with pytest.raises(FeedbackError):
        await learner.rate(uuid.uuid4(), "up", None)


async def test_run_satisfaction_only_after_the_run_ended(db_sessionmaker):
    ids, learner = await world(db_sessionmaker, state="running")
    with pytest.raises(FeedbackError, match="running"):
        await learner.rate_run(ids.run, 7, None)
    async with db_sessionmaker() as s:
        await s.execute(update(Run).where(Run.id == ids.run).values(state="review_ready"))
        await s.commit()
    await learner.rate_run(ids.run, 7, "more retro please")
    await learner.rate_run(ids.run, 8, "more retro please")
    async with db_sessionmaker() as s:
        assert (await s.get(RunFeedback, ids.run)).satisfaction == 8


async def test_refresh_taste_versions_only_when_feedback_changed(db_sessionmaker):
    ids, learner = await world(db_sessionmaker, [text_response("a", structured=LEARNED)] * 2)
    assert await learner.refresh_taste(ids.char, ids.run) is None  # nothing rated yet: no call
    await learner.rate(ids.cluster, "up", "love the ceremony")
    await learner.rate_run(ids.run, 7, "more retro please")
    first = await learner.refresh_taste(ids.char, ids.run)
    assert first.version == 1 and first.retrospective == ["gym direction worked"]
    assert await learner.refresh_taste(ids.char, ids.run) is None  # unchanged since v1
    async with db_sessionmaker() as s:
        body = (await s.execute(select(TasteProfile.body_md))).scalar_one()
    assert "## Owner notes (verbatim, newest first)" in body
    assert "- more retro please" in body and "- love the ceremony" in body
    await learner.rate(ids.cluster, "up", "love the ceremony, a lot")
    second = await learner.refresh_taste(ids.char, ids.run)
    assert second.version == 2


async def test_refresh_failure_is_reported_not_raised(db_sessionmaker):
    from tf_agent.models.errors import UsageLimited

    ids, learner = await world(db_sessionmaker, [UsageLimited("a", "limit")])
    await learner.rate(ids.cluster, "up", None)
    out = await learner.refresh_taste(ids.char, ids.run)
    assert out is not None and out.version is None and "no provider" in out.error
