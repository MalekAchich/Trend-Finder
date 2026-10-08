"""Plan 7 Task 2: keys and scraping-account sessions live only under secrets/, never shown in full."""
import json
import stat

import pytest

from tf_agent.credentials import SESSIONS, Credentials, netscape_cookies

STATE = {"cookies": [
    {"name": "sessionid", "value": "SECRET-SESSION", "domain": ".instagram.com", "path": "/", "expires": 1893456000,
     "httpOnly": True, "secure": True, "sameSite": "None"},
    {"name": "csrftoken", "value": "tok", "domain": "www.instagram.com", "path": "/", "expires": -1,
     "httpOnly": False, "secure": True, "sameSite": "Lax"}], "origins": []}


def mode(p):
    return stat.S_IMODE(p.stat().st_mode)


def test_key_round_trip_is_private_and_masked(tmp_path):
    c = Credentials(tmp_path / "secrets")
    assert c.get("youtube_api_key") is None
    c.set("youtube_api_key", "  AIzaSyD-very-secret-1234  ")
    assert c.get("youtube_api_key") == "AIzaSyD-very-secret-1234"
    f = tmp_path / "secrets" / "keys.json"
    assert mode(f) == 0o600 and mode(f.parent) == 0o700
    item = next(i for i in c.describe() if i["id"] == "youtube_api_key")
    assert item["set"] and item["hint"].endswith("1234") and "secret" not in json.dumps(c.describe())
    c.set("youtube_api_key", "AIzaNEW-5678")  # rotation replaces it
    assert c.get("youtube_api_key") == "AIzaNEW-5678"
    c.delete("youtube_api_key")
    assert c.get("youtube_api_key") is None


def test_unknown_or_empty_keys_are_refused(tmp_path):
    c = Credentials(tmp_path)
    with pytest.raises(KeyError):
        c.set("openai", "x")
    with pytest.raises(ValueError):
        c.set("youtube_api_key", "   ")


def test_session_saved_as_state_and_cookies_txt(tmp_path):
    c = Credentials(tmp_path / "secrets")
    c.save_session("instagram", STATE)
    cookies = c.cookies_file("instagram")
    assert cookies is not None and mode(cookies) == 0o600 and mode(c.state_file("instagram")) == 0o600
    lines = cookies.read_text().splitlines()
    assert lines[0] == "# Netscape HTTP Cookie File"
    assert ".instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tSECRET-SESSION" in lines
    assert "www.instagram.com\tFALSE\t/\tTRUE\t0\tcsrftoken\ttok" in lines
    item = next(i for i in c.describe() if i["id"] == "instagram")
    assert item["status"] == "connected" and "SECRET" not in json.dumps(item)
    c.mark_expired("instagram")
    assert next(i for i in c.describe() if i["id"] == "instagram")["status"] == "expired"
    c.delete("instagram")
    assert c.cookies_file("instagram") is None and not c.has_session("instagram")


def test_every_session_platform_has_a_login_cookie():
    assert set(SESSIONS) == {"tiktok", "instagram", "x", "tiktok_one"}
    assert SESSIONS["tiktok_one"].cookie is None  # saved when the owner closes the window
    assert SESSIONS["x"].cookie == "auth_token" and SESSIONS["tiktok"].cookie == "sessionid"


def test_netscape_http_only_cookies_keep_their_prefix_free_format():
    assert netscape_cookies([]) == "# Netscape HTTP Cookie File\n"


def test_platforms_without_an_account_simply_have_no_cookies(tmp_path):
    """yt-dlp asks for every platform's cookies before a download: YouTube has no scraping account (run 1 bug)."""
    c = Credentials(tmp_path / "secrets")
    assert c.cookies_file("youtube") is None and c.cookies_file("web") is None and not c.has_session("youtube")
