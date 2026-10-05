"""Owner feedback and production briefs (06-data-model.md: feedback, briefs)."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from tf_db.base import Base
from tf_db.ids import uuid7

UUID_T = PgUUID(as_uuid=True)


class CardFeedback(Base):
    __tablename__ = "card_feedback"
    __table_args__ = (CheckConstraint("rating in ('up', 'down', 'skip')", name="rating"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID_T, primary_key=True, default=uuid7)
    cluster_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("trend_clusters.id", ondelete="CASCADE"),
                                                  unique=True)
    rating: Mapped[str] = mapped_column(String(8))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class RunFeedback(Base):
    __tablename__ = "run_feedback"
    __table_args__ = (CheckConstraint("satisfaction between 1 and 10", name="satisfaction"),)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    satisfaction: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    contributions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class Brief(Base):
    __tablename__ = "briefs"
    id: Mapped[uuid.UUID] = mapped_column(UUID_T, primary_key=True, default=uuid7)
    cluster_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("trend_clusters.id", ondelete="CASCADE"),
                                                  unique=True)
    character_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID_T, ForeignKey("character_versions.id"))
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    body_md: Mapped[str] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
