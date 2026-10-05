"""Database fixtures shared by every test directory (registered in the root conftest.py)."""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tf_db.testing import db_reachable, reset_schema_sync, test_database_url, truncate_all


@pytest.fixture(scope="session")
def _db_schema() -> str:
    url = test_database_url()
    if not db_reachable(url):
        pytest.skip("Postgres test DB not reachable: run `docker compose -f Database/docker-compose.yml up -d`")
    reset_schema_sync(url)
    return url


@pytest_asyncio.fixture
async def db_engine(_db_schema):
    engine = create_async_engine(_db_schema, poolclass=NullPool)
    await truncate_all(engine)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_sessionmaker(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)
