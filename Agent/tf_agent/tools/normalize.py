"""Canonical IDs and normalized search keys (03-tools.md: normalization rules)."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_TT_VIDEO = re.compile(r"/(?:@[^/]+/video|v|video)/(\d{8,25})")
_IG_MEDIA = re.compile(r"/(?:[^/]+/)?(?:reels?|p|tv)/([A-Za-z0-9_-]{5,40})")
_YT_PATH = re.compile(r"^/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})")
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _host(url: str) -> str | None:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return parsed.hostname.lower()


def platform_of(url: str) -> str | None:
    host = _host(url)
    if host is None:
        return None
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return "instagram"
    if host in ("youtu.be", "youtube.com") or host.endswith(".youtube.com"):
        return "youtube"
    return None


def canonical_id(url: str) -> str | None:
    """`tiktok:<id>`, `instagram:<shortcode>`, `youtube:<id>`; None for non-video or unresolved short links."""
    platform = platform_of(url)
    if platform is None:
        return None
    parsed = urlparse(url.strip())
    path = parsed.path
    if platform == "tiktok":
        m = _TT_VIDEO.search(path)
        return f"tiktok:{m.group(1)}" if m else None
    if platform == "instagram":
        m = _IG_MEDIA.match(path)
        return f"instagram:{m.group(1)}" if m else None
    host = parsed.hostname or ""
    if host == "youtu.be":
        vid = path.strip("/").split("/")[0]
        return f"youtube:{vid}" if _YT_ID.match(vid) else None
    m = _YT_PATH.match(path)
    if m:
        return f"youtube:{m.group(1)}"
    if path == "/watch":
        vid = (parse_qs(parsed.query).get("v") or [""])[0]
        return f"youtube:{vid}" if _YT_ID.match(vid) else None
    return None


def norm_hashtag(tag: str) -> str:
    return tag.strip().lstrip("#").strip().lower()


def norm_handle(handle: str) -> str:
    return handle.strip().lstrip("@").strip().lower()


def norm_query(query: str) -> str:
    return " ".join(query.lower().split())
