import asyncio
import json
import socket
import stat
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tf_agent.models.chatgpt_auth import (
    CLIENT_ID,
    DEVICE_REDIRECT_URI,
    REDIRECT_URI,
    ChatGptAuth,
    ChatGptAuthError,
    chatgpt_claims,
)
from tf_agent.testing import make_jwt


def factory(handler):
    transport = httpx.MockTransport(handler)
    return lambda: httpx.AsyncClient(transport=transport)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def connected_auth(tmp_path, handler=None, exp_in_s=3600, fedramp=False, **kw) -> ChatGptAuth:
    def never(request):
        raise AssertionError(f"unexpected HTTP call {request.url}")

    auth = ChatGptAuth(tmp_path / "chatgpt-auth.json", client_factory=factory(handler or never), **kw)
    tok = make_jwt(exp=int(time.time()) + exp_in_s, fedramp=fedramp)
    auth._persist({"access_token": tok, "id_token": tok, "refresh_token": "r1"})
    return auth


def test_claims_parsing():
    c = chatgpt_claims(make_jwt(exp=123), None)
    assert c == {"email": "nico@example.com", "plan_type": "plus", "account_id": "acc_1",
                 "is_fedramp": False, "exp": 123}


async def test_fresh_token_is_not_refreshed(tmp_path):
    auth = connected_auth(tmp_path)
    before = auth.load()["access_token"]
    assert (await auth.ensure_fresh())["access_token"] == before


async def test_refresh_near_expiry_uses_json_and_keeps_refresh_token(tmp_path):
    new_token = make_jwt(exp=int(time.time()) + 7200)

    def handler(request):
        assert request.url.path == "/oauth/token"
        assert json.loads(request.content) == {
            "client_id": CLIENT_ID, "grant_type": "refresh_token", "refresh_token": "r1"}
        return httpx.Response(200, json={"access_token": new_token, "id_token": new_token})

    auth = connected_auth(tmp_path, handler, exp_in_s=30)
    data = await auth.ensure_fresh()
    assert data["access_token"] == new_token
    assert data["refresh_token"] == "r1"
    assert stat.S_IMODE(auth.auth_file.stat().st_mode) == 0o600


async def test_concurrent_refresh_single_request(tmp_path):
    calls = 0
    new_token = make_jwt(exp=int(time.time()) + 7200)

    async def handler(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"access_token": new_token, "refresh_token": "r2"})

    auth = connected_auth(tmp_path, handler, exp_in_s=10)
    results = await asyncio.gather(*(auth.ensure_fresh() for _ in range(5)))
    assert calls == 1
    assert {r["access_token"] for r in results} == {new_token}


async def test_refresh_rejected_asks_for_login(tmp_path):
    auth = connected_auth(tmp_path, lambda r: httpx.Response(401, json={"error": "invalid_grant"}), exp_in_s=10)
    with pytest.raises(ChatGptAuthError, match="tf login chatgpt"):
        await auth.ensure_fresh()


async def test_not_connected(tmp_path):
    auth = ChatGptAuth(tmp_path / "missing.json")
    assert auth.status() == {"connected": False, "email": None, "plan": None}
    with pytest.raises(ChatGptAuthError, match="not connected"):
        await auth.ensure_fresh()


async def test_headers_include_account_and_fedramp(tmp_path):
    h = await connected_auth(tmp_path, fedramp=True).headers()
    assert h["chatgpt-account-id"] == "acc_1"
    assert h["authorization"].startswith("Bearer ")
    assert h["x-openai-fedramp"] == "true"
    assert h["accept"] == "text/event-stream"


async def test_browser_callback_exchanges_form_encoded_code(tmp_path):
    tokens = make_jwt()

    def handler(request):
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["authorization_code"]
        assert form["code"] == ["the-code"]
        assert form["redirect_uri"] == [REDIRECT_URI]
        assert form["code_verifier"][0]
        return httpx.Response(200, json={"access_token": tokens, "id_token": tokens, "refresh_token": "r1"})

    port = free_port()
    auth = ChatGptAuth(tmp_path / "a.json", client_factory=factory(handler), callback_port=port)
    url = await auth.browser_login_start()
    q = parse_qs(urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == [CLIENT_ID]
    try:
        async with httpx.AsyncClient() as real:
            r = await real.get(f"http://127.0.0.1:{port}/auth/callback",
                               params={"code": "the-code", "state": q["state"][0]})
        assert r.status_code == 200
        assert auth.status()["connected"] is True
    finally:
        await auth.stop_callback_server()


async def test_callback_rejects_unknown_state(tmp_path):
    auth = ChatGptAuth(tmp_path / "a.json")
    with pytest.raises(ChatGptAuthError, match="state"):
        await auth.handle_callback("code", "bogus")


async def test_port_busy_suggests_device_login(tmp_path):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        auth = ChatGptAuth(tmp_path / "a.json", callback_port=port)
        with pytest.raises(ChatGptAuthError, match="--device"):
            await auth.browser_login_start()


async def test_device_flow_polls_until_authorized(tmp_path):
    polls = 0
    tokens = make_jwt()

    def handler(request):
        nonlocal polls
        path = request.url.path
        if path == "/api/accounts/deviceauth/usercode":
            return httpx.Response(200, json={"user_code": "ABCD-1234", "device_auth_id": "dev1", "interval": 1})
        if path == "/api/accounts/deviceauth/token":
            polls += 1
            if polls == 1:
                return httpx.Response(403, json={"status": "pending"})
            return httpx.Response(200, json={"authorization_code": "ac", "code_verifier": "cv"})
        if path == "/oauth/token":
            form = parse_qs(request.content.decode())
            assert form["redirect_uri"] == [DEVICE_REDIRECT_URI] and form["code_verifier"] == ["cv"]
            return httpx.Response(200, json={"access_token": tokens, "id_token": tokens, "refresh_token": "r1"})
        raise AssertionError(path)

    auth = ChatGptAuth(tmp_path / "a.json", client_factory=factory(handler))
    started = time.time()
    info = await auth.device_login_start()
    assert info["user_code"] == "ABCD-1234"
    assert await auth.wait_connected(since=started, timeout_s=10) is True
    assert polls == 2


def test_logout_removes_file(tmp_path):
    auth = connected_auth(tmp_path)
    auth.logout()
    assert not auth.auth_file.exists()
