from datetime import UTC, datetime, timedelta

import pytest

from tf_agent.tools.normalize import canonical_id, norm_handle, norm_hashtag, norm_query, platform_of
from tf_agent.tools.types import Metrics, ToolFailure, VideoItem

TT = "7567775043346779423"
IG = "DTfu8CIDezV"
YT = "OUZbZ8cz4j8"


@pytest.mark.parametrize("url,expected", [
    (f"https://www.tiktok.com/@kianalede/video/{TT}", f"tiktok:{TT}"),
    (f"https://www.tiktok.com/@kianalede/video/{TT}?is_from_webapp=1&sender_device=pc", f"tiktok:{TT}"),
    (f"https://m.tiktok.com/v/{TT}.html", f"tiktok:{TT}"),
    ("https://vm.tiktok.com/ZMabc123/", None),
    ("https://www.tiktok.com/@kianalede", None),
    (f"https://www.instagram.com/reel/{IG}/", f"instagram:{IG}"),
    (f"https://www.instagram.com/reels/{IG}/?igsh=x", f"instagram:{IG}"),
    (f"https://www.instagram.com/p/{IG}/", f"instagram:{IG}"),
    (f"https://www.instagram.com/someuser/reel/{IG}/", f"instagram:{IG}"),
    (f"https://www.youtube.com/shorts/{YT}", f"youtube:{YT}"),
    (f"https://youtube.com/shorts/{YT}?feature=share", f"youtube:{YT}"),
    (f"https://m.youtube.com/shorts/{YT}", f"youtube:{YT}"),
    (f"https://www.youtube.com/watch?v={YT}&t=3", f"youtube:{YT}"),
    (f"https://youtu.be/{YT}?si=abc", f"youtube:{YT}"),
    ("https://example.com/video/1", None),
    ("not a url", None),
])
def test_canonical_id(url, expected):
    assert canonical_id(url) == expected


def test_platform_of():
    assert platform_of("https://vm.tiktok.com/ZMabc/") == "tiktok"
    assert platform_of(f"https://www.instagram.com/reel/{IG}/") == "instagram"
    assert platform_of(f"https://youtu.be/{YT}") == "youtube"
    assert platform_of("https://example.com") is None


def test_norms():
    assert norm_hashtag(" #DeadPan ") == "deadpan"
    assert norm_handle("@Kiana.Lede") == "kiana.lede"
    assert norm_query("  Deadpan   DANCE ") == "deadpan dance"


def test_video_item_rates():
    now = datetime(2026, 10, 5, tzinfo=UTC)
    v = VideoItem(canonical_id=f"tiktok:{TT}", platform="tiktok", url="u", posted_at=now - timedelta(hours=10),
                  metrics=Metrics(views=1000, likes=100, comments=10, shares=5, saves=5))
    assert v.age_hours(now) == pytest.approx(10)
    assert v.views_per_hour(now) == pytest.approx(100)
    assert v.engagement_rate() == pytest.approx(0.12)
    blank = VideoItem(canonical_id="youtube:x", platform="youtube", url="u")
    assert blank.views_per_hour(now) is None and blank.engagement_rate() is None
    assert blank.media_access == "unknown"


def test_tool_failure_carries_error():
    f = ToolFailure("rate_limited", "slow down", retry_after_s=30)
    assert f.error.model_dump() == {"code": "rate_limited", "message": "slow down", "retry_after_s": 30.0}
