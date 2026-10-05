"""Postgres task queue (D-23: FOR UPDATE SKIP LOCKED + leases) and an asyncio worker pool."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_db.models import Task

log = logging.getLogger(__name__)


class Requeue(Exception):  # noqa: N818 (control-flow signal, not an error)
    """Raised by a handler to put its task back (e.g. both providers usage-limited)."""

    def __init__(self, not_before: datetime | None, reason: str = "") -> None:
        super().__init__(reason)
        self.not_before = not_before
        self.reason = reason


class TaskQueue:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], lease_s: int = 600) -> None:
        self._sm = sessionmaker
        self.lease_s = lease_s

    async def enqueue(self, run_id: uuid.UUID, round_id: uuid.UUID | None, task_type: str, *,
                      platform: str | None = None, direction_id: uuid.UUID | None = None,
                      scope: dict[str, Any] | None = None, goal: str | None = None,
                      budget: dict[str, Any] | None = None, not_before: datetime | None = None,
                      prompt_version: str | None = None) -> uuid.UUID:
        async with self._sm() as s:
            task = Task(run_id=run_id, round_id=round_id, task_type=task_type, platform=platform,
                        direction_id=direction_id, scope=scope or {}, goal=goal, budget=budget or {},
                        not_before=not_before, prompt_version=prompt_version)
            s.add(task)
            await s.commit()
            return task.id

    async def claim_next(self, run_id: uuid.UUID, kinds: Sequence[str]) -> Task | None:
        now = func.now()
        pick = (select(Task.id).where(
            Task.run_id == run_id, Task.task_type.in_(list(kinds)),
            or_(Task.state == "queued", (Task.state == "running") & (Task.lease_until < now)),
            or_(Task.not_before.is_(None), Task.not_before <= now))
            .order_by(Task.created_at).limit(1).with_for_update(skip_locked=True).scalar_subquery())
        stmt = (update(Task).where(Task.id == pick)
                .values(state="running", attempts=Task.attempts + 1,
                        lease_until=now + timedelta(seconds=self.lease_s),
                        started_at=func.coalesce(Task.started_at, now))
                .returning(Task))
        async with self._sm() as s:
            task = (await s.execute(stmt)).scalar_one_or_none()
            await s.commit()
            return task

    async def _set(self, task_id: uuid.UUID, **values: Any) -> None:
        async with self._sm() as s:
            await s.execute(update(Task).where(Task.id == task_id).values(**values))
            await s.commit()

    async def renew(self, task_id: uuid.UUID) -> None:
        await self._set(task_id, lease_until=func.now() + timedelta(seconds=self.lease_s))

    async def complete(self, task_id: uuid.UUID, result: dict[str, Any] | None, *, provider: str | None = None,
                       model: str | None = None) -> None:
        await self._set(task_id, state="succeeded", result=result, provider=provider, model=model, lease_until=None,
                        finished_at=func.now())

    async def fail(self, task_id: uuid.UUID, error: dict[str, Any]) -> None:
        await self._set(task_id, state="failed", error=error, lease_until=None, finished_at=func.now())

    async def requeue(self, task_id: uuid.UUID, not_before: datetime | None, error: dict[str, Any] | None = None) -> None:
        await self._set(task_id, state="queued", lease_until=None, not_before=not_before, error=error)

    async def cancel_queued(self, run_id: uuid.UUID) -> int:
        async with self._sm() as s:
            res = await s.execute(update(Task).where(Task.run_id == run_id, Task.state == "queued")
                                  .values(state="cancelled", finished_at=func.now()))
            await s.commit()
            return res.rowcount or 0

    async def outstanding(self, run_id: uuid.UUID, kinds: Sequence[str] | None = None) -> int:
        q = select(func.count()).select_from(Task).where(Task.run_id == run_id, Task.state.in_(("queued", "running")))
        if kinds:
            q = q.where(Task.task_type.in_(list(kinds)))
        async with self._sm() as s:
            return int((await s.execute(q)).scalar_one())

    async def next_ready_at(self, run_id: uuid.UUID) -> datetime | None:
        async with self._sm() as s:
            return (await s.execute(select(func.min(Task.not_before)).where(
                Task.run_id == run_id, Task.state == "queued"))).scalar_one()

    async def tasks(self, run_id: uuid.UUID) -> list[Task]:
        async with self._sm() as s:
            return list((await s.execute(select(Task).where(Task.run_id == run_id).order_by(Task.created_at)))
                        .scalars())


Handler = Callable[[Task], Awaitable[dict[str, Any] | None]]


class WorkerPool:
    """N workers claim tasks of `kinds` for one run, run the handler, and record the outcome."""

    def __init__(self, queue: TaskQueue, run_id: uuid.UUID, kinds: Sequence[str], handler: Handler,
                 concurrency: int, *, renew_every_s: float = 120.0, idle_poll: float = 0.5) -> None:
        self.queue, self.run_id, self.kinds, self.handler = queue, run_id, list(kinds), handler
        self.concurrency, self.renew_every_s, self.idle_poll = concurrency, renew_every_s, idle_poll
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def _renew_loop(self, task_id: uuid.UUID) -> None:
        while True:
            await asyncio.sleep(self.renew_every_s)
            await self.queue.renew(task_id)

    async def _execute(self, task: Task) -> None:
        renewer = asyncio.create_task(self._renew_loop(task.id))
        try:
            result = await self.handler(task)
        except Requeue as r:
            await self.queue.requeue(task.id, r.not_before, {"type": "Requeue", "message": r.reason})
        except asyncio.CancelledError:
            await asyncio.shield(self.queue.requeue(task.id, None, {"type": "Cancelled", "message": "stopped"}))
            raise
        except Exception as e:
            log.exception("task %s (%s) failed", task.id, task.task_type)
            await self.queue.fail(task.id, {"type": type(e).__name__, "message": str(e)[:500]})
        else:
            await self.queue.complete(task.id, result)
        finally:
            renewer.cancel()

    async def _worker(self, until_idle: bool) -> None:
        while not self._stop.is_set():
            task = await self.queue.claim_next(self.run_id, self.kinds)
            if task is None:
                if until_idle and await self.queue.outstanding(self.run_id, self.kinds) == 0:
                    return
                await asyncio.sleep(self.idle_poll)
                continue
            await self._execute(task)

    async def run_until_idle(self) -> None:
        """Process until no queued/running task of these kinds is left (deferred tasks are waited for)."""
        await asyncio.gather(*(self._worker(until_idle=True) for _ in range(self.concurrency)))
