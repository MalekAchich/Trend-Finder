"""Circuit breaker: N failures inside a window opens the circuit; one probe is allowed after `open_s`."""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Literal

BreakerState = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, window_s: float = 300.0, open_s: float = 600.0,
                 clock: Callable[[], float] = time.time) -> None:
        self.failure_threshold = failure_threshold
        self.window_s = window_s
        self.open_s = open_s
        self._clock = clock
        self._failures: deque[float] = deque()
        self._state: BreakerState = "closed"
        self.opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> BreakerState:
        return self._state

    @property
    def probe_in_flight(self) -> bool:
        return self._state == "half_open" and self._probe_in_flight

    def release_probe(self) -> None:
        """The probe ended without a verdict (cancelled, unexpected error): let the next call probe again."""
        if self._state == "half_open":
            self._probe_in_flight = False

    @property
    def recent_failures(self) -> int:
        self._prune()
        return len(self._failures)

    def _prune(self) -> None:
        cutoff = self._clock() - self.window_s
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()

    def allow(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open":
            if self.opened_at is not None and self._clock() - self.opened_at >= self.open_s:
                self._state = "half_open"
                self._probe_in_flight = True
                return True
            return False
        if not self._probe_in_flight:  # half_open, previous probe finished without a verdict
            self._probe_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        if self._state == "open":
            return  # a late success from before the circuit opened doesn't close it
        self._state = "closed"
        self._failures.clear()
        self.opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        now = self._clock()
        if self._state == "half_open":
            self._open(now)
            return
        self._failures.append(now)
        self._prune()
        if len(self._failures) >= self.failure_threshold:
            self._open(now)

    def _open(self, now: float) -> None:
        self._state = "open"
        self.opened_at = now
        self._probe_in_flight = False
