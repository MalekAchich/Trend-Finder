"""Lookalike discovery: videos like the owner's references, found the way a person finds them by hand.

No model calls here. It starts from the owner's reference videos (and targets):
1. their creators' recent videos;
2. searches built from what the reference studies have in common (AI themes, niches, trend types);
3. the creators behind the biggest hits of those searches (one step further).
Then a gate picks two kinds of recent videos for the analyst, and only those few are judged:
- viral: big numbers, ranked by how fast they're exploding (plays per hour since posting);
- hidden gems: small numbers but close to the references (same themes, AI-made, from the reference creators),
  which can mean an original idea nobody has copied yet.
Both kinds then have to clear the same score bar.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from tf_agent.tools.types import ToolFailure, VideoItem

log = logging.getLogger(__name__)
Emit = Callable[..., Awaitable[None]]
LIKES_TO_PLAYS = 25  # when a platform hides plays (Instagram search), likes x 25 is a typical reach estimate
MIN_AGE_H = 6.0  # a 1-hour-old video's plays/hour isn't meaningful yet
SECOND_DEGREE_MIN_PLAYS = 1_000_000
GEM_MIN_RELEVANCE = 3  # see relevance(): e.g. two shared themes, or a reference creator plus one theme


@dataclass
class Gate:
    days: int = 31
    min_plays: int = 300_000
    max_judged: int = 30
    max_queries: int = 8
    max_second_degree: int = 8
    max_per_creator: int = 3  # one prolific creator mustn't fill the whole run
    max_platform_share: float = 0.6  # leave room for the other platforms' strongest finds
    max_gems: int = 10  # of max_judged: small videos close to the references (originality)
    gem_min_plays: int = 1_000  # below this nobody has really seen it yet; too early to tell


@dataclass
class Pick:
    item: VideoItem
    why: str
    velocity: float
    lane: str = "viral"  # or "gem"
    relevance: int = 0


@dataclass
class Pool:
    """Every candidate seen once, with where it came from."""
    items: dict[str, VideoItem] = field(default_factory=dict)
    why: dict[str, str] = field(default_factory=dict)
    kind: dict[str, str] = field(default_factory=dict)  # seed | search | hit: which step found it

    def add(self, items: list[VideoItem], why: str, kind: str = "search") -> None:
        for i in items:
            if i.canonical_id not in self.items:
                self.items[i.canonical_id], self.why[i.canonical_id], self.kind[i.canonical_id] = i, why, kind


class Sources(Protocol):
    async def instagram_creator(self, handle: str, max_results: int = 24) -> Any: ...
    async def tiktok_creator(self, handle: str, max_results: int = 15) -> Any: ...
    async def instagram_search(self, query: str, max_results: int = 15, recent: Any = None) -> Any: ...
    async def tiktok_search(self, query: str, max_results: int = 15, recent: Any = None) -> Any: ...
    async def shorts_search(self, query: str, max_results: int = 15, recent: Any = None) -> Any: ...


def reach(i: VideoItem) -> int:
    m = i.metrics
    return m.views if m.views is not None else (m.likes or 0) * LIKES_TO_PLAYS


def velocity(i: VideoItem, now: datetime) -> float:
    age_h = max((now - i.posted_at).total_seconds() / 3600, MIN_AGE_H) if i.posted_at else 24 * 30
    return reach(i) / age_h


def theme_terms(studies: list[dict[str, Any]]) -> set[str]:
    """The words the references' studies are about (tags, niches, trend types), for matching captions."""
    raw = [t for s in studies for t in s.get("tags") or []]
    raw += [str(s.get(k) or "") for s in studies for k in ("niche", "trend_type")]
    words = {w for t in raw for w in re.findall(r"[a-z0-9]+", t.lower())}
    return {w for w in words if len(w) >= 3 and w not in STOP_WORDS}


STOP_WORDS = {"the", "and", "for", "with", "video", "videos", "viral", "trending", "fyp", "foryou", "content",
              "style", "clip", "edit", "edits", "reel", "reels", "short", "shorts", "new", "pov"}


def relevance(i: VideoItem, terms: set[str], kind: str | None) -> int:
    """How close a video looks to the references before any model sees it: shared themes in its caption and
    hashtags (up to 3 points), Instagram's AI label (2), from a reference creator (2) or a big-hit creator (1),
    and an unusually engaged audience (1)."""
    text = " ".join([i.caption or "", *i.hashtags]).lower()
    shared = len(terms & set(re.findall(r"[a-z0-9]+", text)))
    score = min(shared, 3)
    score += 2 if i.ai_generated else 0
    score += {"seed": 2, "hit": 1}.get(kind or "", 0)
    views, likes = i.metrics.views, i.metrics.likes
    score += 1 if views and likes and likes / views >= 0.08 else 0
    return score


def _query(text: str) -> str:
    """'AI POV stranger turn-around / punchline (AI)' -> 'ai pov stranger turn-around punchline': plain search words."""
    words: list[str] = []
    for w in re.sub(r"[/()|,:;!?\"]+", " ", text.lower()).split():
        if w not in words or w != "ai":
            words.append(w)
    words = [w for i, w in enumerate(words) if not (w == "ai" and "ai" in words[:i])]
    if "ai" not in words:
        words.insert(0, "ai")
    return " ".join(words[:5])  # short queries search best


def theme_queries(studies: list[dict[str, Any]], limit: int) -> list[str]:
    """Searches built from what the references have in common, AI-first (the references are AI-made content)."""
    tags = Counter(t for s in studies for t in s.get("tags") or [])
    niches = Counter(str(s.get("niche") or "").lower() for s in studies if s.get("niche"))
    kinds = Counter(str(s.get("trend_type") or "").lower() for s in studies if s.get("trend_type"))
    out: list[str] = ["ai generated video viral", "ai influencer"]
    out += [n for n, _ in niches.most_common(3)]
    out += [k for k, _ in kinds.most_common(2)]
    out += [t for t, _ in tags.most_common(8) if t not in ("ai", "viral", "trending", "fyp", "aivideo", "ai video")]
    seen: list[str] = []
    for q in map(_query, out):
        if q and q != "ai" and q not in seen:
            seen.append(q)
    return seen[:limit]


def gate(pool: Pool, exclude: set[str], g: Gate, now: datetime,
         terms: set[str] | frozenset[str] = frozenset()) -> tuple[list[Pick], Counter[str]]:
    """Recent videos only, in two lanes: viral (fastest-growing first) and hidden gems (closest to the references
    first). At most a few per creator, and room for every platform that has strong candidates.
    Returns the picks and why the rest were dropped."""
    oldest = now - timedelta(days=g.days)
    dropped: Counter[str] = Counter()
    viral: list[Pick] = []
    gems: list[Pick] = []
    for cid, i in pool.items.items():
        r = reach(i)
        if cid in exclude:
            dropped["already yours or already found"] += 1
        elif i.posted_at is not None and i.posted_at < oldest:
            dropped[f"older than {g.days} days"] += 1
        elif r >= g.min_plays:
            viral.append(Pick(i, pool.why[cid], velocity(i, now)))
        elif r < g.gem_min_plays:
            dropped[f"under {g.gem_min_plays:,} plays"] += 1
        elif (rel := relevance(i, terms, pool.kind.get(cid))) < GEM_MIN_RELEVANCE:
            dropped["small and not close enough to your references"] += 1
        else:
            gems.append(Pick(i, f"{pool.why[cid]}; a hidden gem: only {r:,} plays but close to your references",
                             velocity(i, now), lane="gem", relevance=rel))
    viral.sort(key=lambda p: p.velocity, reverse=True)
    gems.sort(key=lambda p: (p.relevance, p.velocity), reverse=True)
    picks: list[Pick] = []
    per_creator: Counter[tuple[str, str]] = Counter()
    per_platform: Counter[str] = Counter()

    def take(cands: list[Pick], upto: int, platform_cap: int | None) -> None:
        for p in cands:
            key = (p.item.platform, p.item.creator.handle or p.item.canonical_id)
            if len(picks) >= upto or p in picks or per_creator[key] >= g.max_per_creator:
                continue
            if platform_cap is not None and per_platform[p.item.platform] >= platform_cap:
                continue
            picks.append(p)
            per_creator[key] += 1
            per_platform[p.item.platform] += 1

    cap = math.ceil(g.max_judged * g.max_platform_share)
    viral_room = g.max_judged - min(g.max_gems, len(gems))
    take(viral, viral_room, cap)  # first with the platform share, then fill what's left
    take(viral, viral_room, None)
    take(gems, g.max_judged, None)
    take(viral, g.max_judged, None)  # few gems: the viral lane gets their room
    picks.sort(key=lambda p: (p.lane == "viral", p.velocity), reverse=True)
    left = len(viral) + len(gems) - len(picks)
    if left:
        dropped["not among the best (or too many from one creator)"] += left
    return picks, dropped


class Lookalike:
    def __init__(self, sources: Sources, emit: Emit, gate_settings: Gate | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.sources, self.emit, self.g, self._now = sources, emit, gate_settings or Gate(), clock

    async def _step(self, agent: dict[str, Any], goal: str, calls: list[tuple[str, Callable[[], Awaitable[Any]]]],
                    pool: Pool, why: Callable[[str], str], kind: str = "search") -> list[VideoItem]:
        """One visible step in the live stream: each call is a tool call with its result."""
        await self.emit("agent.started", agent=agent, goal=goal)
        found: list[VideoItem] = []
        for step, (label, call) in enumerate(calls, 1):
            await self.emit("agent.tool_call", agent=agent, tool=label.split(" ", 1)[0],
                            args={"for": label.split(" ", 1)[1] if " " in label else label}, step=step)
            try:
                res = await call()
                items = list(res.items)
                best = max((reach(i) for i in items), default=0)
                summary = f"{len(items)} videos" + (f", best {best:,} plays" if items else "")
                ok = True
            except ToolFailure as e:
                items, summary, ok = [], f"ERROR {e.error.message}", False
            except Exception as e:  # one source failing never stops the others
                log.warning("lookalike source %s failed: %s", label, e)
                items, summary, ok = [], f"ERROR {type(e).__name__}", False
            pool.add(items, why(label), kind)
            found += items
            await self.emit("agent.tool_result", agent=agent, tool=label.split(" ", 1)[0], summary=summary, ok=ok,
                            step=step)
        await self.emit("agent.finished", agent=agent, accepted=len(found), rejected=0, leads=0, failed=None)
        return found

    async def discover(self, seeds: list[VideoItem], studies: list[dict[str, Any]],
                       exclude: set[str]) -> tuple[list[Pick], dict[str, Any]]:
        pool = Pool()
        recent = "month" if self.g.days > 7 else "week"
        creators = {"instagram": [], "tiktok": []}
        for s in seeds:
            h = s.creator.handle
            if h and s.platform in creators and h not in creators[s.platform]:
                creators[s.platform].append(h)

        # 1 and 2 run side by side: each platform's pages are paced on their own
        seed_calls = [(f"instagram_creator @{h}", lambda h=h: self.sources.instagram_creator(h)) for h in creators["instagram"]]
        seed_calls += [(f"tiktok_creator @{h}", lambda h=h: self.sources.tiktok_creator(h)) for h in creators["tiktok"]]
        queries = theme_queries(studies, self.g.max_queries)
        search_calls: dict[str, list[tuple[str, Callable[[], Awaitable[Any]]]]] = {
            "instagram": [(f"instagram_search {q}", lambda q=q: self.sources.instagram_search(q, 15, recent)) for q in queries],
            "tiktok": [(f"tiktok_search {q}", lambda q=q: self.sources.tiktok_search(q, 15, recent)) for q in queries],
            "youtube": [(f"shorts_search {q}", lambda q=q: self.sources.shorts_search(q, 15, recent)) for q in queries],
        }
        steps = [self._step({"id": "seed-creators", "role": "scout", "platform": None},
                            f"recent videos from the creators of your {len(seeds)} reference videos", seed_calls, pool,
                            lambda label: f"new from {label.split(' ', 1)[1]}, the creator of one of your references",
                            "seed")]
        steps += [self._step({"id": f"themes-{p}", "role": "radar", "platform": p},
                             f"{p} searches for what your references have in common", calls, pool,
                             lambda label: f"found searching \"{label.split(' ', 1)[1]}\"")
                  for p, calls in search_calls.items()]
        results = await asyncio.gather(*steps)

        # 3: the creators behind the biggest hits of the theme searches
        hits = [i for found in results[1:] for i in found
                if reach(i) >= SECOND_DEGREE_MIN_PLAYS and i.creator.handle and i.platform in creators]
        hits.sort(key=lambda i: velocity(i, self._now()), reverse=True)
        second: list[tuple[str, Callable[[], Awaitable[Any]]]] = []
        known = {(p, h) for p, hs in creators.items() for h in hs}
        for i in hits:
            key = (i.platform, i.creator.handle)
            if key in known or len(second) >= self.g.max_second_degree:
                continue
            known.add(key)
            tool = self.sources.instagram_creator if i.platform == "instagram" else self.sources.tiktok_creator
            second.append((f"{i.platform}_creator @{i.creator.handle}", lambda t=tool, h=i.creator.handle: t(h)))
        if second:
            await self._step({"id": "hit-creators", "role": "deep_dive", "platform": None},
                             "more from the creators behind the biggest hits", second, pool,
                             lambda label: f"new from {label.split(' ', 1)[1]}, who had one of the biggest hits",
                             "hit")

        picks, dropped = gate(pool, exclude, self.g, self._now(), theme_terms(studies))
        return picks, {"seen": len(pool.items), "picked": len(picks), "gems": sum(p.lane == "gem" for p in picks),
                       "dropped": dict(dropped),
                       "queries": queries, "seed_creators": sum(len(v) for v in creators.values()),
                       "second_degree": len(second)}
