"""YouTube for our own channel (Plan 9): public numbers through the Data API key, and with the owner's Google login
(installed-app OAuth, loopback redirect, PKCE; read-only scopes) the YouTube Analytics reports: views by country and
city for the channel and for each video, ages and genders, watch time and how much of each video people watch."""
from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from tf_agent.socials.types import ChannelRead, PostRead, ReadError
from tf_agent.tools.youtube_api import iso_duration_s

DATA = "https://www.googleapis.com/youtube/v3"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ("https://www.googleapis.com/auth/youtube.readonly "
          "https://www.googleapis.com/auth/yt-analytics.readonly")
MAX_VIDEOS = 50
VIDEO_COUNTRIES = 10  # the newest videos get their own views-by-country (one report each)
AUDIENCE_DAYS = 28


def challenge(verifier: str) -> str:
    """Standard PKCE (S256, base64url without padding): Google's form."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _error(r: httpx.Response, what: str) -> ReadError:
    try:
        err = r.json().get("error") or {}
    except ValueError:
        err = {}
    if isinstance(err, str):  # the token endpoint: {"error": "invalid_grant", ...}
        return ReadError("expired", f"Google: the connection expired ({err})")
    reasons = {str(e.get("reason")) for e in err.get("errors") or []}
    msg = str(err.get("message") or f"HTTP {r.status_code}")[:200]
    if r.status_code == 401:
        return ReadError("expired", f"{what}: the connection expired ({msg})")
    if reasons & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"} or r.status_code == 429:
        return ReadError("rate_limited", f"{what}: quota used up for today")
    if r.status_code == 403:
        return ReadError("revoked", f"{what}: access refused ({msg})")
    return ReadError("unavailable", f"{what}: {msg}")


def _rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    names = [h["name"] for h in report.get("columnHeaders") or []]
    return [dict(zip(names, row, strict=False)) for row in report.get("rows") or []]


class YouTubeChannel:
    """One reader for both levels: `key` (the Data API key) for public numbers, a bearer token for everything."""

    def __init__(self, key: Callable[[], str | None], client: httpx.AsyncClient | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.key, self.client, self._now = key, client or httpx.AsyncClient(timeout=20), clock

    # ---- login ----
    def authorize_url(self, client_id: str, redirect: str, state: str, verifier: str) -> str:
        return AUTH_URL + "?" + urlencode({
            "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
            "access_type": "offline", "prompt": "consent", "state": state,
            "code_challenge": challenge(verifier), "code_challenge_method": "S256"})

    async def _token(self, form: dict[str, str]) -> dict[str, Any]:
        try:
            r = await self.client.post(TOKEN_URL, data=form)
        except httpx.HTTPError as e:
            raise ReadError("unavailable", f"Google didn't answer ({type(e).__name__})") from None
        if r.status_code != 200:
            raise _error(r, "Google")
        return r.json()

    async def exchange(self, app: dict[str, str], code: str, verifier: str, redirect: str) -> dict[str, Any]:
        return await self._token({"client_id": app["client_id"], "client_secret": app["client_secret"], "code": code,
                                  "code_verifier": verifier, "grant_type": "authorization_code",
                                  "redirect_uri": redirect})

    async def refresh(self, app: dict[str, str], refresh_token: str) -> dict[str, Any]:
        return await self._token({"client_id": app["client_id"], "client_secret": app["client_secret"],
                                  "refresh_token": refresh_token, "grant_type": "refresh_token"})

    # ---- reads ----
    async def _get(self, url: str, token: str | None, what: str, **params: Any) -> dict[str, Any]:
        headers = {"authorization": f"Bearer {token}"} if token else {}
        if not token:
            key = self.key()
            if not key:
                raise ReadError("unavailable", "YouTube: add the YouTube API key in Settings (Accounts & keys)")
            headers["x-goog-api-key"] = key  # the key in a header, never in a logged URL
        try:
            r = await self.client.get(url, params=params, headers=headers)
        except httpx.HTTPError as e:
            raise ReadError("unavailable", f"{what} didn't answer ({type(e).__name__})") from None
        if r.status_code != 200:
            raise _error(r, what)
        return r.json()

    async def me(self, token: str) -> dict[str, Any]:
        items = (await self._get(f"{DATA}/channels", token, "YouTube", part="id,snippet", mine="true")).get("items")
        if not items:
            raise ReadError("revoked", "YouTube: this Google account has no YouTube channel")
        return {"id": items[0]["id"], "handle": str(items[0]["snippet"].get("customUrl") or "").lstrip("@")}

    async def _channel(self, token: str | None, handle: str) -> dict[str, Any]:
        params = {"mine": "true"} if token else {"forHandle": f"@{handle}"}
        items = (await self._get(f"{DATA}/channels", token, "YouTube",
                                 part="id,snippet,statistics,contentDetails", **params)).get("items")
        if not items:
            raise ReadError("unavailable", f"YouTube: no channel @{handle}")
        return items[0]

    async def _videos(self, token: str | None, uploads: str, since: datetime) -> list[dict[str, Any]]:
        listed = (await self._get(f"{DATA}/playlistItems", token, "YouTube", part="contentDetails",
                                  playlistId=uploads, maxResults=MAX_VIDEOS)).get("items") or []
        ids = [i["contentDetails"]["videoId"] for i in listed if i.get("contentDetails", {}).get("videoId")]
        if not ids:
            return []
        videos = (await self._get(f"{DATA}/videos", token, "YouTube", part="snippet,statistics,contentDetails",
                                  id=",".join(ids))).get("items") or []
        return [v for v in videos if _published(v) is None or _published(v) >= since]

    async def read_public(self, handle: str, since: datetime) -> ChannelRead:
        ch = await self._channel(None, handle)
        return self._shape(ch, await self._videos(None, _uploads(ch), since), "public")

    async def read(self, token: str, since: datetime) -> ChannelRead:
        ch = await self._channel(token, "")
        out = self._shape(ch, await self._videos(token, _uploads(ch), since), "api")
        try:  # Analytics: a refusal costs only these numbers, never the counts above
            await self._analytics(token, out)
        except ReadError as e:
            if e.code in ("expired", "revoked"):
                raise
        return out

    def _shape(self, ch: dict[str, Any], videos: list[dict[str, Any]], source: str) -> ChannelRead:
        stats, snip = ch.get("statistics") or {}, ch.get("snippet") or {}
        posts = []
        for v in videos:
            st, sn = v.get("statistics") or {}, v.get("snippet") or {}
            thumbs = sn.get("thumbnails") or {}
            thumb = next((thumbs[k]["url"] for k in ("maxres", "high", "medium", "default") if k in thumbs), None)
            posts.append(PostRead(platform_post_id=v["id"], url=f"https://www.youtube.com/shorts/{v['id']}",
                                  caption=sn.get("title"), posted_at=_published(v), thumbnail_url=thumb,
                                  duration_s=iso_duration_s((v.get("contentDetails") or {}).get("duration", "")),
                                  views=_int(st.get("viewCount")), likes=_int(st.get("likeCount")),
                                  comments=_int(st.get("commentCount"))))
        return ChannelRead(handle=str(snip.get("customUrl") or "").lstrip("@"),
                           followers=None if stats.get("hiddenSubscriberCount") else _int(stats.get("subscriberCount")),
                           posts_count=_int(stats.get("videoCount")), posts=posts, source=source)

    async def _report(self, token: str, start: date, **params: Any) -> list[dict[str, Any]]:
        return _rows(await self._get(ANALYTICS, token, "YouTube Analytics", ids="channel==MINE",
                                     startDate=start.isoformat(), endDate=self._now().date().isoformat(), **params))

    async def _analytics(self, token: str, out: ChannelRead) -> None:
        today = self._now().date()
        since, ever = today - timedelta(days=AUDIENCE_DAYS), date(2005, 1, 1)
        per_video = {r["video"]: r for r in await self._report(
            token, ever, dimensions="video", sort="-views", maxResults=200,
            metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,shares")}
        for p in out.posts:
            r = per_video.get(p.platform_post_id)
            if r:
                p.avg_watch_s = float(r["averageViewDuration"]) if r.get("averageViewDuration") is not None else None
                p.total_watch_s = float(r["estimatedMinutesWatched"]) * 60 if r.get("estimatedMinutesWatched") is not None else None
                p.shares = _int(r.get("shares"))
        totals = await self._report(token, since, metrics="views,estimatedMinutesWatched,averageViewDuration,"
                                                          "subscribersGained,subscribersLost")
        if totals:
            t = totals[0]
            out.account_insights = {"days": AUDIENCE_DAYS, "views": _int(t.get("views")),
                                    "watch_minutes": _int(t.get("estimatedMinutesWatched")),
                                    "avg_view_s": t.get("averageViewDuration"),
                                    "subscribers_gained": _int(t.get("subscribersGained")),
                                    "subscribers_lost": _int(t.get("subscribersLost"))}
        out.audience = {"reached": await self._audience(token, since)}
        out.audience = {k: v for k, v in out.audience.items() if v}
        for p in out.posts[:VIDEO_COUNTRIES]:  # the newest videos: where each one's views come from
            rows = await self._report(token, ever, dimensions="country", metrics="views", sort="-views",
                                      maxResults=10, filters=f"video=={p.platform_post_id}")
            if rows:
                p.audience = {"country": [[r["country"], r["views"]] for r in rows if r.get("views")]}

    async def _audience(self, token: str, since: date) -> dict[str, list[list[Any]]]:
        """Where the views come from (last 28 days): countries, cities, ages, genders."""
        out: dict[str, list[list[Any]]] = {}
        for dim, metric, key in (("country", "views", "country"), ("city", "views", "city")):
            try:
                rows = await self._report(token, since, dimensions=dim, metrics=metric, sort=f"-{metric}",
                                          maxResults=12)
            except ReadError as e:
                if e.code != "unavailable":
                    raise
                continue
            out[key] = [[r[dim], r[metric]] for r in rows if r.get(metric)]
        try:
            rows = await self._report(token, since, dimensions="ageGroup,gender", metrics="viewerPercentage")
        except ReadError as e:
            if e.code != "unavailable":
                raise
            rows = []
        ages: dict[str, float] = {}
        genders: dict[str, float] = {}
        for r in rows:
            ages[str(r["ageGroup"]).replace("age", "")] = ages.get(str(r["ageGroup"]).replace("age", ""), 0) + r["viewerPercentage"]
            g = {"male": "M", "female": "F"}.get(str(r["gender"]), "U")
            genders[g] = genders.get(g, 0) + r["viewerPercentage"]
        if ages:
            out["age"] = sorted(([k, round(v, 1)] for k, v in ages.items() if v), key=lambda x: -x[1])
        if genders:
            out["gender"] = sorted(([k, round(v, 1)] for k, v in genders.items() if v), key=lambda x: -x[1])
        return {k: v for k, v in out.items() if v}


def _uploads(ch: dict[str, Any]) -> str:
    return ch["contentDetails"]["relatedPlaylists"]["uploads"]


def _published(v: dict[str, Any]) -> datetime | None:
    s = (v.get("snippet") or {}).get("publishedAt")
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
