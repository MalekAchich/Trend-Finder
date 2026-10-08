"""Public numbers for a channel with no API connected: its own public profile page, read through the scraping
account's browser (the posting account is never logged in or automated)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from tf_agent.socials.types import ChannelRead, PostRead, ReadError
from tf_agent.tools import session_search as ss
from tf_agent.tools.types import ToolFailure, VideoItem


def _post(i: VideoItem) -> PostRead:
    return PostRead(platform_post_id=i.canonical_id.split(":", 1)[1], url=i.url, caption=i.caption,
                    posted_at=i.posted_at, thumbnail_url=i.thumbnail_url, duration_s=i.duration_s,
                    views=i.metrics.views, likes=i.metrics.likes, comments=i.metrics.comments,
                    shares=i.metrics.shares, saves=i.metrics.saves)


def parse(platform: str, handle: str, captured: list[Any], since: datetime) -> ChannelRead:
    if platform == "instagram":
        items, stats = ss.instagram_items(captured), ss.instagram_profile_stats(captured, handle)
    else:
        items, stats = ss.tiktok_items(captured), ss.tiktok_profile_stats(captured, handle)
    mine = [i for i in items if i.creator.handle in (ss.norm_handle(handle), None)
            and (i.posted_at is None or i.posted_at >= since)]
    return ChannelRead(handle=handle, followers=stats["followers"], total_likes=stats["total_likes"],
                       posts_count=stats["posts"], posts=[_post(i) for i in mine], source="public")


class PublicReader:
    PATTERNS = {"instagram": ss.INSTAGRAM_PATTERN, "tiktok": ss.TIKTOK_CREATOR_PATTERN}
    URLS = {"instagram": ss.instagram_creator_url, "tiktok": ss.tiktok_creator_url}

    def __init__(self, browser: Any) -> None:
        self.browser = browser

    async def read(self, platform: str, handle: str, since: datetime) -> ChannelRead:
        if platform not in self.PATTERNS:
            raise ReadError("unavailable", f"no public reader for {platform}")
        if self.browser is None:
            raise ReadError("unavailable", "the browser isn't available in this process")
        try:
            captured = await self.browser.capture_json(platform, self.URLS[platform](handle), self.PATTERNS[platform],
                                                       scrolls=2, blocked=ss.is_captcha)
        except ToolFailure as e:
            raise ReadError("unavailable", f"{platform}: the public page didn't load ({e.error.message})") from None
        return parse(platform, handle, captured, since)
