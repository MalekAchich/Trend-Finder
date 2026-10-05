"""Runs: start, list, detail, stop, resume, and live events over SSE (Last-Event-ID replay)."""
import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from tf_agent.orchestrator.blackboard import Blackboard
from tf_agent.orchestrator.run import RunSettings
from tf_backend.api.deps import ctx, require_client_header
from tf_backend.app_context import AppContext
from tf_backend.runs import TERMINAL, RunError
from tf_db.models import Character, Direction, Finding, Round, Run, Task

router = APIRouter(prefix="/runs", tags=["runs"])


class RunIn(BaseModel):
    slug: str
    platforms: list[str] = Field(default_factory=lambda: ["tiktok", "youtube", "instagram"])
    rounds: int = Field(3, ge=1, le=10)
    tasks_per_round: int = Field(12, ge=1, le=16)
    target_findings: int = Field(20, ge=1, le=200)
    good_score: float = Field(60.0, ge=0, le=100)
    minutes: float = Field(60.0, ge=1, le=600)
    workers: int = Field(8, ge=1, le=16)


@router.post("", dependencies=[Depends(require_client_header)])
async def start_run(body: RunIn, c: AppContext = Depends(ctx)) -> dict[str, str]:
    settings = RunSettings(platforms=body.platforms, rounds=body.rounds, tasks_per_round=body.tasks_per_round,
                           target_findings=body.target_findings, good_score=body.good_score,
                           wall_clock_s=body.minutes * 60, workers=body.workers)
    try:
        run_id = await c.runs.start(body.slug, settings)
    except Exception as e:  # unknown character etc.
        raise HTTPException(422, str(e)) from e
    return {"run_id": str(run_id)}


def _run_row(r: Run, slug: str, active: bool) -> dict[str, Any]:
    return {"id": str(r.id), "character": slug, "state": r.state, "round": r.current_round,
            "stop_reason": r.stop_reason, "error": r.error, "explore_ratio": r.explore_ratio_used,
            "settings": r.settings, "started_at": r.started_at, "finished_at": r.finished_at, "active": active}


@router.get("")
async def list_runs(limit: int = 30, c: AppContext = Depends(ctx)) -> list[dict[str, Any]]:
    async with c.sessionmaker() as s:
        rows = (await s.execute(select(Run, Character.slug).join(Character, Character.id == Run.character_id)
                                .order_by(Run.started_at.desc()).limit(limit))).all()
    return [_run_row(r, slug, c.runs.is_active(r.id)) for r, slug in rows]


def _uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(404, "unknown run") from None


@router.get("/{run_id}")
async def run_detail(run_id: str, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    rid = _uuid(run_id)
    async with c.sessionmaker() as s:
        row = (await s.execute(select(Run, Character.slug).join(Character, Character.id == Run.character_id)
                               .where(Run.id == rid))).one_or_none()
        if row is None:
            raise HTTPException(404, "unknown run")
        run, slug = row
        rounds = (await s.execute(select(Round).where(Round.run_id == rid).order_by(Round.number))).scalars().all()
        tasks = (await s.execute(select(Task, Direction.label).outerjoin(Direction, Direction.id == Task.direction_id)
                                 .where(Task.run_id == rid).order_by(Task.created_at))).all()
        counts = dict((await s.execute(select(Finding.status, func.count()).where(Finding.run_id == rid)
                                       .group_by(Finding.status))).all())
    return {
        **_run_row(run, slug, c.runs.is_active(rid)),
        "rounds": [{"number": r.number, "summary": (r.plan or {}).get("reasoning_summary"),
                    "rejections": r.plan_rejections or [], "result": r.summary, "started_at": r.started_at,
                    "finished_at": r.finished_at} for r in rounds],
        "tasks": [{"id": str(t.id), "type": t.task_type, "platform": t.platform, "state": t.state, "goal": t.goal,
                   "direction": label, "scope": {k: v for k, v in (t.scope or {}).items() if k != "lead_info"},
                   "attempts": t.attempts,
                   "result": {k: (len(v) if isinstance(v, list) else v) for k, v in (t.result or {}).items()
                              if k in ("accepted", "rejected", "leads", "provider", "steps", "failed", "overall",
                                       "filtered", "error")}} for t, label in tasks],
        "findings": counts,
    }


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
    bb = Blackboard(c.sessionmaker)

    async def stream():
        nonlocal last
        quiet_after_end = 0
        heartbeat = 0.0
        while True:
            events = await bb.events_after(rid, last)
            for e in events:
                last = e["id"]
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
