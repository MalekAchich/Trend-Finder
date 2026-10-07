"""Candidate intake and the analysis stage: pipeline → vision Analyst → sub-scores (01-agents, 05-scoring)."""
from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import UTC, datetime
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.models.errors import AllProvidersUnavailable, ContentRefused, InvalidRequest
from tf_agent.orchestrator.found import found_video
from tf_agent.orchestrator.queue import Requeue, TaskQueue
from tf_agent.pipeline.result import VideoAnalysisResult
from tf_agent.roles.runners import RoleOutputError, Roles
from tf_agent.roles.schemas import Candidate
from tf_agent.scoring.subscores import DEFAULT_WEIGHTS, freshness, momentum, overall, peer_stats
from tf_agent.tools.store import VideoStore
from tf_agent.tools.types import CANONICAL_ID_PATTERN, VideoItem
from tf_db.models import Finding, FindingScore, Task, Video, VideoAnalysis

Emit = Callable[[uuid.UUID, str, dict[str, Any]], Awaitable[Any]]

log = logging.getLogger(__name__)
_CID_RE = re.compile(CANONICAL_ID_PATTERN)
PEERS_TTL_S = 600


class CandidateSink:
    """Accepts worker candidates: only real videos a tool returned, once per run; each gets an analysis task."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], queue: TaskQueue) -> None:
        self._sm = sessionmaker
        self.queue = queue

    async def submit(self, run_id: uuid.UUID, task_id: uuid.UUID | None, direction_id: uuid.UUID | None,
                     candidates: list[Candidate]) -> tuple[list[str], list[tuple[str, str]]]:
        accepted: list[str] = []
        rejected: list[tuple[str, str]] = []
        for c in candidates:
            cid = c.canonical_id.strip()
            if not _CID_RE.match(cid):
                rejected.append((cid, "not a canonical id"))
                continue
            async with self._sm() as s:
                if await s.get(Video, cid) is None:
                    rejected.append((cid, "unknown video: not returned by any tool"))
                    continue
                stmt = pg_insert(Finding).values(run_id=run_id, task_id=task_id, canonical_id=cid,
                                                 direction_id=direction_id, why=c.why,
                                                 preliminary_fit=c.preliminary_fit).on_conflict_do_nothing()
                finding_id = (await s.execute(stmt.returning(Finding.id))).scalar_one_or_none()
                await s.commit()
            if finding_id is None:
                rejected.append((cid, "already submitted in this run"))
                continue
            await self.queue.enqueue(run_id, None, "analyze", scope={"finding_id": str(finding_id),
                                                                     "canonical_id": cid})
            accepted.append(cid)
        return accepted, rejected


class Analyzer(Protocol):
    async def analyze(self, item: VideoItem) -> VideoAnalysisResult: ...


class CharacterLike(Protocol):
    brief: str
    canonical_image_path: str


def candidate_facts(item: VideoItem, analysis: VideoAnalysisResult) -> dict[str, Any]:
    pose = analysis.pose or {}
    return {
        "platform": item.platform, "caption": (item.caption or "")[:400], "hashtags": item.hashtags[:12],
        "sound": item.sound.title, "views": item.metrics.views, "likes": item.metrics.likes,
        "posted_at": item.posted_at.isoformat() if item.posted_at else None, "duration_s": item.duration_s,
        "transcript": ((analysis.transcript or {}).get("text") or "")[:500],
        "single_person_ratio": pose.get("single_person_ratio"), "body_visibility": pose.get("body_visibility"),
        "camera_motion": analysis.camera_motion, "scene_cuts": len(analysis.cuts or []),
        "best_clean_segment": analysis.best_clean_segment, "kling_feasibility": analysis.feasibility,
    }


class AnalysisStage:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], analyzer: Analyzer, roles: Roles,
                 store: VideoStore, character: CharacterLike, weights: dict[str, float] | None = None,
                 emit: Emit | None = None) -> None:
        self._sm = sessionmaker
        self.analyzer, self.roles, self.store, self.character = analyzer, roles, store, character
        self._emit_fn = emit
        self.weights = weights or DEFAULT_WEIGHTS
        self._peers: dict[str, tuple[float, list[float], list[float]]] = {}
        # one scoring model per run, so every score in it is on the same scale; it may move once, at a usage limit
        self.analyst_provider: str | None = None
        self._analyst_moved = False

    async def _peer(self, platform: str) -> tuple[list[float], list[float]]:
        cached = self._peers.get(platform)
        if cached is None or time.monotonic() - cached[0] > PEERS_TTL_S:
            vph, eng = await peer_stats(self._sm, platform, datetime.now(UTC))
            cached = (time.monotonic(), vph, eng)
            self._peers[platform] = cached
        return cached[1], cached[2]

    async def _status(self, finding_id: uuid.UUID, status: str) -> None:
        async with self._sm() as s:
            await s.execute(update(Finding).where(Finding.id == finding_id).values(status=status))
            await s.commit()

    async def _save_score(self, finding_id: uuid.UUID, values: dict[str, Any]) -> None:
        stmt = pg_insert(FindingScore).values(finding_id=finding_id, **values)
        stmt = stmt.on_conflict_do_update(index_elements=[FindingScore.finding_id], set_=values)
        async with self._sm() as s:
            await s.execute(stmt)
            await s.commit()

    async def _emit(self, task: Task, type_: str, payload: dict[str, Any], platform: str | None = None) -> None:
        if self._emit_fn is None:
            return
        agent = {"id": f"analyst-{str(task.id)[-6:]}", "role": "analyst", "platform": platform}
        try:
            await self._emit_fn(task.run_id, type_, {"agent": agent, **payload})
        except Exception as e:  # narration never costs an analysis
            log.warning("event %s failed: %s", type_, e)

    async def _finished(self, task: Task, cid: str, verdict: str, platform: str | None, reason: str | None = None
                        ) -> None:
        await self._emit(task, "analysis.finished", {"canonical_id": cid, "verdict": verdict, "reason": reason},
                         platform)
        if verdict != "saved":
            await self._emit(task, "candidate.rejected", {"canonical_id": cid, "reason": reason or verdict}, platform)

    async def handle(self, task: Task) -> dict[str, Any]:
        finding_id = uuid.UUID(task.scope["finding_id"])
        cid = task.scope["canonical_id"]
        item = await self.store.get_video(cid)
        if item is None:
            await self._status(finding_id, "failed")
            await self._finished(task, cid, "failed", None, "video record missing")
            return {"error": "video record missing"}
        await self._emit(task, "analysis.started", {"canonical_id": cid}, item.platform)
        try:
            analysis = await self.analyzer.analyze(item)
        except Exception as e:  # never leave a finding stuck in pending_analysis
            log.exception("analysis of %s crashed", item.canonical_id)
            await self._status(finding_id, "failed")
            await self._finished(task, cid, "failed", item.platform, f"analysis crashed: {type(e).__name__}")
            return {"error": f"analysis crashed: {type(e).__name__}: {e}"[:300]}
        if analysis.filtered_reason or not analysis.contact_sheet_path:
            await self._save_score(finding_id, {"feasibility": analysis.feasibility})
            await self._status(finding_id, "filtered_feasibility")
            reason = analysis.filtered_reason or "no_contact_sheet"
            await self._finished(task, cid, "filtered", item.platform, f"filtered: {reason.replace('_', ' ')}")
            return {"filtered": reason}
        try:
            judged = await self._judge(task, item, analysis)
        except AllProvidersUnavailable as e:
            reset = e.earliest_reset or time.time() + 900
            raise Requeue(datetime.fromtimestamp(reset, UTC), "all providers usage-limited") from e
        except ContentRefused as e:  # not retried: the prompt needs fixing (logged with the request id)
            await self._status(finding_id, "failed")
            await self._finished(task, cid, "failed", item.platform,
                                 f"the analyst was refused ({e.detail or 'safety filter'}, request {e.request_id or '?'})")
            return {"error": "refused", "request_id": e.request_id}
        except (RoleOutputError, InvalidRequest) as e:
            await self._status(finding_id, "failed")
            await self._finished(task, cid, "failed", item.platform, f"analyst error: {str(e)[:160]}")
            return {"error": str(e)[:300]}
        now = datetime.now(UTC)
        vph_peers, eng_peers = await self._peer(item.platform)
        sub = {"fit": judged.result.fit, "feasibility": analysis.feasibility,
               "momentum": momentum(item.views_per_hour(now), item.engagement_rate(), vph_peers, eng_peers),
               "freshness": freshness(item.age_hours(now))}
        score = overall(sub, self.weights)
        r = judged.result
        await self._save_score(finding_id, {
            **sub, "overall": score, "fit_breakdown": r.fit_breakdown.model_dump(), "fit_justification": r.justification,
            "adaptation_idea": r.adaptation_idea, "feasibility_notes": r.feasibility_notes,
            "niche_guess": r.niche_guess, "analyst_provider": judged.provider, "analyst_model": judged.model})
        await self._status(finding_id, "analyzed")
        await self._saved(task, finding_id, item.platform)
        return {"overall": score, "fit": sub["fit"], "provider": judged.provider}

    async def _judge(self, task: Task, item: VideoItem, analysis: VideoAnalysisResult) -> Any:
        async def ask() -> Any:
            return await self.roles.analyze(self.character.brief, self.character.canonical_image_path,
                                            analysis.contact_sheet_path, candidate_facts(item, analysis),
                                            run_id=task.run_id, task_id=task.id, provider=self.analyst_provider)
        try:
            judged = await ask()
        except AllProvidersUnavailable:
            client = self.roles.client
            others = [p for p in client.adapters if p != self.analyst_provider and client.governor.available(p)]
            if self.analyst_provider is None or self._analyst_moved or not others:
                raise
            old, self.analyst_provider, self._analyst_moved = self.analyst_provider, others[0], True
            await self._emit(task, "provider.switched", {
                "from": old, "to": others[0],
                "reason": "the scoring model hit its usage limit; the rest of this run is scored by the other one"},
                item.platform)
            judged = await ask()
        self.analyst_provider = self.analyst_provider or judged.provider
        return judged

    async def _saved(self, task: Task, finding_id: uuid.UUID, platform: str) -> None:
        if self._emit_fn is None:
            return
        async with self._sm() as s:
            f = await s.get(Finding, finding_id)
            sc = await s.get(FindingScore, finding_id)
            v = await s.get(Video, f.canonical_id)
            a = (await s.execute(select(VideoAnalysis).where(VideoAnalysis.canonical_id == f.canonical_id)
                                 .order_by(VideoAnalysis.created_at.desc()).limit(1))).scalar_one_or_none()
        await self._emit(task, "video.saved", {"video": found_video(f, sc, v, a)}, platform)
        await self._finished(task, f.canonical_id, "saved", platform)


async def finding_rows(sessionmaker: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> list[tuple[Finding, FindingScore | None]]:
    async with sessionmaker() as s:
        return list((await s.execute(select(Finding, FindingScore).outerjoin(
            FindingScore, FindingScore.finding_id == Finding.id).where(Finding.run_id == run_id))).all())
