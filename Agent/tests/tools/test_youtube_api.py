"""Plan 7 Task 3: Shorts search through the YouTube Data API, with a hard daily quota."""
import json
from datetime import UTC, datetime

import httpx
import pytest

from tf_agent.tools.types import ToolFailure
from tf_agent.tools.youtube_api import MemoryQuota, YouTubeApi, iso_duration_s

SEARCH = {"items": [{"id": {"videoId": "AAAAAAAAAA1"}}, {"id": {"videoId": "AAAAAAAAAA2"}}]}
VIDEOS = {"items": [
    {"id": "AAAAAAAAAA1", "snippet": {"title": "Deadpan office dance #dance #office", "channelTitle": "Some Guy",
                                      "description": "When the boss walks in #deadpan", "publishedAt":
                                      "2026-10-05T10:00:00Z", "thumbnails": {"high": {"url": "https://i.ytimg.com/1.jpg"}}},
     "statistics": {"viewCount": "1200000", "likeCount": "54000", "commentCount": "800"},
     "contentDetails": {"duration": "PT34S"}},
    {"id": "AAAAAAAAAA2", "snippet": {"title": "Long one", "channelTitle": "X", "publishedAt": "2026-10-05T10:00:00Z"},
     "statistics": {"viewCount": "10"}, "contentDetails": {"duration": "PT3M30S"}},
]}


def api(handler, quota=None, key="AIza-key"):
    transport = httpx.MockTransport(handler)
    return YouTubeApi(lambda: key, quota or MemoryQuota(), client=lambda: httpx.AsyncClient(transport=transport))


def test_iso_durations():
    assert iso_duration_s("PT34S") == 34 and iso_duration_s("PT3M30S") == 210 and iso_duration_s("PT1H") == 3600
    assert iso_duration_s("P0D") == 0 and iso_duration_s("bad") is None


async def test_search_returns_shorts_with_full_stats():
    seen = []

    def handler(r):
        seen.append(r.url)
        assert r.headers["x-goog-api-key"] == "AIza-key" and "key" not in r.url.params
        return httpx.Response(200, json=SEARCH if r.url.path.endswith("/search") else VIDEOS)

    quota = MemoryQuota()
    items = await api(handler, quota).shorts_search("deadpan dance", 10,
                                                    published_after=datetime(2026, 10, 1, tzinfo=UTC))
    assert [i.canonical_id for i in items] == ["youtube:AAAAAAAAAA1"]  # 3m30s is not a Short
    it = items[0]
    assert it.url == "https://www.youtube.com/shorts/AAAAAAAAAA1" and it.source == "api" and it.duration_s == 34
    assert (it.metrics.views, it.metrics.likes, it.metrics.comments) == (1_200_000, 54_000, 800)
    assert set(it.hashtags) == {"dance", "office", "deadpan"} and it.creator.handle == "some guy"
    q = seen[0].params
    assert q["videoDuration"] == "short" and q["type"] == "video" and q["publishedAfter"] == "2026-10-01T00:00:00Z"
    assert quota.used == 101


async def test_quota_is_refused_before_spending():
    quota = MemoryQuota(limit=150, used=100)
    with pytest.raises(ToolFailure) as ei:
        await api(lambda r: httpx.Response(200, json={}), quota).shorts_search("x", 5)
    assert ei.value.error.code == "rate_limited" and "quota" in ei.value.error.message


async def test_rejected_key_and_server_quota_are_clear():
    bad = {"error": {"code": 400, "errors": [{"reason": "badRequest"}], "status": "INVALID_ARGUMENT",
                     "message": "API key not valid. Please pass a valid API key.",
                     "details": [{"reason": "API_KEY_INVALID"}]}}
    with pytest.raises(ToolFailure) as ei:
        await api(lambda r: httpx.Response(400, json=bad)).shorts_search("x", 5)
    assert ei.value.error.code == "login_required" and "Settings" in ei.value.error.message
    out = {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}], "message": "quota"}}
    with pytest.raises(ToolFailure) as ei:
        await api(lambda r: httpx.Response(403, json=out)).shorts_search("x", 5)
    assert ei.value.error.code == "rate_limited"


async def test_no_key_means_not_configured():
    with pytest.raises(ToolFailure) as ei:
        await api(lambda r: httpx.Response(200, json={}), key=None).shorts_search("x", 5)
    assert ei.value.error.code == "login_required"
    assert "AIza" not in json.dumps(ei.value.error.model_dump())


async def test_db_quota_survives_restarts_and_refuses_overspend(db_sessionmaker):
    from tf_agent.tools.youtube_api import DbQuota

    assert await DbQuota(db_sessionmaker, limit=250).spend(101)
    assert await DbQuota(db_sessionmaker, limit=250).spend(101)  # a new instance sees the 101 already spent
    assert not await DbQuota(db_sessionmaker, limit=250).spend(101)


async def test_key_check_costs_one_unit_and_reports_a_bad_key():
    quota = MemoryQuota()
    await api(lambda r: httpx.Response(200, json={"items": []}), quota).check_key("good")
    assert quota.used == 1
    bad = {"error": {"code": 400, "details": [{"reason": "API_KEY_INVALID"}], "message": "API key not valid"}}
    with pytest.raises(ToolFailure) as ei:
        await api(lambda r: httpx.Response(400, json=bad)).check_key("bad")
    assert ei.value.error.code == "login_required"


async def test_a_bad_search_parameter_is_not_a_bad_key():
    bad = {"error": {"code": 400, "errors": [{"reason": "invalidPublishedAfter"}], "message": "bad date"}}
    with pytest.raises(ToolFailure) as ei:
        await api(lambda r: httpx.Response(400, json=bad)).shorts_search("x", 5)
    assert ei.value.error.code == "invalid_input"
