"""Builds the discovery + analysis stack from settings (shared by the CLI and, in Plan 3, the orchestrator)."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.config import AppSettings
from tf_agent.loop.tools import Tool
from tf_agent.pipeline.analyze import HeavyRunner, VideoAnalyzer
from tf_agent.pipeline.retention import MediaRetention
from tf_agent.tools.agent_tools import build_tools
from tf_agent.tools.cache import ToolCache
from tf_agent.tools.health import PlatformRegistry
from tf_agent.tools.limiter import RateLimiter
from tf_agent.tools.platforms import ItemsSink, PlatformTools, SeenFilter
from tf_agent.tools.store import VideoStore
from tf_agent.tools.web import SearxClient
from tf_agent.tools.ytdlp import YtDlp


PLATFORM_TOOLS = {"tiktok": {"tiktok_search", "tiktok_creator", "tiktok_hashtag"},
                  "instagram": {"instagram_search"}, "youtube": {"shorts_search"}}
COMMON_TOOLS = {"get_video", "web_search", "web_fetch"}


@dataclass
class ToolStack:
    searx: SearxClient
    ytdlp: YtDlp
    registry: PlatformRegistry
    store: VideoStore | None
    platforms: PlatformTools
    cache: ToolCache | None = None
    search_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(1.0))
    instagram_enrich: bool = False

    def platform_tools(self, seen_filter: SeenFilter | None = None) -> PlatformTools:
        """A PlatformTools view with its own seen-filter, sharing limiters, breakers, cache and yt-dlp pool."""
        return PlatformTools(self.searx, self.ytdlp, self.cache, self.registry, seen_filter=seen_filter,
                             on_items=self.store.upsert_videos if self.store else None,
                             instagram_enrich=self.instagram_enrich, search_limiter=self.search_limiter)

    def tools_for(self, platform: str | None, seen_filter: SeenFilter | None = None) -> list[Tool]:
        wanted = PLATFORM_TOOLS.get(platform or "", set().union(*PLATFORM_TOOLS.values())) | COMMON_TOOLS
        return [t for t in build_tools(self.platform_tools(seen_filter), self.searx) if t.name in wanted]


def build_tool_stack(settings: AppSettings, sessionmaker: async_sessionmaker[AsyncSession] | None = None, *,
                     seen_filter: SeenFilter | None = None, on_items: ItemsSink | None = None) -> ToolStack:
    searx = SearxClient(settings.searxng_url)
    ytdlp = YtDlp(cookies_file=settings.instagram_cookies)
    registry = PlatformRegistry(sessionmaker)
    store = VideoStore(sessionmaker) if sessionmaker is not None else None
    if on_items is None and store is not None:
        on_items = store.upsert_videos
    cache = ToolCache(sessionmaker) if sessionmaker is not None else None
    limiter = RateLimiter(1.0)
    enrich = settings.instagram_cookies is not None
    platforms = PlatformTools(searx, ytdlp, cache, registry, seen_filter=seen_filter, on_items=on_items,
                              instagram_enrich=enrich, search_limiter=limiter)
    return ToolStack(searx, ytdlp, registry, store, platforms, cache, limiter, enrich)


def build_analyzer(settings: AppSettings, stack: ToolStack, runner: HeavyRunner, max_parallel: int = 2,
                   protected=lambda: ()) -> VideoAnalyzer:
    retention = MediaRetention(settings.media_dir, int(settings.media_quota_gb * 1024**3), protected)
    return VideoAnalyzer(stack.ytdlp, media_dir=settings.media_dir, heavy_runner=runner, store=stack.store,
                         retention=retention, max_parallel=max_parallel)
