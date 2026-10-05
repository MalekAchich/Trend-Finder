import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from tf_agent.orchestrator.queue import Requeue, TaskQueue, WorkerPool
from tf_db.models import Task

from .test_blackboard import make_run


async def test_concurrent_claimers_never_share_a_task(db_sessionmaker):
    q = TaskQueue(db_sessionmaker)
    _, run, _ = await make_run(db_sessionmaker, n_tasks=0)
    for _ in range(20):
        await q.enqueue(run, None, "scout", platform="tiktok")
    claimed = []

    async def claimer():
        while (t := await q.claim_next(run, ["scout"])) is not None:
            claimed.append(t.id)

    await asyncio.gather(*(claimer() for _ in range(8)))
    assert len(claimed) == 20 and len(set(claimed)) == 20


async def test_expired_lease_is_reclaimed(db_sessionmaker):
    q = TaskQueue(db_sessionmaker)
    _, run, _ = await make_run(db_sessionmaker, n_tasks=0)
    tid = await q.enqueue(run, None, "analyze")
    first = await q.claim_next(run, ["analyze"])
    assert first.id == tid and await q.claim_next(run, ["analyze"]) is None
    async with db_sessionmaker() as s:
        await s.execute(update(Task).where(Task.id == tid).values(lease_until=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()
    again = await q.claim_next(run, ["analyze"])
    assert again.id == tid and again.attempts == 2


async def test_not_before_and_kinds_are_respected(db_sessionmaker):
    q = TaskQueue(db_sessionmaker)
    _, run, _ = await make_run(db_sessionmaker, n_tasks=0)
    tid = await q.enqueue(run, None, "scout", not_before=datetime.now(UTC) + timedelta(hours=1))
    await q.enqueue(run, None, "analyze")
    assert await q.claim_next(run, ["scout"]) is None
    assert (await q.claim_next(run, ["analyze"])).task_type == "analyze"
    assert await q.next_ready_at(run) is not None
    await q.requeue(tid, not_before=None)
    assert (await q.claim_next(run, ["scout"])).id == tid


async def test_worker_pool_completes_fails_and_requeues(db_sessionmaker):
    q = TaskQueue(db_sessionmaker)
    _, run, _ = await make_run(db_sessionmaker, n_tasks=0)
    ids = [await q.enqueue(run, None, "scout", goal=g) for g in ("ok", "boom", "later", "ok")]

    async def handler(task):
        if task.goal == "boom":
            raise RuntimeError("worker crashed")
        if task.goal == "later" and task.attempts == 1:
            raise Requeue(not_before=None, reason="usage limited")
        return {"done": task.goal}

    pool = WorkerPool(q, run, ["scout"], handler, concurrency=3, idle_poll=0.01)
    await pool.run_until_idle()
    states = {t.goal: (t.state, t.result, t.error) for t in await q.tasks(run)}
    assert states["ok"][0] == "succeeded" and states["ok"][1] == {"done": "ok"}
    assert states["boom"][0] == "failed" and "worker crashed" in states["boom"][2]["message"]
    assert states["later"][0] == "succeeded"  # requeued once, then done
    assert await q.outstanding(run) == 0 and len(ids) == 4


async def test_renew_extends_the_lease(db_sessionmaker):
    q = TaskQueue(db_sessionmaker, lease_s=60)
    _, run, _ = await make_run(db_sessionmaker, n_tasks=0)
    await q.enqueue(run, None, "scout")
    t = await q.claim_next(run, ["scout"])
    await q.renew(t.id)
    renewed = next(x for x in await q.tasks(run) if x.id == t.id)
    assert renewed.lease_until > t.lease_until
