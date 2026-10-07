"""Logged-in search on TikTok, Instagram and X: the page runs the search, we keep the JSON it fetches.

The walkers look for each platform's video objects anywhere in the captured JSON (by their characteristic fields)
instead of following one exact path, so a reshuffled response layout doesn't break them.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
import json
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

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


# ---- TikTok trends: Creative Center, now inside TikTok One (anonymous visitors get the top few per filter) ----
# the API calls, plus the page's own server-rendered loader data (filtered pages deliver their lists there)
TRENDS_PATTERN = re.compile(r"GetHashtagList|GetTopContentsList|creativeCenter.*__ssrDirect=true")
FOLLOW_HEADERS = {"agw-js-conv": "str"}  # the API then sends 64-bit ids as strings (JS numbers would round them)
SPONSORED_TAGS = {"ad", "ads", "sponsored", "partner", "paidpartnership", "nativepartner", "brandpartner",
                  "collab", "gifted"}
TREND_PERIODS = (7, 30, 120)
MAX_PAID_SHARE = 0.8  # a "top video" whose views are mostly paid reach isn't an organic trend


def trends_page_url(kind: str, region: str, period: int) -> str:
    tab = "hashtag" if kind == "hashtags" else "video"
    return f"https://ads.tiktok.com/creative/creativeCenter/trends/{tab}?region={region.upper()}&period={period}"


def organic_variants(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The page asks for top videos including paid reach; ask the same API for organic-only, by views and by
    engagement (sent from inside the page, so it carries the page's own cookies and headers)."""
    out: list[dict[str, Any]] = []
    for r in requests:
        if "GetTopContentsList" not in r["url"]:
            continue
        parts = urlsplit(r["url"])
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        for metric in ("1", "2"):
            out.append({"url": urlunsplit(parts._replace(query=urlencode({**q, "organicOnly": "true",
                                                                          "orderByMetric": metric}))),
                        "method": "GET"})
    return out


def hashtag_first_page(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The hashtag ranking's first page comes inside the page's HTML; only later pages arrive as data. Ask the same
    API for page 1 with the page's own filters."""
    for r in requests:
        if "GetHashtagList" in r["url"] and r.get("body"):
            try:
                body = json.loads(r["body"])
            except ValueError:
                continue
            if isinstance(body, dict) and body.get("page") != 1:
                return [{"url": r["url"], "method": r.get("method") or "POST", "body": json.dumps({**body, "page": 1})}]
    return []


def _direction(curve: list[dict[str, Any]]) -> str:
    values = [float(p.get("value") or 0) for p in curve]
    if len(values) < 3:
        return "unknown"
    peak, last = max(values), values[-1]
    if peak and last >= 0.9 * peak and last > values[0]:
        return "rising"
    if peak and last < 0.7 * peak:
        return "peaked"
    return "steady"


def trend_hashtags(captured: list[Any]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for d in _walk(captured):
        name = d.get("hashtagName")
        if isinstance(name, str) and "publishCnt" in d and norm_hashtag(name) not in rows:
            rows[norm_hashtag(name)] = {"hashtag": norm_hashtag(name), "rank": _num(d.get("rankIndex")),
                                        "posts": _num(d.get("publishCnt")), "views": _num(d.get("vv")),
                                        "direction": _direction(d.get("popularityCurve") or [])}
    return sorted(rows.values(), key=lambda r: r["rank"] or 999)


def _trend_video(d: dict[str, Any]) -> VideoItem | None:
    info, metrics = d.get("itemInfo"), d.get("itemMetrics")
    if not isinstance(info, dict) or not isinstance(metrics, dict) or not str(info.get("itemID") or "").isdigit():
        return None
    total, organic = _num(metrics.get("videoViews")), _num(metrics.get("organicVideoViews"))
    if total and organic is not None and organic < (1 - MAX_PAID_SHARE) * total:
        return None
    tags = _tags(info.get("title"))
    if any(t in SPONSORED_TAGS or t.endswith("partner") or t.endswith("creatorcollective") for t in tags):
        return None  # branded content: organic reach, but not a trend to recreate
    vid, author = info["itemID"], d.get("itemAuthorInfo") or {}
    handle = norm_handle(str(author.get("handlerName") or "")) or None
    created = _num(info.get("createTime"))
    return VideoItem(
        canonical_id=f"tiktok:{vid}", platform="tiktok",
        url=f"https://www.tiktok.com/@{handle}/video/{vid}" if handle else f"https://www.tiktok.com/video/{vid}",
        creator=Creator(handle=handle, followers=_num((d.get("itemAuthorMetrics") or {}).get("followers"))),
        caption=info.get("title") or None, hashtags=tags,
        posted_at=datetime.fromtimestamp(created, UTC) if created else None,
        metrics=Metrics(views=organic if organic is not None else total), thumbnail_url=info.get("coverURL"),
        media_access="ok", source="api")


def trend_videos(captured: list[Any]) -> list[VideoItem]:
    return _dedupe([it for d in _walk(captured) if (it := _trend_video(d)) is not None])
