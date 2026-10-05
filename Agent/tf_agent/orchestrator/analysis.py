"""Candidate intake and the analysis stage: pipeline → vision Analyst → sub-scores (01-agents, 05-scoring)."""
from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.models.errors import AllProvidersUnavailable, InvalidRequest
from tf_agent.orchestrator.queue import Requeue, TaskQueue
from tf_agent.pipeline.result import VideoAnalysisResult
from tf_agent.roles.runners import RoleOutputError, Roles
from tf_agent.roles.schemas import Candidate
from tf_agent.scoring.subscores import DEFAULT_WEIGHTS, freshness, momentum, overall, peer_stats
from tf_agent.tools.store import VideoStore
from tf_agent.tools.types import CANONICAL_ID_PATTERN, VideoItem
from tf_db.models import Finding, FindingScore, Task, Video

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
                 store: VideoStore, character: CharacterLike, weights: dict[str, float] | None = None) -> None:
        self._sm = sessionmaker
        self.analyzer, self.roles, self.store, self.character = analyzer, roles, store, character
        self.weights = weights or DEFAULT_WEIGHTS
        self._peers: dict[str, tuple[float, list[float], list[float]]] = {}

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

    async def handle(self, task: Task) -> dict[str, Any]:
        finding_id = uuid.UUID(task.scope["finding_id"])
        item = await self.store.get_video(task.scope["canonical_id"])
        if item is None:
            await self._status(finding_id, "failed")
            return {"error": "video record missing"}
        analysis = await self.analyzer.analyze(item)
        if analysis.filtered_reason or not analysis.contact_sheet_path:
            await self._save_score(finding_id, {"feasibility": analysis.feasibility})
            await self._status(finding_id, "filtered_feasibility")
            return {"filtered": analysis.filtered_reason or "no_contact_sheet"}
        try:
            judged = await self.roles.analyze(self.character.brief, self.character.canonical_image_path,
                                              analysis.contact_sheet_path, candidate_facts(item, analysis),
                                              run_id=task.run_id, task_id=task.id)
        except AllProvidersUnavailable as e:
            reset = e.earliest_reset or time.time() + 900
            raise Requeue(datetime.fromtimestamp(reset, UTC), "all providers usage-limited") from e
        except (RoleOutputError, InvalidRequest) as e:
            await self._status(finding_id, "failed")
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
        return {"overall": score, "fit": sub["fit"], "provider": judged.provider}


async def finding_rows(sessionmaker: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> list[tuple[Finding, FindingScore | None]]:
    async with sessionmaker() as s:
        return list((await s.execute(select(Finding, FindingScore).outerjoin(
            FindingScore, FindingScore.finding_id == Finding.id).where(Finding.run_id == run_id))).all())
