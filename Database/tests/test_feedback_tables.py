import pytest
from sqlalchemy.exc import IntegrityError

from tf_agent.testing import create_test_run
from tf_db.models import Brief, CardFeedback, RunFeedback, TrendCluster


async def test_feedback_rows_and_uniqueness(db_sessionmaker):
    _, run_id, _ = await create_test_run(db_sessionmaker, n_tasks=0)
    async with db_sessionmaker() as s:
        c = TrendCluster(run_id=run_id, member_count=1, rank=1)
        s.add(c)
        await s.flush()
        s.add(CardFeedback(cluster_id=c.id, rating="up", note="love it"))
        s.add(RunFeedback(run_id=run_id, satisfaction=7))
        s.add(Brief(cluster_id=c.id, body={"title": "t"}, body_md="# t", provider="claude", model="opus"))
        await s.commit()
        s.add(CardFeedback(cluster_id=c.id, rating="down"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_satisfaction_is_bounded(db_sessionmaker):
    _, run_id, _ = await create_test_run(db_sessionmaker, n_tasks=0)
    async with db_sessionmaker() as s:
        s.add(RunFeedback(run_id=run_id, satisfaction=11))
        with pytest.raises(IntegrityError):
            await s.commit()
