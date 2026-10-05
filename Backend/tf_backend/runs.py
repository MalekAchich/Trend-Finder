"""Runs execute as background tasks inside the API process; unfinished runs resume on startup."""
from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.orchestrator.blackboard import Blackboard
from tf_agent.orchestrator.queue import TaskQueue
from tf_db.models import Run

log = logging.getLogger(__name__)
TERMINAL = ("review_ready", "stopped", "failed")
RESUMABLE = ("created", "planning", "running", "paused_usage", "curating", "interrupted")


class RunError(Exception):
    pass


class RunManager:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], orchestrator_factory: Callable[[], Any]) -> None:
        self._sm = sessionmaker
        self._factory = orchestrator_factory
        self._orchestrator: Any = None
        self._tasks: dict[uuid.UUID, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()

    async def orchestrator(self) -> Any:
        async with self._lock:
            if self._orchestrator is None:
                made = self._factory()
                self._orchestrator = await made if inspect.isawaitable(made) else made
            return self._orchestrator

    def is_active(self, run_id: uuid.UUID) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    def _launch(self, orch: Any, run_id: uuid.UUID) -> None:
        task = asyncio.create_task(orch.execute(run_id), name=f"run-{run_id}")
        self._tasks[run_id] = task

        def done(t: asyncio.Task[Any]) -> None:
            if not t.cancelled() and t.exception() is not None:
                log.error("run %s crashed: %s", run_id, t.exception())

        task.add_done_callback(done)

    async def start(self, slug: str, settings: Any) -> uuid.UUID:
        orch = await self.orchestrator()
        run_id = await orch.create_run(slug, settings)
        self._launch(orch, run_id)
        return run_id

    async def _state(self, run_id: uuid.UUID) -> str:
        async with self._sm() as s:
            run = await s.get(Run, run_id)
        if run is None:
            raise RunError("unknown run")
        return run.state

    async def resume(self, run_id: uuid.UUID) -> None:
        state = await self._state(run_id)
        if state in TERMINAL:
            raise RunError(f"this run is {state}; start a new run instead")
        if not self.is_active(run_id):
            self._launch(await self.orchestrator(), run_id)

    async def stop(self, run_id: uuid.UUID) -> None:
        state = await self._state(run_id)
        if state in TERMINAL:
            raise RunError(f"this run is already {state}")
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        orch = self._orchestrator
        if orch is not None and hasattr(orch, "stop_and_curate"):
            await orch.stop_and_curate(run_id)  # keep what was found: curate, then end as `stopped`
            return
        await TaskQueue(self._sm).cancel_queued(run_id)
        async with self._sm() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(
                state="stopped", stop_reason="stopped by the owner", finished_at=func.now()))
            await s.commit()
        await Blackboard(self._sm).record_event(run_id, "run.state", {"state": "stopped",
                                                                       "stop_reason": "stopped by the owner"})

    async def resume_unfinished(self) -> list[uuid.UUID]:
        async with self._sm() as s:
            ids = list((await s.execute(select(Run.id).where(Run.state.in_(RESUMABLE))
                                        .order_by(Run.started_at))).scalars())
        if ids:
            orch = await self.orchestrator()
            queue = TaskQueue(self._sm)
            for run_id in ids:
                if not self.is_active(run_id):
                    await queue.reset_running(run_id)  # this process owns its runs: no 10-minute lease wait
                    self._launch(orch, run_id)
        return ids

    async def shutdown(self) -> None:
        """Cancel running runs; they stay resumable and continue on the next start."""
        tasks = [t for t in self._tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
