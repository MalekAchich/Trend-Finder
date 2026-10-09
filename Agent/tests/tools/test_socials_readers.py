"""Plan 9: our own channels' numbers from Instagram's and TikTok's APIs and from public pages (made-up answers)."""
import json
from datetime import UTC, datetime

import httpx
import pytest

from tf_agent.socials.instagram_api import InstagramApi
from tf_agent.socials.public import parse
from tf_agent.socials.tiktok_api import TikTokApi, challenge
from tf_agent.socials.types import ReadError

from . import samples

SINCE = datetime(2026, 7, 10, tzinfo=UTC)


def ig_transport(calls, refuse_watch=False, fail=None):
    def handler(r: httpx.Request):
        calls.append(str(r.url))
        if fail:
            return httpx.Response(400, json=samples.ig_error(*fail))
        path, q = r.url.path, dict(r.url.params)
        if path == "/me":
            return httpx.Response(200, json=samples.ig_me())
        if path == "/me/media":
            return httpx.Response(200, json=samples.ig_media_page(q.get("after")))
        if path.endswith("/insights"):
            if refuse_watch and "ig_reels_avg_watch_time" in q["metric"]:
                return httpx.Response(400, json=samples.ig_error(100, "metric not supported"))
            return httpx.Response(200, json=samples.ig_insights())
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_instagram_reads_the_account_and_each_post_s_insights_inside_the_window():
    calls = []
    read = await InstagramApi(ig_transport(calls)).read("made-up-token", SINCE)
    assert (read.handle, read.followers, read.posts_count) == ("made_up_channel", 140, 2)
    reel, photo = read.posts  # the June post on page 2 is outside the window: paging stops there
    assert reel.platform_post_id == "17900000000000001" and reel.url.endswith("/reel/TESTOWN001/")
    assert (reel.views, reel.reach, reel.likes, reel.shares, reel.saves) == (1820, 1400, 41, 12, 9)
    assert reel.avg_watch_s == 6.4 and reel.total_watch_s == 11648.0  # milliseconds -> seconds
    assert photo.platform_post_id == "17900000000000002"
    assert all("access_token=made-up-token" in c for c in calls)


async def test_a_metric_instagram_refuses_is_dropped_not_fatal():
    read = await InstagramApi(ig_transport([], refuse_watch=True)).read("t", SINCE)
    assert read.posts[0].views == 1820  # asked again without the watch-time metrics


@pytest.mark.parametrize("code,kind", [(190, "expired"), (10, "revoked"), (4, "rate_limited")])
async def test_instagram_errors_say_what_to_do(code, kind):
    with pytest.raises(ReadError) as e:
        await InstagramApi(ig_transport([], fail=(code, "made-up error"))).read("t", SINCE)
    assert e.value.code == kind


def tt_transport(calls, error=None):
    def handler(r: httpx.Request):
        calls.append((r.method, r.url.path, r.content.decode() if r.content else ""))
        if error:
            return httpx.Response(401, json=samples.tt_error(error))
        if r.url.path == "/v2/user/info/":
            return httpx.Response(200, json=samples.tt_user_info())
        if r.url.path == "/v2/video/list/":
            return httpx.Response(200, json=samples.tt_video_list(json.loads(r.content).get("cursor")))
        if r.url.path == "/v2/oauth/token/":
            form = dict(httpx.QueryParams(r.content.decode()))
            ok = form.get("code") == "made-up-code" or form.get("refresh_token") == "made-up-refresh"
            return httpx.Response(200, json={"access_token": "act.made-up", "expires_in": 86400, "refresh_token":
                                             "made-up-refresh", "refresh_expires_in": 31536000,
                                             "scope": "user.info.basic,video.list", "open_id": "made-up-open-id"}
                                  if ok else {"error": "invalid_grant", "error_description": "made-up"})
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_tiktok_reads_the_account_and_pages_videos_until_the_window():
    calls = []
    read = await TikTokApi(tt_transport(calls)).read("act.made-up", SINCE)
    assert (read.handle, read.followers, read.total_likes, read.posts_count) == ("made_up_channel", 230, 1900, 2)
    (post,) = read.posts  # the second page's video is from before the window
    assert (post.views, post.likes, post.comments, post.shares, post.saves) == (5400, 610, 22, 31, None)
    assert post.avg_watch_s is None and post.duration_s == 12.0  # TikTok's Display API has no watch time
    assert [c[1] for c in calls] == ["/v2/user/info/", "/v2/video/list/", "/v2/video/list/"]


async def test_tiktok_login_uses_hex_pkce_and_maps_errors():
    api = TikTokApi(tt_transport([]))
    url = api.authorize_url("made-up-key", "http://127.0.0.1:8000/api/socials/tiktok/callback", "st4te", "v" * 64)
    assert f"code_challenge={challenge('v' * 64)}" in url and len(challenge("v" * 64)) == 64  # hex SHA-256
    app = {"client_key": "made-up-key", "client_secret": "made-up-secret"}
    token = await api.exchange(app, "made-up-code", "v" * 64, "http://127.0.0.1:8000/api/socials/tiktok/callback")
    assert token["refresh_token"] == "made-up-refresh"
    with pytest.raises(ReadError) as e:
        await api.exchange(app, "wrong-code", "v" * 64, "http://127.0.0.1:8000/x")
    assert e.value.code == "expired"
    with pytest.raises(ReadError) as e:
        await TikTokApi(tt_transport([], error="scope_not_authorized")).read("t", SINCE)
    assert e.value.code == "revoked"


def test_public_pages_give_followers_and_the_numbers_they_show():
    ig = parse("instagram", "made_up_channel", samples.ig_profile_capture(), SINCE)
    assert (ig.followers, ig.posts_count, ig.source) == (138, 2, "public")
    tt = parse("tiktok", "made_up_channel", samples.tt_profile_capture(), SINCE)
    assert (tt.followers, tt.total_likes, tt.posts_count) == (229, 1890, 2)
    assert (tt.posts[0].views, tt.posts[0].reach) == (5300, None)  # public pages have no reach: unknown, not 0


async def test_instagram_audience_waits_for_100_followers_and_reads_breakdowns_after():
    def handler(r: httpx.Request):
        q = dict(r.url.params)
        if r.url.path == "/me/insights" and q["metric"] == "follower_demographics":
            rows = {"country": [(["US"], 40), (["TN"], 25)], "age": [(["18-24"], 50)], "gender": [(["M"], 60)]}
            return httpx.Response(200, json={"data": [{"name": "follower_demographics", "total_value": {"breakdowns": [
                {"results": [{"dimension_values": d, "value": v} for d, v in rows[q["breakdown"]]]}]}}]})
        if r.url.path == "/me/insights":
            return httpx.Response(200, json={"data": [{"name": q["metric"], "total_value": {"value": 900}}]})
        return httpx.Response(404)
    api = InstagramApi(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert "100 followers" in (await api.audience("t", 12))["note"]
    who = await api.audience("t", 140)
    assert who["country"] == [["US", 40], ["TN", 25]] and who["age"] == [["18-24", 50]] and who["gender"] == [["M", 60]]
    totals = await api.account_insights("t")
    assert totals["reach"] == 900 and totals["days"] == 28
