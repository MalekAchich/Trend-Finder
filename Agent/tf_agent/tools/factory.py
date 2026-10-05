"""Builds the discovery + analysis stack from settings (shared by the CLI and, in Plan 3, the orchestrator)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.config import AppSettings
from tf_agent.pipeline.analyze import HeavyRunner, VideoAnalyzer
from tf_agent.pipeline.retention import MediaRetention
from tf_agent.tools.cache import ToolCache
from tf_agent.tools.health import PlatformRegistry
from tf_agent.tools.platforms import ItemsSink, PlatformTools, SeenFilter
from tf_agent.tools.store import VideoStore
from tf_agent.tools.web import SearxClient
from tf_agent.tools.ytdlp import YtDlp


@dataclass
class ToolStack:
    searx: SearxClient
    ytdlp: YtDlp
    registry: PlatformRegistry
    store: VideoStore | None
    platforms: PlatformTools


def build_tool_stack(settings: AppSettings, sessionmaker: async_sessionmaker[AsyncSession] | None = None, *,
                     seen_filter: SeenFilter | None = None, on_items: ItemsSink | None = None) -> ToolStack:
    searx = SearxClient(settings.searxng_url)
    ytdlp = YtDlp(cookies_file=settings.instagram_cookies)
    registry = PlatformRegistry(sessionmaker)
    store = VideoStore(sessionmaker) if sessionmaker is not None else None
    if on_items is None and store is not None:
        on_items = store.upsert_videos
    platforms = PlatformTools(searx, ytdlp, ToolCache(sessionmaker) if sessionmaker is not None else None, registry,
                              seen_filter=seen_filter, on_items=on_items,
                              instagram_enrich=settings.instagram_cookies is not None)
    return ToolStack(searx, ytdlp, registry, store, platforms)


def build_analyzer(settings: AppSettings, stack: ToolStack, runner: HeavyRunner, max_parallel: int = 2,
                   protected=lambda: ()) -> VideoAnalyzer:
    retention = MediaRetention(settings.media_dir, int(settings.media_quota_gb * 1024**3), protected)
    return VideoAnalyzer(stack.ytdlp, media_dir=settings.media_dir, heavy_runner=runner, store=stack.store,
                         retention=retention, max_parallel=max_parallel)
