"""Trend cards, owner feedback (→ learner) and production briefs."""
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from tf_agent.characters.sync import load_character
from tf_agent.learning.briefs import BriefError
from tf_agent.learning.learner import CardRating, FeedbackError
from tf_agent.models.errors import AllProvidersUnavailable, ProviderError
from tf_agent.pipeline import PIPELINE_VERSION
from tf_agent.roles.runners import RoleOutputError
from tf_backend.api.deps import ctx, require_client_header
from tf_backend.app_context import AppContext
from tf_db.models import (
    Brief,
    CardFeedback,
    Character,
    Finding,
    FindingScore,
    Run,
    RunFeedback,
    TrendCluster,
    TrendMember,
    Video,
    VideoAnalysis,
)

router = APIRouter(tags=["trends"])


def _uuid(raw: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(404, f"unknown {what}") from None


def sheet_url(c: AppContext, path: str | None) -> str | None:
    if not path:
        return None
    try:
        rel = Path(path).resolve().relative_to((c.media_dir / "sheets").resolve())
    except ValueError:
        return None
    return f"/api/media/sheets/{rel.as_posix()}"


def _video(v: Video) -> dict[str, Any]:
    return {"canonical_id": v.canonical_id, "platform": v.platform, "url": v.url, "creator": v.creator_handle,
            "caption": v.caption, "hashtags": v.hashtags, "sound": v.sound_title, "metrics": v.metrics,
            "posted_at": v.posted_at, "duration_s": v.duration_s}


@router.get("/runs/{run_id}/trends")
async def trends(run_id: str, include: str | None = None, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    rid = _uuid(run_id, "run")
    async with c.sessionmaker() as s:
        run = await s.get(Run, rid)
        if run is None:
            raise HTTPException(404, "unknown run")
        rows = (await s.execute(
            select(TrendCluster, Finding, FindingScore, Video, VideoAnalysis, CardFeedback, Brief.id)
            .join(Finding, Finding.id == TrendCluster.best_finding_id)
            .join(FindingScore, FindingScore.finding_id == Finding.id)
            .join(Video, Video.canonical_id == Finding.canonical_id)
            .outerjoin(VideoAnalysis, (VideoAnalysis.canonical_id == Finding.canonical_id)
                       & (VideoAnalysis.pipeline_version == PIPELINE_VERSION))
            .outerjoin(CardFeedback, CardFeedback.cluster_id == TrendCluster.id)
            .outerjoin(Brief, Brief.cluster_id == TrendCluster.id)
            .where(TrendCluster.run_id == rid).order_by(TrendCluster.rank))).all()
        members = (await s.execute(select(TrendMember.cluster_id, Video.url, Video.canonical_id)
                                   .join(Finding, Finding.id == TrendMember.finding_id)
                                   .join(Video, Video.canonical_id == Finding.canonical_id)
                                   .join(TrendCluster, TrendCluster.id == TrendMember.cluster_id)
                                   .where(TrendCluster.run_id == rid))).all()
        run_fb = await s.get(RunFeedback, rid)
        filtered = []
        if include == "filtered":
            frows = (await s.execute(select(Finding, Video, VideoAnalysis)
                                     .join(Video, Video.canonical_id == Finding.canonical_id)
                                     .outerjoin(VideoAnalysis, (VideoAnalysis.canonical_id == Finding.canonical_id)
                                                & (VideoAnalysis.pipeline_version == PIPELINE_VERSION))
                                     .where(Finding.run_id == rid, Finding.status != "analyzed"))).all()
            filtered = [{"video": _video(v), "status": f.status, "reason": a.filtered_reason if a else None,
                         "why": f.why, "contact_sheet_url": sheet_url(c, a.contact_sheet_path if a else None)}
                        for f, v, a in frows]
    by_cluster: dict[uuid.UUID, list[dict[str, str]]] = {}
    for cid, url, canon in members:
        by_cluster.setdefault(cid, []).append({"url": url, "canonical_id": canon})
    cards = [{
        "id": str(cl.id), "rank": cl.rank, "label": cl.label, "overall": cl.overall, "member_count": cl.member_count,
        "best": _video(v), "why": f.why,
        "scores": {"fit": sc.fit, "feasibility": sc.feasibility, "momentum": sc.momentum, "freshness": sc.freshness,
                   "overall": sc.overall},
        "fit_breakdown": sc.fit_breakdown, "justification": sc.fit_justification, "adaptation_idea": sc.adaptation_idea,
        "feasibility_notes": sc.feasibility_notes, "niche_guess": sc.niche_guess, "disagreement": sc.disagreement,
        "cross_check": None if sc.cross_fit is None else {"fit": sc.cross_fit, "provider": sc.cross_provider,
                                                          "justification": sc.cross_justification},
        "analyst": {"provider": sc.analyst_provider, "model": sc.analyst_model},
        "motion_window": a.best_clean_segment if a else None,
        "pose": (a.pose or {}) if a else {}, "camera_motion": a.camera_motion if a else None,
        "contact_sheet_url": sheet_url(c, a.contact_sheet_path if a else None),
        "members": by_cluster.get(cl.id, []),
        "feedback": None if fb is None else {"rating": fb.rating, "note": fb.note},
        "has_brief": brief_id is not None,
    } for cl, f, sc, v, a, fb, brief_id in rows]
    return {"run_id": str(rid), "state": run.state, "cards": cards, "filtered": filtered,
            "run_feedback": None if run_fb is None else {"satisfaction": run_fb.satisfaction, "note": run_fb.note}}


class CardIn(BaseModel):
    cluster_id: uuid.UUID
    rating: Literal["up", "down", "skip"]
    note: str | None = Field(None, max_length=1000)


class FeedbackIn(BaseModel):
    cards: list[CardIn] = Field(min_length=1, max_length=200)
    satisfaction: int = Field(ge=1, le=10)
    note: str | None = Field(None, max_length=2000)


@router.post("/runs/{run_id}/feedback", dependencies=[Depends(require_client_header)])
async def feedback(run_id: str, body: FeedbackIn, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    try:
        out = await c.learner.apply_feedback(_uuid(run_id, "run"),
                                             [CardRating(x.cluster_id, x.rating, x.note) for x in body.cards],
                                             body.satisfaction, body.note)
    except FeedbackError as e:
        raise HTTPException(409, str(e)) from e
    return {"taste_version": out.taste_version, "retrospective": out.retrospective,
            "primary_niche": out.primary_niche, "weight_suggestion": out.weight_suggestion,
            "learner_error": out.learner_error}


async def _character_for_cluster(c: AppContext, cluster_id: uuid.UUID):
    async with c.sessionmaker() as s:
        slug = (await s.execute(select(Character.slug).join(Run, Run.character_id == Character.id)
                                .join(TrendCluster, TrendCluster.run_id == Run.id)
                                .where(TrendCluster.id == cluster_id))).scalar_one_or_none()
    if slug is None:
        raise HTTPException(404, "unknown trend card")
    return await load_character(c.sessionmaker, slug)


def _brief_out(b: Brief) -> dict[str, Any]:
    return {"cluster_id": str(b.cluster_id), "body": b.body, "body_md": b.body_md, "provider": b.provider,
            "model": b.model, "created_at": b.created_at}


@router.post("/trends/{cluster_id}/brief", dependencies=[Depends(require_client_header)])
async def make_brief(cluster_id: str, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    cid = _uuid(cluster_id, "trend card")
    character = await _character_for_cluster(c, cid)
    try:
        brief = await c.briefs.generate(cid, character)
    except BriefError as e:
        raise HTTPException(409, str(e)) from e
    except AllProvidersUnavailable as e:
        wait = f" in about {max(1, round((e.earliest_reset - time.time()) / 60))} min" if e.earliest_reset else " later"
        raise HTTPException(503, f"both AI subscriptions are at their usage limit; try again{wait}") from e
    except RoleOutputError as e:
        raise HTTPException(502, "the AI returned an unusable brief twice; try again") from e
    except ProviderError as e:
        raise HTTPException(502, f"the AI provider failed ({e}); try again") from e
    return _brief_out(brief)


@router.get("/trends/{cluster_id}/brief")
async def get_brief(cluster_id: str, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    async with c.sessionmaker() as s:
        b = (await s.execute(select(Brief).where(Brief.cluster_id == _uuid(cluster_id, "trend card")))
             ).scalar_one_or_none()
    if b is None:
        raise HTTPException(404, "no brief yet")
    return _brief_out(b)


@router.get("/briefs")
async def list_briefs(c: AppContext = Depends(ctx)) -> list[dict[str, Any]]:
    async with c.sessionmaker() as s:
        rows = (await s.execute(select(Brief, TrendCluster.run_id).join(TrendCluster, TrendCluster.id == Brief.cluster_id)
                                .order_by(Brief.created_at.desc()).limit(100))).all()
    return [{**_brief_out(b), "run_id": str(run_id)} for b, run_id in rows]
