"""Videos, their pipeline analyses, the tool response cache and per-platform health (06-data-model.md)."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from tf_db.base import Base
from tf_db.ids import uuid7


class Video(Base):
    __tablename__ = "videos"

    canonical_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(Text)
    creator_handle: Mapped[str | None] = mapped_column(String(128))
    creator_followers: Mapped[int | None] = mapped_column(BigInteger)
    caption: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default=text("'{}'::text[]"))
    sound_id: Mapped[str | None] = mapped_column(String(128), index=True)
    sound_title: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[float | None] = mapped_column(Float)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    metrics_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    media_access: Mapped[str] = mapped_column(String(16), default="unknown", server_default="unknown")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VideoAnalysis(Base):
    __tablename__ = "video_analyses"
    __table_args__ = (UniqueConstraint("canonical_id", "pipeline_version"),)

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    canonical_id: Mapped[str] = mapped_column(String(64), ForeignKey("videos.canonical_id", ondelete="CASCADE"),
                                              index=True)
    pipeline_version: Mapped[str] = mapped_column(String(16))
    probe: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cuts: Mapped[list[float] | None] = mapped_column(JSONB)
    cut_rate: Mapped[float | None] = mapped_column(Float)
    transcript: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    pose: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    camera_motion: Mapped[float | None] = mapped_column(Float)
    best_clean_segment: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    feasibility: Mapped[float | None] = mapped_column(Float)
    filtered_reason: Mapped[str | None] = mapped_column(String(64))
    fingerprint: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    contact_sheet_path: Mapped[str | None] = mapped_column(Text)
    thumbnail_path: Mapped[str | None] = mapped_column(Text)
    media_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolCacheRow(Base):
    __tablename__ = "tool_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    tool: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PlatformStateRow(Base):
    __tablename__ = "platform_state"

    platform: Mapped[str] = mapped_column(String(16), primary_key=True)
    health: Mapped[str] = mapped_column(String(16), default="ok", server_default="ok")
    breaker_state: Mapped[str] = mapped_column(String(16), default="closed", server_default="closed")
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    quota_used_today: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
