"""Plan 9: the Socials API on the test DB with fake readers (made-up accounts, tokens and numbers)."""
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tf_agent.credentials import Credentials
from tf_agent.socials.sync import SocialSync
from tf_agent.socials.tiktok_api import TikTokApi
from tf_agent.socials.types import ChannelRead, PostRead, ReadError
from tf_backend.app_context import AppContext
from tf_backend.main import create_app
from tf_backend.socials import Socials
from tf_db.models import Character

H = {"x-trendfinder-client": "test"}
SECRETS = ("IGAA-made-up-secret-token", "made-up-client-secret-5678", "act.made-up-access", "made-up-refresh")


def read(handle="made_up_channel", views=500, source="api"):
    return ChannelRead(handle=handle, followers=140, posts_count=1, source=source, posts=[
        PostRead(platform_post_id="TESTOWN001", url="https://www.instagram.com/reel/TESTOWN001/",
                 posted_at=datetime.now(UTC) - timedelta(hours=10), views=views, likes=50, comments=5,
                 duration_s=None, avg_watch_s=5.0 if source == "api" else None)])  # Instagram gives no length


class FakeInstagram:
    async def me(self, token):
        if token != SECRETS[0]:
            raise ReadError("expired", "made-up invalid token")
        return {"username": "made_up_channel"}

    async def refresh(self, token):
        raise ReadError("unavailable", "too new to refresh")

    async def read(self, token, since):
        return read()


class FakeTikTok(TikTokApi):
    async def exchange(self, app, code, verifier, redirect):
        if code != "made-up-code":
            raise ReadError("expired", "made-up bad code")
        self.verifier = verifier
        return {"access_token": SECRETS[2], "refresh_token": SECRETS[3], "expires_in": 86400,
                "refresh_expires_in": 31536000, "scope": "user.info.basic,video.list"}

    who = "made_up_tiktok"

    async def me(self, token):
        return {"username": self.who}

    async def read(self, token, since):
        return ChannelRead(handle="made_up_tiktok", followers=12, posts_count=0, posts=[])


class FakeYouTube:
    who = "made-up-yt"

    def authorize_url(self, client_id, redirect, state, verifier):
        return f"https://accounts.invalid/auth?client_id={client_id}&redirect_uri={redirect}&state={state}"

    async def exchange(self, app, code, verifier, redirect):
        if code != "made-up-code":
            raise ReadError("expired", "made-up bad code")
        return {"access_token": SECRETS[2], "refresh_token": SECRETS[3], "expires_in": 3599,
                "scope": "https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/yt-analytics.readonly"}

    async def me(self, token):
        return {"id": "UCmadeup", "handle": self.who}

    async def read(self, token, since):
        return ChannelRead(handle="made-up-yt", followers=12, posts_count=0, posts=[])


class FakePublic:
    async def read(self, platform, handle, since):
        return read(handle, views=300, source="public")


@pytest.fixture
async def setup(db_sessionmaker, tmp_path):
    async with db_sessionmaker() as s:
        s.add(Character(slug="testy", name="Testy", folder_path=str(tmp_path)))
        await s.commit()
    creds = Credentials(tmp_path / "secrets")
    ig, tt = FakeInstagram(), FakeTikTok()

    async def image(url):
        raise httpx.ConnectError("no network in tests")

    async def meta(url):
        from types import SimpleNamespace
        return SimpleNamespace(duration_s=20.0)

    yt = FakeYouTube()
    socials = Socials(db_sessionmaker, SocialSync(db_sessionmaker, creds, ig, tt, FakePublic(), youtube=yt), creds, ig, tt,
                      thumbs_dir=tmp_path / "thumbs", fetch_image=image, get_meta=meta, youtube=yt)
    ctx = AppContext(sessionmaker=db_sessionmaker, runs=None, learner=None, media_dir=tmp_path,
                     characters_dir=tmp_path, socials=socials)
    app = create_app(services=None, context=ctx)
    responses = []

    async def keep(r):
        await r.aread()
        responses.append(r.text)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers=H,
                                 event_hooks={"response": [keep]}) as c:
        yield c, socials, responses
    for body in responses:  # Review Focus 5: no secret ever leaves the app
        assert not any(secret in body for secret in SECRETS), body[:200]


async def test_a_public_channel_fills_in_at_once_and_shows_its_numbers(setup):
    http, _, _ = setup
    ch = (await http.post("/api/socials/channels", json={"platform": "instagram", "handle": "https://www.instagram.com/Made_Up_Channel/",
                                                          "character": "testy"})).json()
    assert (ch["handle"], ch["mode"], ch["followers"], ch["synced"]) == ("made_up_channel", "public", 140, 1)
    again = await http.post("/api/socials/channels", json={"platform": "instagram", "handle": "@made_up_channel",
                                                            "character": "testy"})
    assert again.status_code == 409
    bad = await http.post("/api/socials/channels", json={"platform": "tiktok", "handle": "not a handle!", "character": "testy"})
    assert bad.status_code == 422
    (post,) = (await http.get(f"/api/socials/channels/{ch['id']}/posts?window=7d")).json()
    assert (post["views"], post["views_gained"], post["platform_id"]) == (300, 300, "TESTOWN001")
    assert post["engagement"] == pytest.approx(55 / 300) and post["watch_through"] is None  # public: no watch time
    overview = (await http.get("/api/socials")).json()
    assert overview["characters"][0]["channels"][0]["views_7d"] == 300
    assert overview["tiktok_redirect_uri"].endswith("/api/socials/tiktok/callback")
    report = (await http.get("/api/socials/report?character=testy")).json()["text"]
    assert "Instagram @made_up_channel: 140 followers" in report


async def test_an_instagram_token_is_checked_saved_and_turns_on_the_official_numbers(setup):
    http, socials, _ = setup
    ch = (await http.post("/api/socials/channels", json={"platform": "instagram", "handle": "made_up_channel",
                                                          "character": "testy"})).json()
    wrong = await http.post(f"/api/socials/channels/{ch['id']}/instagram-token", json={"token": "x" * 30})
    assert wrong.status_code == 422 and "didn't accept" in wrong.json()["detail"]
    ok = (await http.post(f"/api/socials/channels/{ch['id']}/instagram-token", json={"token": SECRETS[0]})).json()
    assert ok["mode"] == "api" and "instagram_business_manage_insights" in ok["scopes"]
    (post,) = (await http.get(f"/api/socials/channels/{ch['id']}/posts?window=all")).json()
    assert post["duration_s"] == 20.0 and post["watch_through"] == 0.25  # length looked up once: 5 s of 20 s
    assert socials.creds.social_token(ch["id"])["access_token"] == SECRETS[0]
    off = (await http.post(f"/api/socials/channels/{ch['id']}/disconnect")).json()
    assert off["mode"] == "public" and socials.creds.social_token(ch["id"]) is None


async def test_tiktok_login_round_trip_and_its_refusals(setup):
    http, socials, _ = setup
    ch = (await http.post("/api/socials/channels", json={"platform": "tiktok", "handle": "made_up_tiktok",
                                                          "character": "testy"})).json()
    assert (await http.get(f"/api/socials/tiktok/connect?channel={ch['id']}")).status_code == 409  # no app yet
    shown = (await http.put("/api/socials/tiktok-app", json={"client_key": "made-up-client-key-1234",
                                                             "client_secret": SECRETS[1]})).json()
    assert shown == {"set": True, "hint": "••••1234", "updated_at": shown["updated_at"]}
    go = await http.get(f"/api/socials/tiktok/connect?channel={ch['id']}")
    q = parse_qs(urlparse(go.headers["location"]).query)
    assert go.status_code == 302 and q["redirect_uri"] == ["http://127.0.0.1:8000/api/socials/tiktok/callback"]
    state = q["state"][0]
    back = await http.get(f"/api/socials/tiktok/callback?state={state}&code=made-up-code")
    assert back.headers["location"] == "/socials?connected=tiktok"
    assert len(socials.tiktok.verifier) == 64 and socials.creds.social_token(ch["id"])["refresh_token"] == SECRETS[3]
    replay = await http.get(f"/api/socials/tiktok/callback?state={state}&code=made-up-code")
    assert "already used or has expired" in parse_qs(urlparse(replay.headers["location"]).query)["error"][0]
    state2 = parse_qs(urlparse((await http.get(f"/api/socials/tiktok/connect?channel={ch['id']}")).headers["location"]
                               ).query)["state"][0]
    denied = await http.get(f"/api/socials/tiktok/callback?state={state2}&error=access_denied&error_description=User+cancelled")
    assert "User cancelled" in parse_qs(urlparse(denied.headers["location"]).query)["error"][0]
    over = (await http.get("/api/socials")).json()["characters"][0]["channels"][0]
    assert over["mode"] == "api" and over["scopes"] == ["user.info.basic", "video.list"]  # a refused scope shows


async def test_a_post_can_be_linked_to_the_video_it_recreates_and_has_history(setup):
    http, _, _ = setup
    ch = (await http.post("/api/socials/channels", json={"platform": "instagram", "handle": "made_up_channel",
                                                          "character": "testy"})).json()
    (post,) = (await http.get(f"/api/socials/channels/{ch['id']}/posts?window=all")).json()
    assert (await http.patch(f"/api/socials/posts/{post['id']}", json={"inspired_by": "not an id"})).status_code == 422
    ok = await http.patch(f"/api/socials/posts/{post['id']}", json={"inspired_by": "instagram:TESTREEL050"})
    assert ok.json()["inspired_by"] == "instagram:TESTREEL050"
    hist = (await http.get(f"/api/socials/posts/{post['id']}/history")).json()
    assert [h["views"] for h in hist] == [300]
    assert (await http.delete(f"/api/socials/channels/{ch['id']}")).json() == {"ok": True}
    assert (await http.get("/api/socials")).json()["characters"][0]["channels"] == []


async def test_the_channel_over_time_and_each_post_s_life_start_at_zero(setup):
    http, socials, _ = setup
    ch = (await http.post("/api/socials/channels", json={"platform": "instagram", "handle": "made_up_channel",
                                                          "character": "testy"})).json()
    line = (await http.get(f"/api/socials/channels/{ch['id']}/timeline")).json()
    assert [(p["views"], p["followers"]) for p in line] == [(300, 140)]
    (post,) = (await http.get(f"/api/socials/channels/{ch['id']}/posts?window=all")).json()
    assert post["spark"][0] == [0.0, 0] and post["spark"][-1][1] == 300  # one reading still draws a line
    assert ch["audience"] is None  # public pages don't say who the audience is



async def test_tiktok_refuses_a_login_to_another_account(setup):
    http, socials, _ = setup
    ch = (await http.post("/api/socials/channels", json={"platform": "tiktok", "handle": "made_up_tiktok",
                                                          "character": "testy"})).json()
    await http.put("/api/socials/tiktok-app", json={"client_key": "made-up-client-key-1234", "client_secret": SECRETS[1]})
    socials.tiktok.who = "made_up_developer"  # the browser was logged in to the developer's own account
    state = parse_qs(urlparse((await http.get(f"/api/socials/tiktok/connect?channel={ch['id']}")).headers["location"]
                              ).query)["state"][0]
    back = await http.get(f"/api/socials/tiktok/callback?state={state}&code=made-up-code")
    msg = parse_qs(urlparse(back.headers["location"]).query)["error"][0]
    assert "connected @made_up_developer, not @made_up_tiktok" in msg
    assert socials.creds.social_token(ch["id"]) is None
    assert (await http.get("/api/socials")).json()["characters"][0]["channels"][0]["mode"] == "public"



async def test_youtube_connects_with_a_google_login_and_refuses_another_channel(setup):
    http, socials, _ = setup
    ch = (await http.post("/api/socials/channels", json={"platform": "youtube", "handle": "https://www.youtube.com/@Made-Up-YT",
                                                          "character": "testy"})).json()
    assert ch["handle"] == "made-up-yt"
    assert (await http.get(f"/api/socials/youtube/connect?channel={ch['id']}")).status_code == 409  # no Google app yet
    shown = (await http.put("/api/socials/google-app", json={"client_id": "1234-madeup.apps.googleusercontent.com",
                                                             "client_secret": "made-up-google-secret"})).json()
    assert shown["set"] is True and "made-up-google-secret" not in str(shown)

    go = await http.get(f"/api/socials/youtube/connect?channel={ch['id']}")
    q = parse_qs(urlparse(go.headers["location"]).query)
    assert q["redirect_uri"] == ["http://127.0.0.1:8000/api/socials/youtube/callback"]
    socials.youtube.who = "someone-else"
    wrong = await http.get(f"/api/socials/youtube/callback?state={q['state'][0]}&code=made-up-code")
    assert "connected @someone-else, not @made-up-yt" in parse_qs(urlparse(wrong.headers["location"]).query)["error"][0]
    assert socials.creds.social_token(ch["id"]) is None
    socials.youtube.who = "made-up-yt"
    q2 = parse_qs(urlparse((await http.get(f"/api/socials/youtube/connect?channel={ch['id']}")).headers["location"]).query)
    ok = await http.get(f"/api/socials/youtube/callback?state={q2['state'][0]}&code=made-up-code")
    assert ok.headers["location"] == "/socials?connected=youtube"
    row = [x for x in (await http.get("/api/socials")).json()["characters"][0]["channels"] if x["platform"] == "youtube"][0]
    assert row["mode"] == "api" and row["scopes"] == ["youtube.readonly", "yt-analytics.readonly"]
