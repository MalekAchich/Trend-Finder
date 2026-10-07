"""Plan 8: the manually chosen videos API."""
import asyncio

import httpx
import pytest

from tf_agent.characters.folders import sync_characters
from tf_agent.learning.learner import Learner
from tf_agent.manual import ManualVideos
from tf_agent.models.fake import FakeAdapter
from tf_agent.roles.runners import Roles
from tf_agent.testing import make_client
from tf_agent.tools.types import Creator, Metrics, ToolFailure, VideoItem
from tf_backend.app_context import AppContext
from tf_backend.main import create_app
from tf_backend.runs import RunManager
from tf_backend.services import Services

H = {"x-trendfinder-client": "test"}
TT = "https://www.tiktok.com/@creator_a/video/1000000000000000001"
BAD = "https://www.instagram.com/reel/TESTREEL001/"


async def get_video(url, fresh=False):
    from tf_agent.tools.normalize import canonical_id, platform_of

    if url == BAD:
        raise ToolFailure("not_found", "this reel was removed")
    return VideoItem(canonical_id=canonical_id(url), platform=platform_of(url), url=url, creator=Creator(handle="a"),
                     metrics=Metrics(views=10), duration_s=9)


@pytest.fixture
async def http(db_sessionmaker, tmp_path):
    chars = tmp_path / "chars" / "Testy"
    chars.mkdir(parents=True)
    (chars / "Testy.png").write_bytes(b"\x89PNG")
    await sync_characters(tmp_path / "chars", db_sessionmaker)
    adapters = {"a": FakeAdapter("a")}
    client, governor, _ = make_client(adapters)
    sv = Services(settings=None, chatgpt_auth=None, claude_auth=None, adapters=adapters,
                  registry=client.router.registry, router=client.router, governor=governor, client=client)
    ctx = AppContext(sessionmaker=db_sessionmaker, runs=RunManager(db_sessionmaker, lambda: None),
                     learner=Learner(db_sessionmaker, Roles(client)), media_dir=tmp_path / "m",
                     characters_dir=tmp_path / "chars",
                     manual=ManualVideos(db_sessionmaker, get_video=get_video, thumbs_dir=tmp_path / "m" / "thumbs"))
    app = create_app(services=sv, context=ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers=H) as c:
        yield c


async def settle(http, n):
    for _ in range(100):
        items = (await http.get("/api/manual-videos")).json()["items"]
        if len(items) == n and all(i["status"] != "checking" for i in items):
            return items
        await asyncio.sleep(0.02)
    raise AssertionError(items)


async def test_add_check_edit_and_remove(http):
    r = await http.post("/api/manual-videos", json={"urls": [TT, BAD], "reference": True})
    assert r.status_code == 201 and r.json() == {"added": 2}
    items = {i["url"]: i for i in await settle(http, 2)}
    assert items[TT]["status"] == "ready" and items[TT]["views"] == 10
    assert items[BAD]["status"] == "problem" and "removed" in items[BAD]["problem"]
    vid = items[TT]["id"]
    assert (await http.patch(f"/api/manual-videos/{vid}", json={"target": "testy"})).status_code == 200
    card = next(i for i in (await http.get("/api/manual-videos?character=testy")).json()["items"] if i["id"] == vid)
    assert card["target"]["slug"] == "testy" and card["is_reference"]
    bad = await http.patch(f"/api/manual-videos/{vid}", json={"is_reference": False, "target": None})
    assert bad.status_code == 422
    assert (await http.post(f"/api/manual-videos/{items[BAD]['id']}/check")).status_code == 202
    assert (await http.delete(f"/api/manual-videos/{vid}")).status_code == 200
    assert (await http.delete(f"/api/manual-videos/{vid}")).status_code == 404


@pytest.mark.parametrize("body", [{"urls": ["https://example.invalid/x"]}, {"urls": []},
                                  {"urls": [TT], "target": "nobody"}, {"urls": [TT] * 101}])
async def test_bad_adds_are_refused(http, body):
    assert (await http.post("/api/manual-videos", json=body)).status_code == 422


async def test_unknown_character_filter_is_404(http):
    assert (await http.get("/api/manual-videos?character=nobody")).status_code == 404
