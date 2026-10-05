"""Turn a run's analyzed findings into ranked trend cards (05-scoring: clustering, cross-check, ranking)."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.curation.cluster import Member, best_source, cluster_members
from tf_agent.models.errors import AllProvidersUnavailable, InvalidRequest
from tf_agent.orchestrator.analysis import candidate_facts
from tf_agent.pipeline import PIPELINE_VERSION
from tf_agent.pipeline.result import VideoAnalysisResult
from tf_agent.roles.runners import RoleOutputError, Roles
from tf_agent.scoring.subscores import DEFAULT_WEIGHTS, freshness, overall, percentile
from tf_agent.tools.store import VideoStore
from tf_db.models import CardFeedback, Finding, FindingScore, TrendCluster, TrendMember, Video, VideoAnalysis

log = logging.getLogger(__name__)
DISAGREEMENT = 30.0


def final_fit(sc: FindingScore) -> float | None:
    """The analyst's fit stays untouched; with a cross-check the score uses the mean of both opinions."""
    if sc.fit is None:
        return None
    return round((sc.fit + sc.cross_fit) / 2, 2) if sc.cross_fit is not None else sc.fit


class CharacterLike(Protocol):
    brief: str
    canonical_image_path: str


class Curator:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], roles: Roles, *, top_k: int = 15,
                 weights: dict[str, float] | None = None) -> None:
        self._sm = sessionmaker
        self.roles = roles
        self.top_k = top_k
        self.weights = weights or DEFAULT_WEIGHTS
        self.store = VideoStore(sessionmaker)

    async def _rows(self, run_id: uuid.UUID) -> list[tuple[Finding, FindingScore, Video, VideoAnalysis | None]]:
        async with self._sm() as s:
            return list((await s.execute(
                select(Finding, FindingScore, Video, VideoAnalysis)
                .join(FindingScore, FindingScore.finding_id == Finding.id)
                .join(Video, Video.canonical_id == Finding.canonical_id)
                .outerjoin(VideoAnalysis, (VideoAnalysis.canonical_id == Finding.canonical_id)
                           & (VideoAnalysis.pipeline_version == PIPELINE_VERSION))
                .where(Finding.run_id == run_id, Finding.status == "analyzed"))).all())

    async def _save_score(self, finding_id: uuid.UUID, **values: Any) -> None:
        async with self._sm() as s:
            await s.execute(update(FindingScore).where(FindingScore.finding_id == finding_id).values(**values))
            await s.commit()

    async def curate(self, run_id: uuid.UUID, character: CharacterLike,
                     weights: dict[str, float] | None = None) -> None:
        weights = weights or self.weights
        async with self._sm() as s:
            rated = (await s.execute(select(CardFeedback.id).join(TrendCluster, TrendCluster.id == CardFeedback.cluster_id)
                                     .where(TrendCluster.run_id == run_id).limit(1))).first()
        if rated is not None:  # cards were rated: re-curating would delete the owner's ratings and briefs
            log.info("run %s already has feedback; keeping its trend cards", run_id)
            return
        rows = await self._rows(run_id)
        by_id = {str(f.id): (f, sc, v, a) for f, sc, v, a in rows}
        members = [Member(finding_id=str(f.id), canonical_id=f.canonical_id, sound_id=v.sound_id,
                          frame_hashes=list(((a.fingerprint or {}).get("frame_hashes") or []) if a else []),
                          feasibility=sc.feasibility, height=((a.probe or {}).get("height") if a else None),
                          momentum=sc.momentum, overall=sc.overall) for f, sc, v, a in rows]
        clusters = cluster_members(members)
        sizes = [float(len(c)) for c in clusters]
        now = datetime.now(UTC)
        cards: list[dict[str, Any]] = []
        for cluster in clusters:
            saturation = percentile(float(len(cluster)), sizes) if len(clusters) > 1 else 0.0
            for m in cluster:
                f, sc, v, _ = by_id[m.finding_id]
                age = (now - v.posted_at).total_seconds() / 3600 if v.posted_at else None
                fresh = freshness(age, saturation or 0.0)
                sub = {"fit": final_fit(sc), "feasibility": sc.feasibility, "momentum": sc.momentum,
                       "freshness": fresh}
                m.overall = overall(sub, weights)
                await self._save_score(f.id, freshness=fresh, overall=m.overall)
            best = best_source(cluster)
            cards.append({"members": cluster, "best": best, "overall": best.overall or 0.0})
        cards.sort(key=lambda c: c["overall"], reverse=True)
        for card in cards[: self.top_k]:
            await self._cross_check(card, by_id, character, weights)
        cards.sort(key=lambda c: c["overall"], reverse=True)
        async with self._sm() as s:
            await s.execute(delete(TrendCluster).where(TrendCluster.run_id == run_id))
            for rank, card in enumerate(cards, start=1):
                best = card["best"]
                f, sc, v, _ = by_id[best.finding_id]
                cluster = TrendCluster(run_id=run_id, label=sc.niche_guess or (v.caption or "")[:80],
                                       best_finding_id=f.id, member_count=len(card["members"]),
                                       sound_id=best.sound_id, overall=card["overall"], rank=rank)
                s.add(cluster)
                await s.flush()
                s.add_all([TrendMember(cluster_id=cluster.id, finding_id=uuid.UUID(m.finding_id))
                           for m in card["members"]])
            await s.commit()

    async def _cross_check(self, card: dict[str, Any], by_id: dict[str, Any], character: CharacterLike,
                           weights: dict[str, float]) -> None:
        best = card["best"]
        f, sc, v, a = by_id[best.finding_id]
        if a is None or not a.contact_sheet_path or sc.fit is None or sc.cross_fit is not None:
            return  # already cross-checked: never pay for (or average in) a second opinion twice
        item = await self.store.get_video(f.canonical_id)
        analysis = VideoAnalysisResult(canonical_id=a.canonical_id, probe=a.probe, cuts=list(a.cuts or []),
                                       transcript=a.transcript, pose=a.pose, camera_motion=a.camera_motion,
                                       best_clean_segment=a.best_clean_segment, feasibility=a.feasibility)
        try:
            judged = await self.roles.cross_check(character.brief, character.canonical_image_path,
                                                  a.contact_sheet_path, candidate_facts(item, analysis),
                                                  exclude_provider=sc.analyst_provider or "", run_id=f.run_id)
        except (AllProvidersUnavailable, RoleOutputError, InvalidRequest) as e:
            log.warning("cross-check skipped for %s: %s", f.canonical_id, e)
            return
        cross = judged.result.fit
        async with self._sm() as s:
            fresh = (await s.get(FindingScore, f.id)).freshness
        sub = {"fit": round((sc.fit + cross) / 2, 2), "feasibility": sc.feasibility, "momentum": sc.momentum,
               "freshness": fresh}
        new_overall = overall(sub, weights)
        await self._save_score(f.id, cross_fit=cross, cross_provider=judged.provider,
                               cross_justification=judged.result.justification,
                               disagreement=abs(sc.fit - cross) >= DISAGREEMENT, overall=new_overall)
        best.overall = new_overall
        card["overall"] = new_overall or 0.0
