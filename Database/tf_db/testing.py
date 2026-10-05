"""Helpers for tests in any workspace package (schema reset, truncation)."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import text
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


async def _reset(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


def reset_schema_sync(url: str) -> None:
    _run_in_thread(_reset, url)


async def truncate_all(engine: AsyncEngine) -> None:
    names = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    if names:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
