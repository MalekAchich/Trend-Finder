"""Discovery for TikTok, Instagram Reels, YouTube Shorts and X (D-34, 03-tools.md).

Best source first, falling back automatically:
- a connected scraping account (TikTok, Instagram, X): the site's own search, read from the JSON it fetches;
- the YouTube Data API when a key is set (Shorts);
- anonymous: SearXNG `site:` query → canonical IDs → seen-filter → per-video yt-dlp enrichment (cached).
Instagram stays discovery-only until a logged-in session exists.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol

from tf_agent.tools.health import PlatformRegistry
from tf_agent.tools.limiter import RateLimiter
from tf_agent.tools.normalize import (
    canonical_id,
    canonical_url,
    norm_handle,
    norm_hashtag,
    norm_query,
    platform_of,
)
from tf_agent.tools import session_search as ss
from tf_agent.tools.types import ToolFailure, VideoItem
from tf_agent.tools.web import SearchHit, SearchResponse

if TYPE_CHECKING:
    from tf_agent.tools.browser import BrowserSessions
    from tf_agent.tools.youtube_api import YouTubeApi

SeenFilter = Callable[[list[VideoItem]], Awaitable[tuple[list[VideoItem], int]]]
ItemsSink = Callable[[list[VideoItem]], Awaitable[None]]
HITS_TTL_S = 6 * 3600
SEARCH_PAGE = 20
META_TTL_S = 6 * 3600
_HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)
_COUNTED_FAILURES = ("platform_unavailable", "rate_limited")
Recent = Literal["day", "week", "month", "year"] | None
RECENT_DAYS = {"day": 1, "week": 7, "month": 31, "year": 366}


def recent_since(recent: Recent) -> datetime | None:
    return datetime.now(UTC) - timedelta(days=RECENT_DAYS[recent]) if recent else None


class SearchBackend(Protocol):
    async def search(self, query: str, max_results: int = 10, time_range: str | None = None) -> SearchResponse: ...


class MetadataBackend(Protocol):
    async def metadata(self, url: str) -> VideoItem: ...

    async def search_youtube(self, query: str, n: int = 10) -> list[VideoItem]: ...


class Cache(Protocol):
    async def get(self, tool: str, key_input: Any) -> dict[str, Any] | None: ...

    async def put(self, tool: str, key_input: Any, response: dict[str, Any], ttl_s: float) -> None: ...


@dataclass
class DiscoveryResult:
    items: list[VideoItem]
    hidden_already_seen: int = 0
    platform_health: str = "ok"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"items": [i.model_dump(mode="json") for i in self.items],
                "hidden_already_seen": self.hidden_already_seen, "platform_health": self.platform_health,
                "notes": self.notes}


def _skeleton(cid: str, hit: SearchHit | None, url: str, media_access: str) -> VideoItem:
    caption = " ".join(x for x in ((hit.title, hit.snippet) if hit else ()) if x).strip() or None
    tags: list[str] = []
    for t in _HASHTAG_RE.findall(caption or ""):
        n = norm_hashtag(t)
        if n not in tags:
            tags.append(n)
    return VideoItem(canonical_id=cid, platform=cid.split(":", 1)[0], url=canonical_url(cid, url), caption=caption,
                     hashtags=tags, media_access=media_access, source="searxng")


class PlatformTools:
    def __init__(self, searx: SearchBackend, ytdlp: MetadataBackend, cache: Cache | None,
                 registry: PlatformRegistry, *, seen_filter: SeenFilter | None = None,
                 on_items: ItemsSink | None = None, instagram_enrich: bool = False, enrich_parallel: int = 4,
                 search_limiter: RateLimiter | None = None, clock: Callable[[], float] = time.time,
                 browser: BrowserSessions | None = None, youtube_api: YouTubeApi | None = None) -> None:
        self.searx, self.ytdlp, self.cache, self.registry = searx, ytdlp, cache, registry
        self.browser, self.youtube_api = browser, youtube_api
        self.seen_filter, self.on_items = seen_filter, on_items
        self.instagram_enrich = instagram_enrich
        self._enrich_slots = asyncio.Semaphore(enrich_parallel)
        # every SearXNG query (site: searches and web_search) shares one politeness lane: same upstream engines
        self.search_limiter = search_limiter or RateLimiter(1.0)
        self._clock = clock
        self.sync_modes()

    def sync_modes(self) -> None:
        """Instagram is discovery-only until an account is connected; connecting one (any time) lifts it."""
        self.registry.configure_mode("instagram", "full" if self._ig_enrich() else "discovery_only")

    def _has_session(self, platform: str) -> bool:
        return self.browser is not None and self.browser.creds.has_session(platform)

    def _ig_enrich(self) -> bool:
        """Instagram metadata needs a logged-in session's cookies (read now: an account can be connected any time)."""
        return self.instagram_enrich or self._has_session("instagram")

    async def _session_search(self, platform: str, url: str, pattern: re.Pattern[str],
                              parse: Callable[[list[Any]], list[VideoItem]], max_results: int, recent: Recent,
                              notes: list[str]) -> DiscoveryResult | None:
        """The site's own logged-in search; None (with a note) when it can't be used, so the caller falls back."""
        if self.browser is None or not self._has_session(platform):
            return None
        browser = self.browser

        async def run() -> list[VideoItem]:
            captured = await browser.capture_json(platform, url, pattern, need_session=True, blocked=ss.is_captcha)
            return parse(captured)

        try:
            items = await self._guarded(platform, run)
        except ToolFailure as e:
            notes.append(f"logged-in search unavailable ({e.error.message}); used web search instead")
            return None
        since = recent_since(recent)
        if since is not None:
            items = [i for i in items if i.posted_at is None or i.posted_at >= since]
        kept, hidden = await self._filter_seen(items)
        notes.append("from the logged-in account's search")
        return await self._finish(platform, kept[:max_results], hidden, notes)

    # ---- plumbing ----
    async def _guarded(self, platform: str, fn: Callable[[], Awaitable[Any]],
                       limiter: RateLimiter | None = None) -> Any:
        if not self.registry.allow(platform):
            raise ToolFailure("platform_unavailable",
                              f"{platform}: circuit open after repeated failures; try another platform or later",
                              retry_after_s=600)
        verdict = False
        try:
            await (limiter or self.registry.limiter(platform)).acquire()
            result = await fn()
            await self.registry.record_success(platform)
            verdict = True
            return result
        except ToolFailure as e:
            if e.error.code in _COUNTED_FAILURES:
                await self.registry.record_failure(platform, e.error.message)
            elif e.error.code == "login_required":
                await self.registry.record_login_wall(platform)  # per item; repeated walls flip the platform
            else:
                await self.registry.record_alive(platform)  # not_found / invalid_input: the platform answered
            verdict = True
            raise
        finally:
            if not verdict:  # cancelled or unexpected error: never leave a half-open probe stuck
                self.registry.release_probe(platform)

    async def _search_hits(self, platform: str, query: str, n: int, recent: Recent = None,
                           notes: list[str] | None = None) -> list[SearchHit]:
        key = {"q": query, "n": n, "recent": recent}
        if self.cache is not None and (cached := await self.cache.get("search_hits", key)) is not None:
            return [SearchHit(**h) for h in cached["hits"]]
        resp: SearchResponse = await self._guarded(
            platform, lambda: self.searx.search(query, max_results=n, time_range=recent), self.search_limiter)
        if resp.unresponsive and notes is not None:
            notes.append(f"search engines degraded ({', '.join(resp.unresponsive)}): results may be incomplete")
        if self.cache is not None and resp.results and not resp.unresponsive:  # never cache thin results
            await self.cache.put("search_hits", key, {"hits": [h.__dict__ for h in resp.results]}, HITS_TTL_S)
        return resp.results

    def _skeletons(self, hits: list[SearchHit], media_access: str) -> list[VideoItem]:
        items, seen = [], set()
        for h in hits:
            cid = canonical_id(h.url)
            if cid and cid not in seen:
                seen.add(cid)
                items.append(_skeleton(cid, h, h.url, media_access))
        return items

    async def _filter_seen(self, items: list[VideoItem]) -> tuple[list[VideoItem], int]:
        if self.seen_filter is None or not items:
            return items, 0
        return await self.seen_filter(items)

    async def _metadata(self, platform: str, url: str, cid: str | None) -> VideoItem:
        key = {"cid": cid or url}
        if self.cache is not None and (cached := await self.cache.get("video_meta", key)) is not None:
            return VideoItem.model_validate(cached)
        async with self._enrich_slots:
            item: VideoItem = await self._guarded(platform, lambda: self.ytdlp.metadata(url))
        if self.cache is not None:
            await self.cache.put("video_meta", {"cid": item.canonical_id}, item.model_dump(mode="json"), META_TTL_S)
        return item

    async def _enrich(self, platform: str, items: list[VideoItem], notes: list[str]) -> list[VideoItem]:
        async def one(it: VideoItem) -> VideoItem | None:
            try:
                return await self._metadata(platform, it.url, it.canonical_id)
            except ToolFailure:
                return None

        enriched = await asyncio.gather(*(one(i) for i in items))
        failed = sum(1 for e in enriched if e is None)
        if failed:
            notes.append(f"{failed} item(s) could not be enriched; shown with search-snippet data only")
        return [e if e is not None else i for i, e in zip(items, enriched, strict=True)]

    async def _finish(self, platform: str, items: list[VideoItem], hidden: int, notes: list[str]) -> DiscoveryResult:
        if self.on_items is not None and items:
            try:
                await self.on_items(items)
            except Exception as e:  # persistence trouble must not throw away a paid-for search
                notes.append(f"results not saved ({type(e).__name__}); they are still shown here")
        health = self.registry.health(platform)
        if health == "ok" and any(n.startswith("search engines degraded") for n in notes):
            health = "degraded"
        return DiscoveryResult(items=items, hidden_already_seen=hidden, platform_health=health, notes=notes)

    async def _site_search(self, platform: str, site_query: str, max_results: int, enrich: bool,
                           media_access: str = "unknown", recent: Recent = None) -> DiscoveryResult:
        notes: list[str] = []
        # ask for a full page and keep only video URLs before slicing: engines mix in tag/profile pages
        hits = await self._search_hits(platform, site_query, SEARCH_PAGE, recent, notes)
        kept, hidden = await self._filter_seen(self._skeletons(hits, media_access))
        kept = kept[:max_results]
        if enrich:
            kept = await self._enrich(platform, kept, notes)
        return await self._finish(platform, kept, hidden, notes)

    # ---- public tools ----
    async def tiktok_search(self, query: str, max_results: int = 15, recent: Recent = None) -> DiscoveryResult:
        notes: list[str] = []
        q = norm_query(query)
        if (res := await self._session_search("tiktok", ss.tiktok_search_url(q), ss.TIKTOK_PATTERN, ss.tiktok_items,
                                              max_results, recent, notes)) is not None:
            return res
        # `site:tiktok.com/@` keeps engines on /@user/video/ID pages (measured: 20/20 videos vs 2/20 with recency)
        res = await self._site_search("tiktok", f"site:tiktok.com/@ {q}", max_results, True, recent=recent)
        res.notes[:0] = notes
        return res

    async def tiktok_trends(self, kind: Literal["hashtags", "videos"] = "videos", region: str = "US",
                            period: int = 7) -> DiscoveryResult | dict[str, Any]:
        """TikTok's own trend rankings (Creative Center in TikTok One). Anonymous visitors get the top few per filter;
        organic-only videos are asked for separately, since the default ranking is mostly paid reach."""
        if self.browser is None:
            raise ToolFailure("platform_unavailable", "tiktok trends need the browser (not available here)")
        browser = self.browser
        url = ss.trends_page_url(kind, region, period)

        async def run() -> list[Any]:
            return await browser.capture_json("tiktok_one", url, ss.TRENDS_PATTERN, scrolls=2, settle_ms=6000,
                                              follow=ss.organic_variants if kind == "videos" else ss.hashtag_first_page,
                                              follow_headers=ss.FOLLOW_HEADERS)

        captured = await self._guarded("tiktok_trends", run)
        if kind == "hashtags":
            rows = ss.trend_hashtags(captured)
            return {"hashtags": rows, "region": region.upper(), "period_days": period,
                    "notes": [] if self._has_session("tiktok_one") else
                    ["only the top few: connect TikTok One in Settings for the full rankings"]}
        notes = ["organic top videos from TikTok's trend rankings (branded #ad posts left out)"]
        kept, hidden = await self._filter_seen(ss.trend_videos(captured))
        kept = await self._enrich("tiktok", kept, notes)  # the rankings carry no duration or likes
        return await self._finish("tiktok", kept, hidden, notes)

    async def tiktok_creator(self, handle: str, max_results: int = 15) -> DiscoveryResult:
        return await self._site_search("tiktok", f"site:tiktok.com/@{norm_handle(handle)}", max_results, True)

    async def tiktok_hashtag(self, tag: str, max_results: int = 15) -> DiscoveryResult:
        return await self._site_search("tiktok", f'site:tiktok.com "#{norm_hashtag(tag)}"', max_results, True)

    async def instagram_search(self, query: str, max_results: int = 15, recent: Recent = None) -> DiscoveryResult:
        notes: list[str] = []
        if (res := await self._session_search("instagram", ss.instagram_search_url(norm_query(query)),
                                              ss.INSTAGRAM_PATTERN, ss.instagram_items, max_results, recent,
                                              notes)) is not None:
            return res
        q = f"site:instagram.com/reel {norm_query(query)}"
        if self._ig_enrich():
            res = await self._site_search("instagram", q, max_results, True, recent=recent)
            res.notes[:0] = notes
            return res
        res = await self._site_search("instagram", q, max_results, False, media_access="login_required",
                                      recent=recent)
        res.notes.append("instagram: discovery only without a logged-in session (no metrics, no video analysis)")
        return res

    async def shorts_search(self, query: str, max_results: int = 15, recent: Recent = None) -> DiscoveryResult:
        q = norm_query(query)
        notes: list[str] = []
        if self.youtube_api is not None and self.youtube_api.configured():
            api = self.youtube_api
            try:
                found = await self._guarded("youtube", lambda: api.shorts_search(
                    q, max_results * 2, published_after=recent_since(recent)))
            except ToolFailure as e:
                notes.append(f"YouTube API unavailable ({e.error.message}); used the search page instead")
            else:
                kept, hidden = await self._filter_seen(found)
                notes.append("from the YouTube Data API")
                return await self._finish("youtube", kept[:max_results], hidden, notes)
        merged: dict[str, VideoItem] = {}
        failures: list[ToolFailure] = []
        try:
            for it in await self._guarded("youtube", lambda: self.ytdlp.search_youtube(q, n=max_results)):
                merged.setdefault(it.canonical_id, it)
        except ToolFailure as e:
            failures.append(e)
            notes.append(f"youtube search unavailable: {e.error.message}")
        try:
            for it in self._skeletons(await self._search_hits("youtube", f"site:youtube.com/shorts {q}",
                                                              SEARCH_PAGE, recent, notes), "unknown"):
                merged.setdefault(it.canonical_id, it)
        except ToolFailure as e:
            failures.append(e)
            notes.append(f"web search unavailable: {e.error.message}")
        if len(failures) == 2:
            raise failures[-1]
        kept, hidden = await self._filter_seen(list(merged.values()))
        kept = await self._enrich("youtube", kept[:max_results], notes)
        return await self._finish("youtube", kept, hidden, notes)

    async def x_search(self, query: str, max_results: int = 15, recent: Recent = None) -> DiscoveryResult:
        notes: list[str] = []
        q = norm_query(query)
        since = recent_since(recent)
        if (res := await self._session_search("x", ss.x_search_url(q, since.date().isoformat() if since else None),
                                              ss.X_PATTERN, ss.x_items, max_results, recent, notes)) is not None:
            return res
        notes.append("X without a connected account: web search only finds a few, older posts")
        res = await self._site_search("x", f"site:x.com {q} video", max_results, self._has_session("x"),
                                      recent=recent)
        res.notes[:0] = notes
        return res

    async def web_search(self, query: str, max_results: int = 8, time_range: Recent = None) -> SearchResponse:
        return await self._guarded("web", lambda: self.searx.search(query, max_results=max_results,
                                                                     time_range=time_range), self.search_limiter)

    async def get_video(self, url: str) -> VideoItem:
        platform = platform_of(url)
        if platform is None:
            raise ToolFailure("invalid_input", "not a TikTok, Instagram, YouTube or X URL")
        cid = canonical_id(url)
        if platform == "instagram" and not self._ig_enrich():
            if cid is None:
                raise ToolFailure("invalid_input", "not an Instagram reel URL")
            return _skeleton(cid, None, url, "login_required")
        item = await self._metadata(platform, url, cid)
        if self.on_items is not None:
            await self.on_items([item])
        return item
