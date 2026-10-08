"""Instagram API with Instagram login (graph.instagram.com): our own professional account's numbers.
Scopes: instagram_business_basic, instagram_business_manage_insights. Read-only."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from tf_agent.socials.types import ChannelRead, PostRead, ReadError

BASE = "https://graph.instagram.com"
MEDIA_FIELDS = "id,caption,media_type,media_product_type,permalink,thumbnail_url,timestamp,like_count,comments_count"
# Meta renames metrics now and then: when the full set is refused, the next smaller one is tried
METRIC_SETS = (
    ["views", "reach", "likes", "comments", "shares", "saved", "ig_reels_avg_watch_time",
     "ig_reels_video_view_total_time"],
    ["views", "reach", "likes", "comments", "shares", "saved"],
    ["reach", "likes", "comments", "saved"],
)
IMAGE_METRICS = ["views", "reach", "likes", "comments", "shares", "saved"]
MAX_PAGES = 20  # 50 posts a page: far more than a 90-day window holds; a safety stop against a looping cursor


def _error(r: httpx.Response) -> ReadError:
    try:
        err = r.json().get("error") or {}
    except ValueError:
        err = {}
    code, msg = err.get("code"), str(err.get("message") or f"HTTP {r.status_code}")[:200]
    if code == 190:
        return ReadError("expired", f"Instagram: the token is no longer valid ({msg})")
    if code in (10, 200) or r.status_code == 403:
        return ReadError("revoked", f"Instagram: access was removed or a permission is missing ({msg})")
    if code in (4, 17, 32, 613) or r.status_code == 429:
        return ReadError("rate_limited", f"Instagram: too many requests, try later ({msg})")
    return ReadError("unavailable", f"Instagram: {msg}")


def _time(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z") if value else None
    except ValueError:
        return None


def _metric(entry: dict[str, Any]) -> float | None:
    if isinstance(entry.get("total_value"), dict):
        return entry["total_value"].get("value")
    values = entry.get("values") or []
    return values[0].get("value") if values and isinstance(values[0], dict) else None


class InstagramApi:
    def __init__(self, client: httpx.AsyncClient | None = None, base: str = BASE) -> None:
        self.client = client or httpx.AsyncClient(timeout=20)
        self.base = base

    async def _get(self, path_or_url: str, token: str, **params: Any) -> dict[str, Any]:
        url = httpx.URL(path_or_url if path_or_url.startswith("http") else f"{self.base}{path_or_url}")
        # a "next" link carries its own query (the page cursor): keep it and add ours on top
        query = {**dict(url.params), **params, "access_token": token}
        try:
            r = await self.client.get(url.copy_with(query=None), params=query)
        except httpx.HTTPError as e:
            raise ReadError("unavailable", f"Instagram didn't answer ({type(e).__name__})") from None
        if r.status_code != 200:
            raise _error(r)
        return r.json()

    async def me(self, token: str) -> dict[str, Any]:
        return await self._get("/me", token, fields="user_id,username,followers_count,media_count,account_type")

    async def refresh(self, token: str) -> dict[str, Any]:
        """A long-lived token, refreshed for another 60 days: {access_token, expires_in}."""
        return await self._get("/refresh_access_token", token, grant_type="ig_refresh_token")

    async def _insights(self, token: str, media_id: str, reel: bool) -> dict[str, float | None]:
        for metrics in (METRIC_SETS if reel else (IMAGE_METRICS, METRIC_SETS[2])):
            try:
                data = await self._get(f"/{media_id}/insights", token, metric=",".join(metrics))
            except ReadError as e:
                if e.code == "unavailable":
                    continue  # a metric this post (or this API version) doesn't have: try fewer
                raise
            return {d.get("name"): _metric(d) for d in data.get("data") or [] if isinstance(d, dict)}
        return {}

    async def read(self, token: str, since: datetime) -> ChannelRead:
        me = await self.me(token)
        out = ChannelRead(handle=str(me.get("username") or ""), followers=me.get("followers_count"),
                          posts_count=me.get("media_count"))
        url: str | None = "/me/media"
        params: dict[str, Any] = {"fields": MEDIA_FIELDS, "limit": 50}
        for _ in range(MAX_PAGES):
            if not url:
                break
            page = await self._get(url, token, **params)
            params = {}  # the next link carries its own query
            old = False
            for m in page.get("data") or []:
                posted = _time(m.get("timestamp"))
                if posted is not None and posted < since:
                    old = True
                    continue
                reel = m.get("media_product_type") == "REELS" or m.get("media_type") == "VIDEO"
                ins = await self._insights(token, str(m["id"]), reel)
                watch, total = ins.get("ig_reels_avg_watch_time"), ins.get("ig_reels_video_view_total_time")
                out.posts.append(PostRead(
                    platform_post_id=str(m["id"]), url=str(m.get("permalink") or ""), caption=m.get("caption"),
                    posted_at=posted, thumbnail_url=m.get("thumbnail_url"),
                    views=_int(ins.get("views")), reach=_int(ins.get("reach")),
                    likes=_int(ins.get("likes", m.get("like_count"))),
                    comments=_int(ins.get("comments", m.get("comments_count"))),
                    shares=_int(ins.get("shares")), saves=_int(ins.get("saved")),
                    avg_watch_s=watch / 1000 if watch is not None else None,  # milliseconds
                    total_watch_s=total / 1000 if total is not None else None))
            url = None if old else (page.get("paging") or {}).get("next")
        return out


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
