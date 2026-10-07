"""Shorts discovery through the YouTube Data API v3 (03-tools.md), used when a key is set in Settings.

One search = `search.list` (100 units) + `videos.list` (1 unit) for exact stats and durations. The free quota is
10,000 units a day, reset at midnight Pacific; the quota book refuses a call before it would exceed that.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.tools.normalize import norm_handle, norm_hashtag
from tf_agent.tools.types import Creator, Metrics, ToolFailure, VideoItem

API = "https://www.googleapis.com/youtube/v3"
DAILY_UNITS = 10_000
SEARCH_UNITS = 101  # search.list + videos.list
MAX_SHORT_S = 180
QUOTA_KEY = "youtube_quota"
_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
_HASHTAG = re.compile(r"#(\w+)", re.UNICODE)
PACIFIC = timezone(timedelta(hours=-8))  # quota day boundary (Google resets at midnight Pacific time)


def iso_duration_s(value: str) -> float | None:
    m = _DURATION.match(value or "")
    if not m or value in ("P", "PT"):
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return float(d * 86400 + h * 3600 + mi * 60 + s)


def _quota_day() -> str:
    return datetime.now(PACIFIC).date().isoformat()


class QuotaBook(Protocol):
    async def spend(self, units: int) -> bool: ...


class MemoryQuota:
    def __init__(self, limit: int = DAILY_UNITS, used: int = 0) -> None:
        self.limit, self.used = limit, used

    async def spend(self, units: int) -> bool:
        if self.used + units > self.limit:
            return False
        self.used += units
        return True


class DbQuota:
    """The day's spent units in `settings.youtube_quota`, so restarts don't forget them."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], limit: int = DAILY_UNITS) -> None:
        from tf_db.models import Setting

        self._sm, self.limit, self._setting = sessionmaker, limit, Setting

    async def spend(self, units: int) -> bool:
        Setting = self._setting
        day = _quota_day()
        async with self._sm() as s:
            await s.execute(pg_insert(Setting).values(key=QUOTA_KEY, value={"day": day, "used": 0})
                            .on_conflict_do_nothing(index_elements=[Setting.key]))
            row = (await s.execute(select(Setting).where(Setting.key == QUOTA_KEY).with_for_update())).scalar_one()
            used = int(row.value.get("used") or 0) if row.value.get("day") == day else 0
            if used + units > self.limit:
                await s.rollback()
                return False
            row.value = {"day": day, "used": used + units}
            await s.commit()
            return True


def _error(r: httpx.Response) -> ToolFailure:
    try:
        err = r.json().get("error") or {}
    except ValueError:
        err = {}
    reasons = {str(e.get("reason")) for e in err.get("errors") or []} | {
        str(d.get("reason")) for d in err.get("details") or []}
    if reasons & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
        return ToolFailure("rate_limited", "YouTube API quota used up for today (resets at midnight Pacific)",
                           retry_after_s=3600)
    if reasons & {"API_KEY_INVALID", "keyInvalid", "accessNotConfigured", "SERVICE_DISABLED", "forbidden"} or r.status_code in (400, 401, 403):
        return ToolFailure("login_required", "YouTube API key rejected: check it, and that YouTube Data API v3 is "
                                             "enabled for its project (Settings, Accounts & keys)")
    return ToolFailure("platform_unavailable", f"YouTube API error {r.status_code}")


def _item(v: dict[str, Any]) -> VideoItem | None:
    vid = v.get("id")
    sn, st, cd = v.get("snippet") or {}, v.get("statistics") or {}, v.get("contentDetails") or {}
    duration = iso_duration_s(cd.get("duration") or "")
    if not vid or duration is None or duration > MAX_SHORT_S:
        return None
    text = f"{sn.get('title') or ''} {sn.get('description') or ''}"
    tags: list[str] = []
    for t in _HASHTAG.findall(text):
        if (n := norm_hashtag(t)) not in tags:
            tags.append(n)
    posted = sn.get("publishedAt")
    thumbs = sn.get("thumbnails") or {}
    thumb = next((thumbs[k]["url"] for k in ("high", "medium", "default") if thumbs.get(k, {}).get("url")), None)

    def num(k: str) -> int | None:
        return int(st[k]) if str(st.get(k, "")).isdigit() else None

    return VideoItem(
        canonical_id=f"youtube:{vid}", platform="youtube", url=f"https://www.youtube.com/shorts/{vid}",
        creator=Creator(handle=norm_handle(sn["channelTitle"]) if sn.get("channelTitle") else None),
        caption=(sn.get("title") or None), hashtags=tags,
        posted_at=datetime.fromisoformat(posted) if posted else None,
        duration_s=duration, metrics=Metrics(views=num("viewCount"), likes=num("likeCount"),
                                             comments=num("commentCount")),
        thumbnail_url=thumb, media_access="ok", source="api")


class YouTubeApi:
    def __init__(self, key: Callable[[], str | None], quota: QuotaBook,
                 client: Callable[[], httpx.AsyncClient] | None = None) -> None:
        self.key, self.quota = key, quota
        self._client = client or (lambda: httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10)))

    def configured(self) -> bool:
        return bool(self.key())

    async def check_key(self, key: str) -> None:
        """One 1-unit call, so a mistyped or disabled key is caught when it's saved, not mid-run."""
        if not await self.quota.spend(1):
            return  # out of quota today: can't check, accept it
        try:
            async with self._client() as c:
                r = await c.get(f"{API}/videos", params={"part": "id", "id": "dQw4w9WgXcQ", "key": key})
        except httpx.HTTPError as e:
            raise ToolFailure("platform_unavailable", f"couldn't reach Google to check the key ({type(e).__name__})") \
                from None
        if r.status_code != 200:
            raise _error(r)

    async def shorts_search(self, query: str, n: int = 15, published_after: datetime | None = None,
                            order: str = "viewCount", region: str | None = None) -> list[VideoItem]:
        key = self.key()
        if not key:
            raise ToolFailure("login_required", "no YouTube API key set (Settings, Accounts & keys)")
        if not await self.quota.spend(SEARCH_UNITS):
            raise ToolFailure("rate_limited", "YouTube API daily quota used up (resets at midnight Pacific)",
                              retry_after_s=3600)
        params: dict[str, Any] = {"part": "snippet", "type": "video", "videoDuration": "short", "q": query,
                                  "maxResults": min(max(n * 2, 10), 50), "order": order, "key": key,
                                  "safeSearch": "none"}
        if published_after is not None:
            params["publishedAfter"] = published_after.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if region:
            params["regionCode"] = region
        try:
            async with self._client() as c:
                r = await c.get(f"{API}/search", params=params)
                if r.status_code != 200:
                    raise _error(r)
                ids = [i["id"]["videoId"] for i in r.json().get("items") or [] if (i.get("id") or {}).get("videoId")]
                if not ids:
                    return []
                r = await c.get(f"{API}/videos", params={"part": "snippet,statistics,contentDetails",
                                                          "id": ",".join(ids), "key": key})
                if r.status_code != 200:
                    raise _error(r)
        except httpx.HTTPError as e:  # never echo the request URL: it carries the key
            raise ToolFailure("platform_unavailable", f"YouTube API unreachable ({type(e).__name__})") from None
        items = [it for v in r.json().get("items") or [] if (it := _item(v)) is not None]
        return items[:n]
