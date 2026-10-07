"""Plan 7 Task 5: Settings, Accounts & keys. Secrets go in, never come out."""
import asyncio
import json

import httpx
import pytest

from tf_agent.credentials import Credentials
from tf_agent.learning.learner import Learner
from tf_agent.models.fake import FakeAdapter
from tf_agent.roles.runners import Roles
from tf_agent.testing import make_client
from tf_agent.tools.types import ToolFailure
from tf_backend.app_context import AppContext
from tf_backend.main import create_app
from tf_backend.runs import RunManager
from tf_backend.services import Services

H = {"x-trendfinder-client": "test"}
SECRET = "AIzaSyTOP-SECRET-KEY-9876"


class FakeBrowser:
    def __init__(self, creds):
        self.creds, self.connecting, self.started = creds, {}, []

    async def connect(self, platform, timeout_s=600):
        self.started.append(platform)
        self.connecting[platform] = {"state": "waiting", "message": "Log in in the browser window that just opened."}
        await asyncio.sleep(0)
        self.creds.save_session(platform, {"cookies": [{"name": "sessionid", "value": "S3CRET", "domain": ".x.com"}],
                                           "origins": []})
        self.connecting[platform] = {"state": "connected", "message": "Connected."}


class FakeKeyCheck:
    def __init__(self, ok=True):
        self.ok, self.checked = ok, []

    async def check_key(self, key):
        self.checked.append(key)
        if not self.ok:
            raise ToolFailure("login_required", "YouTube API key rejected")


@pytest.fixture
async def setup(db_sessionmaker, tmp_path):
    adapters = {"claude": FakeAdapter("claude")}
    client, governor, _ = make_client(adapters)
    sv = Services(settings=None, chatgpt_auth=None, claude_auth=None, adapters=adapters,
                  registry=client.router.registry, router=client.router, governor=governor, client=client)
    creds = Credentials(tmp_path / "secrets")
    browser, keycheck = FakeBrowser(creds), FakeKeyCheck()
    ctx = AppContext(sessionmaker=db_sessionmaker, runs=RunManager(db_sessionmaker, lambda: None),
                     learner=Learner(db_sessionmaker, Roles(client)), media_dir=tmp_path / "m",
                     characters_dir=tmp_path / "c", browser=browser, youtube_api=keycheck)
    app = create_app(services=sv, context=ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
                                 headers=H) as c:
        yield c, creds, browser, keycheck


async def test_list_shows_every_item_unset(setup):
    http, *_ = setup
    items = (await http.get("/api/settings/accounts")).json()["items"]
    assert [i["id"] for i in items] == ["youtube_api_key", "tiktok", "instagram", "x", "tiktok_one"]
    assert not any(i["set"] for i in items)


async def test_key_is_checked_saved_masked_and_rotated(setup):
    http, creds, _, keycheck = setup
    r = await http.put("/api/settings/accounts/youtube_api_key", json={"value": SECRET})
    assert r.status_code == 200 and SECRET not in r.text and r.json()["hint"].endswith("9876")
    assert keycheck.checked == [SECRET] and creds.get("youtube_api_key") == SECRET
    listing = (await http.get("/api/settings/accounts")).text
    assert SECRET not in listing and "TOP-SECRET" not in listing
    assert (await http.delete("/api/settings/accounts/youtube_api_key")).json()["set"] is False
    assert creds.get("youtube_api_key") is None


async def test_rejected_key_is_not_saved(setup):
    http, creds, _, keycheck = setup
    keycheck.ok = False
    r = await http.put("/api/settings/accounts/youtube_api_key", json={"value": SECRET})
    assert r.status_code == 422 and "rejected" in r.json()["detail"] and SECRET not in r.text
    assert creds.get("youtube_api_key") is None


@pytest.mark.parametrize("path,body", [("/api/settings/accounts/openai_key", {"value": "x"}),
                                        ("/api/settings/accounts/tiktok", {"value": "x"}),
                                        ("/api/settings/accounts/youtube_api_key", {"value": "  "})])
async def test_bad_key_writes_are_refused(setup, path, body):
    http, *_ = setup
    assert (await http.put(path, json=body)).status_code == 422


async def test_connect_opens_the_login_window_and_reports_progress(setup):
    http, creds, browser, _ = setup
    r = await http.post("/api/settings/accounts/x/connect")
    assert r.status_code == 202
    for _ in range(50):
        item = next(i for i in (await http.get("/api/settings/accounts")).json()["items"] if i["id"] == "x")
        if item["status"] == "connected":
            break
        await asyncio.sleep(0.01)
    assert browser.started == ["x"] and item["connecting"]["state"] == "connected"
    assert "S3CRET" not in json.dumps(item)
    assert (await http.delete("/api/settings/accounts/x")).json()["status"] == "not connected"
    assert not creds.has_session("x")
    assert (await http.post("/api/settings/accounts/youtube_api_key/connect")).status_code == 422


async def test_writes_need_the_app_header(setup):
    http, *_ = setup
    r = await http.put("/api/settings/accounts/youtube_api_key", json={"value": SECRET},
                       headers={"x-trendfinder-client": ""})
    assert r.status_code in (400, 403)
