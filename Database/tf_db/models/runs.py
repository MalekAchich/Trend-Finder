"""Characters, runs, tasks, blackboard, findings and trend clusters (06-data-model.md)."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from tf_db.base import Base
from tf_db.ids import uuid7

UUID_T = PgUUID(as_uuid=True)
EMPTY_JSON = text("'{}'::jsonb")


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(UUID_T, primary_key=True, default=uuid7)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Character(Base):
    __tablename__ = "characters"
    id: Mapped[uuid.UUID] = _id()
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    folder_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    created_at: Mapped[datetime] = _created()


class CharacterVersion(Base):
    __tablename__ = "character_versions"
    __table_args__ = (UniqueConstraint("character_id", "version"),)
    id: Mapped[uuid.UUID] = _id()
    character_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("characters.id", ondelete="CASCADE"),
                                                    index=True)
    version: Mapped[int] = mapped_column(Integer)
    images: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    canonical_image_path: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


class Target(Base):
    """A video the owner found for a character: analysed in that character's next run."""
    __tablename__ = "targets"
    __table_args__ = (UniqueConstraint("character_id", "canonical_id"),)
    id: Mapped[uuid.UUID] = _id()
    character_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("characters.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(Text)
    canonical_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID_T, ForeignKey("runs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()


class TasteProfile(Base):
    __tablename__ = "taste_profiles"
    __table_args__ = (UniqueConstraint("character_id", "version"),)
    id: Mapped[uuid.UUID] = _id()
    character_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("characters.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    body_md: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(16))
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID_T)
    created_at: Mapped[datetime] = _created()


class Direction(Base):
    __tablename__ = "directions"
    __table_args__ = (UniqueConstraint("character_id", "key"),)
    id: Mapped[uuid.UUID] = _id()
    character_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("characters.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(128))
    label: Mapped[str] = mapped_column(Text)
    hypothesis: Mapped[str | None] = mapped_column(Text)
    niche: Mapped[str | None] = mapped_column(String(128))
    alpha: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    beta: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    runs_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = _created()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[uuid.UUID] = _id()
    character_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("characters.id", ondelete="CASCADE"))
    character_version_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("character_versions.id"))
    state: Mapped[str] = mapped_column(String(24), index=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=EMPTY_JSON)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=EMPTY_JSON)
    character_read: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    feedback_contributions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=EMPTY_JSON)
    explore_ratio_used: Mapped[float | None] = mapped_column(Float)
    current_round: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    stop_reason: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (UniqueConstraint("run_id", "number"),)
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("runs.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    plan_rejections: Mapped[list[Any] | None] = mapped_column(JSONB)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_queue", "run_id", "state", "not_before"),)
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("runs.id", ondelete="CASCADE"))
    round_id: Mapped[uuid.UUID | None] = mapped_column(UUID_T, ForeignKey("rounds.id", ondelete="SET NULL"))
    task_type: Mapped[str] = mapped_column(String(24))
    direction_id: Mapped[uuid.UUID | None] = mapped_column(UUID_T, ForeignKey("directions.id", ondelete="SET NULL"))
    platform: Mapped[str | None] = mapped_column(String(16))
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=EMPTY_JSON)
    goal: Mapped[str | None] = mapped_column(Text)
    budget: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=EMPTY_JSON)
    state: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(128))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    transcript_path: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScopeClaim(Base):
    __tablename__ = "scope_claims"
    run_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[str] = mapped_column(String(256), primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("tasks.id", ondelete="CASCADE"))


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("run_id", "platform", "type", "value"),)
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("runs.id", ondelete="CASCADE"))
    source_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID_T, ForeignKey("tasks.id", ondelete="SET NULL"))
    platform: Mapped[str] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(16))
    value: Mapped[str] = mapped_column(String(256))
    why: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="new", server_default="new")
    assigned_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID_T)
    created_at: Mapped[datetime] = _created()


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=EMPTY_JSON)
    created_at: Mapped[datetime] = _created()


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (UniqueConstraint("run_id", "canonical_id"), Index("ix_findings_run_status", "run_id", "status"))
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("runs.id", ondelete="CASCADE"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID_T, ForeignKey("tasks.id", ondelete="SET NULL"))
    canonical_id: Mapped[str] = mapped_column(String(64), ForeignKey("videos.canonical_id"))
    direction_id: Mapped[uuid.UUID | None] = mapped_column(UUID_T, ForeignKey("directions.id", ondelete="SET NULL"))
    why: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=EMPTY_JSON)
    preliminary_fit: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(8), default="agent", server_default="agent")
    status: Mapped[str] = mapped_column(String(24), default="pending_analysis", server_default="pending_analysis")
    created_at: Mapped[datetime] = _created()


class FindingScore(Base):
    __tablename__ = "finding_scores"
    finding_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("findings.id", ondelete="CASCADE"),
                                                  primary_key=True)
    fit: Mapped[float | None] = mapped_column(Float)
    fit_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    fit_justification: Mapped[str | None] = mapped_column(Text)
    adaptation_idea: Mapped[str | None] = mapped_column(Text)
    feasibility_notes: Mapped[str | None] = mapped_column(Text)
    niche_guess: Mapped[str | None] = mapped_column(String(128))
    feasibility: Mapped[float | None] = mapped_column(Float)
    momentum: Mapped[float | None] = mapped_column(Float)
    freshness: Mapped[float | None] = mapped_column(Float)
    overall: Mapped[float | None] = mapped_column(Float)
    analyst_provider: Mapped[str | None] = mapped_column(String(32))
    analyst_model: Mapped[str | None] = mapped_column(String(128))
    cross_fit: Mapped[float | None] = mapped_column(Float)
    cross_provider: Mapped[str | None] = mapped_column(String(32))
    cross_justification: Mapped[str | None] = mapped_column(Text)
    disagreement: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = _created()


class TrendCluster(Base):
    __tablename__ = "trend_clusters"
    id: Mapped[uuid.UUID] = _id()
    run_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    label: Mapped[str | None] = mapped_column(Text)
    best_finding_id: Mapped[uuid.UUID | None] = mapped_column(UUID_T,
                                                              ForeignKey("findings.id", ondelete="SET NULL"))
    member_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    sound_id: Mapped[str | None] = mapped_column(String(128))
    overall: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class TrendMember(Base):
    __tablename__ = "trend_members"
    cluster_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("trend_clusters.id", ondelete="CASCADE"),
                                                  primary_key=True)
    finding_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("findings.id", ondelete="CASCADE"),
                                                  primary_key=True)


class SeenItem(Base):
    __tablename__ = "seen_items"
    character_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("characters.id", ondelete="CASCADE"),
                                                    primary_key=True)
    canonical_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID_T)
    rated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = _created()


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
