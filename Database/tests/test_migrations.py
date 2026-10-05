import asyncio

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tf_db.base import Base
from tf_db.testing import ALEMBIC_INI, reset_schema_sync


def _cfg(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["url"] = url
    return cfg


async def _run(url, fn):
    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            return await conn.run_sync(fn)
    finally:
        await eng.dispose()


def _drop_everything(conn):
    Base.metadata.drop_all(conn)
    conn.execute(text("DROP TABLE IF EXISTS alembic_version"))


def test_upgrade_matches_orm_and_downgrade_is_clean(_db_schema):
    url = _db_schema
    try:
        asyncio.run(_run(url, _drop_everything))
        command.upgrade(_cfg(url), "head")
        diff = asyncio.run(_run(url, lambda c: compare_metadata(MigrationContext.configure(c), Base.metadata)))
        assert diff == []
        command.downgrade(_cfg(url), "base")
        tables = asyncio.run(_run(url, lambda c: set(inspect(c).get_table_names())))
        assert tables == {"alembic_version"}
    finally:
        reset_schema_sync(url)
