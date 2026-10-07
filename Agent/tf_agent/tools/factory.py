"""Builds the discovery + analysis stack from settings (shared by the CLI and, in Plan 3, the orchestrator)."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.config import AppSettings
from tf_agent.credentials import Credentials
from tf_agent.loop.tools import Tool
from tf_agent.pipeline.analyze import HeavyRunner, VideoAnalyzer
from tf_agent.tools.agent_tools import build_tools
from tf_agent.tools.browser import BrowserSessions
from tf_agent.tools.cache import ToolCache
from tf_agent.tools.health import PlatformRegistry
from tf_agent.tools.limiter import RateLimiter
from tf_agent.tools.platforms import ItemsSink, PlatformTools, SeenFilter
from tf_agent.tools.store import VideoStore
from tf_agent.tools.types import VideoItem
from tf_agent.tools.web import SearxClient
from tf_agent.tools.youtube_api import DbQuota, MemoryQuota, YouTubeApi
from tf_agent.tools.ytdlp import YtDlp


PLATFORM_TOOLS = {"tiktok": {"tiktok_search", "tiktok_creator", "tiktok_hashtag", "tiktok_trends"},
                  "instagram": {"instagram_search"}, "youtube": {"shorts_search"}, "x": {"x_search"}}
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
    browser: BrowserSessions | None = None
    youtube_api: YouTubeApi | None = None

    def platform_tools(self, seen_filter: SeenFilter | None = None) -> PlatformTools:
        """A PlatformTools view with its own seen-filter, sharing limiters, breakers, cache and yt-dlp pool."""
        return PlatformTools(self.searx, self.ytdlp, self.cache, self.registry, seen_filter=seen_filter,
                             on_items=self.store.upsert_videos if self.store else None,
                             instagram_enrich=self.instagram_enrich, search_limiter=self.search_limiter,
                             browser=self.browser, youtube_api=self.youtube_api)

    def tools_for(self, platform: str | None, seen_filter: SeenFilter | None = None,
                  recent: str | None = None) -> list[Tool]:
        wanted = PLATFORM_TOOLS.get(platform or "", set().union(*PLATFORM_TOOLS.values())) | COMMON_TOOLS
        return [t for t in build_tools(self.platform_tools(seen_filter), self.searx, default_recent=recent)
                if t.name in wanted]

    def refresh_modes(self) -> None:
        """Re-read which accounts are connected (Settings can connect one between runs)."""
        self.platforms.sync_modes()

    async def get_video(self, url: str, fresh: bool = False) -> VideoItem:
        """Metadata for one URL the owner pasted (stored like any tool result); `fresh` skips the cache."""
        return await self.platform_tools().get_video(url, fresh=fresh)


def build_tool_stack(settings: AppSettings, sessionmaker: async_sessionmaker[AsyncSession] | None = None, *,
                     seen_filter: SeenFilter | None = None, on_items: ItemsSink | None = None) -> ToolStack:
    searx = SearxClient(settings.searxng_url)
    creds = Credentials(settings.secrets_dir)
    ytdlp = YtDlp(cookies_for=creds.cookies_file)
    browser = BrowserSessions(creds, max_pages=settings.browser_pages, firefox_bin=settings.firefox_bin)
    quota = DbQuota(sessionmaker) if sessionmaker is not None else MemoryQuota()
    youtube_api = YouTubeApi(lambda: creds.get("youtube_api_key"), quota)
    registry = PlatformRegistry(sessionmaker)
    store = VideoStore(sessionmaker) if sessionmaker is not None else None
    if on_items is None and store is not None:
        on_items = store.upsert_videos
    cache = ToolCache(sessionmaker) if sessionmaker is not None else None
    limiter = RateLimiter(1.0)
    platforms = PlatformTools(searx, ytdlp, cache, registry, seen_filter=seen_filter, on_items=on_items,
                              search_limiter=limiter, browser=browser,
                              youtube_api=youtube_api)
    return ToolStack(searx, ytdlp, registry, store, platforms, cache, limiter, browser=browser,
                     youtube_api=youtube_api)


def build_analyzer(settings: AppSettings, stack: ToolStack, runner: HeavyRunner, max_parallel: int = 2
                   ) -> VideoAnalyzer:
    return VideoAnalyzer(stack.ytdlp, media_dir=settings.media_dir, heavy_runner=runner, store=stack.store,
                         max_parallel=max_parallel)
