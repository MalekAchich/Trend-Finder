"""Found videos of a run (live while it runs, curated after), and the owner's feedback on them."""
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from tf_agent.learning.learner import FeedbackError
from tf_agent.orchestrator.found import found_video
from tf_agent.pipeline import PIPELINE_VERSION
from tf_backend.api.deps import ctx, require_client_header
from tf_backend.app_context import AppContext
from tf_db.models import CardFeedback, Finding, FindingScore, Run, TrendCluster, Video, VideoAnalysis

router = APIRouter(tags=["videos"])


def _uuid(raw: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(404, f"unknown {what}") from None


def _latest_analysis():
    """One analysis per video: the current pipeline version's when there is one, else the newest older one,
    so videos from runs before a version bump keep their thumbnail and clean segment."""
    return (select(VideoAnalysis.canonical_id, VideoAnalysis.thumbnail_path, VideoAnalysis.best_clean_segment)
            .ext(distinct_on(VideoAnalysis.canonical_id))
            .order_by(VideoAnalysis.canonical_id, (VideoAnalysis.pipeline_version == PIPELINE_VERSION).desc(),
                      VideoAnalysis.created_at.desc()).subquery())


async def videos_for_run(s: AsyncSession, rid: uuid.UUID) -> list[dict[str, Any]]:
    """The curated cards once the run is ranked, otherwise the live set (analysed findings, best first)."""
    a = _latest_analysis()
    curated = (await s.execute(
        select(TrendCluster, Finding, FindingScore, Video, a.c.thumbnail_path, a.c.best_clean_segment, CardFeedback)
        .join(Finding, Finding.id == TrendCluster.best_finding_id)
        .join(Video, Video.canonical_id == Finding.canonical_id)
        .outerjoin(FindingScore, FindingScore.finding_id == Finding.id)
        .outerjoin(a, a.c.canonical_id == Finding.canonical_id)
        .outerjoin(CardFeedback, CardFeedback.cluster_id == TrendCluster.id)
        .where(TrendCluster.run_id == rid, Finding.source != "owner").order_by(TrendCluster.rank))).all()
    if curated:
        return [found_video(f, sc, v, _Analysis(thumb, seg), cluster_id=cl.id, feedback=fb)
                for cl, f, sc, v, thumb, seg, fb in curated]
    live = (await s.execute(
        select(Finding, FindingScore, Video, a.c.thumbnail_path, a.c.best_clean_segment)
        .join(FindingScore, FindingScore.finding_id == Finding.id)
        .join(Video, Video.canonical_id == Finding.canonical_id)
        .outerjoin(a, a.c.canonical_id == Finding.canonical_id)
        .where(Finding.run_id == rid, Finding.status == "analyzed", Finding.source != "owner")
        .order_by(FindingScore.overall.desc().nulls_last()))).all()
    return [found_video(f, sc, v, _Analysis(thumb, seg)) for f, sc, v, thumb, seg in live]


@router.get("/runs/{run_id}/videos")
async def run_videos(run_id: str, c: AppContext = Depends(ctx)) -> list[dict[str, Any]]:
    rid = _uuid(run_id, "run")
    async with c.sessionmaker() as s:
        if await s.get(Run, rid) is None:
            raise HTTPException(404, "unknown run")
        return await videos_for_run(s, rid)


@router.get("/runs/{run_id}/below-bar")
async def run_below_bar(run_id: str, c: AppContext = Depends(ctx)) -> list[dict[str, Any]]:
    """The videos a run judged but dropped for scoring under the owner's bar, closest first: to see what it cuts."""
    rid = _uuid(run_id, "run")
    a = _latest_analysis()
    async with c.sessionmaker() as s:
        if await s.get(Run, rid) is None:
            raise HTTPException(404, "unknown run")
        rows = (await s.execute(
            select(Finding, FindingScore, Video, a.c.thumbnail_path, a.c.best_clean_segment)
            .join(FindingScore, FindingScore.finding_id == Finding.id)
            .join(Video, Video.canonical_id == Finding.canonical_id)
            .outerjoin(a, a.c.canonical_id == Finding.canonical_id)
            .where(Finding.run_id == rid, Finding.status == "below_bar", Finding.source != "owner")
            .order_by(FindingScore.overall.desc().nulls_last()).limit(60))).all()
    return [found_video(f, sc, v, _Analysis(thumb, seg)) for f, sc, v, thumb, seg in rows]


class _Analysis:
    """The two analysis fields a card needs (avoids loading the whole row)."""

    def __init__(self, thumbnail_path: str | None, best_clean_segment: dict[str, float] | None) -> None:
        self.thumbnail_path, self.best_clean_segment = thumbnail_path, best_clean_segment


class VideoFeedbackIn(BaseModel):
    rating: Literal["up", "down"] | None
    note: str | None = Field(None, max_length=1000)


@router.put("/videos/{cluster_id}/feedback", dependencies=[Depends(require_client_header)])
async def video_feedback(cluster_id: str, body: VideoFeedbackIn, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    try:
        await c.learner.rate(_uuid(cluster_id, "video"), body.rating, body.note)
    except FeedbackError as e:
        raise HTTPException(404, str(e)) from e
    return {"rating": body.rating, "note": (body.note or "").strip() or None}


class RunFeedbackIn(BaseModel):
    satisfaction: int = Field(ge=1, le=10)
    note: str | None = Field(None, max_length=2000)


@router.put("/runs/{run_id}/feedback", dependencies=[Depends(require_client_header)])
async def run_feedback(run_id: str, body: RunFeedbackIn, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    try:
        await c.learner.rate_run(_uuid(run_id, "run"), body.satisfaction, body.note)
    except FeedbackError as e:
        raise HTTPException(404 if "unknown" in str(e) else 409, str(e)) from e
    return {"satisfaction": body.satisfaction}
