"""Explore vs. exploit (D-18): satisfaction → explore share; Thompson sampling over direction stats."""
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class DirectionStat:
    key: str
    alpha: int
    beta: int
    id: object = None
    label: str = ""
    niche: str | None = None


def explore_ratio(satisfaction: int | None, rated_directions: int) -> float:
    if satisfaction is None or rated_directions < 3:
        return 1.0
    return min(max(0.85 - 0.075 * (satisfaction - 1), 0.15), 0.85)


def split_tasks(n_tasks: int, ratio: float) -> tuple[int, int]:
    """(explore, exploit) task counts; at least one explore task whenever ratio > 0."""
    explore = round(n_tasks * ratio)
    if ratio > 0 and n_tasks > 0:
        explore = max(explore, 1)
    return explore, n_tasks - explore


def thompson_rank(directions: list[DirectionStat], rng: random.Random) -> list[tuple[DirectionStat, float]]:
    scored = [(d, rng.betavariate(1 + d.alpha, 1 + d.beta)) for d in directions]
    return sorted(scored, key=lambda x: x[1], reverse=True)
