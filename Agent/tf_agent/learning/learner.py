"""Owner feedback: one video at a time (👍/👎 + note), a score per run, and the taste profile it all feeds."""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.models.errors import AllProvidersUnavailable, InvalidRequest
from tf_agent.roles.runners import RoleOutputError, Roles
from tf_agent.socials.report import channel_report
from tf_db.models import (
    SocialChannel,
    SocialChannelSnapshot,
    CardFeedback,
    Direction,
    Finding,
    FindingScore,
    Run,
    RunFeedback,
    SeenItem,
    TasteProfile,
    TrendCluster,
    TrendMember,
)

log = logging.getLogger(__name__)
ENDED = ("review_ready", "stopped", "failed")
EVIDENCE_CARDS = 60
NOTES_KEPT = 40


class FeedbackError(Exception):
    pass


class LearnerResult(BaseModel):
    taste_profile_md: str = Field(min_length=10, max_length=6000)
    retrospective: list[str] = Field(min_length=1, max_length=6)
    primary_niche: str | None = Field(None, max_length=80)


@dataclass
class TasteUpdate:
    version: int | None
    retrospective: list[str] = field(default_factory=list)
    primary_niche: str | None = None
    error: str | None = None


class Learner:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], roles: Roles) -> None:
        self._sm = sessionmaker
        self.roles = roles

    # ---------- ratings ----------
    async def rate(self, cluster_id: uuid.UUID, rating: Literal["up", "down"] | None, note: str | None) -> None:
        """Save (or clear, with None) the owner's verdict on one found video. Idempotent; any run state."""
        note = (note or "").strip() or None
        async with self._sm() as s:
            cluster = await s.get(TrendCluster, cluster_id)
            if cluster is None:
                raise FeedbackError("unknown video")
            # one rating at a time per run: the contribution delta below must see the previous one
            run = (await s.execute(select(Run).where(Run.id == cluster.run_id).with_for_update())).scalar_one()
            if rating is None:
                await s.execute(delete(CardFeedback).where(CardFeedback.cluster_id == cluster_id))
            else:
                stmt = pg_insert(CardFeedback).values(cluster_id=cluster_id, rating=rating, note=note)
                await s.execute(stmt.on_conflict_do_update(index_elements=[CardFeedback.cluster_id], set_={
                    "rating": rating, "note": note, "updated_at": func.now()}))
            await s.flush()
            contributions = await self._contributions(s, run.id)
            old = run.feedback_contributions or {}
            for did in set(contributions) | set(old):
                up_new, down_new = contributions.get(did, [0, 0])
                up_old, down_old = old.get(did, [0, 0])
                if (up_new, down_new) != (up_old, down_old):
                    await s.execute(update(Direction).where(Direction.id == uuid.UUID(did)).values(
                        alpha=Direction.alpha + (up_new - up_old), beta=Direction.beta + (down_new - down_old)))
            await s.execute(update(Run).where(Run.id == run.id).values(feedback_contributions=contributions))
            if rating is not None:
                members = (await s.execute(select(Finding.canonical_id).join(
                    TrendMember, TrendMember.finding_id == Finding.id).where(TrendMember.cluster_id == cluster_id))
                           ).scalars().all()
                for cid in set(members):
                    stmt = pg_insert(SeenItem).values(character_id=run.character_id, canonical_id=cid,
                                                      first_run_id=run.id, rated=True)
                    await s.execute(stmt.on_conflict_do_update(
                        index_elements=[SeenItem.character_id, SeenItem.canonical_id], set_={"rated": True}))
            await s.commit()

    async def rate_run(self, run_id: uuid.UUID, satisfaction: int, note: str | None) -> None:
        if not 1 <= satisfaction <= 10:
            raise FeedbackError("satisfaction must be between 1 and 10")
        note = (note or "").strip() or None
        async with self._sm() as s:
            run = await s.get(Run, run_id)
            if run is None:
                raise FeedbackError("unknown run")
            if run.state not in ENDED:
                raise FeedbackError(f"this run is {run.state}; score it once it has finished")
            stmt = pg_insert(RunFeedback).values(run_id=run_id, satisfaction=satisfaction, note=note)
            await s.execute(stmt.on_conflict_do_update(index_elements=[RunFeedback.run_id], set_={
                "satisfaction": satisfaction, "note": note, "updated_at": func.now()}))
            await s.commit()

    async def _contributions(self, s: AsyncSession, run_id: uuid.UUID) -> dict[str, list[int]]:
        """Final 👍/👎 per direction for this run (each rated card counts once per direction it contains)."""
        rows = (await s.execute(select(CardFeedback.rating, Finding.direction_id, TrendMember.cluster_id)
                                .join(TrendMember, TrendMember.cluster_id == CardFeedback.cluster_id)
                                .join(Finding, Finding.id == TrendMember.finding_id)
                                .where(Finding.run_id == run_id, Finding.direction_id.is_not(None)))).all()
        seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
        out: dict[str, list[int]] = {}
        for rating, did, cluster in rows:
            if (did, cluster) in seen:
                continue
            seen.add((did, cluster))
            pair = out.setdefault(str(did), [0, 0])
            pair[0 if rating == "up" else 1] += 1
        return out

    # ---------- taste profile ----------
    async def refresh_taste(self, character_id: uuid.UUID, run_id: uuid.UUID | None = None,
                            brief: str | None = None) -> TasteUpdate | None:
        """Rewrite the taste profile when ratings, notes or our own channels' numbers changed since the last version;
        None when unchanged."""
        async with self._sm() as s:
            prev = (await s.execute(select(TasteProfile).where(TasteProfile.character_id == character_id)
                                    .order_by(TasteProfile.version.desc()).limit(1))).scalar_one_or_none()
            card_change = (await s.execute(select(func.max(CardFeedback.updated_at))
                                           .join(TrendCluster, TrendCluster.id == CardFeedback.cluster_id)
                                           .join(Run, Run.id == TrendCluster.run_id)
                                           .where(Run.character_id == character_id))).scalar_one()
            run_change = (await s.execute(select(func.max(RunFeedback.updated_at)).join(Run, Run.id == RunFeedback.run_id)
                                          .where(Run.character_id == character_id))).scalar_one()
            channel_change = (await s.execute(select(func.max(SocialChannelSnapshot.taken_at)).join(
                SocialChannel, SocialChannel.id == SocialChannelSnapshot.channel_id).where(
                SocialChannel.character_id == character_id))).scalar_one()
            changes = [t for t in (card_change, run_change, channel_change) if t is not None]
            if not changes or (prev is not None and prev.created_at >= max(changes)):
                return None
            cards = (await s.execute(select(CardFeedback, TrendCluster, FindingScore, Direction.key)
                                     .join(TrendCluster, TrendCluster.id == CardFeedback.cluster_id)
                                     .join(Run, Run.id == TrendCluster.run_id)
                                     .join(FindingScore, FindingScore.finding_id == TrendCluster.best_finding_id)
                                     .join(Finding, Finding.id == TrendCluster.best_finding_id)
                                     .outerjoin(Direction, Direction.id == Finding.direction_id)
                                     .where(Run.character_id == character_id)
                                     .order_by(CardFeedback.updated_at.desc()).limit(EVIDENCE_CARDS))).all()
            runs = (await s.execute(select(RunFeedback.satisfaction, RunFeedback.note).join(Run, Run.id == RunFeedback.run_id)
                                    .where(Run.character_id == character_id)
                                    .order_by(RunFeedback.updated_at.desc()).limit(5))).all()
            notes = (await s.execute(select(CardFeedback.note, CardFeedback.updated_at)
                                     .join(TrendCluster, TrendCluster.id == CardFeedback.cluster_id)
                                     .join(Run, Run.id == TrendCluster.run_id)
                                     .where(Run.character_id == character_id, CardFeedback.note.is_not(None))
                                     .order_by(CardFeedback.updated_at.desc()).limit(NOTES_KEPT))).all()
            run_notes = (await s.execute(select(RunFeedback.note, RunFeedback.updated_at).join(Run, Run.id == RunFeedback.run_id)
                                         .where(Run.character_id == character_id, RunFeedback.note.is_not(None))
                                         .order_by(RunFeedback.updated_at.desc()).limit(20))).all()
        evidence = [{"direction": key, "label": c.label, "niche_guess": sc.niche_guess, "rating": fb.rating,
                     "note": fb.note, "fit": sc.fit, "feasibility": sc.feasibility, "overall": sc.overall}
                    for fb, c, sc, key in cards]
        text = (f"Previous taste profile:\n{(prev.body_md.split('## Owner notes')[0] if prev else 'none yet')}\n\n"
                f"Rated videos (newest first):\n{json.dumps(evidence, default=str)}\n\n"
                f"Recent run scores: {json.dumps([{'satisfaction': a, 'note': b} for a, b in runs])}")
        report = await channel_report(self._sm, character_id, datetime.now(UTC))
        if report:  # what our own audience rewarded: evidence next to the owner's thumbs
            text += f"\n\n{report}"
        try:
            judged = await self.roles._structured("learner", self.roles.prompts.render(
                "learner", brief=brief or "(character known only from images)"), text, (), LearnerResult,
                "taste_profile", run_id=run_id)
        except (RoleOutputError, AllProvidersUnavailable, InvalidRequest) as e:
            log.warning("taste refresh failed for character %s: %s", character_id, e)
            return TasteUpdate(None, error=str(e)[:300])
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
        return TasteUpdate(version, list(r.retrospective), r.primary_niche)
