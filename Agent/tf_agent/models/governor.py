"""Per-provider concurrency + usage-window state (D-37, Docs/Code docs/02-model-layer.md)."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from tf_agent.models.types import RateInfo


@dataclass(frozen=True)
class ProviderStatus:
    status: Literal["ok", "cooling", "auth_error"] = "ok"
    cooling_until: float | None = None
    used_percent: float | None = None
    last_error: str | None = None


class ProviderStateStore(Protocol):
    async def save(self, provider: str, status: ProviderStatus) -> None: ...

    async def load_all(self) -> dict[str, ProviderStatus]: ...


class UsageGovernor:
    def __init__(self, providers: Iterable[str], concurrency: int = 4, *,
                 clock: Callable[[], float] = time.time, store: ProviderStateStore | None = None,
                 cool_at_percent: float = 98.0, probe_after_s: float = 900.0) -> None:
        names = list(providers)
        self._clock = clock
        self._store = store
        self.cool_at_percent = cool_at_percent
        self.probe_after_s = probe_after_s
        self._status = {p: ProviderStatus() for p in names}
        self._slots = {p: asyncio.Semaphore(concurrency) for p in names}

    def status(self, provider: str) -> ProviderStatus:
        return self._status[provider]

    def available(self, provider: str) -> bool:
        st = self._status[provider]
        if st.status == "ok":
            return True
        if st.status == "auth_error":
            return False
        return st.cooling_until is None or self._clock() >= st.cooling_until

    def slot(self, provider: str) -> asyncio.Semaphore:
        return self._slots[provider]

    async def _set(self, provider: str, status: ProviderStatus) -> None:
        self._status[provider] = status
        if self._store is not None:
            await self._store.save(provider, status)

    async def mark_cooling(self, provider: str, reset_at: float | None, reason: str) -> None:
        now = self._clock()
        until = reset_at if reset_at and reset_at > now else now + self.probe_after_s
        await self._set(provider, replace(self._status[provider], status="cooling", cooling_until=until,
                                          last_error=reason))

    async def mark_auth_error(self, provider: str, reason: str) -> None:
        await self._set(provider, replace(self._status[provider], status="auth_error", cooling_until=None,
                                          last_error=reason))

    async def mark_ok(self, provider: str) -> None:
        if self._status[provider].status != "ok":
            await self._set(provider, replace(self._status[provider], status="ok", cooling_until=None,
                                              last_error=None))

    async def observe_rate(self, provider: str, rate: RateInfo | None) -> None:
        if rate is None or rate.used_percent is None:
            return
        self._status[provider] = replace(self._status[provider], used_percent=rate.used_percent)
        if rate.used_percent >= self.cool_at_percent:
            await self.mark_cooling(provider, rate.resets_at,
                                    f"pre-emptive: {rate.used_percent:.0f}% of the usage window used")
        elif self._store is not None:
            await self._store.save(provider, self._status[provider])

    def earliest_recovery(self) -> float | None:
        times = [s.cooling_until for s in self._status.values() if s.status == "cooling" and s.cooling_until]
        return min(times) if times else None

    async def load(self) -> None:
        """Restore cooling windows after a restart. auth_error is not restored: it is re-detected on use."""
        if self._store is None:
            return
        for provider, st in (await self._store.load_all()).items():
            if provider in self._status and st.status != "auth_error":
                self._status[provider] = st
