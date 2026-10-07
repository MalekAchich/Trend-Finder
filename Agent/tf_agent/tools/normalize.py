"""Canonical IDs and normalized search keys (03-tools.md: normalization rules)."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_TT_VIDEO = re.compile(r"/(?:@[^/]+/video|v|video)/(\d{8,25})")
_IG_MEDIA = re.compile(r"/(?:[^/]+/)?(?:reels?|p|tv)/([A-Za-z0-9_-]{8,40})/?$")
_IG_RESERVED = {"audio", "videos", "explore", "tags", "stories", "accounts", "reels", "reel"}
_YT_PATH = re.compile(r"^/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})")
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_X_STATUS = re.compile(r"^/(?:[^/]+|i(?:/web)?)/status(?:es)?/(\d{5,25})(?:/|$)")
_X_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com", "mobile.x.com", "www.x.com", "www.twitter.com"}


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
    if host in _X_HOSTS:
        return "x"
    return None


def canonical_id(url: str) -> str | None:
    """`tiktok:<id>`, `instagram:<shortcode>`, `youtube:<id>`, `x:<status id>`; None for non-video or unresolved
    short links."""
    platform = platform_of(url)
    if platform is None:
        return None
    parsed = urlparse(url.strip())
    path = parsed.path
    if platform == "tiktok":
        m = _TT_VIDEO.search(path)
        return f"tiktok:{m.group(1)}" if m else None
    if platform == "x":
        m = _X_STATUS.match(path)
        return f"x:{m.group(1)}" if m else None
    if platform == "instagram":
        m = _IG_MEDIA.match(path)
        if not m or m.group(1).lower() in _IG_RESERVED:
            return None
        return f"instagram:{m.group(1)}"
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


def canonical_url(cid: str, original: str | None = None) -> str:
    """A clean, fetchable URL for a canonical ID (TikTok keeps its original path: it needs the handle)."""
    platform, _, vid = cid.partition(":")
    if platform == "youtube":
        return f"https://www.youtube.com/shorts/{vid}"
    if platform == "instagram":
        return f"https://www.instagram.com/reel/{vid}/"
    if platform == "x":
        return f"https://x.com/i/status/{vid}"
    return (original or "").split("?")[0].split("#")[0]


def norm_hashtag(tag: str) -> str:
    return tag.strip().lstrip("#").strip().lower()


def norm_handle(handle: str) -> str:
    return handle.strip().lstrip("@").strip().lower()


def norm_query(query: str) -> str:
    return " ".join(query.lower().split())
