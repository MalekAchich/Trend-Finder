import asyncio
import random

from tf_agent.tools.breaker import CircuitBreaker
from tf_agent.tools.limiter import RateLimiter


class FakeTime:
    def __init__(self):
        self.t = 0.0

    def clock(self):
        return self.t

    async def sleep(self, s):
        self.t += s
        await asyncio.sleep(0)


async def test_limiter_spaces_calls():
    ft = FakeTime()
    lim = RateLimiter(2.0, clock=ft.clock, sleep=ft.sleep)
    times = []
    for _ in range(3):
        await lim.acquire()
        times.append(ft.t)
    assert times == [0.0, 2.0, 4.0]


async def test_limiter_jitter_within_bounds():
    ft = FakeTime()
    lim = RateLimiter(1.0, jitter_s=0.5, clock=ft.clock, sleep=ft.sleep, rng=random.Random(7))
    times = []
    for _ in range(6):
        await lim.acquire()
        times.append(ft.t)
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert all(1.0 <= g <= 1.5 for g in gaps)


async def test_limiter_serializes_concurrent_callers():
    ft = FakeTime()
    lim = RateLimiter(1.0, clock=ft.clock, sleep=ft.sleep)
    times = []

    async def one():
        await lim.acquire()
        times.append(ft.t)

    await asyncio.gather(*(one() for _ in range(4)))
    assert sorted(times) == [0.0, 1.0, 2.0, 3.0]


def test_breaker_lifecycle():
    ft = FakeTime()
    b = CircuitBreaker(failure_threshold=5, window_s=300, open_s=600, clock=ft.clock)
    for _ in range(4):
        b.record_failure()
    assert b.state == "closed" and b.allow()
    b.record_failure()
    assert b.state == "open" and not b.allow()
    ft.t += 600
    assert b.allow() and b.state == "half_open"
    assert not b.allow()  # only one probe in flight
    b.record_success()
    assert b.state == "closed" and b.allow()


def test_breaker_half_open_failure_reopens():
    ft = FakeTime()
    b = CircuitBreaker(failure_threshold=1, window_s=300, open_s=10, clock=ft.clock)
    b.record_failure()
    ft.t += 10
    assert b.allow()
    b.record_failure()
    assert b.state == "open" and not b.allow()


def test_breaker_old_failures_expire():
    ft = FakeTime()
    b = CircuitBreaker(failure_threshold=3, window_s=100, open_s=10, clock=ft.clock)
    for _ in range(5):
        b.record_failure()
        ft.t += 60
    # failures 60 s apart: at most 2 inside any 100 s window
    assert b.state == "closed"
