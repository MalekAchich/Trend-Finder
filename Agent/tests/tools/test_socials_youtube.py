"""Plan 9: our YouTube channel, public numbers by key and the Analytics reports by login (made-up answers)."""
from datetime import UTC, datetime

import httpx

from tf_agent.socials.youtube import YouTubeChannel, challenge

from . import samples

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)
SINCE = datetime(2026, 7, 11, tzinfo=UTC)


def transport(calls):
    def handler(r: httpx.Request):
        q = dict(r.url.params)
        calls.append((r.url.path, q, r.headers.get("authorization"), r.headers.get("x-goog-api-key")))
        if r.url.path.endswith("/channels"):
            return httpx.Response(200, json=samples.yt_channel())
        if r.url.path.endswith("/playlistItems"):
            return httpx.Response(200, json=samples.yt_playlist())
        if r.url.path.endswith("/videos"):
            return httpx.Response(200, json=samples.yt_videos())
        if r.url.path.endswith("/reports"):
            dims = q.get("dimensions")
            if dims == "video":
                return httpx.Response(200, json=samples.yt_report(
                    ["video", "views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage", "likes",
                     "shares"], [["TestShort01", 350, 35, 6, 50.0, 20, 4]]))
            if dims == "country" and "filters" in q:
                return httpx.Response(200, json=samples.yt_report(["country", "views"], [["US", 200], ["TN", 150]]))
            if dims == "country":
                return httpx.Response(200, json=samples.yt_report(["country", "views"], [["US", 210], ["TN", 140]]))
            if dims == "city":
                return httpx.Response(400, json={"error": {"message": "city not available", "errors": []}})
            if dims == "ageGroup,gender":
                return httpx.Response(200, json=samples.yt_report(["ageGroup", "gender", "viewerPercentage"],
                                                                  [["age18-24", "male", 60.0], ["age25-34", "female", 40.0]]))
            return httpx.Response(200, json=samples.yt_report(
                ["views", "estimatedMinutesWatched", "averageViewDuration", "subscribersGained", "subscribersLost"],
                [[350, 35, 6, 12, 0]]))
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_public_numbers_come_through_the_key_in_a_header_never_the_url():
    calls = []
    read = await YouTubeChannel(lambda: "made-up-key", transport(calls), clock=lambda: NOW).read_public("madeupchannel", SINCE)
    assert (read.handle, read.followers, read.posts_count, read.source) == ("MadeUpChannel", 12, 2, "public")
    (post,) = read.posts  # the May short is outside the window
    assert (post.platform_post_id, post.views, post.likes, post.comments, post.duration_s) == ("TestShort01", 350, 20, 3, 12.0)
    assert post.url == "https://www.youtube.com/shorts/TestShort01" and post.thumbnail_url == "https://cover.invalid/y1.jpg"
    assert calls[0][1]["forHandle"] == "@madeupchannel" and all(c[3] == "made-up-key" and "key" not in c[1] for c in calls)


async def test_the_login_gives_views_by_country_for_the_channel_and_each_short():
    calls = []
    read = await YouTubeChannel(lambda: None, transport(calls), clock=lambda: NOW).read("made-up-token", SINCE)
    (post,) = read.posts
    assert (post.avg_watch_s, post.total_watch_s, post.shares) == (6.0, 2100.0, 4)
    assert post.audience == {"country": [["US", 200], ["TN", 150]]}
    reached = read.audience["reached"]
    assert reached["country"] == [["US", 210], ["TN", 140]] and "city" not in reached  # a refused report is left out
    assert reached["age"] == [["18-24", 60.0], ["25-34", 40.0]] and reached["gender"] == [["M", 60.0], ["F", 40.0]]
    assert read.account_insights["subscribers_gained"] == 12 and read.account_insights["days"] == 28
    assert all(c[2] == "Bearer made-up-token" for c in calls)


def test_google_pkce_is_base64url_sha256():
    assert challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"  # RFC 7636
