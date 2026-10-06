"""Regression tests for the Plan 3 final review."""
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text, update

from tf_agent.orchestrator.blackboard import Blackboard, EventCursor
from tf_agent.orchestrator.queue import TaskQueue, WorkerPool
from tf_db.models import Direction, Finding, Round, Run, Task

from .test_blackboard import make_run
from .test_run import SETTINGS, World, build, findings


# ---- I1: infrastructure errors cancel siblings and leave the run resumable ----
async def test_pool_cancels_siblings_when_a_worker_crashes(db_sessionmaker):
    q = TaskQueue(db_sessionmaker)
    _, run, _ = await make_run(db_sessionmaker, n_tasks=0)
    slow = await q.enqueue(run, None, "scout", goal="slow")
    await q.enqueue(run, None, "scout", goal="other")
    cancelled = asyncio.Event()

    async def handler(task):
        if task.goal == "slow":
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.set()
                raise
        raise SystemError("db connection lost")  # infrastructure failure outside handler logic

    pool = WorkerPool(q, run, ["scout"], handler, concurrency=2, idle_poll=0.01, crash_on=(SystemError,))
    with pytest.raises(ExceptionGroup):
        await pool.run_until_idle()
    assert cancelled.is_set()
    states = {t.goal: t.state for t in await q.tasks(run)}
    assert states["slow"] == "queued"  # requeued, not lost


async def test_worker_survives_a_transient_queue_error(db_sessionmaker):
    q = TaskQueue(db_sessionmaker)
    _, run, _ = await make_run(db_sessionmaker, n_tasks=0)
    for _ in range(3):
        await q.enqueue(run, None, "scout")
    real = q.claim_next
    calls = {"n": 0}

    async def flaky(run_id, kinds):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("blip")
        return await real(run_id, kinds)

    q.claim_next = flaky

    async def handler(task):
        return {"ok": True}

    await WorkerPool(q, run, ["scout"], handler, concurrency=1, idle_poll=0.01, retry_s=0.01).run_until_idle()
    assert all(t.state == "succeeded" for t in await q.tasks(run))


async def test_stale_worker_cannot_overwrite_a_reclaimed_task(db_sessionmaker):
    q = TaskQueue(db_sessionmaker)
    _, run, _ = await make_run(db_sessionmaker, n_tasks=0)
    tid = await q.enqueue(run, None, "scout")
    first = await q.claim_next(run, ["scout"])
    async with db_sessionmaker() as s:
        await s.execute(update(Task).where(Task.id == tid).values(lease_until=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()
    second = await q.claim_next(run, ["scout"])
    await q.complete(tid, {"from": "stale"}, attempts=first.attempts)
    t = next(x for x in await q.tasks(run) if x.id == tid)
    assert t.state == "running" and t.result is None and second.attempts == 2


async def test_unexpected_error_marks_run_interrupted_not_failed(db_sessionmaker, tmp_path):
    world = World()
    orch = await build(db_sessionmaker, tmp_path, world)
    run_id = await orch.create_run("testy", SETTINGS)

    async def boom(*a, **k):
        raise ConnectionError("database went away")

    orch._summarize = boom
    outcome = await orch.execute(run_id)
    assert outcome.state == "interrupted"


async def test_poison_tasks_stop_after_max_attempts(db_sessionmaker):
    q = TaskQueue(db_sessionmaker)
    _, run, _ = await make_run(db_sessionmaker, n_tasks=0)
    tid = await q.enqueue(run, None, "scout")
    async with db_sessionmaker() as s:
        await s.execute(update(Task).where(Task.id == tid).values(attempts=5))
        await s.commit()
    ran = []

    async def handler(task):
        ran.append(task.id)
        return {}

    await WorkerPool(q, run, ["scout"], handler, concurrency=1, idle_poll=0.01, max_attempts=5).run_until_idle()
    t = (await q.tasks(run))[0]
    assert ran == [] and t.state == "failed" and "attempts" in t.error["message"]


# ---- I2: one loop per run ----
async def test_concurrent_execute_of_one_run_runs_once(db_sessionmaker, tmp_path):
    world = World()
    orch = await build(db_sessionmaker, tmp_path, world)
    run_id = await orch.create_run("testy", SETTINGS)
    a, b = await asyncio.gather(orch.execute(run_id), orch.execute(run_id))
    assert "review_ready" in (a.state, b.state) and "failed" not in (a.state, b.state)
    assert world.rounds_planned == 1


# ---- I3: resume semantics ----
async def test_resume_uses_active_time_not_calendar_time(db_sessionmaker, tmp_path):
    orch = await build(db_sessionmaker, tmp_path, World())
    run_id = await orch.create_run("testy", SETTINGS)
    async with db_sessionmaker() as s:
        await s.execute(update(Run).where(Run.id == run_id).values(started_at=datetime.now(UTC) - timedelta(hours=5)))
        await s.commit()
    outcome = await orch.execute(run_id)
    assert "target" in outcome.stop_reason


async def test_resume_during_curation_goes_straight_to_curation(db_sessionmaker, tmp_path):
    world = World()
    orch = await build(db_sessionmaker, tmp_path, world)
    run_id = await orch.create_run("testy", SETTINGS)
    async with db_sessionmaker() as s:
        await s.execute(update(Run).where(Run.id == run_id).values(state="curating", stop_reason="target reached (9)"))
        await s.commit()
    outcome = await orch.execute(run_id)
    assert outcome.state == "review_ready" and outcome.stop_reason == "target reached (9)" and world.rounds_planned == 0


async def test_a_round_without_a_plan_is_reused(db_sessionmaker, tmp_path):
    orch = await build(db_sessionmaker, tmp_path, World())
    run_id = await orch.create_run("testy", SETTINGS)
    async with db_sessionmaker() as s:
        s.add(Round(run_id=run_id, number=1))
        await s.execute(update(Run).where(Run.id == run_id).values(current_round=1, state="planning"))
        await s.commit()
    await orch.execute(run_id)
    async with db_sessionmaker() as s:
        numbers = (await s.execute(select(Round.number).where(Round.run_id == run_id))).scalars().all()
    assert numbers == [1]


# ---- I4: exploit tasks keep their direction; unknown keys are rejected ----
async def test_exploit_task_resolves_existing_direction(db_sessionmaker, tmp_path):
    from tf_agent.roles.schemas import ScopeSpec, TaskPlan, WorkPlan

    orch = await build(db_sessionmaker, tmp_path, World())
    run_id = await orch.create_run("testy", SETTINGS)
    async with db_sessionmaker() as s:
        run = await s.get(Run, run_id)
        d = Direction(character_id=run.character_id, key="gym-culture", label="Gym culture", alpha=3, beta=1)
        s.add(d)
        rnd = Round(run_id=run_id, number=1)
        s.add(rnd)
        await s.commit()
        did, rid = d.id, rnd.id
    plan = WorkPlan(reasoning_summary="x", directions=[], tasks=[
        TaskPlan(task_type="scout", direction_key="Gym Culture", platform="tiktok",
                 scope=ScopeSpec(queries=["gym deadpan"]), goal="exploit gym"),
        TaskPlan(task_type="scout", direction_key="never-declared", platform="tiktok",
                 scope=ScopeSpec(queries=["other"]), goal="ghost direction")])
    from tf_agent.characters.folders import load_character

    ch = await load_character(db_sessionmaker, "testy")
    enqueued, rejections = await orch._apply_plan(run_id, rid, plan, SETTINGS, ch, 0)
    assert len(enqueued) == 1 and "unknown direction" in rejections[0]["reason"]
    async with db_sessionmaker() as s:
        assert (await s.get(Task, enqueued[0])).direction_id == did


# ---- I6: events committed out of order are still delivered exactly once ----
async def test_event_cursor_catches_late_commits(db_sessionmaker):
    bb = Blackboard(db_sessionmaker)
    _, run, _ = await make_run(db_sessionmaker, n_tasks=0)
    async with db_sessionmaker() as s:
        reserved = (await s.execute(text("select nextval('events_id_seq')"))).scalar_one()
    await bb.record_event(run, "a", {})
    cursor = EventCursor(bb, run)
    first = [e["type"] for e in await cursor.next_batch()]
    async with db_sessionmaker() as s:  # the slow transaction commits its earlier id now
        await s.execute(text("insert into events (id, run_id, type, payload) values (:i, :r, 'late', '{}')"),
                        {"i": reserved, "r": run})
        await s.commit()
    second = [e["type"] for e in await cursor.next_batch()]
    third = await cursor.next_batch()
    assert first == ["a"] and second == ["late"] and third == []


# ---- I7: weights are fixed per run ----
async def test_run_settings_capture_saved_weights(db_sessionmaker, tmp_path):
    from tf_db.models import Setting

    orch = await build(db_sessionmaker, tmp_path, World())
    async with db_sessionmaker() as s:
        s.add(Setting(key="weights", value={"fit": 0.7, "feasibility": 0.1, "momentum": 0.1, "freshness": 0.1}))
        await s.commit()
    run_id = await orch.create_run("testy", SETTINGS)
    async with db_sessionmaker() as s:
        assert (await s.get(Run, run_id)).settings["weights"]["fit"] == 0.7


# ---- M4: analyzer crash fails the finding instead of leaving it pending ----
async def test_analyzer_crash_fails_the_finding(db_sessionmaker, tmp_path):
    from .test_analysis import run_one, setup

    class Broken:
        async def analyze(self, item):
            raise RuntimeError("pipeline exploded")

    run, task_id, q, stage, _ = await setup(db_sessionmaker, tmp_path, None, [])
    stage.analyzer = Broken()
    out = await run_one(db_sessionmaker, q, stage, run, task_id)
    async with db_sessionmaker() as s:
        assert (await s.execute(select(Finding))).scalar_one().status == "failed"
    assert "pipeline exploded" in out["error"]


async def test_stop_curates_and_ends_review_ready(db_sessionmaker, tmp_path):
    """Plan 4 final #1: a stopped run's cards must stay reviewable (07: stop "curates; ends in review_ready")."""
    curated = []

    class RecordingCurator:
        async def curate(self, run_id, character, weights=None):
            curated.append((run_id, weights))

    orch = await build(db_sessionmaker, tmp_path, World())
    orch.curator = RecordingCurator()
    run_id = await orch.create_run("testy", SETTINGS)
    outcome = await orch.stop_and_curate(run_id)
    assert outcome.state == "review_ready" and outcome.stop_reason == "stopped by the owner"
    assert curated and curated[0][1]["fit"] == 0.4


class FlakyCurator:
    def __init__(self):
        self.calls = 0

    async def curate(self, run_id, character, weights=None):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("db blip during curation")


async def _rounds(sm, run_id):
    async with sm() as s:
        return len((await s.execute(select(Round).where(Round.run_id == run_id))).scalars().all())


async def test_curation_crash_keeps_the_stop_decision(db_sessionmaker, tmp_path):
    """Plan 4 final #3: a resumed run whose curation crashed curates again instead of planning more rounds."""
    orch = await build(db_sessionmaker, tmp_path, World())
    orch.curator = FlakyCurator()
    run_id = await orch.create_run("testy", SETTINGS)
    first = await orch.execute(run_id)
    assert first.state == "interrupted" and first.stop_reason
    rounds = await _rounds(db_sessionmaker, run_id)
    second = await orch.execute(run_id)
    assert second.state == "review_ready" and second.stop_reason == first.stop_reason
    assert await _rounds(db_sessionmaker, run_id) == rounds and orch.curator.calls == 2


async def test_failed_stop_curation_resumes_into_curation(db_sessionmaker, tmp_path):
    orch = await build(db_sessionmaker, tmp_path, World())
    orch.curator = FlakyCurator()
    run_id = await orch.create_run("testy", SETTINGS)
    failed = await orch.stop_and_curate(run_id)
    assert failed.state == "interrupted" and failed.stop_reason == "stopped by the owner"
    done = await orch.execute(run_id)
    assert done.state == "review_ready" and await _rounds(db_sessionmaker, run_id) == 0