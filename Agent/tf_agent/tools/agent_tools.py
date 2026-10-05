"""Discovery tools exposed to agents with validated params and compact, token-cheap outputs."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from tf_agent.loop.tools import Tool
from tf_agent.tools.platforms import DiscoveryResult, PlatformTools
from tf_agent.tools.types import ToolFailure, VideoItem
from tf_agent.tools.web import SearxClient, web_fetch

MAX_LINES = 12


class SearchParams(BaseModel):
    query: str = Field(min_length=2, max_length=200, description="search words, e.g. 'deadpan office dance'")
    max_results: int = Field(15, ge=1, le=30)
    recent: Literal["day", "week", "month", "year"] | None = Field(
        None, description="only results indexed within this period (use for fresh trends)")


class CreatorParams(BaseModel):
    handle: str = Field(min_length=1, max_length=64, description="creator handle, with or without @")
    max_results: int = Field(15, ge=1, le=30)


class HashtagParams(BaseModel):
    tag: str = Field(min_length=1, max_length=64, description="hashtag, with or without #")
    max_results: int = Field(15, ge=1, le=30)


class VideoParams(BaseModel):
    url: str = Field(min_length=10, max_length=500)


class WebSearchParams(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    max_results: int = Field(8, ge=1, le=10)
    time_range: Literal["day", "week", "month", "year"] | None = None


class WebFetchParams(BaseModel):
    url: str = Field(min_length=10, max_length=500)


def _num(n: int | None) -> str:
    if n is None:
        return "?"
    for div, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= div:
            return f"{n / div:.1f}{suffix}"
    return str(n)


def _age(posted: str | None, now: datetime) -> str:
    if not posted:
        return "age ?"
    hours = max((now - datetime.fromisoformat(posted)).total_seconds() / 3600, 0)
    return f"{hours:.0f}h" if hours < 48 else f"{hours / 24:.0f}d"


def _item_line(i: dict[str, Any], now: datetime) -> str:
    dur = f"{i['duration_s']:.0f}s" if i.get("duration_s") is not None else "?s"
    handle = (i.get("creator") or {}).get("handle") or "?"
    caption = " ".join((i.get("caption") or "").split())[:80]
    access = "" if i.get("media_access") != "login_required" else " | no-video-access"
    return (f"{i['canonical_id']} | {_num((i.get('metrics') or {}).get('views'))} views | {_age(i.get('posted_at'), now)}"
            f" | {dur} | @{handle}{access} | {caption}")


def _error_text(out: dict[str, Any]) -> str:
    e = out["error"]
    retry = f" (retry after {e['retry_after_s']:.0f}s)" if e.get("retry_after_s") else ""
    return f"ERROR {e['code']}: {e['message']}{retry}"


def compact_discovery(out: dict[str, Any], now: datetime | None = None) -> str:
    if "error" in out:
        return _error_text(out)
    now = now or datetime.now(UTC)
    head = f"platform_health={out['platform_health']}; hidden_already_seen={out['hidden_already_seen']}"
    if out.get("notes"):
        head += "; notes: " + "; ".join(out["notes"])
    items = out["items"]
    lines = [head] + [_item_line(i, now) for i in items[:MAX_LINES]]
    if len(items) > MAX_LINES:
        lines.append(f"... +{len(items) - MAX_LINES} more (refine the query to narrow results)")
    if not items:
        lines.append("no new videos found")
    return "\n".join(lines)


def _compact_video(out: dict[str, Any]) -> str:
    return _error_text(out) if "error" in out else _item_line(out, datetime.now(UTC))


def _compact_web(out: dict[str, Any]) -> str:
    if "error" in out:
        return _error_text(out)
    if "results" in out:
        return "\n".join(f"{r['title'][:90]} | {r['url']} | {r['snippet'][:160]}" for r in out["results"]) or "no results"
    return f"{out['title']}\n{out['text']}\nlinks: {' '.join(out['links'][:15])}"


def _safe(fn: Callable[[Any], Awaitable[Any]]) -> Callable[[Any], Awaitable[dict[str, Any]]]:
    async def handler(params: Any) -> dict[str, Any]:
        try:
            result = await fn(params)
        except ToolFailure as e:
            return {"error": e.error.model_dump()}
        if isinstance(result, DiscoveryResult):
            return result.to_dict()
        if isinstance(result, VideoItem):
            return result.model_dump(mode="json")
        return result

    return handler


def build_tools(pt: PlatformTools, searx: SearxClient, *, fetch: Callable[..., Awaitable[Any]] = web_fetch
                ) -> list[Tool]:
    async def do_web_search(p: WebSearchParams) -> dict[str, Any]:
        resp = await searx.search(p.query, max_results=p.max_results, time_range=p.time_range)
        return {"results": [{"title": h.title, "url": h.url, "snippet": h.snippet} for h in resp.results]}

    async def do_web_fetch(p: WebFetchParams) -> dict[str, Any]:
        page = await fetch(p.url)
        return {"title": page.title, "text": page.text, "links": page.links}

    return [
        Tool("tiktok_search", "Search TikTok videos by keywords. Returns ids, views, age, duration, creator, caption.",
             SearchParams, _safe(lambda p: pt.tiktok_search(p.query, p.max_results, p.recent)), compact_discovery),
        Tool("tiktok_creator", "Recent videos from one TikTok creator.", CreatorParams,
             _safe(lambda p: pt.tiktok_creator(p.handle, p.max_results)), compact_discovery),
        Tool("tiktok_hashtag", "TikTok videos using a hashtag.", HashtagParams,
             _safe(lambda p: pt.tiktok_hashtag(p.tag, p.max_results)), compact_discovery),
        Tool("instagram_search", "Search Instagram Reels by keywords (discovery only without login).", SearchParams,
             _safe(lambda p: pt.instagram_search(p.query, p.max_results, p.recent)), compact_discovery),
        Tool("shorts_search", "Search YouTube Shorts by keywords.", SearchParams,
             _safe(lambda p: pt.shorts_search(p.query, p.max_results, p.recent)), compact_discovery),
        Tool("get_video", "Full metadata for one TikTok/Instagram/YouTube video URL.", VideoParams,
             _safe(lambda p: pt.get_video(p.url)), _compact_video),
        Tool("web_search", "General web search (articles, trend reports, news).", WebSearchParams,
             _safe(do_web_search), _compact_web),
        Tool("web_fetch", "Read a public https web page as text plus links.", WebFetchParams,
             _safe(do_web_fetch), _compact_web),
    ]
