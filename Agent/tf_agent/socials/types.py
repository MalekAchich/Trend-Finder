"""One shape for a channel's numbers, whatever they were read from."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

ReadCode = Literal["expired", "revoked", "rate_limited", "unavailable", "not_professional"]


@dataclass
class PostRead:
    platform_post_id: str
    url: str
    caption: str | None = None
    posted_at: datetime | None = None
    thumbnail_url: str | None = None
    duration_s: float | None = None
    views: int | None = None  # None: the source doesn't give it (never 0 for "unknown")
    reach: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    avg_watch_s: float | None = None
    total_watch_s: float | None = None
    audience: dict[str, Any] | None = None  # where this post's views come from (YouTube): {"country": [[code, n]]}


@dataclass
class ChannelRead:
    handle: str
    followers: int | None = None
    total_likes: int | None = None
    posts_count: int | None = None
    posts: list[PostRead] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    source: str = "api"  # api | public
    audience: dict[str, Any] | None = None  # {"country": [["US", 40], ...], "age": [...], "gender": [...]} or {"note"}
    account_insights: dict[str, Any] | None = None  # {"days": 28, "reach": n, "views": n, ...}


class ReadError(Exception):
    def __init__(self, code: ReadCode, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message
