"""Plan 7 Task 3: best source first (account search, YouTube API), automatic fallback to web search."""
from tf_agent.credentials import Credentials
from tf_agent.tools.health import PlatformConfig, PlatformRegistry
from tf_agent.tools.platforms import PlatformTools
from tf_agent.tools.types import ToolFailure

from . import samples
from .test_platforms import NOW, FakeSearx, FakeYtDlp, MemCache, hits, item, tt


class FakeBrowser:
    def __init__(self, creds, captured=None, fail=None):
        self.creds, self.captured, self.fail, self.urls = creds, captured or [], fail, []

    async def capture_json(self, platform, url, pattern, need_session=False, blocked=None, **kw):
        self.urls.append(url)
        if self.fail:
            raise self.fail
        return self.captured


class FakeYouTubeApi:
    def __init__(self, items=(), fail=None, key="k"):
        self.items, self.fail, self.key, self.calls = list(items), fail, key, []

    def configured(self):
        return bool(self.key)

    async def shorts_search(self, q, n, published_after=None):
        self.calls.append((q, n, published_after))
        if self.fail:
            raise self.fail
        return self.items


def make(tmp_path, searx=None, browser_captured=None, browser_fail=None, connect=(), yt_api=None):
    creds = Credentials(tmp_path / "secrets")
    for p in connect:
        creds.save_session(p, {"cookies": [], "origins": []})
    cfg = {p: PlatformConfig(0.0) for p in ("tiktok", "tiktok_trends", "instagram", "youtube", "x", "web")}
    browser = FakeBrowser(creds, browser_captured, browser_fail)
    pt = PlatformTools(searx or FakeSearx(), FakeYtDlp(), MemCache(), PlatformRegistry(None, config=cfg),
                       clock=lambda: NOW.timestamp(), browser=browser, youtube_api=yt_api)
    return pt, browser


async def test_connected_tiktok_account_searches_tiktok_itself(tmp_path):
    pt, browser = make(tmp_path, browser_captured=samples.tiktok_search(), connect=["tiktok"])
    res = await pt.tiktok_search("deadpan office", 10)
    assert len(res.items) == 2 and "logged-in" in " ".join(res.notes)
    assert browser.urls == ["https://www.tiktok.com/search/video?q=deadpan%20office"]


async def test_captcha_or_expired_session_falls_back_to_web_search(tmp_path):
    q = "site:tiktok.com/@ deadpan"
    pt, _ = make(tmp_path, searx=FakeSearx({q: hits(tt(1))}), connect=["tiktok"],
                 browser_fail=ToolFailure("rate_limited", "tiktok: the site showed a captcha / bot check"))
    res = await pt.tiktok_search("deadpan", 5)
    assert [i.canonical_id for i in res.items] == ["tiktok:7000000000000000001"]
    assert "captcha" in res.notes[0] and "web search instead" in res.notes[0]


async def test_without_an_account_nothing_opens_a_browser(tmp_path):
    q = "site:tiktok.com/@ deadpan"
    pt, browser = make(tmp_path, searx=FakeSearx({q: hits(tt(1))}))
    await pt.tiktok_search("deadpan", 5)
    assert browser.urls == []


async def test_x_search_with_and_without_an_account(tmp_path):
    pt, browser = make(tmp_path, browser_captured=samples.x_search(), connect=["x"])
    res = await pt.x_search("meeting email", 10, recent="year")
    assert [i.canonical_id for i in res.items] == ["x:1000000000000000003"]
    assert "filter%3Anative_video" in browser.urls[0] and "since%3A" in browser.urls[0]
    q = "site:x.com meeting email video"
    pt2, _ = make(tmp_path / "b", searx=FakeSearx({q: hits("https://x.com/a/status/1000000000000000003")}))
    res2 = await pt2.x_search("meeting email", 10)
    assert [i.canonical_id for i in res2.items] == ["x:1000000000000000003"]
    assert any("without a connected account" in n for n in res2.notes)


async def test_instagram_account_unlocks_metadata_for_pasted_reels(tmp_path):
    pt, _ = make(tmp_path, connect=["instagram"])
    got = await pt.get_video("https://www.instagram.com/reel/TESTREEL001/")
    assert got.media_access == "ok"  # read through yt-dlp with the account's cookies, not a skeleton


async def test_youtube_api_first_then_the_old_route_on_failure(tmp_path):
    api = FakeYouTubeApi([item("youtube:AAAAAAAAAA1", platform="youtube")])
    pt, _ = make(tmp_path, yt_api=api)
    res = await pt.shorts_search("deadpan", 5, recent="week")
    assert [i.canonical_id for i in res.items] == ["youtube:AAAAAAAAAA1"] and "YouTube Data API" in res.notes[-1]
    assert api.calls[0][2] is not None  # freshness becomes publishedAfter
    failing = FakeYouTubeApi(fail=ToolFailure("rate_limited", "YouTube API quota used up"))
    pt2, _ = make(tmp_path / "b", yt_api=failing)
    res2 = await pt2.shorts_search("deadpan", 5)
    assert any("quota" in n for n in res2.notes)


async def test_instagram_health_follows_the_connected_account(tmp_path):
    pt, browser = make(tmp_path)
    assert pt.registry.health("instagram") == "degraded"  # discovery only
    browser.creds.save_session("instagram", {"cookies": [], "origins": []})
    pt.sync_modes()
    assert pt.registry.health("instagram") == "ok"


async def test_tiktok_trends_hashtags_and_videos(tmp_path):
    from tf_agent.tools.agent_tools import compact_trends

    pt, browser = make(tmp_path, browser_captured=samples.trend_hashtags())
    out = await pt.tiktok_trends("hashtags", "us", 30)
    assert [r["hashtag"] for r in out["hashtags"]][:1] == ["tagone"] and out["region"] == "US"
    assert browser.urls == ["https://ads.tiktok.com/creative/creativeCenter/trends/hashtag?region=US&period=30"]
    assert "#tagone | rank 1" in compact_trends(out) and "top few" in compact_trends(out)
    pt2, _ = make(tmp_path / "b", browser_captured=samples.trend_videos())
    res = await pt2.tiktok_trends("videos")
    assert len(res.items) == 4


async def test_trend_failures_never_pause_tiktok_search(tmp_path):
    import pytest

    pt, _ = make(tmp_path, browser_fail=ToolFailure("platform_unavailable", "tiktok: page didn't load"))
    for _ in range(6):
        with pytest.raises(ToolFailure):
            await pt.tiktok_trends("hashtags")
    assert pt.registry.health("tiktok_trends") == "unavailable" and pt.registry.health("tiktok") == "ok"



async def test_an_empty_api_answer_falls_back_to_the_search_page(tmp_path):
    from .test_platforms import item as mk

    pt, _ = make(tmp_path, yt_api=FakeYouTubeApi([]))
    pt.ytdlp.yt = [mk("youtube:BBBBBBBBBB1", platform="youtube")]
    res = await pt.shorts_search("rare words", 5)
    assert [i.canonical_id for i in res.items] == ["youtube:BBBBBBBBBB1"] and "found nothing" in res.notes[0]
