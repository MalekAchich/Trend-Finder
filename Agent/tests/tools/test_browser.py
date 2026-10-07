"""Plan 7 Task 2: login detection and JSON capture in a real (headless) Chromium against a local page."""
import re
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tf_agent.credentials import SESSIONS, Credentials
from tf_agent.tools.browser import BrowserSessions, LoginCancelled, logged_in, wait_for_close, wait_for_login
from tf_agent.tools.types import ToolFailure

PAGE = """<html><body><h1>feed</h1><script>
fetch('/api/feed.json').then(r => r.json()).then(d => document.body.dataset.n = d.items.length);
fetch('/other.json');
</script></body></html>"""
WALL = """<html><body><script>location.replace('/accounts/login/?next=/feed');</script></body></html>"""


class Jar:
    def __init__(self, rounds):
        self.rounds = list(rounds)

    async def cookies(self):
        return self.rounds.pop(0) if len(self.rounds) > 1 else self.rounds[0]


async def test_login_is_detected_by_the_session_cookie():
    spec = SESSIONS["instagram"]
    jar = Jar([[], [{"name": "csrftoken", "value": "x", "domain": ".instagram.com"}],
               [{"name": "sessionid", "value": "abc", "domain": ".instagram.com"}]])
    await wait_for_login(jar, spec, 60, closed=lambda: False, poll_s=0)


async def test_closing_the_window_or_waiting_too_long_ends_the_login():
    spec = SESSIONS["x"]
    with pytest.raises(LoginCancelled):
        await wait_for_login(Jar([[]]), spec, 60, closed=lambda: True, poll_s=0)
    ticks = iter([0, 0, 1000])
    with pytest.raises(TimeoutError):
        await wait_for_login(Jar([[]]), spec, 60, closed=lambda: False, poll_s=0, clock=lambda: next(ticks))


@pytest.fixture
def site(tmp_path):
    (tmp_path / "feed").mkdir()
    (tmp_path / "feed" / "index.html").write_text(PAGE)
    (tmp_path / "wall").mkdir()
    (tmp_path / "wall" / "index.html").write_text(WALL)
    (tmp_path / "accounts" / "login").mkdir(parents=True)
    (tmp_path / "accounts" / "login" / "index.html").write_text("<html><body>log in</body></html>")
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "feed.json").write_text('{"items": [1, 2, 3]}')
    (tmp_path / "other.json").write_text("{}")
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


async def test_capture_keeps_only_matching_json(site, tmp_path):
    b = BrowserSessions(Credentials(tmp_path / "secrets"))
    try:
        got = await b.capture_json("tiktok", f"{site}/feed/", re.compile(r"/api/feed"), scrolls=0, settle_ms=800)
    finally:
        await b.close()
    assert got == [{"items": [1, 2, 3]}]


async def test_a_login_wall_expires_the_session(site, tmp_path):
    creds = Credentials(tmp_path / "secrets")
    creds.save_session("instagram", {"cookies": [], "origins": []})
    b = BrowserSessions(creds)
    try:
        with pytest.raises(ToolFailure) as ei:
            await b.capture_json("instagram", f"{site}/wall/", re.compile(r"/api/"), scrolls=0, settle_ms=800)
    finally:
        await b.close()
    assert ei.value.error.code == "login_required" and creds.session_status("instagram") == "expired"


async def test_session_only_tools_need_a_connected_account(tmp_path):
    b = BrowserSessions(Credentials(tmp_path / "secrets"))
    with pytest.raises(ToolFailure) as ei:
        await b.capture_json("x", "http://127.0.0.1:1/", re.compile("x"), need_session=True)
    assert ei.value.error.code == "login_required" and "Settings" in ei.value.error.message


class States:
    def __init__(self, n_open):
        self.n_open, self.reads = n_open, 0

    async def storage_state(self):
        self.reads += 1
        return {"cookies": [{"name": f"c{self.reads}", "value": "v", "domain": ".tiktok.com"}], "origins": []}

    def closed(self):
        return self.reads >= self.n_open


async def test_closing_the_window_keeps_the_last_session_it_had():
    s = States(3)
    state = await wait_for_close(s, 60, closed=s.closed, poll_s=0)
    assert state["cookies"][0]["name"] == "c3"


def test_a_close_the_window_login_needs_a_real_login_cookie():
    spec = SESSIONS["tiktok_one"]
    tracking_only = {"cookies": [{"name": "ttwid", "value": "x", "domain": ".tiktok.com"},
                                 {"name": "msToken", "value": "x", "domain": "ads.tiktok.com"}]}
    assert not logged_in(tracking_only, spec)
    assert logged_in({"cookies": [{"name": "sid_tt", "value": "x", "domain": ".tiktok.com"}]}, spec)
