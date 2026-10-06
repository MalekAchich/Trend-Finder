"""Helpers for tests in any workspace package (schema reset, truncation)."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

import tf_db.models  # noqa: F401  (registers tables on Base.metadata)
from tf_db.base import Base
from tf_db.config import DbSettings
from tf_db.session import ping

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def test_database_url() -> str:
    return DbSettings().database_url_test


def _run_in_thread(coro_fn, *args):
    """Run a coroutine on a fresh loop in a worker thread, so a test's own event loop is never touched."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro_fn(*args))).result()


def db_reachable(url: str) -> bool:
    return _run_in_thread(ping, url)


def _require_test_database(url: str) -> None:
    name = make_url(url).database or ""
    if not name.endswith("_test"):
        raise RuntimeError(f"refusing to reset {name!r}: only databases whose name ends in _test are reset")


async def _reset(url: str) -> None:
    _require_test_database(url)
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            # every table in the test schema, including ones a newer migration removed from the ORM
            names = (await conn.execute(text("select tablename from pg_tables where schemaname = 'public'"))).scalars()
            for name in list(names):
                await conn.execute(text(f'DROP TABLE IF EXISTS "{name}" CASCADE'))
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


def reset_schema_sync(url: str) -> None:
    _require_test_database(url)
    _run_in_thread(_reset, url)


async def truncate_all(engine: AsyncEngine) -> None:
    _require_test_database(engine.url.render_as_string(hide_password=False))
    names = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    if names:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
