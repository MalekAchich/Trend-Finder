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


SESSIONS = {
    "tiktok": SessionSpec("tiktok", "TikTok scraping account", "https://www.tiktok.com/login", "sessionid",
                          "tiktok.com"),
    "instagram": SessionSpec("instagram", "Instagram scraping account", "https://www.instagram.com/accounts/login/",
                             "sessionid", "instagram.com"),
    "x": SessionSpec("x", "X scraping account", "https://x.com/i/flow/login", "auth_token", "x.com"),
    # TikTok's trend rankings (Creative Center, now inside TikTok One) have their own login on ads.tiktok.com
    "tiktok_one": SessionSpec("tiktok_one", "TikTok One (trend rankings)",
                              "https://ads.tiktok.com/creative/creativeCenter/trends", None, "tiktok.com"),
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
        return self.state_file(platform).is_file()

    def cookies_file(self, platform: str) -> Path | None:
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
