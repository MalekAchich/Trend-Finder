"""Regression tests for the Plan 2 final review (tool layer)."""
import asyncio
import threading
import time

import pytest
from pydantic import ValidationError

from tf_agent.tools.health import PlatformConfig, PlatformRegistry
from tf_agent.tools.limiter import RateLimiter
from tf_agent.tools.normalize import canonical_id, canonical_url
from tf_agent.tools.platforms import PlatformTools
from tf_agent.tools.store import VideoStore
from tf_agent.tools.types import ToolFailure, VideoItem
from tf_agent.tools.web import SearchHit, SearchResponse
from tf_agent.tools.ytdlp import YtDlp, info_to_video_item


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def reg(clock, threshold=1):
    return PlatformRegistry(None, config={p: PlatformConfig(0.0) for p in ("tiktok", "instagram", "youtube", "web")},
                            clock=clock, failure_threshold=threshold, open_s=10)


class Searx:
    def __init__(self, hits=(), unresponsive=(), fail=None):
        self.hits, self.unresponsive, self.fail, self.calls = list(hits), list(unresponsive), fail, 0

    async def search(self, query, max_results=10, time_range=None):
        self.calls += 1
        if self.fail:
            raise self.fail
        return SearchResponse(results=self.hits[:max_results], unresponsive=self.unresponsive)


class Meta:
    def __init__(self, error=None, block=None):
        self.error, self.block = error, block

    async def metadata(self, url):
        if self.block:
            await self.block.wait()
        if self.error:
            raise self.error
        return VideoItem(canonical_id=canonical_id(url), platform="tiktok", url=url, media_access="ok")

    async def search_youtube(self, query, n=10):
        return []


TT = "https://www.tiktok.com/@u/video/7000000000000000001"


# ---- C1: half-open probe is always released ----
async def test_probe_released_after_non_counted_failure():
    clock = Clock()
    pt = PlatformTools(Searx(hits=[SearchHit("t", TT, "s")]), Meta(error=ToolFailure("not_found", "gone")), None,
                       reg(clock))
    await pt.registry.record_failure("tiktok", "boom")
    assert pt.registry.health("tiktok") == "unavailable"
    clock.t += 10
    with pytest.raises(ToolFailure):
        await pt.get_video(TT)  # half-open probe answered "not found": the platform is alive
    assert pt.registry.health("tiktok") == "ok" and pt.registry.allow("tiktok")


async def test_probe_released_on_cancellation():
    clock = Clock()
    gate = asyncio.Event()
    pt = PlatformTools(Searx(), Meta(block=gate), None, reg(clock))
    await pt.registry.record_failure("tiktok", "boom")
    clock.t += 10
    task = asyncio.create_task(pt.get_video(TT))
    await asyncio.sleep(0.01)
    assert pt.registry.health("tiktok") == "unavailable"  # probe in flight: not usable yet
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pt.registry.allow("tiktok")  # a new probe may go


# ---- I1: yt-dlp thread count stays bounded even when calls time out ----
async def test_ytdlp_threads_bounded_after_timeouts():
    active = peak = 0
    lock = threading.Lock()

    def slow(url, opts, download):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.3)
        with lock:
            active -= 1
        return {}

    y = YtDlp(timeout_s=0.05, max_parallel=2, extractor=slow)
    results = await asyncio.gather(*(y.metadata(TT) for _ in range(8)), return_exceptions=True)
    assert all(isinstance(r, ToolFailure) for r in results)
    await asyncio.sleep(0.8)
    assert peak <= 2


def test_ytdlp_restricts_extractors():
    seen = {}

    def capture(url, opts, download):
        seen.update(opts)
        raise RuntimeError("stop")

    with pytest.raises(ToolFailure):
        asyncio.run(YtDlp(extractor=capture).metadata(TT))
    assert seen["allowed_extractors"] and all("generic" not in p for p in seen["allowed_extractors"])
    assert seen["noprogress"] is True  # progress bars must never leak into CLI/API output


# ---- I4: concurrent upserts never deadlock; sink failures never lose a search ----
async def test_concurrent_upserts_do_not_deadlock(db_sessionmaker):
    store = VideoStore(db_sessionmaker)
    items = [VideoItem(canonical_id=f"tiktok:{i}", platform="tiktok", url="u") for i in range(40)]
    await asyncio.gather(*(store.upsert_videos(items if k % 2 else list(reversed(items))) for k in range(10)))


async def test_items_sink_failure_becomes_a_note():
    async def broken_sink(items):
        raise RuntimeError("db down")

    pt = PlatformTools(Searx(hits=[SearchHit("t", TT, "s")]), Meta(), None, reg(Clock()), on_items=broken_sink)
    res = await pt.tiktok_search("deadpan")
    assert len(res.items) == 1 and any("not saved" in n for n in res.notes)


# ---- I5: one login wall is per-item; repeated walls flip the platform; success resets ----
async def test_login_walls_flip_platform_only_when_repeated():
    pt = PlatformTools(Searx(), Meta(error=ToolFailure("login_required", "age gate")), None, reg(Clock(), 5))
    with pytest.raises(ToolFailure):
        await pt.get_video(TT)
    assert pt.registry.health("tiktok") == "ok"
    for _ in range(2):
        with pytest.raises(ToolFailure):
            await pt.get_video(TT)
    assert pt.registry.health("tiktok") == "needs_login"
    pt.ytdlp.error = None
    await pt.get_video(TT)
    assert pt.registry.health("tiktok") == "ok"


# ---- I6: one shared SearXNG limiter; degraded engines are reported and never cached ----
class MemCache:
    def __init__(self):
        self.data = {}

    async def get(self, tool, key):
        return self.data.get((tool, repr(key)))

    async def put(self, tool, key, response, ttl_s):
        self.data[(tool, repr(key))] = response


class CountingLimiter(RateLimiter):
    def __init__(self):
        super().__init__(0.0)
        self.count = 0

    async def acquire(self):
        self.count += 1


async def test_searx_shared_limiter_degraded_note_and_no_caching():
    searx = Searx(hits=[SearchHit("t", TT, "s")], unresponsive=["brave"])
    limiter = CountingLimiter()
    pt = PlatformTools(searx, Meta(), MemCache(), reg(Clock()), search_limiter=limiter)
    first = await pt.tiktok_search("deadpan")
    await pt.tiktok_search("deadpan")
    await pt.web_search("deadpan trend")
    assert searx.calls == 3 and limiter.count == 3
    assert first.platform_health == "degraded" and any("engines" in n for n in first.notes)


async def test_empty_results_are_not_cached():
    searx = Searx(hits=[])
    pt = PlatformTools(searx, Meta(), MemCache(), reg(Clock()))
    await pt.tiktok_search("nothing")
    await pt.tiktok_search("nothing")
    assert searx.calls == 2


# ---- I7: a stable sound key from track + artist ----
@pytest.mark.parametrize("track,artists,expected", [
    ("The Dead Dance", ["Lady Gaga"], "track:the dead dance|lady gaga"),
    ("original sound - mumii.cos", None, "track:original sound - mumii.cos|"),
    ("original sound", None, None),
    ("", None, None),
])
def test_sound_key(track, artists, expected):
    info = {"extractor_key": "TikTok", "webpage_url": TT, "id": "7000000000000000001", "track": track,
            "artists": artists}
    assert info_to_video_item(info).sound.id == expected


# ---- I8 / I9: canonical IDs and URLs ----
@pytest.mark.parametrize("url", ["https://www.instagram.com/reels/audio/1234567890123/",
                                 "https://www.instagram.com/explore/tags/deadpan/",
                                 "https://www.instagram.com/reel/abc/"])
def test_instagram_non_media_paths_are_rejected(url):
    assert canonical_id(url) is None


def test_canonical_url_rebuilds_platform_urls():
    assert canonical_url("youtube:OUZbZ8cz4j8") == "https://www.youtube.com/shorts/OUZbZ8cz4j8"
    assert canonical_url("instagram:DTfu8CIDezV") == "https://www.instagram.com/reel/DTfu8CIDezV/"


async def test_skeleton_keeps_youtube_watch_ids():
    hit = SearchHit("t", "https://www.youtube.com/watch?v=OUZbZ8cz4j8&t=3", "s")
    pt = PlatformTools(Searx(hits=[hit]), Meta(), None, reg(Clock()))
    res = await pt._site_search("youtube", "q", 5, enrich=False)
    assert res.items[0].url == "https://www.youtube.com/shorts/OUZbZ8cz4j8"


# ---- I12: canonical IDs are validated ----
@pytest.mark.parametrize("cid", ["tiktok:/../../etc", "myspace:1", "tiktok:", "youtube:a b"])
def test_invalid_canonical_ids_are_rejected(cid):
    with pytest.raises(ValidationError):
        VideoItem(canonical_id=cid, platform="tiktok", url="u")


async def test_tiktok_query_targets_video_urls_and_skips_tag_pages():
    tags = [SearchHit("t", f"https://www.tiktok.com/tag/x{i}", "s") for i in range(6)]
    vids = [SearchHit("v", f"https://www.tiktok.com/@u/video/700000000000000000{i}", "s") for i in range(2)]
    searx = Searx(hits=tags + vids)
    seen_queries = []
    orig = searx.search

    async def record(query, max_results=10, time_range=None):
        seen_queries.append(query)
        return await orig(query, max_results, time_range)

    searx.search = record
    res = await PlatformTools(searx, Meta(), None, reg(Clock())).tiktok_search("deadpan", max_results=3)
    assert seen_queries == ["site:tiktok.com/@ deadpan"]
    assert len(res.items) == 2
