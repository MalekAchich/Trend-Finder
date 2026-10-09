"""Builds the production AppContext: both subscriptions, tools, lazily-started orchestrator + process pool."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from tf_agent.config import AppSettings
from tf_agent.curation.curate import Curator
from tf_agent.learning.learner import Learner
from tf_agent.manual import ManualVideos
from tf_agent.orchestrator.run import Orchestrator
from tf_agent.pipeline.analyze import process_pool_runner
from tf_agent.roles.runners import Roles
from tf_agent.tools.factory import build_analyzer, build_tool_stack
from tf_backend.app_context import AppContext
from tf_agent.credentials import Credentials
from tf_agent.manual import _http_image
from tf_agent.socials.instagram_api import InstagramApi
from tf_agent.socials.public import PublicReader
from tf_agent.socials.sync import SocialSync
from tf_agent.socials.tiktok_api import TikTokApi
from tf_agent.socials.youtube import YouTubeChannel
from tf_backend.downloads import Downloads
from tf_backend.socials import Socials
from tf_backend.previews import Previews
from tf_backend.runs import RunManager
from tf_backend.services import Services, build_services, close_services


async def build_context(settings: AppSettings | None = None) -> tuple[AppContext, Services, Callable[[], Awaitable[None]]]:
    s = settings or AppSettings()
    services = await build_services(s)
    assert services.sessionmaker is not None
    sm = services.sessionmaker
    roles = Roles(services.client)
    stack = build_tool_stack(s, sm)
    pools: list[process_pool_runner] = []
    learner = Learner(sm, roles)

    def make_orchestrator() -> Orchestrator:  # built on first run: the process pool only starts when needed
        heavy = process_pool_runner(max_workers=2, whisper_model=s.whisper_model)
        pools.append(heavy)
        analyzer = build_analyzer(s, stack, heavy, max_parallel=2)
        return Orchestrator(sm, roles, stack, analyzer, stack.store, curator=Curator(sm, roles), learner=learner)

    runs = RunManager(sm, make_orchestrator)
    context = AppContext(sessionmaker=sm, runs=runs, learner=learner,
                         media_dir=s.media_dir, characters_dir=s.characters_dir,
                         sse_poll_s=1.0, browser=stack.browser, youtube_api=stack.youtube_api,
                         manual=ManualVideos(sm, get_video=stack.get_video, thumbs_dir=s.media_dir / "thumbs"),
                         previews=Previews(stack.ytdlp.stream_url),
                         downloads=Downloads(stack.ytdlp.save, s.media_dir / "downloads"))
    creds, instagram, tiktok = Credentials(s.secrets_dir), InstagramApi(), TikTokApi()
    youtube = YouTubeChannel(lambda: creds.get("youtube_api_key"))
    context.socials = Socials(sm, SocialSync(sm, creds, instagram, tiktok, PublicReader(stack.browser, youtube),
                                             youtube=youtube), creds,
                              instagram, tiktok, thumbs_dir=s.media_dir / "thumbs", fetch_image=_http_image,
                              get_meta=stack.ytdlp.metadata, youtube=youtube)
    context.socials.start()  # every 3 hours, the first a minute after start

    async def cleanup() -> None:
        await context.socials.stop()
        await runs.shutdown()
        for pool in pools:
            pool.close()
        if stack.browser is not None:
            await stack.browser.close()
        if context.previews is not None:
            await context.previews.close()
        await close_services(services)

    return context, services, cleanup
