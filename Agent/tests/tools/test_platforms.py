from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tf_agent.tools.agent_tools import SearchParams, build_tools, compact_discovery
from tf_agent.tools.health import PlatformConfig, PlatformRegistry
from tf_agent.tools.platforms import PlatformTools
from tf_agent.tools.types import Creator, Metrics, ToolFailure, VideoItem
from tf_agent.tools.web import SearchHit, SearchResponse

NOW = datetime(2026, 10, 5, tzinfo=UTC)


def tt(i):
    return f"https://www.tiktok.com/@user{i}/video/{7000000000000000000 + i}"


def url_for(cid):
    platform, vid = cid.split(":", 1)
    return {"tiktok": f"https://www.tiktok.com/@u/video/{vid}", "youtube": f"https://www.youtube.com/shorts/{vid}",
            "instagram": f"https://www.instagram.com/reel/{vid}/"}[platform]


def item(cid, views=1000, platform="tiktok"):
    return VideoItem(canonical_id=cid, platform=platform, url=url_for(cid), creator=Creator(handle="u"),
                     caption="deadpan", posted_at=NOW - timedelta(hours=5), duration_s=9.0,
                     metrics=Metrics(views=views), media_access="ok")


class FakeSearx:
    def __init__(self, hits=None, fail=None):
        self.hits, self.fail, self.queries = hits or {}, fail, []

    async def search(self, query, max_results=10, time_range=None):
        self.queries.append(query)
        if self.fail:
            raise self.fail
        return SearchResponse(results=self.hits.get(query, [])[:max_results])


class FakeYtDlp:
    def __init__(self, fail_ids=(), yt=()):
        self.calls, self.fail_ids, self.yt = [], set(fail_ids), list(yt)

    async def metadata(self, url):
        self.calls.append(url)
        from tf_agent.tools.normalize import canonical_id
        cid = canonical_id(url)
        if cid in self.fail_ids:
            raise ToolFailure("not_found", "gone")
        return item(cid, platform=cid.split(":")[0])

    async def search_youtube(self, query, n=10):
        return list(self.yt)


class MemCache:
    def __init__(self):
        self.data = {}

    async def get(self, tool, key):
        return self.data.get((tool, repr(sorted(key.items()) if isinstance(key, dict) else key)))

    async def put(self, tool, key, response, ttl_s):
        self.data[(tool, repr(sorted(key.items()) if isinstance(key, dict) else key))] = response


def registry():
    cfg = {p: PlatformConfig(0.0) for p in ("tiktok", "instagram", "youtube", "web")}
    return PlatformRegistry(None, config=cfg, failure_threshold=2)


def tools(searx, ytdlp=None, **kw):
    return PlatformTools(searx, ytdlp or FakeYtDlp(), MemCache(), registry(), clock=lambda: NOW.timestamp(), **kw)


def hits(*urls):
    return [SearchHit(title=f"t{i}", url=u, snippet=f"snippet {i} #deadpan") for i, u in enumerate(urls)]


async def test_tiktok_search_dedupes_url_shapes_and_enriches():
    q = "site:tiktok.com/@ deadpan dance"
    searx = FakeSearx({q: hits(tt(1) + "?is_from_webapp=1", tt(1), "https://www.tiktok.com/@user9", tt(2))})
    yt = FakeYtDlp()
    res = await tools(searx, yt).tiktok_search("Deadpan  dance", max_results=10)
    assert [i.canonical_id for i in res.items] == ["tiktok:7000000000000000001", "tiktok:7000000000000000002"]
    assert len(yt.calls) == 2 and all(i.metrics.views == 1000 for i in res.items)
    assert res.platform_health == "ok"


async def test_seen_filter_hides_items_before_enrichment():
    q = "site:tiktok.com/@ deadpan"
    yt = FakeYtDlp()

    async def seen(items):
        kept = [i for i in items if not i.canonical_id.endswith("1")]
        return kept, len(items) - len(kept)

    res = await tools(FakeSearx({q: hits(tt(1), tt(2))}), yt, seen_filter=seen).tiktok_search("deadpan")
    assert [i.canonical_id for i in res.items] == ["tiktok:7000000000000000002"]
    assert res.hidden_already_seen == 1 and len(yt.calls) == 1


async def test_enrichment_failure_falls_back_to_snippet():
    q = "site:tiktok.com/@ deadpan"
    res = await tools(FakeSearx({q: hits(tt(1), tt(2))}), FakeYtDlp(fail_ids={"tiktok:7000000000000000002"})
                      ).tiktok_search("deadpan")
    fallback = res.items[1]
    assert fallback.source == "searxng" and fallback.metrics.views is None and "snippet 1" in fallback.caption
    assert any("could not be enriched" in n for n in res.notes)


async def test_instagram_is_discovery_only_without_login():
    q = "site:instagram.com/reel deadpan"
    yt = FakeYtDlp()
    pt = tools(FakeSearx({q: hits("https://www.instagram.com/reel/DTfu8CIDezV/?igsh=1")}), yt)
    res = await pt.instagram_search("deadpan")
    assert res.items[0].canonical_id == "instagram:DTfu8CIDezV"
    assert res.items[0].media_access == "login_required" and res.items[0].metrics.views is None
    assert yt.calls == [] and res.platform_health == "degraded"
    assert any("discovery only" in n for n in res.notes)


async def test_per_video_metadata_is_cached_across_searches():
    yt = FakeYtDlp()
    searx = FakeSearx({"site:tiktok.com/@ a": hits(tt(1)), "site:tiktok.com/@ b": hits(tt(1))})
    pt = tools(searx, yt)
    await pt.tiktok_search("a")
    await pt.tiktok_search("b")
    assert len(yt.calls) == 1


async def test_search_failures_trip_the_platform_breaker():
    pt = tools(FakeSearx(fail=ToolFailure("platform_unavailable", "engines down")))
    for _ in range(2):
        with pytest.raises(ToolFailure):
            await pt.tiktok_search("deadpan")
    with pytest.raises(ToolFailure) as ei:
        await pt.tiktok_search("deadpan")
    assert ei.value.error.code == "platform_unavailable" and "circuit open" in ei.value.error.message


async def test_shorts_merges_ytsearch_and_searx_without_duplicates():
    yt_items = [item("youtube:aaaaaaaaaaa", platform="youtube")]
    searx = FakeSearx({"site:youtube.com/shorts deadpan": hits("https://www.youtube.com/shorts/aaaaaaaaaaa",
                                                                "https://youtu.be/bbbbbbbbbbb")})
    res = await tools(searx, FakeYtDlp(yt=yt_items)).shorts_search("deadpan")
    assert [i.canonical_id for i in res.items] == ["youtube:aaaaaaaaaaa", "youtube:bbbbbbbbbbb"]


async def test_on_items_receives_enriched_items():
    got = []

    async def on_items(items):
        got.extend(i.canonical_id for i in items)

    await tools(FakeSearx({"site:tiktok.com/@ x": hits(tt(3))}), on_items=on_items).tiktok_search("x")
    assert got == ["tiktok:7000000000000000003"]


async def test_get_video_for_instagram_and_tiktok():
    pt = tools(FakeSearx())
    ig = await pt.get_video("https://www.instagram.com/reel/DTfu8CIDezV/")
    assert ig.media_access == "login_required"
    t = await pt.get_video(tt(4))
    assert t.canonical_id == "tiktok:7000000000000000004" and t.metrics.views == 1000


def test_compact_output_is_bounded():
    from tf_agent.tools.platforms import DiscoveryResult

    res = DiscoveryResult(items=[item(f"tiktok:{i}", views=1_234_567) for i in range(20)], hidden_already_seen=3,
                          platform_health="ok", notes=["note"])
    text = compact_discovery(res.to_dict(), now=NOW)
    lines = text.splitlines()
    assert len(lines) <= 14 and "hidden_already_seen=3" in lines[0] and "1.2M views" in lines[1]
    assert "+8 more" in text and len(text) < 2500


def test_search_params_are_validated():
    with pytest.raises(ValidationError):
        SearchParams(query="x", max_results=99)


async def test_tool_wrapper_returns_structured_errors():
    pt = tools(FakeSearx(fail=ToolFailure("rate_limited", "slow", retry_after_s=30)))
    tool = next(t for t in build_tools(pt, FakeSearx()) if t.name == "tiktok_search")
    out = await tool.handler(SearchParams(query="deadpan"))
    assert out == {"error": {"code": "rate_limited", "message": "slow", "retry_after_s": 30.0}}
    assert tool.compact(out).startswith("ERROR rate_limited")


async def test_recency_filter_reaches_the_search_engine():
    seen = []

    class RecordingSearx(FakeSearx):
        async def search(self, query, max_results=10, time_range=None):
            seen.append(time_range)
            return SearchResponse(results=hits(tt(5)))

    pt = tools(RecordingSearx())
    tool = next(t for t in build_tools(pt, RecordingSearx()) if t.name == "tiktok_search")
    await tool.handler(SearchParams(query="deadpan", recent="week"))
    await pt.shorts_search("deadpan", recent="month")
    assert seen == ["week", "month"]


def test_tool_stack_gives_platform_specific_tools():
    from tf_agent.config import AppSettings
    from tf_agent.tools.factory import build_tool_stack

    stack = build_tool_stack(AppSettings())
    names = {t.name for t in stack.tools_for("youtube")}
    assert names == {"shorts_search", "get_video", "web_search", "web_fetch"}
    a, b = stack.platform_tools(), stack.platform_tools()
    assert a.search_limiter is b.search_limiter and a.registry is b.registry
