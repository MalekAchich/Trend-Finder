import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from tf_db.models import (
    Character,
    CharacterVersion,
    Event,
    Finding,
    Run,
    ScopeClaim,
    Task,
    Video,
)


async def seed(s):
    c = Character(slug="nicolaiz", name="Nicolaiz", folder_path="/x")
    s.add(c)
    await s.flush()
    v = CharacterVersion(character_id=c.id, version=1, profile_md="p", front_matter={}, canonical_image_path="/i.png",
                         content_hash="h")
    s.add(v)
    await s.flush()
    r = Run(character_id=c.id, character_version_id=v.id, state="created", settings={"rounds": 3})
    s.add(r)
    await s.flush()
    t = Task(run_id=r.id, task_type="scout", platform="tiktok", scope={"queries": ["a"]})
    s.add(t)
    s.add(Video(canonical_id="tiktok:1", platform="tiktok", url="u"))
    await s.flush()
    return c, r, t


async def test_scope_claims_are_unique_per_run(db_sessionmaker):
    async with db_sessionmaker() as s:
        _, r, t = await seed(s)
        s.add(ScopeClaim(run_id=r.id, platform="tiktok", kind="query", value="a", task_id=t.id))
        await s.commit()
        s.add(ScopeClaim(run_id=r.id, platform="tiktok", kind="query", value="a", task_id=t.id))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_findings_unique_per_run_and_cascade_with_run(db_sessionmaker):
    async with db_sessionmaker() as s:
        _, r, t = await seed(s)
        run_id, task_id = r.id, t.id
        s.add(Finding(run_id=run_id, task_id=task_id, canonical_id="tiktok:1"))
        await s.commit()
        s.add(Finding(run_id=run_id, task_id=task_id, canonical_id="tiktok:1"))
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()
        await s.execute(delete(Run).where(Run.id == run_id))
        await s.commit()
        assert (await s.execute(select(Finding))).scalars().all() == []
        assert (await s.execute(select(Task))).scalars().all() == []


async def test_events_have_increasing_ids(db_sessionmaker):
    async with db_sessionmaker() as s:
        _, r, _ = await seed(s)
        for i in range(3):
            s.add(Event(run_id=r.id, type="task.started", payload={"i": i}))
            await s.flush()
        await s.commit()
        ids = [e.id for e in (await s.execute(select(Event).order_by(Event.id))).scalars()]
    assert ids == sorted(ids) and len(set(ids)) == 3


async def test_task_defaults(db_sessionmaker):
    async with db_sessionmaker() as s:
        _, _, t = await seed(s)
        await s.commit()
        got = await s.get(Task, t.id)
    assert (got.state, got.attempts, got.lease_until, got.not_before) == ("queued", 0, None, None)
