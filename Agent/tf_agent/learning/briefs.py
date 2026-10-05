"""Production briefs for 👍 trend cards (D-21): everything needed to recreate the trend with Kling."""
from __future__ import annotations

import uuid
from typing import Literal, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.models.types import ImagePart
from tf_agent.orchestrator.analysis import candidate_facts
from tf_agent.pipeline import PIPELINE_VERSION
from tf_agent.pipeline.result import VideoAnalysisResult
from tf_agent.roles.runners import Roles
from tf_agent.tools.store import VideoStore
from tf_db.models import Brief, CardFeedback, Finding, FindingScore, TrendCluster, VideoAnalysis


class BriefError(Exception):
    pass


class Window(BaseModel):
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)


class Shot(BaseModel):
    seconds: float = Field(gt=0, le=15)
    description: str = Field(min_length=3, max_length=300)


class BriefBody(BaseModel):
    title: str = Field(min_length=2, max_length=100)
    concept: str = Field(min_length=5, max_length=500)
    record_yourself: str = Field(min_length=5, max_length=600)
    character_orientation: Literal["video", "image"]
    kling_prompt: str = Field(min_length=10, max_length=600)
    framing: str = Field(min_length=3, max_length=200)
    motion_window: Window
    shots: list[Shot] = Field(min_length=1, max_length=4)
    risks: list[str] = Field(default_factory=list, max_length=6)


def to_markdown(b: BriefBody, source_url: str) -> str:
    lines = [f"# {b.title}", "", b.concept, "", f"**Source:** {source_url}",
             f"**Motion window:** {b.motion_window.start_s:.1f}s → {b.motion_window.end_s:.1f}s",
             f"**Character orientation:** {b.character_orientation}", f"**Framing:** {b.framing}", "",
             "## Kling prompt", "", f"> {b.kling_prompt}", "", "## Record it yourself", "", b.record_yourself, "",
             "## Shots"]
    lines += [f"{i}. ({s.seconds:g}s) {s.description}" for i, s in enumerate(b.shots, start=1)]
    if b.risks:
        lines += ["", "## Risks"] + [f"- {r}" for r in b.risks]
    return "\n".join(lines) + "\n"


class CharacterLike(Protocol):
    brief: str
    canonical_image_path: str
    version_id: uuid.UUID | None


class BriefWriter:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], roles: Roles) -> None:
        self._sm = sessionmaker
        self.roles = roles
        self.store = VideoStore(sessionmaker)

    async def generate(self, cluster_id: uuid.UUID, character: CharacterLike) -> Brief:
        async with self._sm() as s:
            row = (await s.execute(
                select(TrendCluster, Finding, FindingScore, VideoAnalysis, CardFeedback.rating)
                .join(Finding, Finding.id == TrendCluster.best_finding_id)
                .join(FindingScore, FindingScore.finding_id == Finding.id)
                .outerjoin(VideoAnalysis, (VideoAnalysis.canonical_id == Finding.canonical_id)
                           & (VideoAnalysis.pipeline_version == PIPELINE_VERSION))
                .outerjoin(CardFeedback, CardFeedback.cluster_id == TrendCluster.id)
                .where(TrendCluster.id == cluster_id))).one_or_none()
        if row is None:
            raise BriefError("unknown trend card")
        cluster, finding, score, analysis, rating = row
        if rating != "up":
            raise BriefError("briefs are only written for 👍 cards: rate this card 👍 first")
        if analysis is None or not analysis.contact_sheet_path:
            raise BriefError("this card has no analysed video to brief from")
        item = await self.store.get_video(finding.canonical_id)
        facts = candidate_facts(item, VideoAnalysisResult(
            canonical_id=analysis.canonical_id, probe=analysis.probe, cuts=list(analysis.cuts or []),
            transcript=analysis.transcript, pose=analysis.pose, camera_motion=analysis.camera_motion,
            best_clean_segment=analysis.best_clean_segment, feasibility=analysis.feasibility))
        text = (f"Source facts: {facts}\nAnalyst's adaptation idea: {score.adaptation_idea}\n"
                f"Best clean segment for the motion reference: {analysis.best_clean_segment}")
        judged = await self.roles._structured(
            "brief", self.roles.prompts.render("brief", brief=character.brief), text,
            [ImagePart(character.canonical_image_path), ImagePart(analysis.contact_sheet_path)], BriefBody, "brief")
        body_md = to_markdown(judged.result, item.url if item else finding.canonical_id)
        values = {"body": judged.result.model_dump(), "body_md": body_md, "provider": judged.provider,
                  "model": judged.model, "character_version_id": character.version_id}
        async with self._sm() as s:
            stmt = pg_insert(Brief).values(cluster_id=cluster_id, **values)
            await s.execute(stmt.on_conflict_do_update(index_elements=[Brief.cluster_id], set_=values))
            await s.commit()
            return (await s.execute(select(Brief).where(Brief.cluster_id == cluster_id))).scalar_one()
