"""Normalized tool outputs shared by every platform (03-tools.md)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Platform = Literal["tiktok", "instagram", "youtube", "x"]
ToolErrorCode = Literal["rate_limited", "login_required", "platform_unavailable", "not_found", "invalid_input"]


class Creator(BaseModel):
    handle: str | None = None
    followers: int | None = None


class Sound(BaseModel):
    id: str | None = None
    title: str | None = None
    uses: int | None = None


class Metrics(BaseModel):
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None


CANONICAL_ID_PATTERN = r"^(tiktok|instagram|youtube|x):[A-Za-z0-9_-]{1,64}$"


class VideoItem(BaseModel):
    canonical_id: str = Field(pattern=CANONICAL_ID_PATTERN)
    platform: Platform
    url: str
    creator: Creator = Field(default_factory=Creator)
    caption: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    sound: Sound = Field(default_factory=Sound)
    posted_at: datetime | None = None
    duration_s: float | None = None
    metrics: Metrics = Field(default_factory=Metrics)
    thumbnail_url: str | None = None
    media_access: Literal["ok", "login_required", "unknown"] = "unknown"
    source: Literal["yt-dlp", "searxng", "api"] = "yt-dlp"

    def age_hours(self, now: datetime) -> float | None:
        return None if self.posted_at is None else max((now - self.posted_at).total_seconds() / 3600, 0.0)

    def views_per_hour(self, now: datetime) -> float | None:
        age = self.age_hours(now)
        if age is None or self.metrics.views is None:
            return None
        return self.metrics.views / max(age, 1.0)

    def engagement_rate(self) -> float | None:
        m = self.metrics
        if not m.views:
            return None
        parts = [x for x in (m.likes, m.comments, m.shares, m.saves) if x is not None]
        return sum(parts) / m.views if parts else None


class ToolError(BaseModel):
    code: ToolErrorCode
    message: str
    retry_after_s: float | None = None


class ToolFailure(Exception):
    def __init__(self, code: ToolErrorCode, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.error = ToolError(code=code, message=message, retry_after_s=retry_after_s)
