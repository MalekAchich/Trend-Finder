import httpx
import pytest

from tf_agent.models.fake import FakeAdapter
from tf_agent.testing import make_client
from tf_backend.main import create_app
from tf_backend.services import Services


class StubChatAuth:
    def __init__(self):
        self.logged_out = False

    async def browser_login_start(self):
        return "https://auth.openai.com/oauth/authorize?x=1"

    async def device_login_start(self):
        return {"verification_url": "https://auth.openai.com/codex/device", "user_code": "ABCD"}

    def logout(self):
        self.logged_out = True


class StubClaudeAuth:
    def __init__(self):
        self.logged_out = False

    async def login_start(self, email=None):
        return {"started": True, "auth_url": "https://claude.ai/oauth/authorize?x=1"}

    async def login_complete(self, raw):
        if raw == "bad":
            raise RuntimeError("bad code")
        return True

    def logout(self):
        self.logged_out = True


@pytest.fixture
def services():
    adapters = {"claude": FakeAdapter("claude"), "chatgpt": FakeAdapter("chatgpt")}
    client, governor, _ = make_client(adapters)
    return Services(settings=None, chatgpt_auth=StubChatAuth(), claude_auth=StubClaudeAuth(), adapters=adapters,
                    registry=client.router.registry, router=client.router, governor=governor, client=client)


@pytest.fixture
async def http(services):
    app = create_app(services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_health(http):
    assert (await http.get("/api/health")).json() == {"ok": True}


async def test_list_providers(http):
    body = (await http.get("/api/providers")).json()
    assert {p["provider"] for p in body} == {"claude", "chatgpt"}
    assert all(p["connected"] and p["status"] == "ok" for p in body)


async def test_models_and_unknown_provider(http):
    models = (await http.get("/api/providers/chatgpt/models")).json()
    assert models[0]["model_id"] == "chatgpt-model"
    assert (await http.get("/api/providers/nope/models")).status_code == 404


async def test_chatgpt_login_start_browser_and_device(http):
    assert (await http.post("/api/providers/chatgpt/login/start", json={})).json()["auth_url"].startswith("https://")
    dev = (await http.post("/api/providers/chatgpt/login/start", json={"device_code": True})).json()
    assert dev["user_code"] == "ABCD"


async def test_claude_login_flow(http, services):
    await services.governor.mark_auth_error("claude", "expired")
    assert (await http.post("/api/providers/claude/login/start")).json()["auth_url"].startswith("https://")
    assert (await http.post("/api/providers/claude/login/complete", json={"code": "good"})).json() == {
        "connected": True}
    assert services.governor.status("claude").status == "ok"
    assert (await http.post("/api/providers/claude/login/complete", json={"code": "bad"})).status_code == 409


async def test_logout(http, services):
    assert (await http.post("/api/providers/chatgpt/logout")).json() == {"ok": True}
    assert services.chatgpt_auth.logged_out is True


async def test_connected_provider_clears_stale_auth_error(http, services):
    await services.governor.mark_auth_error("chatgpt", "old")
    body = {p["provider"]: p for p in (await http.get("/api/providers")).json()}
    assert body["chatgpt"]["status"] == "ok"
