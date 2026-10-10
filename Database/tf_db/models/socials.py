"""Our own posting channels (one per platform per character), their posts and the numbers over time (Plan 9).
Tokens never live here: they stay in secrets/ (tf_agent.credentials)."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tf_db.base import Base
from tf_db.models.runs import UUID_T, _created, _id


class SocialChannel(Base):
    __tablename__ = "social_channels"
    __table_args__ = (UniqueConstraint("platform", "handle"),)
    id: Mapped[uuid.UUID] = _id()
    character_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("characters.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(16))
    handle: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(8), default="public", server_default="public")  # public | api
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # as the platform last gave them (Instagram's official API only): {country|age|gender: [[label, count]], ...}
    audience: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    account_insights: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # last 28 days: reach, views, …
    created_at: Mapped[datetime] = _created()


class SocialPost(Base):
    __tablename__ = "social_posts"
    __table_args__ = (UniqueConstraint("channel_id", "platform_post_id"),)
    id: Mapped[uuid.UUID] = _id()
    channel_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("social_channels.id", ondelete="CASCADE"))
    platform_post_id: Mapped[str] = mapped_column(String(64))
    canonical_id: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    duration_s: Mapped[float | None] = mapped_column(Float)
    inspired_by: Mapped[str | None] = mapped_column(String(64))  # the found / manual video it recreates (owner's link)
    study: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # what it is (format, tags): never a verdict
    audience: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # where its views come from, when the platform says
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # gone from the platform (deleted)
    created_at: Mapped[datetime] = _created()


class SocialSnapshot(Base):
    __tablename__ = "social_snapshots"
    __table_args__ = (Index("ix_social_snapshots_post_taken", "post_id", "taken_at"),)
    id: Mapped[uuid.UUID] = _id()
    post_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("social_posts.id", ondelete="CASCADE"))
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    views: Mapped[int | None] = mapped_column(BigInteger)
    reach: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
    saves: Mapped[int | None] = mapped_column(BigInteger)
    avg_watch_s: Mapped[float | None] = mapped_column(Float)
    total_watch_s: Mapped[float | None] = mapped_column(Float)


class SocialChannelSnapshot(Base):
    __tablename__ = "social_channel_snapshots"
    __table_args__ = (Index("ix_social_channel_snapshots_channel_taken", "channel_id", "taken_at"),)
    id: Mapped[uuid.UUID] = _id()
    channel_id: Mapped[uuid.UUID] = mapped_column(UUID_T, ForeignKey("social_channels.id", ondelete="CASCADE"))
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    followers: Mapped[int | None] = mapped_column(BigInteger)
    total_likes: Mapped[int | None] = mapped_column(BigInteger)
    posts: Mapped[int | None] = mapped_column(BigInteger)
