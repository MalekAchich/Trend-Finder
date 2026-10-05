"""Per-platform limiter + circuit breaker + persisted health (03-tools.md: rate limiting, circuit breaker)."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.tools.breaker import CircuitBreaker
from tf_agent.tools.limiter import RateLimiter
from tf_db.models import PlatformStateRow

log = logging.getLogger(__name__)
Health = Literal["ok", "degraded", "unavailable", "needs_login"]
Mode = Literal["full", "discovery_only", "needs_login"]


@dataclass(frozen=True)
class PlatformConfig:
    min_interval_s: float
    jitter_s: float = 0.0


DEFAULT_PLATFORMS: dict[str, PlatformConfig] = {
    "tiktok": PlatformConfig(3.0, 3.0),
    "instagram": PlatformConfig(5.0, 5.0),
    "youtube": PlatformConfig(1.0, 0.5),
    "web": PlatformConfig(1.0),
}


class PlatformRegistry:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession] | None = None,
                 config: dict[str, PlatformConfig] | None = None, *, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, failure_threshold: int = 5,
                 window_s: float = 300.0, open_s: float = 600.0) -> None:
        self._sm = sessionmaker
        cfg = config or DEFAULT_PLATFORMS
        self._limiters = {p: RateLimiter(c.min_interval_s, c.jitter_s, sleep=sleep) for p, c in cfg.items()}
        self._breakers = {p: CircuitBreaker(failure_threshold, window_s, open_s, clock=clock) for p in cfg}
        self._modes: dict[str, Mode] = {p: "full" for p in cfg}

    @property
    def platforms(self) -> list[str]:
        return list(self._breakers)

    def limiter(self, platform: str) -> RateLimiter:
        return self._limiters[platform]

    def allow(self, platform: str) -> bool:
        return self._breakers[platform].allow()

    def health(self, platform: str) -> Health:
        b = self._breakers[platform]
        if b.state == "open":
            return "unavailable"
        if self._modes[platform] == "needs_login":
            return "needs_login"
        if b.state == "half_open" or b.recent_failures > 0 or self._modes[platform] == "discovery_only":
            return "degraded"
        return "ok"

    def snapshot(self) -> dict[str, Health]:
        return {p: self.health(p) for p in self._breakers}

    async def record_success(self, platform: str) -> None:
        self._breakers[platform].record_success()
        await self._persist(platform)

    async def record_failure(self, platform: str, reason: str) -> None:
        log.info("platform %s failure: %s", platform, reason)
        self._breakers[platform].record_failure()
        await self._persist(platform)

    async def set_mode(self, platform: str, mode: Mode) -> None:
        self._modes[platform] = mode
        await self._persist(platform)

    async def _persist(self, platform: str) -> None:
        if self._sm is None:
            return
        b = self._breakers[platform]
        values = {"health": self.health(platform), "breaker_state": b.state, "failures": b.recent_failures,
                  "opened_at": datetime.fromtimestamp(b.opened_at, tz=UTC) if b.opened_at else None}
        stmt = pg_insert(PlatformStateRow).values(platform=platform, **values)
        stmt = stmt.on_conflict_do_update(index_elements=[PlatformStateRow.platform],
                                          set_={**values, "updated_at": func.now()})
        try:
            async with self._sm() as s:
                await s.execute(stmt)
                await s.commit()
        except Exception as e:  # health persistence is informational; never break a tool call
            log.warning("platform_state save failed for %s: %s", platform, e)
