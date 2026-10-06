"""Runs: start (with the owner's URLs), the active run, detail with its agents, stop, resume, live events (SSE)."""
import asyncio
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from tf_agent.characters.folders import sync_characters
from tf_agent.orchestrator.blackboard import Blackboard, EventCursor
from tf_agent.orchestrator.run import InputError, RunSettings
from tf_backend.api.characters import character_card
from tf_backend.api.deps import ctx, require_client_header
from tf_backend.app_context import AppContext
from tf_backend.runs import TERMINAL, RunError
from tf_db.models import Character, Finding, Run, Task

router = APIRouter(prefix="/runs", tags=["runs"])
TASK_STATUS = {"queued": "waiting", "running": "working", "succeeded": "done", "failed": "failed",
               "cancelled": "done"}


class TargetIn(BaseModel):
    url: str = Field(min_length=10, max_length=500)
    character: str = Field(min_length=1, max_length=64)


class RunIn(BaseModel):
    character: str = Field(min_length=1, max_length=64)
    platforms: list[Literal["tiktok", "instagram", "youtube"]] = Field(
        default_factory=lambda: ["tiktok", "youtube", "instagram"], min_length=1)
    freshness: Literal["day", "week", "month", "any"] = "week"
    minutes: float = Field(60.0, ge=5, le=600)
    trend_urls: list[str] = Field(default_factory=list, max_length=20)
    targets: list[TargetIn] = Field(default_factory=list, max_length=20)


@router.post("", dependencies=[Depends(require_client_header)])
async def start_run(body: RunIn, c: AppContext = Depends(ctx)) -> dict[str, str]:
    await sync_characters(c.characters_dir, c.sessionmaker)
    settings = RunSettings(platforms=list(body.platforms), wall_clock_s=body.minutes * 60, freshness=body.freshness,
                           trend_urls=[u.strip() for u in body.trend_urls if u.strip()],
                           targets=[t.model_dump() for t in body.targets])
    try:
        run_id = await c.runs.start(body.character, settings)
    except InputError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:  # unknown character etc.
        raise HTTPException(422, str(e)) from e
    return {"run_id": str(run_id)}


def _uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(404, "unknown run") from None


async def _run_out(c: AppContext, run: Run, ch: Character) -> dict[str, Any]:
    async with c.sessionmaker() as s:
        tasks = (await s.execute(select(Task).where(Task.run_id == run.id, Task.task_type != "analyze")
                                 .order_by(Task.created_at))).scalars().all()
        counts = dict((await s.execute(select(Finding.status, func.count()).where(Finding.run_id == run.id)
                                       .group_by(Finding.status))).all())
    return {
        "id": str(run.id), "state": run.state, "character": await character_card(c, ch),
        "stop_reason": run.stop_reason, "error": run.error, "started_at": run.started_at,
        "finished_at": run.finished_at, "active": c.runs.is_active(run.id), "inputs": run.inputs or {},
        "character_read": run.character_read, "round": run.current_round,
        "minutes": round(float((run.settings or {}).get("wall_clock_s", 3600)) / 60),
        "agents": [{"id": str(t.id), "role": t.task_type, "platform": t.platform,
                    "status": TASK_STATUS.get(t.state, t.state), "goal": t.goal} for t in tasks],
        "findings": counts,
    }


@router.get("/active")
async def active_run(c: AppContext = Depends(ctx)) -> dict[str, Any] | None:
    async with c.sessionmaker() as s:
        rows = (await s.execute(select(Run, Character).join(Character, Character.id == Run.character_id)
                                .where(Run.state.not_in(TERMINAL)).order_by(Run.started_at.desc()))).all()
    for run, ch in rows:
        if c.runs.is_active(run.id) or run.state != "interrupted":
            return await _run_out(c, run, ch)
    return None


@router.get("/{run_id}")
async def run_detail(run_id: str, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    rid = _uuid(run_id)
    async with c.sessionmaker() as s:
        row = (await s.execute(select(Run, Character).join(Character, Character.id == Run.character_id)
                               .where(Run.id == rid))).one_or_none()
    if row is None:
        raise HTTPException(404, "unknown run")
    return await _run_out(c, *row)


@router.post("/{run_id}/stop", dependencies=[Depends(require_client_header)])
async def stop_run(run_id: str, c: AppContext = Depends(ctx)) -> dict[str, bool]:
    try:
        await c.runs.stop(_uuid(run_id))
    except RunError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/{run_id}/resume", dependencies=[Depends(require_client_header)])
async def resume_run(run_id: str, c: AppContext = Depends(ctx)) -> dict[str, bool]:
    try:
        await c.runs.resume(_uuid(run_id))
    except RunError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.get("/{run_id}/events")
async def run_events(run_id: str, request: Request, after: int | None = None,
                     c: AppContext = Depends(ctx)) -> StreamingResponse:
    rid = _uuid(run_id)
    last = after if after is not None else int(request.headers.get("last-event-id") or 0)
    cursor = EventCursor(Blackboard(c.sessionmaker), rid, after=last)

    async def stream():
        quiet_after_end = 0
        heartbeat = 0.0
        while True:
            events = await cursor.next_batch()
            for e in events:
                yield f"id: {e['id']}\nevent: {e['type']}\ndata: {json.dumps(e, default=str)}\n\n"
            async with c.sessionmaker() as s:
                state = (await s.execute(select(Run.state).where(Run.id == rid))).scalar_one_or_none()
            if state is None:
                return
            if state in TERMINAL and not events:
                quiet_after_end += 1
                if quiet_after_end >= 2:  # the final event may land just after the state flips
                    return
            else:
                quiet_after_end = 0
            if await request.is_disconnected():
                return
            heartbeat += c.sse_poll_s
            if heartbeat >= 15:
                heartbeat = 0.0
                yield ": keep-alive\n\n"
            await asyncio.sleep(c.sse_poll_s)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
