"""Apply owner feedback: card ratings → direction Beta stats (idempotent), seen items, taste profile, weights."""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.learning.weights import FEATURES, suggest_weights
from tf_agent.models.errors import AllProvidersUnavailable, InvalidRequest
from tf_agent.roles.runners import RoleOutputError, Roles
from tf_db.models import (
    CardFeedback,
    Character,
    Direction,
    Finding,
    FindingScore,
    Run,
    RunFeedback,
    SeenItem,
    Setting,
    TasteProfile,
    TrendCluster,
    TrendMember,
)

log = logging.getLogger(__name__)


class FeedbackError(Exception):
    pass


class LearnerResult(BaseModel):
    taste_profile_md: str = Field(min_length=10, max_length=6000)
    retrospective: list[str] = Field(min_length=1, max_length=6)
    primary_niche: str | None = Field(None, max_length=80)


@dataclass(frozen=True)
class CardRating:
    cluster_id: uuid.UUID
    rating: Literal["up", "down", "skip"]
    note: str | None = None


@dataclass
class FeedbackOutcome:
    taste_version: int | None = None
    retrospective: list[str] = field(default_factory=list)
    primary_niche: str | None = None
    weight_suggestion: dict[str, float] | None = None
    learner_error: str | None = None


class Learner:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], roles: Roles) -> None:
        self._sm = sessionmaker
        self.roles = roles

    async def apply_feedback(self, run_id: uuid.UUID, cards: list[CardRating], satisfaction: int,
                             note: str | None) -> FeedbackOutcome:
        async with self._sm() as s:
            run = await s.get(Run, run_id)
            if run is None:
                raise FeedbackError("unknown run")
            if run.state != "review_ready":
                raise FeedbackError(f"this run is {run.state}; ratings are accepted once it is ready for review")
            valid = set((await s.execute(select(TrendCluster.id).where(TrendCluster.run_id == run_id))).scalars())
            for c in cards:
                if c.cluster_id not in valid:
                    raise FeedbackError(f"card {c.cluster_id} does not belong to this run")
                stmt = pg_insert(CardFeedback).values(cluster_id=c.cluster_id, rating=c.rating, note=c.note)
                await s.execute(stmt.on_conflict_do_update(index_elements=[CardFeedback.cluster_id],
                                                           set_={"rating": c.rating, "note": c.note}))
            await s.flush()
            contributions = await self._contributions(s, run_id)
            previous = (await s.get(RunFeedback, run_id))
            old = previous.contributions if previous else {}
            for did in set(contributions) | set(old):
                up_new, down_new = contributions.get(did, [0, 0])
                up_old, down_old = old.get(did, [0, 0])
                await s.execute(update(Direction).where(Direction.id == uuid.UUID(did)).values(
                    alpha=Direction.alpha + (up_new - up_old), beta=Direction.beta + (down_new - down_old)))
            stmt = pg_insert(RunFeedback).values(run_id=run_id, satisfaction=satisfaction, note=note,
                                                 contributions=contributions)
            await s.execute(stmt.on_conflict_do_update(index_elements=[RunFeedback.run_id], set_={
                "satisfaction": satisfaction, "note": note, "contributions": contributions,
                "updated_at": func.now()}))
            members = (await s.execute(select(Finding.canonical_id).join(TrendMember, TrendMember.finding_id == Finding.id)
                                       .join(CardFeedback, CardFeedback.cluster_id == TrendMember.cluster_id)
                                       .where(Finding.run_id == run_id))).scalars().all()
            for cid in set(members):
                stmt = pg_insert(SeenItem).values(character_id=run.character_id, canonical_id=cid, first_run_id=run_id,
                                                  rated=True)
                await s.execute(stmt.on_conflict_do_update(index_elements=[SeenItem.character_id, SeenItem.canonical_id],
                                                           set_={"rated": True}))
            await s.commit()
            character_id = run.character_id
        outcome = FeedbackOutcome(weight_suggestion=await self._weights(character_id))
        try:
            await self._learn(run_id, character_id, satisfaction, note, outcome)
        except (RoleOutputError, AllProvidersUnavailable, InvalidRequest) as e:
            log.warning("learner failed for run %s: %s", run_id, e)
            outcome.learner_error = str(e)[:300]
        return outcome

    async def _contributions(self, s: AsyncSession, run_id: uuid.UUID) -> dict[str, list[int]]:
        """Final 👍/👎 per direction for this run (each rated card counts once per direction it contains)."""
        rows = (await s.execute(select(CardFeedback.rating, Finding.direction_id, TrendMember.cluster_id)
                                .join(TrendMember, TrendMember.cluster_id == CardFeedback.cluster_id)
                                .join(Finding, Finding.id == TrendMember.finding_id)
                                .where(Finding.run_id == run_id, Finding.direction_id.is_not(None)))).all()
        seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
        out: dict[str, list[int]] = {}
        for rating, did, cluster in rows:
            if rating == "skip" or (did, cluster) in seen:
                continue
            seen.add((did, cluster))
            pair = out.setdefault(str(did), [0, 0])
            pair[0 if rating == "up" else 1] += 1
        return out

    async def _weights(self, character_id: uuid.UUID) -> dict[str, float] | None:
        async with self._sm() as s:
            rows = (await s.execute(select(CardFeedback.rating, FindingScore)
                                    .join(TrendCluster, TrendCluster.id == CardFeedback.cluster_id)
                                    .join(FindingScore, FindingScore.finding_id == TrendCluster.best_finding_id)
                                    .join(Run, Run.id == TrendCluster.run_id)
                                    .where(Run.character_id == character_id, CardFeedback.rating != "skip"))).all()
        X = [[getattr(sc, f) or 0.0 for f in FEATURES] for _, sc in rows]
        y = [1 if rating == "up" else 0 for rating, _ in rows]
        suggestion = suggest_weights(np.array(X), np.array(y)) if X else None
        if suggestion is not None:
            async with self._sm() as s:
                stmt = pg_insert(Setting).values(key="weight_suggestion", value=suggestion)
                await s.execute(stmt.on_conflict_do_update(index_elements=[Setting.key], set_={"value": suggestion}))
                await s.commit()
        return suggestion

    async def _learn(self, run_id: uuid.UUID, character_id: uuid.UUID, satisfaction: int, note: str | None,
                     outcome: FeedbackOutcome) -> None:
        from tf_agent.characters.profile import CharacterError
        from tf_agent.characters.sync import load_character

        async with self._sm() as s:
            slug = (await s.execute(select(Character.slug).where(Character.id == character_id))).scalar_one()
            prev = (await s.execute(select(TasteProfile).where(TasteProfile.character_id == character_id)
                                    .order_by(TasteProfile.version.desc()).limit(1))).scalar_one_or_none()
            cards = (await s.execute(select(CardFeedback, TrendCluster, FindingScore, Direction.key)
                                     .join(TrendCluster, TrendCluster.id == CardFeedback.cluster_id)
                                     .join(FindingScore, FindingScore.finding_id == TrendCluster.best_finding_id)
                                     .join(Finding, Finding.id == TrendCluster.best_finding_id)
                                     .outerjoin(Direction, Direction.id == Finding.direction_id)
                                     .where(TrendCluster.run_id == run_id))).all()
            notes = (await s.execute(select(CardFeedback.note, CardFeedback.updated_at)
                                     .join(TrendCluster, TrendCluster.id == CardFeedback.cluster_id)
                                     .join(Run, Run.id == TrendCluster.run_id)
                                     .where(Run.character_id == character_id, CardFeedback.note.is_not(None))
                                     .order_by(CardFeedback.updated_at.desc()).limit(40))).all()
            run_notes = (await s.execute(select(RunFeedback.note, RunFeedback.updated_at).join(Run, Run.id == RunFeedback.run_id)
                                         .where(Run.character_id == character_id, RunFeedback.note.is_not(None))
                                         .order_by(RunFeedback.updated_at.desc()).limit(20))).all()
        try:
            brief = (await load_character(self._sm, slug)).brief
        except CharacterError:  # an unparsable profile must not block learning from the owner's ratings
            brief = f"# Character: {slug}"
        evidence = [{"direction": key, "label": c.label, "niche_guess": sc.niche_guess, "rating": fb.rating,
                     "note": fb.note, "fit": sc.fit, "feasibility": sc.feasibility, "overall": sc.overall}
                    for fb, c, sc, key in cards]
        text = (f"Previous taste profile:\n{(prev.body_md.split('## Owner notes')[0] if prev else 'none yet')}\n\n"
                f"Rated cards:\n{json.dumps(evidence, default=str)}\n\n"
                f"Overall satisfaction: {satisfaction}/10. Owner note: {note or '-'}")
        judged = await self.roles._structured("learner", self.roles.prompts.render("learner", brief=brief), text, (),
                                              LearnerResult, "taste_profile", run_id=run_id)
        r = judged.result
        owner = sorted([*notes, *run_notes], key=lambda x: x[1], reverse=True)
        body = r.taste_profile_md.strip()
        if owner:
            body += "\n\n## Owner notes (verbatim, newest first)\n" + "\n".join(f"- {n}" for n, _ in owner)
        async with self._sm() as s:
            version = (await s.execute(select(func.coalesce(func.max(TasteProfile.version), 0)).where(
                TasteProfile.character_id == character_id))).scalar_one() + 1
            s.add(TasteProfile(character_id=character_id, version=version, body_md=body, author="learner",
                               source_run_id=run_id))
            await s.commit()
        outcome.taste_version, outcome.retrospective, outcome.primary_niche = version, r.retrospective, r.primary_niche
