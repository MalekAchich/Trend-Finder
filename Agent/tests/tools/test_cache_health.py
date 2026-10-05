from sqlalchemy import select

from tf_agent.tools.cache import ToolCache
from tf_agent.tools.health import PlatformConfig, PlatformRegistry
from tf_db.models import PlatformStateRow


async def test_cache_hit_miss_and_expiry(db_sessionmaker):
    now = [1_800_000_000.0]
    cache = ToolCache(db_sessionmaker, clock=lambda: now[0])
    assert await cache.get("tiktok_search", {"q": "a", "n": 5}) is None
    await cache.put("tiktok_search", {"n": 5, "q": "a"}, {"items": [1]}, ttl_s=60)
    assert await cache.get("tiktok_search", {"q": "a", "n": 5}) == {"items": [1]}
    assert await cache.get("shorts_search", {"q": "a", "n": 5}) is None
    await cache.put("tiktok_search", {"q": "a", "n": 5}, {"items": [2]}, ttl_s=60)
    assert await cache.get("tiktok_search", {"q": "a", "n": 5}) == {"items": [2]}
    now[0] += 61
    assert await cache.get("tiktok_search", {"q": "a", "n": 5}) is None


async def test_platform_registry_health_and_persistence(db_sessionmaker):
    t = [1_800_000_000.0]
    reg = PlatformRegistry(db_sessionmaker, config={"tiktok": PlatformConfig(0.0), "instagram": PlatformConfig(0.0)},
                           clock=lambda: t[0], failure_threshold=2)
    assert reg.snapshot() == {"tiktok": "ok", "instagram": "ok"}
    await reg.record_failure("tiktok", "captcha")
    assert reg.health("tiktok") == "degraded"
    await reg.record_failure("tiktok", "captcha")
    assert reg.health("tiktok") == "unavailable" and not reg.allow("tiktok")
    await reg.set_mode("instagram", "discovery_only")
    assert reg.health("instagram") == "degraded"
    async with db_sessionmaker() as s:
        rows = {r.platform: r for r in (await s.execute(select(PlatformStateRow))).scalars()}
    assert rows["tiktok"].breaker_state == "open" and rows["tiktok"].health == "unavailable"
    await reg.record_success("tiktok")
    assert reg.health("tiktok") == "unavailable"  # still open until the probe window passes
    t[0] += 600
    assert reg.allow("tiktok")
    await reg.record_success("tiktok")
    assert reg.health("tiktok") == "ok"
