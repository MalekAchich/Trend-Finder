"""Plan 7 Task 3: logged-in search results read from the JSON each site fetches (tolerant walkers)."""
import json
from datetime import UTC, datetime
from pathlib import Path

from tf_agent.tools.session_search import instagram_items, is_captcha, tiktok_items, x_items

FX = Path(__file__).parent.parent / "fixtures" / "sessions"


def load(name):
    return [json.loads((FX / f"{name}.json").read_text())]


def test_tiktok_items():
    items = tiktok_items(load("tiktok_search"))
    assert [i.canonical_id for i in items] == ["tiktok:7560000000000000001", "tiktok:7560000000000000002"]
    a = items[0]
    assert a.url == "https://www.tiktok.com/@stiff.guy/video/7560000000000000001"
    assert (a.metrics.views, a.metrics.likes, a.metrics.saves, a.creator.followers) == (2_300_000, 210_000, 12_000, 120_000)
    assert a.hashtags == ["deadpan", "office"] and a.duration_s == 14 and a.sound.title == "original sound"
    assert a.posted_at == datetime.fromtimestamp(1791200000, UTC) and a.media_access == "ok"
    assert items[1].metrics.views == 5000  # statsV2 numbers come as strings


def test_instagram_items_keep_only_reels():
    items = instagram_items(load("instagram_search"))
    assert [i.canonical_id for i in items] == ["instagram:DXyz12345ab"]
    r = items[0]
    assert r.url == "https://www.instagram.com/reel/DXyz12345ab/" and r.creator.handle == "dry.humor"
    assert (r.metrics.views, r.metrics.likes, r.duration_s) == (880_000, 41_000, 21.5) and r.hashtags == ["deadpan"]


def test_x_items_keep_only_video_posts():
    items = x_items(load("x_search"))
    assert [i.canonical_id for i in items] == ["x:1843213434565656789"]
    t = items[0]
    assert t.url == "https://x.com/i/status/1843213434565656789" and t.creator.handle == "deadpandan"
    assert (t.metrics.views, t.metrics.likes, t.metrics.shares, t.metrics.saves) == (450_000, 12_000, 2100, 800)
    assert t.duration_s == 17 and t.posted_at == datetime(2026, 10, 5, 12, tzinfo=UTC) and t.hashtags == ["office"]


def test_walkers_ignore_junk_and_duplicates():
    junk = [{"a": [1, "x", None]}, "text", None]
    assert tiktok_items(junk) == [] and instagram_items(junk) == [] and x_items(junk) == []
    twice = load("tiktok_search") * 2
    assert len(tiktok_items(twice)) == 2


def test_captcha_pages_are_recognised():
    assert is_captcha("Log in | Drag the slider to fit the puzzle | Audio", "https://www.tiktok.com/search?q=x")
    assert is_captcha("", "https://www.instagram.com/challenge/?next=/")
    assert not is_captcha("For You | Following", "https://www.tiktok.com/search/video?q=x")
