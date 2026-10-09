"""API keys and scraping-account sessions, kept only under `secrets/` (files 0600, folders 0700).

Nothing here ever returns a secret to the API: `describe()` gives a masked hint and dates. Logins happen in a real
browser window (tools/browser.py), so passwords are never stored, only the session: Playwright's storage state plus
a Netscape cookies.txt that yt-dlp reads. Everything is read at use time, so a rotation needs no restart.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

KEYS = {"youtube_api_key": "YouTube Data API key"}


@dataclass(frozen=True)
class SessionSpec:
    platform: str
    label: str
    login_url: str
    cookie: str | None  # present (non-empty) once logged in; None: the owner closes the window when done
    domain: str
    proof: tuple[str, ...] = ()  # for close-the-window logins: one of these cookies shows the login really happened
    browser: str = "chromium"  # "firefox": the login opens the owner's real Firefox (some logins break elsewhere)


SESSIONS = {
    "tiktok": SessionSpec("tiktok", "TikTok scraping account", "https://www.tiktok.com/login", "sessionid",
                          "tiktok.com"),
    "instagram": SessionSpec("instagram", "Instagram scraping account", "https://www.instagram.com/accounts/login/",
                             "sessionid", "instagram.com"),
    "x": SessionSpec("x", "X scraping account", "https://x.com/i/flow/login", "auth_token", "x.com"),
    # TikTok's trend rankings (Creative Center, now inside TikTok One) have their own login on ads.tiktok.com
    "tiktok_one": SessionSpec("tiktok_one", "TikTok One (trend rankings)",
                              "https://ads.tiktok.com/creative/creativeCenter/trends", None, "tiktok.com",
                              # TikTok One's own login cookies end in "_ads" (seen on a real login, 2026-10-07)
                              proof=("sessionid_ads", "sid_tt_ads", "sid_guard_ads", "sid_ucp_v1_ads"),
                              browser="firefox"),
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def netscape_cookies(cookies: list[dict[str, Any]]) -> str:
    """Playwright cookies as the Netscape cookies.txt yt-dlp reads."""
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = str(c.get("domain") or "")
        expires = c.get("expires")
        expiry = int(expires) if isinstance(expires, int | float) and expires > 0 else 0
        lines.append("\t".join([domain, "TRUE" if domain.startswith(".") else "FALSE", str(c.get("path") or "/"),
                                "TRUE" if c.get("secure") else "FALSE", str(expiry), str(c.get("name") or ""),
                                str(c.get("value") or "")]))
    return "\n".join(lines) + "\n"


class Credentials:
    def __init__(self, secrets_dir: Path) -> None:
        self.dir = Path(secrets_dir)

    # ---- files ----
    def _private_dir(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
        return path

    def _write(self, path: Path, text: str) -> None:
        self._private_dir(path.parent)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    # ---- keys ----
    @property
    def _keys_file(self) -> Path:
        return self.dir / "keys.json"

    def get(self, key: str) -> str | None:
        entry = self._read_json(self._keys_file).get(key)
        return entry.get("value") if isinstance(entry, dict) else None

    def set(self, key: str, value: str) -> None:
        if key not in KEYS:
            raise KeyError(key)
        value = value.strip()
        if not value:
            raise ValueError("empty value")
        keys = self._read_json(self._keys_file)
        keys[key] = {"value": value, "updated_at": _now()}
        self._write(self._keys_file, json.dumps(keys, indent=2))

    # ---- sessions ----
    def session_dir(self, platform: str) -> Path:
        if platform not in SESSIONS:
            raise KeyError(platform)
        return self.dir / "sessions" / platform

    def state_file(self, platform: str) -> Path:
        return self.session_dir(platform) / "state.json"

    def has_session(self, platform: str) -> bool:
        return platform in SESSIONS and self.state_file(platform).is_file()

    def cookies_file(self, platform: str) -> Path | None:
        """The account's cookies.txt; None for a platform without one (never an error: yt-dlp asks for every
        platform, YouTube included)."""
        if platform not in SESSIONS:
            return None
        f = self.session_dir(platform) / "cookies.txt"
        return f if f.is_file() else None

    def save_session(self, platform: str, storage_state: dict[str, Any]) -> None:
        d = self._private_dir(self.session_dir(platform))
        self._write(d / "state.json", json.dumps(storage_state))
        self._write(d / "cookies.txt", netscape_cookies(storage_state.get("cookies") or []))
        self._write(d / "meta.json", json.dumps({"connected_at": _now()}))

    def mark_expired(self, platform: str) -> None:
        meta_file = self.session_dir(platform) / "meta.json"
        if self.has_session(platform):
            self._write(meta_file, json.dumps({**self._read_json(meta_file), "expired_at": _now()}))

    def session_status(self, platform: str) -> str:
        if not self.has_session(platform):
            return "not connected"
        return "expired" if self._read_json(self.session_dir(platform) / "meta.json").get("expired_at") else "connected"

    # ---- our own channels' tokens (Plan 9): read-only API access, never a login ----
    @property
    def _socials_dir(self) -> Path:
        return self.dir / "socials"

    def _token_file(self, channel_id: str) -> Path:
        if not channel_id or not all(ch.isalnum() or ch == "-" for ch in channel_id):
            raise ValueError("bad channel id")
        return self._socials_dir / f"{channel_id}.json"

    def social_token(self, channel_id: str) -> dict[str, Any] | None:
        data = self._read_json(self._token_file(channel_id))
        return data if data.get("access_token") else None

    def save_social_token(self, channel_id: str, data: dict[str, Any]) -> None:
        if not data.get("access_token"):
            raise ValueError("empty token")
        self._write(self._token_file(channel_id), json.dumps({**data, "updated_at": _now()}))

    def delete_social_token(self, channel_id: str) -> None:
        self._token_file(channel_id).unlink(missing_ok=True)

    def tiktok_app(self) -> dict[str, str] | None:
        data = self._read_json(self._socials_dir / "tiktok_app.json")
        return ({"client_key": data["client_key"], "client_secret": data["client_secret"]}
                if data.get("client_key") and data.get("client_secret") else None)

    def save_tiktok_app(self, client_key: str, client_secret: str) -> None:
        client_key, client_secret = client_key.strip(), client_secret.strip()
        if not client_key or not client_secret:
            raise ValueError("empty client key or secret")
        self._write(self._socials_dir / "tiktok_app.json", json.dumps(
            {"client_key": client_key, "client_secret": client_secret, "updated_at": _now()}))

    def google_app(self) -> dict[str, str] | None:
        data = self._read_json(self._socials_dir / "google_app.json")
        return ({"client_id": data["client_id"], "client_secret": data["client_secret"]}
                if data.get("client_id") and data.get("client_secret") else None)

    def save_google_app(self, client_id: str, client_secret: str) -> None:
        client_id, client_secret = client_id.strip(), client_secret.strip()
        if not client_id or not client_secret:
            raise ValueError("empty client id or secret")
        self._write(self._socials_dir / "google_app.json", json.dumps(
            {"client_id": client_id, "client_secret": client_secret, "updated_at": _now()}))

    def describe_socials(self) -> dict[str, Any]:
        """Never a secret: whether the TikTok app is set (with a masked hint of its key)."""
        app = self._read_json(self._socials_dir / "tiktok_app.json")
        key = str(app.get("client_key") or "")
        g = self._read_json(self._socials_dir / "google_app.json")
        gid = str(g.get("client_id") or "")
        return {"tiktok_app": {"set": bool(key and app.get("client_secret")),
                               "hint": f"••••{key[-4:]}" if len(key) >= 8 else None,
                               "updated_at": app.get("updated_at")},
                "google_app": {"set": bool(gid and g.get("client_secret")),
                               "hint": f"••••{gid.split('.')[0][-4:]}" if len(gid) >= 8 else None,
                               "updated_at": g.get("updated_at")}}

    # ---- both ----
    def delete(self, item: str) -> None:
        if item in KEYS:
            keys = self._read_json(self._keys_file)
            if keys.pop(item, None) is not None:
                self._write(self._keys_file, json.dumps(keys, indent=2))
        elif item in SESSIONS:
            shutil.rmtree(self.session_dir(item), ignore_errors=True)
        else:
            raise KeyError(item)

    def describe(self) -> list[dict[str, Any]]:
        """What Settings shows: never a secret, only whether it's set, a masked hint and dates."""
        keys = self._read_json(self._keys_file)
        out: list[dict[str, Any]] = []
        for key, label in KEYS.items():
            entry = keys.get(key) if isinstance(keys.get(key), dict) else None
            value = (entry or {}).get("value") or ""
            out.append({"id": key, "label": label, "kind": "key", "set": bool(value),
                        "hint": f"••••{value[-4:]}" if len(value) >= 8 else ("••••" if value else None),
                        "updated_at": (entry or {}).get("updated_at"), "status": "set" if value else "not set"})
        for platform, spec in SESSIONS.items():
            meta = self._read_json(self.session_dir(platform) / "meta.json")
            status = self.session_status(platform)
            out.append({"id": platform, "label": spec.label, "kind": "session", "set": status != "not connected",
                        "hint": None, "updated_at": meta.get("connected_at"), "status": status})
        return out
