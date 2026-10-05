"""Momentum, freshness and the weighted overall score (D-17, 05-scoring-and-learning.md)."""
from __future__ import annotations

import bisect
import math
from collections.abc import Mapping
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_db.models import Video

DEFAULT_WEIGHTS: dict[str, float] = {"fit": 0.40, "feasibility": 0.30, "momentum": 0.20, "freshness": 0.10}
MIN_PEERS = 20


def percentile(value: float, population: list[float]) -> float | None:
    """Mid-rank percentile (0–100): share below + half of ties."""
    if not population:
        return None
    ordered = sorted(population)
    below = bisect.bisect_left(ordered, value)
    equal = bisect.bisect_right(ordered, value) - below
    return 100.0 * (below + 0.5 * equal) / len(ordered)


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


def momentum(views_per_hour: float | None, engagement: float | None, peers_vph: list[float],
             peers_eng: list[float]) -> float | None:
    """0.7·pct(views/hour) + 0.3·pct(engagement) against same-platform peers; log-scaled fallback with few peers."""
    if views_per_hour is None:
        return None
    if len(peers_vph) >= MIN_PEERS:
        p_v = percentile(views_per_hour, peers_vph) or 0.0
    else:
        p_v = _clamp(100 * math.log10(views_per_hour + 1) / 5)  # 100k views/hour ≈ 100
    if engagement is None:
        return round(p_v, 2)
    if len(peers_eng) >= MIN_PEERS:
        p_e = percentile(engagement, peers_eng) or 0.0
    else:
        p_e = _clamp(100 * engagement / 0.15)  # 15 % engagement ≈ top
    return round(0.7 * p_v + 0.3 * p_e, 2)


def freshness(age_hours: float | None, saturation_pct: float = 0.0) -> float | None:
    """1.0 up to 3 days, linear to 0.2 at 30 days; saturation (0–100) halves it at most."""
    if age_hours is None:
        return None
    days = age_hours / 24
    decay = 1.0 if days <= 3 else max(0.2, 1.0 - 0.8 * (days - 3) / 27)
    return round(100 * decay * (1 - 0.5 * min(max(saturation_pct, 0.0), 100.0) / 100), 2)


def overall(sub: Mapping[str, float | None], weights: Mapping[str, float] = DEFAULT_WEIGHTS) -> float | None:
    """Weighted mean of the available sub-scores (weights renormalized); None without a fit score."""
    if sub.get("fit") is None:
        return None
    parts = [(weights[k], float(v)) for k, v in sub.items() if k in weights and v is not None]
    total = sum(w for w, _ in parts)
    return round(sum(w * v for w, v in parts) / total, 2) if total else None


async def peer_stats(sessionmaker: async_sessionmaker[AsyncSession], platform: str, now: datetime,
                     days: int = 30, limit: int = 5000) -> tuple[list[float], list[float]]:
    async with sessionmaker() as s:
        rows = (await s.execute(select(Video.posted_at, Video.metrics).where(
            Video.platform == platform, Video.metrics_at >= now - timedelta(days=days),
            Video.posted_at.is_not(None)).limit(limit))).all()
    vph, eng = [], []
    for posted_at, metrics in rows:
        views = (metrics or {}).get("views")
        if not views:
            continue
        hours = max((now - posted_at).total_seconds() / 3600, 1.0)
        vph.append(views / hours)
        inter = [metrics.get(k) for k in ("likes", "comments", "shares", "saves") if metrics.get(k) is not None]
        if inter:
            eng.append(sum(inter) / views)
    return vph, eng
