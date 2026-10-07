"""Logged-in search on TikTok, Instagram and X: the page runs the search, we keep the JSON it fetches.

The walkers look for each platform's video objects anywhere in the captured JSON (by their characteristic fields)
instead of following one exact path, so a reshuffled response layout doesn't break them.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from tf_agent.tools.normalize import norm_handle, norm_hashtag
from tf_agent.tools.types import Creator, Metrics, Sound, VideoItem

_HASHTAG = re.compile(r"#(\w+)", re.UNICODE)
CAPTCHA_RE = re.compile(r"drag the slider|verify to continue|captcha|unusual activity|/challenge/|suspicious", re.IGNORECASE)

TIKTOK_PATTERN = re.compile(r"/api/search/(?:item|general|video)/full")
INSTAGRAM_PATTERN = re.compile(r"/api/v1/fbsearch/|/api/v1/tags/|/graphql/query")
X_PATTERN = re.compile(r"/SearchTimeline")


def tiktok_search_url(query: str) -> str:
    return f"https://www.tiktok.com/search/video?q={quote(query)}"


def instagram_search_url(query: str) -> str:
    return f"https://www.instagram.com/explore/search/keyword/?q={quote(query)}"


def x_search_url(query: str, since: str | None = None) -> str:
    q = f"{query} filter:native_video" + (f" since:{since}" if since else "")
    return f"https://x.com/search?q={quote(q)}&src=typed_query&f=top"


def is_captcha(body_text: str, url: str) -> bool:
    return bool(CAPTCHA_RE.search(body_text[:3000]) or CAPTCHA_RE.search(url))


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(reversed(list(cur.values())))
        elif isinstance(cur, list):
            stack.extend(reversed(cur))


def _num(v: Any) -> int | None:
    try:
        return int(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _tags(text: str | None) -> list[str]:
    out: list[str] = []
    for t in _HASHTAG.findall(text or ""):
        if (n := norm_hashtag(t)) not in out:
            out.append(n)
    return out


def _dedupe(items: list[VideoItem]) -> list[VideoItem]:
    seen: set[str] = set()
    return [i for i in items if not (i.canonical_id in seen or seen.add(i.canonical_id))]


# ---- TikTok ----
def _tiktok(d: dict[str, Any]) -> VideoItem | None:
    vid, author = str(d.get("id") or ""), d.get("author")
    stats = d.get("statsV2") or d.get("stats")
    if not vid.isdigit() or not isinstance(author, dict) or not isinstance(stats, dict) or "desc" not in d:
        return None
    handle = norm_handle(str(author.get("uniqueId") or "")) or None
    video, music = d.get("video") or {}, d.get("music") or {}
    created = _num(d.get("createTime"))
    return VideoItem(
        canonical_id=f"tiktok:{vid}", platform="tiktok",
        url=f"https://www.tiktok.com/@{handle}/video/{vid}" if handle else f"https://www.tiktok.com/video/{vid}",
        creator=Creator(handle=handle, followers=_num((d.get("authorStats") or {}).get("followerCount"))),
        caption=d.get("desc") or None, hashtags=_tags(d.get("desc")),
        sound=Sound(id=str(music["id"]) if music.get("id") else None, title=music.get("title")),
        posted_at=datetime.fromtimestamp(created, UTC) if created else None,
        duration_s=float(video["duration"]) if _num(video.get("duration")) else None,
        metrics=Metrics(views=_num(stats.get("playCount")), likes=_num(stats.get("diggCount")),
                        comments=_num(stats.get("commentCount")), shares=_num(stats.get("shareCount")),
                        saves=_num(stats.get("collectCount"))),
        thumbnail_url=video.get("cover") or video.get("originCover"), media_access="ok", source="api")


def tiktok_items(captured: list[Any]) -> list[VideoItem]:
    return _dedupe([it for d in _walk(captured) if (it := _tiktok(d)) is not None])


# ---- Instagram ----
def _instagram(d: dict[str, Any]) -> VideoItem | None:
    code = d.get("code")
    is_video = d.get("media_type") == 2 or d.get("product_type") == "clips" or d.get("video_duration")
    if not isinstance(code, str) or not is_video or "taken_at" not in d:
        return None
    user, caption = d.get("user") or d.get("owner") or {}, (d.get("caption") or {})
    text = caption.get("text") if isinstance(caption, dict) else None
    taken = _num(d.get("taken_at"))
    cands = ((d.get("image_versions2") or {}).get("candidates") or [{}])
    return VideoItem(
        canonical_id=f"instagram:{code}", platform="instagram", url=f"https://www.instagram.com/reel/{code}/",
        creator=Creator(handle=norm_handle(user["username"]) if user.get("username") else None),
        caption=text, hashtags=_tags(text), posted_at=datetime.fromtimestamp(taken, UTC) if taken else None,
        duration_s=float(d["video_duration"]) if d.get("video_duration") else None,
        metrics=Metrics(views=_num(d.get("play_count") or d.get("ig_play_count") or d.get("view_count")),
                        likes=_num(d.get("like_count")), comments=_num(d.get("comment_count"))),
        thumbnail_url=cands[0].get("url"), media_access="ok", source="api")


def instagram_items(captured: list[Any]) -> list[VideoItem]:
    return _dedupe([it for d in _walk(captured) if (it := _instagram(d)) is not None])


# ---- X ----
def _x(d: dict[str, Any]) -> VideoItem | None:
    legacy = d.get("legacy")
    if not isinstance(legacy, dict) or not str(legacy.get("id_str") or "").isdigit():
        return None
    media = (legacy.get("extended_entities") or {}).get("media") or []
    video = next((m for m in media if m.get("type") in ("video", "animated_gif")), None)
    if video is None:
        return None
    sid = legacy["id_str"]
    user = (((d.get("core") or {}).get("user_results") or {}).get("result") or {})
    name = (user.get("core") or {}).get("screen_name") or (user.get("legacy") or {}).get("screen_name")
    text = re.sub(r"\s*https://t\.co/\S+$", "", legacy.get("full_text") or "") or None
    try:
        posted = datetime.strptime(legacy["created_at"], "%a %b %d %H:%M:%S %z %Y").astimezone(UTC)
    except (KeyError, ValueError):
        posted = None
    millis = _num((video.get("video_info") or {}).get("duration_millis"))
    shares = [x for x in (_num(legacy.get("retweet_count")), _num(legacy.get("quote_count"))) if x is not None]
    return VideoItem(
        canonical_id=f"x:{sid}", platform="x", url=f"https://x.com/i/status/{sid}",
        creator=Creator(handle=norm_handle(name) if name else None,
                        followers=_num((user.get("legacy") or {}).get("followers_count"))),
        caption=text, hashtags=_tags(text), posted_at=posted, duration_s=millis / 1000 if millis else None,
        metrics=Metrics(views=_num((d.get("views") or {}).get("count")), likes=_num(legacy.get("favorite_count")),
                        comments=_num(legacy.get("reply_count")), shares=sum(shares) if shares else None,
                        saves=_num(legacy.get("bookmark_count"))),
        thumbnail_url=video.get("media_url_https"), media_access="ok", source="api")


def x_items(captured: list[Any]) -> list[VideoItem]:
    return _dedupe([it for d in _walk(captured) if (it := _x(d)) is not None])
