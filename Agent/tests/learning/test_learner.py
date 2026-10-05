import random
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import select, update

from tf_agent.learning.learner import CardRating, FeedbackError, Learner
from tf_agent.learning.weights import suggest_weights
from tf_agent.models.fake import FakeAdapter, text_response
from tf_agent.roles.runners import Roles
from tf_agent.testing import create_test_run, make_client
from tf_db.models import (
    Direction,
    Finding,
    FindingScore,
    Run,
    SeenItem,
    TasteProfile,
    TrendCluster,
    TrendMember,
    Video,
)

LEARNED = {"taste_profile_md": "## Loves\n- mundane duties done with ceremony\n## Avoid\n- group dances",
           "retrospective": ["gym direction worked", "group dances filtered"], "primary_niche": None}


async def world(sm, script):
    char_id, run_id, (task_id,) = await create_test_run(sm)
    async with sm() as s:
        d = Direction(character_id=char_id, key="gym", label="Gym culture", niche="man out of time")
        s.add(d)
        s.add(Video(canonical_id="tiktok:1", platform="tiktok", url="u"))
        await s.flush()
        f = Finding(run_id=run_id, task_id=task_id, canonical_id="tiktok:1", direction_id=d.id, status="analyzed")
        s.add(f)
        await s.flush()
        s.add(FindingScore(finding_id=f.id, fit=70, feasibility=80, momentum=50, freshness=90, overall=70,
                           niche_guess="man out of time"))
        c = TrendCluster(run_id=run_id, best_finding_id=f.id, member_count=1, rank=1, overall=70, label="gym")
        s.add(c)
        await s.flush()
        s.add(TrendMember(cluster_id=c.id, finding_id=f.id))
        await s.execute(update(Run).where(Run.id == run_id).values(state="review_ready"))
        await s.commit()
        ids = SimpleNamespace(char=char_id, run=run_id, direction=d.id, cluster=c.id)
    client, _, _ = make_client({"a": FakeAdapter("a", script)})
    return ids, Learner(sm, Roles(client))


async def direction(sm, did):
    async with sm() as s:
        return await s.get(Direction, did)


async def test_rerating_replaces_previous_contribution(db_sessionmaker):
    ids, learner = await world(db_sessionmaker, [text_response("a", structured=LEARNED)] * 2)
    await learner.apply_feedback(ids.run, [CardRating(ids.cluster, "up", "great")], satisfaction=7, note=None)
    d = await direction(db_sessionmaker, ids.direction)
    assert (d.alpha, d.beta) == (1, 0)
    await learner.apply_feedback(ids.run, [CardRating(ids.cluster, "down", "changed my mind")], satisfaction=4,
                                 note=None)
    d = await direction(db_sessionmaker, ids.direction)
    assert (d.alpha, d.beta) == (0, 1)


async def test_rated_videos_are_marked_seen(db_sessionmaker):
    ids, learner = await world(db_sessionmaker, [text_response("a", structured=LEARNED)])
    await learner.apply_feedback(ids.run, [CardRating(ids.cluster, "skip", None)], satisfaction=5, note=None)
    async with db_sessionmaker() as s:
        seen = (await s.execute(select(SeenItem))).scalars().all()
    assert [(x.canonical_id, x.rated) for x in seen] == [("tiktok:1", True)]


async def test_taste_profile_versions_keep_owner_notes_verbatim(db_sessionmaker):
    ids, learner = await world(db_sessionmaker, [text_response("a", structured=LEARNED)] * 2)
    out = await learner.apply_feedback(ids.run, [CardRating(ids.cluster, "up", "MORE gyms, less dancing!")],
                                       satisfaction=8, note="Great first run")
    assert out.taste_version == 1 and out.retrospective == LEARNED["retrospective"]
    async with db_sessionmaker() as s:
        body = (await s.execute(select(TasteProfile.body_md))).scalar_one()
    assert "mundane duties" in body and "MORE gyms, less dancing!" in body and "Great first run" in body
    out2 = await learner.apply_feedback(ids.run, [CardRating(ids.cluster, "up", None)], satisfaction=8, note=None)
    assert out2.taste_version == 2


async def test_learner_failure_keeps_ratings(db_sessionmaker):
    bad = text_response("a", structured={"nope": True})
    ids, learner = await world(db_sessionmaker, [bad, bad])
    out = await learner.apply_feedback(ids.run, [CardRating(ids.cluster, "up", None)], satisfaction=6, note=None)
    assert out.learner_error and out.taste_version is None
    assert (await direction(db_sessionmaker, ids.direction)).alpha == 1


async def test_feedback_requires_a_review_ready_run(db_sessionmaker):
    ids, learner = await world(db_sessionmaker, [])
    async with db_sessionmaker() as s:
        await s.execute(update(Run).where(Run.id == ids.run).values(state="running"))
        await s.commit()
    with pytest.raises(FeedbackError, match="review"):
        await learner.apply_feedback(ids.run, [CardRating(ids.cluster, "up", None)], satisfaction=5, note=None)


def test_weight_suggestion_needs_data_and_finds_the_driver():
    assert suggest_weights(np.zeros((10, 4)), np.zeros(10)) is None
    rng = random.Random(3)
    X, y = [], []
    for _ in range(200):
        fit, feas, mom, fresh = (rng.uniform(0, 100) for _ in range(4))
        X.append([fit, feas, mom, fresh])
        y.append(1 if fit + rng.gauss(0, 10) > 50 else 0)
    w = suggest_weights(np.array(X), np.array(y))
    assert w is not None and max(w, key=w.get) == "fit" and sum(w.values()) == pytest.approx(1.0)
