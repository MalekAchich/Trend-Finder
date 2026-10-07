"""Logged-in search results read from the JSON each site fetches (tolerant walkers), on made-up samples."""
from datetime import UTC, datetime

from tf_agent.tools.session_search import (
    instagram_items,
    is_captcha,
    organic_variants,
    tiktok_items,
    trend_hashtags,
    trend_videos,
    trends_page_url,
    x_items,
)

from . import samples
from .samples import T0


def test_tiktok_items():
    items = tiktok_items(samples.tiktok_search())
    assert [i.canonical_id for i in items] == ["tiktok:1000000000000000001", "tiktok:1000000000000000002"]
    a = items[0]
    assert a.url == "https://www.tiktok.com/@creator_a/video/1000000000000000001"
    assert (a.metrics.views, a.metrics.likes, a.metrics.saves, a.creator.followers) == (5000, 400, 10, 1200)
    assert a.hashtags == ["tagone", "tagtwo"] and a.duration_s == 14 and a.sound.title == "test sound"
    assert a.posted_at == datetime.fromtimestamp(T0, UTC) and a.media_access == "ok"
    assert items[1].metrics.views == 700  # statsV2 numbers come as strings


def test_instagram_items_keep_only_reels():
    items = instagram_items(samples.instagram_search())
    assert [i.canonical_id for i in items] == ["instagram:TESTREEL001"]
    r = items[0]
    assert r.url == "https://www.instagram.com/reel/TESTREEL001/" and r.creator.handle == "creator_c"
    assert (r.metrics.views, r.metrics.likes, r.duration_s) == (8000, 600, 21.5) and r.hashtags == ["tagone"]


def test_x_items_keep_only_video_posts():
    items = x_items(samples.x_search())
    assert [i.canonical_id for i in items] == ["x:1000000000000000003"]
    t = items[0]
    assert t.url == "https://x.com/i/status/1000000000000000003" and t.creator.handle == "creator_d"
    assert (t.metrics.views, t.metrics.likes, t.metrics.shares, t.metrics.saves) == (9000, 300, 45, 8)
    assert t.duration_s == 17 and t.posted_at == datetime(2026, 10, 5, 12, tzinfo=UTC) and t.hashtags == ["tagone"]


def test_walkers_ignore_junk_and_duplicates():
    junk = [{"a": [1, "x", None]}, "text", None]
    assert tiktok_items(junk) == [] and instagram_items(junk) == [] and x_items(junk) == []
    assert len(tiktok_items(samples.tiktok_search() * 2)) == 2


def test_captcha_pages_are_recognised():
    assert is_captcha("Log in | Drag the slider to fit the puzzle | Audio", "https://www.tiktok.com/search?q=x")
    assert is_captcha("", "https://www.instagram.com/challenge/?next=/")
    assert not is_captcha("For You | Following", "https://www.tiktok.com/search/video?q=x")


def test_trend_hashtags_with_their_curve():
    rows = trend_hashtags(samples.trend_hashtags())
    assert [r["hashtag"] for r in rows] == ["tagone", "tagtwo", "tagthree"]
    assert (rows[0]["rank"], rows[0]["posts"], rows[0]["views"]) == (1, 5000, 900_000)
    assert [r["direction"] for r in rows] == ["peaked", "rising", "steady"]


def test_trend_videos_count_organic_views_and_drop_paid_or_branded_ones():
    assert trend_videos(samples.trend_videos(organic_share=0.05)) == []  # mostly paid reach
    assert trend_videos(samples.trend_videos(title="#ad try our new product")) == []  # branded content
    items = trend_videos(samples.trend_videos())
    assert items[0].canonical_id == "tiktok:1000000000000000011"
    assert items[0].url == "https://www.tiktok.com/@creator_1/video/1000000000000000011"
    assert items[0].metrics.views == 400_000 and items[0].creator.followers == 2000


def test_trend_urls_and_organic_variants():
    assert trends_page_url("videos", "gb", 30) == \
        "https://ads.tiktok.com/creative/creativeCenter/trends/video?region=GB&period=30"
    api = "https://api.invalid/CreativeCenterGetTopContentsList?countryCode=US&limit=20&orderByMetric=1&organicOnly=false"
    out = organic_variants([api, "https://api.invalid/GetHashtagList"])
    assert len(out) == 2 and all("organicOnly=true" in u for u in out)
    assert {u.split("orderByMetric=")[1][0] for u in out} == {"1", "2"}
