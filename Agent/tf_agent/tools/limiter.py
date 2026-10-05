"""Per-platform politeness: callers are serialized and spaced by `min_interval_s` (+ jitter)."""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable


class RateLimiter:
    def __init__(self, min_interval_s: float, jitter_s: float = 0.0, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 rng: random.Random | None = None) -> None:
        self.min_interval_s = min_interval_s
        self.jitter_s = jitter_s
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._lock = asyncio.Lock()
        self._next_at: float | None = None

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            if self._next_at is not None and now < self._next_at:
                await self._sleep(self._next_at - now)
            gap = self.min_interval_s + (self._rng.uniform(0, self.jitter_s) if self.jitter_s else 0.0)
            self._next_at = self._clock() + gap
