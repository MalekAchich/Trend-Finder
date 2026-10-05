# Plan 1: Foundation, Model Layer & Agent Loop: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working uv workspace (Database / Agent / Backend) where both subscriptions (Claude via `claude -p`, ChatGPT via OAuth) sit behind one adapter interface, with a usage-aware router, and a provider-agnostic tool-calling agent loop that a `tf demo-agent` command runs end-to-end on either provider.

**Architecture:** `Database/` (package `tf_db`) owns Postgres (Docker), SQLAlchemy models and Alembic. `Agent/` (package `tf_agent`) owns provider adapters, model registry, role router, usage governor, call ledger and `run_agent()`. `Backend/` (package `tf_backend`) exposes provider status/login over FastAPI and the `tf` CLI. Dependency direction: Backend → Agent → Database.

**Tech Stack:** Python 3.12, uv workspace, SQLAlchemy 2 async + asyncpg, Alembic, pydantic v2 + pydantic-settings, httpx, PyYAML, FastAPI, Typer, pytest + pytest-asyncio, Docker (pgvector/pgvector:pg17).

**Spec:** `Docs/` (start at `Docs/README.md`). Most relevant: `Docs/02-decisions.md` (D-02, D-03, D-23, D-29…D-37), `Docs/Code docs/01-agents.md` (worker loop, agent-loop implementation), `Docs/Code docs/02-model-layer.md`, `Docs/Code docs/06-data-model.md` (`models`, `provider_state`, `model_calls`), `Docs/Code docs/08-infrastructure-and-repo.md`, `Docs/Code docs/claude-codex-auth-reference.md`.

**Roadmap:** Plan 1 (this) → Plan 2: tools + video pipeline → Plan 3: orchestrator, roles, scoring → Plan 4: feedback/learning, API, frontend.

## Global Constraints

- App root: `/home/malek-achich/Desktop/Ai Influencer Project/Trend Finder App` (path contains spaces: always quote it). All commands run from the app root unless stated.
- Python `>=3.12`; packages: `tf_db` in `Database/`, `tf_agent` in `Agent/`, `tf_backend` in `Backend/`; one uv workspace at the app root (D-32).
- `Database` imports nothing from `Agent`/`Backend`; `Agent` imports nothing from `Backend`.
- Claude is called **only** through the `claude` CLI: `-p --output-format json --permission-mode dontAsk --tools "" --no-session-persistence --strict-mcp-config --mcp-config <empty> --setting-sources ""`, custom `--system-prompt`, prompt on **stdin**, images as `@<space-free path>` (D-29, D-36).
- Claude subprocesses never inherit `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` (subscription billing only).
- ChatGPT: OAuth tokens in `secrets/chatgpt-auth.json` (chmod 600), separate from `~/.codex/auth.json`; calls go to `https://chatgpt.com/backend-api/codex/responses` with `store:false, stream:true` (D-29).
- Token refresh is JSON-encoded; code exchange is form-encoded (auth reference §5).
- Usage governor cools a provider at ≥ 98% of its window (D-37); default concurrency 4 per provider.
- IDs are UUIDv7; timestamps are `timestamptz`.
- `secrets/`, `media/`, `.env` are git-ignored. Credentials never appear in logs, DB rows, prompts or exceptions.
- Tests marked `live` never run by default (`-m 'not live'`); DB tests skip with a clear message when Postgres isn't reachable.
- Commit messages end with: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## Review Focus

1. **Image paths with spaces.** The app folder is `Trend Finder App`; a Claude `@` mention breaks on spaces → images must be staged to a space-free path (test in Task 6: `test_image_with_spaces_is_staged`).
2. **Prompts over 128 KB.** Linux caps one argv string at 128 KB; agent transcripts get bigger → the prompt must go through stdin, and oversized system prompts move into stdin (Task 6: `test_large_prompt_goes_through_stdin`, `test_oversized_system_prompt_moves_to_stdin`).
3. **`ANTHROPIC_API_KEY` set in the shell.** The CLI would silently bill the API instead of the subscription → it must be stripped from the subprocess environment (Task 6: `test_api_key_env_is_stripped`).
4. **Many agents hitting ChatGPT as the token expires.** Exactly one refresh must happen, not N racing refreshes that invalidate each other (Task 4: `test_concurrent_refresh_single_request`).
5. **Usage limit mid-run, then a restart.** The limited provider cools and work moves to the other one; when both cool, the caller gets the earliest reset time; after a restart, an old `auth_error` must not lock a provider out forever (Task 8: `test_usage_limit_falls_back_and_cools`, `test_all_cooling_reports_earliest_reset`, `test_load_ignores_stale_auth_error`).

---

## File Structure

```
Trend Finder App/
├── .gitignore · .env.example · pyproject.toml (uv workspace, pytest config) · conftest.py
├── config/
│   ├── model_aliases.yaml        # best/fast → concrete model per provider
│   └── roles.yaml                # role → ordered provider/model candidates
├── Database/
│   ├── pyproject.toml · alembic.ini · docker-compose.yml · init/01-test-db.sql
│   ├── migrations/ (env.py, script.py.mako, versions/0001_providers.py)
│   ├── tf_db/
│   │   ├── __init__.py · config.py (DbSettings) · ids.py (uuid7) · base.py (Base + naming) · session.py (engine, ping)
│   │   ├── models/__init__.py · models/providers.py (ModelRow, ProviderStateRow, ModelCallRow)
│   │   ├── testing.py (schema reset helpers) · pytest_plugin.py (db fixtures)
│   └── tests/ (test_ids.py, test_provider_tables.py, test_migrations.py)
├── Agent/
│   ├── pyproject.toml
│   ├── tf_agent/
│   │   ├── __init__.py · config.py (AppSettings) · testing.py (make_jwt, ListLedger, make_client)
│   │   ├── models/
│   │   │   ├── types.py · errors.py · base.py (ProviderAdapter protocol) · fake.py
│   │   │   ├── chatgpt_auth.py · chatgpt_adapter.py · claude_cli.py
│   │   │   ├── registry.py · router.py · governor.py · client.py · persistence.py
│   │   └── loop/
│   │       ├── tools.py · compaction.py · agent.py
│   └── tests/ (conftest.py, fixtures/fake_claude.py, test_*.py, live/test_live_providers.py)
└── Backend/
    ├── pyproject.toml
    ├── tf_backend/
    │   ├── __init__.py · services.py · main.py · cli.py · doctor.py
    │   └── api/__init__.py · api/health.py · api/providers.py
    └── tests/ (test_workspace.py, test_providers_api.py, test_cli.py)
```

---

### Task 1: Workspace scaffold, git, tooling

**Files:**
- Create: `.gitignore`, `.env.example`, `pyproject.toml`, `conftest.py`
- Create: `Database/pyproject.toml`, `Database/tf_db/__init__.py`
- Create: `Agent/pyproject.toml`, `Agent/tf_agent/__init__.py`
- Create: `Backend/pyproject.toml`, `Backend/tf_backend/__init__.py`
- Test: `Backend/tests/test_workspace.py`

**Interfaces:**
- Produces: importable packages `tf_db`, `tf_agent`, `tf_backend` (each with `__version__ = "0.1.0"`); `uv run pytest` runs all three test dirs; the console script `tf` (filled in Task 10).

- [ ] **Step 1: Install uv and initialize git**

```bash
cd "/home/malek-achich/Desktop/Ai Influencer Project/Trend Finder App"
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
git init -b main
```
Expected: `uv 0.x.y` printed; `Initialized empty Git repository`.

- [ ] **Step 2: Write the workspace files**

`.gitignore`:
```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.env
media/
secrets/
Frontend/node_modules/
Frontend/dist/
```

`.env.example`:
```dotenv
DATABASE_URL=postgresql+asyncpg://tf:tf@localhost:5433/trendfinder
DATABASE_URL_TEST=postgresql+asyncpg://tf:tf@localhost:5433/trendfinder_test
CLAUDE_BIN=claude
CHATGPT_AUTH_FILE=secrets/chatgpt-auth.json
PER_PROVIDER_CONCURRENCY=4
```

`pyproject.toml` (app root):
```toml
[project]
name = "trend-finder"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["tf-db", "tf-agent", "tf-backend"]

[tool.uv]
package = false

[tool.uv.workspace]
members = ["Database", "Agent", "Backend"]

[tool.uv.sources]
tf-db = { workspace = true }
tf-agent = { workspace = true }
tf-backend = { workspace = true }

[dependency-groups]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "ruff>=0.7"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["Database/tests", "Agent/tests", "Backend/tests"]
addopts = "--import-mode=importlib -m 'not live'"
markers = [
  "live: calls real providers or the network (opt-in: -m live)",
]

[tool.ruff]
line-length = 110
target-version = "py312"
```

`conftest.py` (app root):
```python
"""Root pytest config. Shared DB fixtures are registered in Task 2."""
```

`Database/pyproject.toml`:
```toml
[project]
name = "tf-db"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "sqlalchemy[asyncio]>=2.0.36",
  "asyncpg>=0.30",
  "alembic>=1.14",
  "pydantic-settings>=2.6",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["tf_db"]
```

`Agent/pyproject.toml`:
```toml
[project]
name = "tf-agent"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "tf-db",
  "httpx>=0.28",
  "pydantic>=2.9",
  "pydantic-settings>=2.6",
  "pyyaml>=6.0",
]

[tool.uv.sources]
tf-db = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["tf_agent"]
```

`Backend/pyproject.toml`:
```toml
[project]
name = "tf-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "tf-agent",
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "typer>=0.13",
]

[project.scripts]
tf = "tf_backend.cli:app"

[tool.uv.sources]
tf-agent = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["tf_backend"]
```

`Database/tf_db/__init__.py`, `Agent/tf_agent/__init__.py`, `Backend/tf_backend/__init__.py` (same content in each):
```python
__version__ = "0.1.0"
```

`Backend/tf_backend/cli.py` (placeholder entry point so the script resolves; replaced in Task 10):
```python
import typer

app = typer.Typer(no_args_is_help=True, help="Trend Finder command line")


@app.command()
def version() -> None:
    """Print the app version."""
    from tf_backend import __version__

    typer.echo(__version__)
```

- [ ] **Step 3: Write the failing test**

`Backend/tests/test_workspace.py`:
```python
def test_packages_import_with_same_version():
    import tf_agent
    import tf_backend
    import tf_db

    assert tf_db.__version__ == tf_agent.__version__ == tf_backend.__version__ == "0.1.0"
```

- [ ] **Step 4: Sync and run tests**

```bash
uv sync
uv run pytest -v
uv run tf version
```
Expected: `1 passed`; `0.1.0`.

- [ ] **Step 5: Commit (includes the existing Docs/)**

```bash
git add .
git commit -m "chore: scaffold uv workspace (Database, Agent, Backend) and docs" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Database container, base models, migrations, test fixtures

**Files:**
- Create: `Database/docker-compose.yml`, `Database/init/01-test-db.sql`
- Create: `Database/tf_db/config.py`, `ids.py`, `base.py`, `session.py`, `models/__init__.py`, `models/providers.py`, `testing.py`, `pytest_plugin.py`
- Create: `Database/alembic.ini`, `Database/migrations/env.py`, `Database/migrations/script.py.mako`, `Database/migrations/versions/0001_providers.py`
- Modify: `conftest.py` (register plugin)
- Test: `Database/tests/test_ids.py`, `Database/tests/test_provider_tables.py`, `Database/tests/test_migrations.py`

**Interfaces:**
- Produces:
  - `tf_db.config.DbSettings` (`database_url: str`, `database_url_test: str`)
  - `tf_db.ids.uuid7() -> uuid.UUID`
  - `tf_db.base.Base` (DeclarativeBase with naming convention)
  - `tf_db.session.make_engine(url) -> AsyncEngine`, `make_sessionmaker(engine) -> async_sessionmaker[AsyncSession]`, `async ping(url) -> bool`
  - ORM: `tf_db.models.ModelRow`, `ProviderStateRow`, `ModelCallRow` (columns exactly as in Step 4)
  - pytest fixtures (any test dir): `_db_schema` (session, returns test URL or skips), `db_engine`, `db_sessionmaker` (function-scoped, tables truncated)
  - `tf_db.testing.reset_schema_sync(url)`, `ALEMBIC_INI: Path`

- [ ] **Step 1: Docker Compose and test-DB init**

`Database/docker-compose.yml`:
```yaml
name: trendfinder
services:
  db:
    image: pgvector/pgvector:pg17
    environment:
      POSTGRES_USER: tf
      POSTGRES_PASSWORD: tf
      POSTGRES_DB: trendfinder
    ports:
      - "127.0.0.1:5433:5432"
    volumes:
      - tf_pgdata:/var/lib/postgresql/data
      - ./init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U tf -d trendfinder"]
      interval: 5s
      timeout: 3s
      retries: 20
volumes:
  tf_pgdata: {}
```

`Database/init/01-test-db.sql`:
```sql
CREATE DATABASE trendfinder_test;
```

Start it (Docker Desktop must be running):
```bash
docker compose -f Database/docker-compose.yml up -d
docker compose -f Database/docker-compose.yml ps
```
Expected: `db` is `healthy` after ~10 s.

- [ ] **Step 2: Write failing tests for ids and tables**

`Database/tests/test_ids.py`:
```python
import time
import uuid

from tf_db.ids import uuid7


def test_uuid7_version_and_variant():
    u = uuid7()
    assert u.version == 7
    assert u.variant == uuid.RFC_4122


def test_uuid7_sorts_by_creation_time():
    a = uuid7()
    time.sleep(0.002)
    b = uuid7()
    assert a < b


def test_uuid7_is_unique():
    assert len({uuid7() for _ in range(2000)}) == 2000
```

`Database/tests/test_provider_tables.py`:
```python
from sqlalchemy import select

from tf_db.models import ModelCallRow, ModelRow, ProviderStateRow


async def test_model_call_roundtrip(db_sessionmaker):
    async with db_sessionmaker() as s:
        s.add(ModelCallRow(role="scout", provider="claude", model="opus", latency_ms=1200,
                           status="ok", input_tokens=10, output_tokens=5))
        await s.commit()
        got = (await s.execute(select(ModelCallRow))).scalar_one()
    assert got.id.version == 7
    assert got.images == 0
    assert got.created_at is not None
    assert got.run_id is None


async def test_model_row_defaults(db_sessionmaker):
    async with db_sessionmaker() as s:
        s.add(ModelRow(provider="chatgpt", model_id="gpt-6-luna"))
        await s.commit()
        got = (await s.execute(select(ModelRow))).scalar_one()
    assert got.capabilities == {}
    assert got.available is True


async def test_provider_state_defaults(db_sessionmaker):
    async with db_sessionmaker() as s:
        s.add(ProviderStateRow(provider="claude"))
        await s.commit()
        got = (await s.execute(select(ProviderStateRow))).scalar_one()
    assert got.status == "ok"
    assert got.cooling_until is None
```

`Database/tests/test_migrations.py`:
```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest Database/tests -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'tf_db.ids'` (and fixtures not found).

- [ ] **Step 4: Implement tf_db**

`Database/tf_db/config.py`:
```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://tf:tf@localhost:5433/trendfinder"
    database_url_test: str = "postgresql+asyncpg://tf:tf@localhost:5433/trendfinder_test"
```

`Database/tf_db/ids.py`:
```python
"""UUIDv7 (RFC 9562): 48-bit unix-ms timestamp, version 7, RFC 4122 variant, 74 random bits."""
import os
import time
import uuid


def uuid7() -> uuid.UUID:
    ts_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")  # 80 random bits; 74 are used
    rand_a = (rand >> 62) & 0xFFF
    rand_b = rand & ((1 << 62) - 1)
    value = ((ts_ms & ((1 << 48) - 1)) << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)
```

`Database/tf_db/base.py`:
```python
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

`Database/tf_db/session.py`:
```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping(url: str) -> bool:
    """True when the database at `url` accepts a connection."""
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()
```

`Database/tf_db/models/providers.py`:
```python
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func, text, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from tf_db.base import Base
from tf_db.ids import uuid7


class ModelRow(Base):
    __tablename__ = "models"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(256))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    available: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProviderStateRow(Base):
    __tablename__ = "provider_state"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="ok", server_default="ok")
    cooling_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_percent: Mapped[float | None] = mapped_column(Float)
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ModelCallRow(Base):
    __tablename__ = "model_calls"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    run_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    role: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    images: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    error_class: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
```

`Database/tf_db/models/__init__.py`:
```python
from tf_db.models.providers import ModelCallRow, ModelRow, ProviderStateRow

__all__ = ["ModelCallRow", "ModelRow", "ProviderStateRow"]
```

`Database/tf_db/testing.py`:
```python
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
```

`Database/tf_db/pytest_plugin.py`:
```python
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
```

Root `conftest.py` (replace content):
```python
"""Root pytest config: shared DB fixtures for Database/, Agent/ and Backend/ tests."""
pytest_plugins = ["tf_db.pytest_plugin"]
```

- [ ] **Step 5: Alembic config and first migration**

`Database/alembic.ini`:
```ini
[alembic]
script_location = %(here)s/migrations
prepend_sys_path = .
path_separator = os

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

`Database/migrations/env.py`:
```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import tf_db.models  # noqa: F401
from tf_db.base import Base
from tf_db.config import DbSettings

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def _url() -> str:
    return config.attributes.get("url") or DbSettings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url(), poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

`Database/migrations/script.py.mako`:
```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

`Database/migrations/versions/0001_providers.py`:
```python
"""providers: models, provider_state, model_calls

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "models",
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=True),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("available", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("provider", "model_id", name=op.f("pk_models")),
    )
    op.create_table(
        "provider_state",
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), server_default="ok", nullable=False),
        sa.Column("cooling_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_percent", sa.Float(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("provider", name=op.f("pk_provider_state")),
    )
    op.create_table(
        "model_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("images", sa.Integer(), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_class", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_calls")),
    )
    op.create_index(op.f("ix_model_calls_run_id"), "model_calls", ["run_id"])
    op.create_index(op.f("ix_model_calls_created_at"), "model_calls", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_model_calls_created_at"), table_name="model_calls")
    op.drop_index(op.f("ix_model_calls_run_id"), table_name="model_calls")
    op.drop_table("model_calls")
    op.drop_table("provider_state")
    op.drop_table("models")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest Database/tests -v`
Expected: `7 passed` (if Docker is down, the 4 DB tests show `SKIPPED (Postgres test DB not reachable…)`; start Docker and rerun until they pass).

- [ ] **Step 7: Migrate the dev database and commit**

```bash
uv run alembic -c Database/alembic.ini upgrade head
git add Database conftest.py
git commit -m "feat(db): postgres container, base models, provider tables, alembic 0001" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
Expected: `Running upgrade  -> 0001`.

---

### Task 3: Provider-neutral model types, errors, adapter protocol, FakeAdapter

**Files:**
- Create: `Agent/tf_agent/models/__init__.py` (empty), `types.py`, `errors.py`, `base.py`, `fake.py`
- Test: `Agent/tests/test_model_types.py`

**Interfaces:**
- Produces (used by every later task):
  - `TextPart(text)`, `ImagePart(path, mime="image/jpeg")`, `ToolCall(id, name, arguments: dict)`
  - `Message(role, parts, tool_calls, tool_call_id, tool_name)` with `Message.user(text, images=())`, `Message.assistant(text="", tool_calls=None)`, `Message.tool_result(call, content)`, `.text()`, `.images()`
  - `ToolSpec(name, description, parameters: dict)`
  - `CompletionRequest(model, system, messages, tools=[], require_tool=False, output_schema=None, schema_name="result", reasoning_effort=None, timeout_s=300.0)`: raises `ValueError` if tools+output_schema, require_tool without tools, or no messages
  - `Usage(input_tokens, output_tokens)`, `RateInfo(used_percent, window_minutes, resets_at)`
  - `CompletionResponse(provider, model, text="", tool_calls=[], structured=None, usage=Usage(), rate=None)`
  - `ModelInfo(provider, model_id, display_name=None, context_window=None, vision=True, priority=100, hidden=False, reasoning_levels=())`
  - `ProviderHealth(provider, connected, detail="", account=None)`
  - Errors: `ProviderError(provider, message)` → `AuthRequired`, `UsageLimited(provider, message, reset_at=None)`, `TransientProviderError`, `InvalidRequest`, `MalformedResponse`, `AllProvidersUnavailable(role, earliest_reset)`
  - `ProviderAdapter` protocol: `name`, `async list_models()`, `async complete(req)`, `async health()`
  - `FakeAdapter(name="fake", script=(), models=None, delay_s=0.0)` with `.requests`, `.push(*items)`, `.max_in_flight`, `.models_error`; `tool_call_response(provider, *calls, text="")`, `text_response(provider, text="", structured=None)`

- [ ] **Step 1: Write the failing tests**

`Agent/tests/test_model_types.py`:
```python
import pytest

from tf_agent.models.base import ProviderAdapter
from tf_agent.models.errors import AllProvidersUnavailable, ProviderError, UsageLimited
from tf_agent.models.fake import FakeAdapter, text_response, tool_call_response
from tf_agent.models.types import CompletionRequest, ImagePart, Message, ToolCall, ToolSpec


def _req(**kw):
    return CompletionRequest(model="m", system="s", messages=[Message.user("hi")], **kw)


def test_message_helpers():
    m = Message.user("hello", [ImagePart("/tmp/a.jpg")])
    assert m.text() == "hello"
    assert [i.path for i in m.images()] == ["/tmp/a.jpg"]
    call = ToolCall("c1", "add", {"a": 1})
    assert Message.assistant("", [call]).tool_calls == [call]
    tr = Message.tool_result(call, "2")
    assert (tr.role, tr.tool_call_id, tr.tool_name, tr.text()) == ("tool", "c1", "add", "2")


def test_request_rejects_tools_with_schema():
    with pytest.raises(ValueError, match="mutually exclusive"):
        _req(tools=[ToolSpec("t", "d", {})], output_schema={"type": "object"})


def test_request_require_tool_needs_tools():
    with pytest.raises(ValueError, match="require_tool"):
        _req(require_tool=True)


def test_request_needs_messages():
    with pytest.raises(ValueError, match="message"):
        CompletionRequest(model="m", system="s", messages=[])


def test_errors_carry_provider_and_reset():
    e = UsageLimited("chatgpt", "limit", reset_at=123.0)
    assert isinstance(e, ProviderError) and e.provider == "chatgpt" and e.reset_at == 123.0
    a = AllProvidersUnavailable("scout", 99.0)
    assert a.role == "scout" and a.earliest_reset == 99.0


async def test_fake_adapter_scripted_and_records():
    fa = FakeAdapter("claude", [tool_call_response("claude", ("add", {"a": 1, "b": 2}))])
    req = _req()
    resp = await fa.complete(req)
    assert resp.tool_calls[0].name == "add" and resp.tool_calls[0].arguments == {"a": 1, "b": 2}
    assert fa.requests == [req]


async def test_fake_adapter_raises_scripted_exception():
    fa = FakeAdapter("claude", [UsageLimited("claude", "x")])
    with pytest.raises(UsageLimited):
        await fa.complete(_req())


async def test_fake_adapter_callable_script_and_empty_script():
    fa = FakeAdapter("x", [lambda r: text_response("x", text=r.system)])
    assert (await fa.complete(_req())).text == "s"
    with pytest.raises(AssertionError, match="no scripted response"):
        await fa.complete(_req())


def test_fake_adapter_satisfies_protocol():
    assert isinstance(FakeAdapter(), ProviderAdapter)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest Agent/tests/test_model_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tf_agent.models'`.

- [ ] **Step 3: Implement**

`Agent/tf_agent/models/__init__.py`: empty file.

`Agent/tf_agent/models/types.py`:
```python
"""Provider-neutral model types shared by every adapter and the agent loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class TextPart:
    text: str
    kind: Literal["text"] = "text"


@dataclass(frozen=True)
class ImagePart:
    path: str
    mime: str = "image/jpeg"
    kind: Literal["image"] = "image"


Part = TextPart | ImagePart


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    parts: list[Part] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None

    @classmethod
    def user(cls, text: str, images: list[ImagePart] | tuple[ImagePart, ...] = ()) -> Message:
        return cls(role="user", parts=[TextPart(text), *images])

    @classmethod
    def assistant(cls, text: str = "", tool_calls: list[ToolCall] | None = None) -> Message:
        return cls(role="assistant", parts=[TextPart(text)] if text else [], tool_calls=list(tool_calls or []))

    @classmethod
    def tool_result(cls, call: ToolCall, content: str) -> Message:
        return cls(role="tool", parts=[TextPart(content)], tool_call_id=call.id, tool_name=call.name)

    def text(self) -> str:
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    def images(self) -> list[ImagePart]:
        return [p for p in self.parts if isinstance(p, ImagePart)]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class CompletionRequest:
    model: str
    system: str
    messages: list[Message]
    tools: list[ToolSpec] = field(default_factory=list)
    require_tool: bool = False
    output_schema: dict[str, Any] | None = None
    schema_name: str = "result"
    reasoning_effort: str | None = None
    timeout_s: float = 300.0

    def __post_init__(self) -> None:
        if self.tools and self.output_schema is not None:
            raise ValueError("tools and output_schema are mutually exclusive")
        if self.require_tool and not self.tools:
            raise ValueError("require_tool needs at least one tool")
        if not self.messages:
            raise ValueError("at least one message is required")


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class RateInfo:
    used_percent: float | None = None
    window_minutes: int | None = None
    resets_at: float | None = None


@dataclass
class CompletionResponse:
    provider: str
    model: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    structured: dict[str, Any] | None = None
    usage: Usage = field(default_factory=Usage)
    rate: RateInfo | None = None


@dataclass(frozen=True)
class ModelInfo:
    provider: str
    model_id: str
    display_name: str | None = None
    context_window: int | None = None
    vision: bool = True
    priority: int = 100
    hidden: bool = False
    reasoning_levels: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    connected: bool
    detail: str = ""
    account: str | None = None
```

`Agent/tf_agent/models/errors.py`:
```python
class ProviderError(Exception):
    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.message = message


class AuthRequired(ProviderError):
    """Credentials missing, expired or rejected. Needs a human to log in again."""


class UsageLimited(ProviderError):
    """Subscription window exhausted. `reset_at` is a unix timestamp when known."""

    def __init__(self, provider: str, message: str, reset_at: float | None = None) -> None:
        super().__init__(provider, message)
        self.reset_at = reset_at


class TransientProviderError(ProviderError):
    """Network/5xx/timeout. Safe to retry."""


class InvalidRequest(ProviderError):
    """The provider rejected the request itself. Retrying won't help."""


class MalformedResponse(ProviderError):
    """The provider answered but the payload couldn't be parsed into the expected shape."""


class AllProvidersUnavailable(ProviderError):
    def __init__(self, role: str, earliest_reset: float | None) -> None:
        super().__init__("router", f"no provider available for role {role!r}")
        self.role = role
        self.earliest_reset = earliest_reset
```

`Agent/tf_agent/models/base.py`:
```python
from typing import Protocol, runtime_checkable

from tf_agent.models.types import CompletionRequest, CompletionResponse, ModelInfo, ProviderHealth


@runtime_checkable
class ProviderAdapter(Protocol):
    name: str

    async def list_models(self) -> list[ModelInfo]: ...

    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...

    async def health(self) -> ProviderHealth: ...
```

`Agent/tf_agent/models/fake.py`:
```python
"""Scripted adapter for tests: every complete() pops the next scripted item."""
import asyncio
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

from tf_agent.models.types import (
    CompletionRequest,
    CompletionResponse,
    ModelInfo,
    ProviderHealth,
    ToolCall,
)

Scripted = CompletionResponse | Exception | Callable[[CompletionRequest], CompletionResponse]


class FakeAdapter:
    def __init__(
        self,
        name: str = "fake",
        script: Iterable[Scripted] = (),
        models: list[ModelInfo] | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self.name = name
        self._script: deque[Scripted] = deque(script)
        self._models = models if models is not None else [ModelInfo(name, f"{name}-model")]
        self.delay_s = delay_s
        self.requests: list[CompletionRequest] = []
        self.models_error: Exception | None = None
        self.in_flight = 0
        self.max_in_flight = 0

    def push(self, *items: Scripted) -> None:
        self._script.extend(items)

    async def list_models(self) -> list[ModelInfo]:
        if self.models_error is not None:
            raise self.models_error
        return list(self._models)

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        self.requests.append(req)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            if not self._script:
                raise AssertionError(f"FakeAdapter {self.name!r}: no scripted response left")
            item = self._script.popleft()
            if isinstance(item, Exception):
                raise item
            if callable(item):
                return item(req)
            return item
        finally:
            self.in_flight -= 1

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "fake adapter")


def tool_call_response(provider: str, *calls: tuple[str, dict[str, Any]], text: str = "") -> CompletionResponse:
    return CompletionResponse(
        provider=provider,
        model=f"{provider}-model",
        text=text,
        tool_calls=[ToolCall(f"call_{uuid.uuid4().hex[:8]}", name, args) for name, args in calls],
    )


def text_response(provider: str, text: str = "", structured: dict[str, Any] | None = None) -> CompletionResponse:
    return CompletionResponse(provider=provider, model=f"{provider}-model", text=text, structured=structured)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest Agent/tests/test_model_types.py -v`
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add Agent
git commit -m "feat(agent): provider-neutral model types, errors, adapter protocol, fake adapter" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: ChatGPT subscription OAuth (`ChatGptAuth`)

Port of the owner's reference (`Docs/Code docs/claude-codex-auth-reference.md` §2 and §4), with these changes: an injectable HTTP client factory (for tests), an injectable callback port, a clear error when the port is busy, `status()` / `logout()` / `wait_connected()` helpers, and an `updated_at` stamp so a new login can be told apart from a stale file.

**Files:**
- Create: `Agent/tf_agent/models/chatgpt_auth.py`, `Agent/tf_agent/testing.py` (only `make_jwt` in this task)
- Test: `Agent/tests/test_chatgpt_auth.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - constants `ISSUER`, `CLIENT_ID`, `CODEX_BASE_URL`, `CALLBACK_PORT`, `REDIRECT_URI`, `DEVICE_REDIRECT_URI`
  - `ChatGptAuthError(RuntimeError)`
  - `jwt_payload(token) -> dict`, `chatgpt_claims(id_token, access_token) -> dict` (keys: email, plan_type, account_id, is_fedramp, exp)
  - `ChatGptAuth(auth_file: Path, client_factory: Callable[[], httpx.AsyncClient] | None = None, callback_port: int = 1455, clock=time.time)` with: `load() -> dict | None`, `status() -> {"connected", "email", "plan"}`, `logout()`, `async ensure_fresh(force=False) -> dict`, `async headers(accept="text/event-stream") -> dict[str, str]`, `async browser_login_start() -> str`, `async handle_callback(code, state, error=None) -> dict`, `async stop_callback_server()`, `async device_login_start() -> {"verification_url", "user_code"}`, `async wait_connected(since: float, timeout_s=900, interval_s=0.25) -> bool`
  - `tf_agent.testing.make_jwt(exp=None, account_id="acc_1", email="nico@example.com", plan="plus", fedramp=False) -> str`

- [ ] **Step 1: Write the failing tests**

`Agent/tf_agent/testing.py`:
```python
"""Test helpers importable from any test directory."""
import base64
import json
import time


def make_jwt(exp: int | None = None, account_id: str = "acc_1", email: str = "nico@example.com",
             plan: str = "plus", fedramp: bool = False) -> str:
    def enc(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    payload = {
        "exp": exp if exp is not None else int(time.time()) + 3600,
        "email": email,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": plan,
            "chatgpt_account_is_fedramp": fedramp,
        },
    }
    return f"{enc({'alg': 'none'})}.{enc(payload)}.sig"
```

`Agent/tests/test_chatgpt_auth.py`:
```python
import asyncio
import json
import socket
import stat
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tf_agent.models.chatgpt_auth import (
    CLIENT_ID,
    DEVICE_REDIRECT_URI,
    REDIRECT_URI,
    ChatGptAuth,
    ChatGptAuthError,
    chatgpt_claims,
)
from tf_agent.testing import make_jwt


def factory(handler):
    transport = httpx.MockTransport(handler)
    return lambda: httpx.AsyncClient(transport=transport)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def connected_auth(tmp_path, handler=None, exp_in_s=3600, fedramp=False, **kw) -> ChatGptAuth:
    def never(request):
        raise AssertionError(f"unexpected HTTP call {request.url}")

    auth = ChatGptAuth(tmp_path / "chatgpt-auth.json", client_factory=factory(handler or never), **kw)
    tok = make_jwt(exp=int(time.time()) + exp_in_s, fedramp=fedramp)
    auth._persist({"access_token": tok, "id_token": tok, "refresh_token": "r1"})
    return auth


def test_claims_parsing():
    c = chatgpt_claims(make_jwt(exp=123), None)
    assert c == {"email": "nico@example.com", "plan_type": "plus", "account_id": "acc_1",
                 "is_fedramp": False, "exp": 123}


async def test_fresh_token_is_not_refreshed(tmp_path):
    auth = connected_auth(tmp_path)
    before = auth.load()["access_token"]
    assert (await auth.ensure_fresh())["access_token"] == before


async def test_refresh_near_expiry_uses_json_and_keeps_refresh_token(tmp_path):
    new_token = make_jwt(exp=int(time.time()) + 7200)

    def handler(request):
        assert request.url.path == "/oauth/token"
        assert json.loads(request.content) == {
            "client_id": CLIENT_ID, "grant_type": "refresh_token", "refresh_token": "r1"}
        return httpx.Response(200, json={"access_token": new_token, "id_token": new_token})

    auth = connected_auth(tmp_path, handler, exp_in_s=30)
    data = await auth.ensure_fresh()
    assert data["access_token"] == new_token
    assert data["refresh_token"] == "r1"
    assert stat.S_IMODE(auth.auth_file.stat().st_mode) == 0o600


async def test_concurrent_refresh_single_request(tmp_path):
    calls = 0
    new_token = make_jwt(exp=int(time.time()) + 7200)

    async def handler(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"access_token": new_token, "refresh_token": "r2"})

    auth = connected_auth(tmp_path, handler, exp_in_s=10)
    results = await asyncio.gather(*(auth.ensure_fresh() for _ in range(5)))
    assert calls == 1
    assert {r["access_token"] for r in results} == {new_token}


async def test_refresh_rejected_asks_for_login(tmp_path):
    auth = connected_auth(tmp_path, lambda r: httpx.Response(401, json={"error": "invalid_grant"}), exp_in_s=10)
    with pytest.raises(ChatGptAuthError, match="tf login chatgpt"):
        await auth.ensure_fresh()


async def test_not_connected(tmp_path):
    auth = ChatGptAuth(tmp_path / "missing.json")
    assert auth.status() == {"connected": False, "email": None, "plan": None}
    with pytest.raises(ChatGptAuthError, match="not connected"):
        await auth.ensure_fresh()


async def test_headers_include_account_and_fedramp(tmp_path):
    h = await connected_auth(tmp_path, fedramp=True).headers()
    assert h["chatgpt-account-id"] == "acc_1"
    assert h["authorization"].startswith("Bearer ")
    assert h["x-openai-fedramp"] == "true"
    assert h["accept"] == "text/event-stream"


async def test_browser_callback_exchanges_form_encoded_code(tmp_path):
    tokens = make_jwt()

    def handler(request):
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["authorization_code"]
        assert form["code"] == ["the-code"]
        assert form["redirect_uri"] == [REDIRECT_URI]
        assert form["code_verifier"][0]
        return httpx.Response(200, json={"access_token": tokens, "id_token": tokens, "refresh_token": "r1"})

    port = free_port()
    auth = ChatGptAuth(tmp_path / "a.json", client_factory=factory(handler), callback_port=port)
    url = await auth.browser_login_start()
    q = parse_qs(urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == [CLIENT_ID]
    try:
        async with httpx.AsyncClient() as real:
            r = await real.get(f"http://127.0.0.1:{port}/auth/callback",
                               params={"code": "the-code", "state": q["state"][0]})
        assert r.status_code == 200
        assert auth.status()["connected"] is True
    finally:
        await auth.stop_callback_server()


async def test_callback_rejects_unknown_state(tmp_path):
    auth = ChatGptAuth(tmp_path / "a.json")
    with pytest.raises(ChatGptAuthError, match="state"):
        await auth.handle_callback("code", "bogus")


async def test_port_busy_suggests_device_login(tmp_path):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        auth = ChatGptAuth(tmp_path / "a.json", callback_port=port)
        with pytest.raises(ChatGptAuthError, match="--device"):
            await auth.browser_login_start()


async def test_device_flow_polls_until_authorized(tmp_path):
    polls = 0
    tokens = make_jwt()

    def handler(request):
        nonlocal polls
        path = request.url.path
        if path == "/api/accounts/deviceauth/usercode":
            return httpx.Response(200, json={"user_code": "ABCD-1234", "device_auth_id": "dev1", "interval": 1})
        if path == "/api/accounts/deviceauth/token":
            polls += 1
            if polls == 1:
                return httpx.Response(403, json={"status": "pending"})
            return httpx.Response(200, json={"authorization_code": "ac", "code_verifier": "cv"})
        if path == "/oauth/token":
            form = parse_qs(request.content.decode())
            assert form["redirect_uri"] == [DEVICE_REDIRECT_URI] and form["code_verifier"] == ["cv"]
            return httpx.Response(200, json={"access_token": tokens, "id_token": tokens, "refresh_token": "r1"})
        raise AssertionError(path)

    auth = ChatGptAuth(tmp_path / "a.json", client_factory=factory(handler))
    started = time.time()
    info = await auth.device_login_start()
    assert info["user_code"] == "ABCD-1234"
    assert await auth.wait_connected(since=started, timeout_s=10) is True
    assert polls == 2


def test_logout_removes_file(tmp_path):
    auth = connected_auth(tmp_path)
    auth.logout()
    assert not auth.auth_file.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest Agent/tests/test_chatgpt_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tf_agent.models.chatgpt_auth'`.

- [ ] **Step 3: Implement**

`Agent/tf_agent/models/chatgpt_auth.py`:
```python
"""ChatGPT (Codex backend) subscription OAuth: PKCE browser flow + device-code flow.

Ported from the owner's Operis reference: Docs/Code docs/claude-codex-auth-reference.md.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # public Codex CLI client id
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CALLBACK_PORT = 1455  # the loopback port registered for this client id
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/auth/callback"
DEVICE_REDIRECT_URI = f"{ISSUER}/deviceauth/callback"
SCOPES = "openid profile email offline_access api.connectors.read api.connectors.invoke"
REFRESH_SKEW_S = 90

ClientFactory = Callable[[], httpx.AsyncClient]


class ChatGptAuthError(RuntimeError):
    pass


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def jwt_payload(token: str | None) -> dict[str, Any]:
    try:
        part = (token or "").split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def chatgpt_claims(id_token: str | None, access_token: str | None) -> dict[str, Any]:
    raw = jwt_payload(id_token) or jwt_payload(access_token)
    auth = raw.get("https://api.openai.com/auth") or {}
    profile = raw.get("https://api.openai.com/profile") or {}
    return {
        "email": raw.get("email") or profile.get("email"),
        "plan_type": auth.get("chatgpt_plan_type"),
        "account_id": auth.get("chatgpt_account_id"),
        "is_fedramp": bool(auth.get("chatgpt_account_is_fedramp")),
        "exp": raw.get("exp"),
    }


class ChatGptAuth:
    def __init__(
        self,
        auth_file: Path,
        client_factory: ClientFactory | None = None,
        callback_port: int = CALLBACK_PORT,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.auth_file = Path(auth_file)
        self.callback_port = callback_port
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=30))
        self._clock = clock
        self._pending: dict[str, str] = {}  # state -> PKCE verifier
        self._lock = asyncio.Lock()
        self._server: asyncio.AbstractServer | None = None
        self._device_task: asyncio.Task[None] | None = None

    # ---- storage ----
    def load(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.auth_file.read_text())
        except Exception:
            return None

    def _save(self, data: dict[str, Any]) -> None:
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.auth_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.auth_file)

    def _persist(self, tokens: dict[str, Any]) -> dict[str, Any]:
        claims = chatgpt_claims(tokens.get("id_token"), tokens.get("access_token"))
        data = {
            "id_token": tokens.get("id_token"),
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "account_id": claims["account_id"],
            "claims": claims,
            "updated_at": self._clock(),
        }
        self._save(data)
        return data

    def status(self) -> dict[str, Any]:
        data = self.load()
        if not data or not data.get("refresh_token"):
            return {"connected": False, "email": None, "plan": None}
        claims = data.get("claims") or {}
        return {"connected": True, "email": claims.get("email"), "plan": claims.get("plan_type")}

    def logout(self) -> None:
        self.auth_file.unlink(missing_ok=True)

    # ---- token endpoint ----
    async def _exchange(self, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
        async with self._client_factory() as client:
            r = await client.post(f"{ISSUER}/oauth/token", data={  # form-encoded (auth reference §5)
                "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                "client_id": CLIENT_ID, "code_verifier": verifier,
            })
        if r.status_code >= 400:
            raise ChatGptAuthError(f"token exchange failed ({r.status_code})")
        return self._persist(r.json())

    async def ensure_fresh(self, force: bool = False) -> dict[str, Any]:
        async with self._lock:
            data = self.load()
            if not data or not data.get("refresh_token"):
                raise ChatGptAuthError("not connected: run `tf login chatgpt`")
            exp = jwt_payload(data["access_token"]).get("exp")
            if not force and (not exp or exp - self._clock() > REFRESH_SKEW_S):
                return data
            async with self._client_factory() as client:
                r = await client.post(f"{ISSUER}/oauth/token", json={  # JSON body for refresh
                    "client_id": CLIENT_ID, "grant_type": "refresh_token",
                    "refresh_token": data["refresh_token"],
                })
            if r.status_code >= 400:
                raise ChatGptAuthError(f"token refresh rejected ({r.status_code}): run `tf login chatgpt` again")
            new = r.json()
            return self._persist({
                "id_token": new.get("id_token") or data.get("id_token"),
                "access_token": new.get("access_token") or data["access_token"],
                "refresh_token": new.get("refresh_token") or data["refresh_token"],
            })

    async def headers(self, accept: str = "text/event-stream") -> dict[str, str]:
        data = await self.ensure_fresh()
        if not data.get("account_id"):
            raise ChatGptAuthError("missing chatgpt account id in token claims")
        h = {
            "authorization": f"Bearer {data['access_token']}",
            "chatgpt-account-id": data["account_id"],
            "content-type": "application/json",
            "accept": accept,
        }
        if (data.get("claims") or {}).get("is_fedramp"):
            h["x-openai-fedramp"] = "true"
        return h

    # ---- Flow A: browser + loopback callback ----
    async def browser_login_start(self) -> str:
        await self._ensure_callback_server()
        verifier = _b64url(secrets.token_bytes(64))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        state = secrets.token_urlsafe(32)
        self._pending[state] = verifier
        return f"{ISSUER}/oauth/authorize?" + urlencode({
            "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
            "scope": SCOPES, "code_challenge": challenge, "code_challenge_method": "S256",
            "id_token_add_organizations": "true", "codex_cli_simplified_flow": "true",
            "state": state, "originator": "codex_cli_rs",
        })

    async def handle_callback(self, code: str | None, state: str | None, error: str | None = None) -> dict[str, Any]:
        if error:
            raise ChatGptAuthError(f"login refused: {error}")
        verifier = self._pending.pop(state or "", None)
        if not code or not verifier:
            raise ChatGptAuthError("invalid or expired login state; start the login again")
        return await self._exchange(code, verifier, REDIRECT_URI)

    async def _ensure_callback_server(self) -> None:
        if self._server and self._server.is_serving():
            return

        async def on_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            ok = True
            try:
                head = (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)).decode(errors="replace")
                target = urlparse(head.split()[1])
                if target.path != "/auth/callback":
                    raise ChatGptAuthError("bad path")
                q = parse_qs(target.query)
                await self.handle_callback((q.get("code") or [None])[0], (q.get("state") or [None])[0],
                                           (q.get("error") or [None])[0])
            except Exception:
                ok = False
            body = (b"<h2>Login complete. You can close this tab.</h2><script>setTimeout(()=>close(),1200)</script>"
                    if ok else b"<h2>Login failed. Retry from the app.</h2>")
            writer.write(b"HTTP/1.1 " + (b"200 OK" if ok else b"400 Bad Request")
                         + b"\r\nContent-Type: text/html\r\nContent-Length: " + str(len(body)).encode()
                         + b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
            writer.close()

        try:
            self._server = await asyncio.start_server(on_conn, "127.0.0.1", self.callback_port)
        except OSError as e:
            raise ChatGptAuthError(
                f"port {self.callback_port} is busy ({e.strerror}); is a Codex CLI login running? "
                "Use `tf login chatgpt --device` instead."
            ) from e

    async def stop_callback_server(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ---- Flow B: device code ----
    async def device_login_start(self) -> dict[str, str]:
        async with self._client_factory() as client:
            r = await client.post(f"{ISSUER}/api/accounts/deviceauth/usercode", json={"client_id": CLIENT_ID})
        if r.status_code >= 400:
            raise ChatGptAuthError(f"device login could not start ({r.status_code})")
        d = r.json()
        info = {
            "verification_url": f"{ISSUER}/codex/device",
            "user_code": d.get("user_code") or d.get("usercode"),
            "device_auth_id": d["device_auth_id"],
            "interval": max(1, int(d.get("interval") or 5)),
        }
        self._device_task = asyncio.create_task(self._device_poll(info))
        return {"verification_url": info["verification_url"], "user_code": info["user_code"]}

    async def _device_poll(self, info: dict[str, Any], timeout_s: int = 900) -> None:
        deadline = self._clock() + timeout_s
        async with self._client_factory() as client:
            while self._clock() < deadline:
                r = await client.post(f"{ISSUER}/api/accounts/deviceauth/token",
                                      json={"device_auth_id": info["device_auth_id"], "user_code": info["user_code"]})
                if r.status_code in (403, 404):  # still pending (auth reference §5)
                    await asyncio.sleep(info["interval"])
                    continue
                if r.status_code >= 400:
                    raise ChatGptAuthError(f"device login failed ({r.status_code})")
                d = r.json()
                await self._exchange(d["authorization_code"], d["code_verifier"], DEVICE_REDIRECT_URI)
                return
        raise ChatGptAuthError("device login timed out")

    async def wait_connected(self, since: float, timeout_s: float = 900, interval_s: float = 0.25) -> bool:
        """Wait until a login newer than `since` has been persisted."""
        deadline = self._clock() + timeout_s
        while self._clock() < deadline:
            data = self.load()
            if data and data.get("refresh_token") and float(data.get("updated_at") or 0) >= since:
                return True
            task = self._device_task
            if task is not None and task.done() and task.exception() is not None:
                raise ChatGptAuthError(str(task.exception()))
            await asyncio.sleep(interval_s)
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest Agent/tests/test_chatgpt_auth.py -v`
Expected: `12 passed` (the device-flow test takes ~1 s).

- [ ] **Step 5: Commit**

```bash
git add Agent
git commit -m "feat(agent): ChatGPT subscription OAuth (PKCE + device code, locked refresh)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: ChatGPT adapter (Responses API over SSE)

**Files:**
- Create: `Agent/tf_agent/models/chatgpt_adapter.py`
- Test: `Agent/tests/test_chatgpt_adapter.py`

**Interfaces:**
- Consumes: Task 3 types/errors; Task 4 `ChatGptAuthError`, `CODEX_BASE_URL`; anything with `async headers(accept)`, `async ensure_fresh(force)`, `status()` (duck-typed auth).
- Produces:
  - `build_payload(req: CompletionRequest) -> dict` (pure)
  - `parse_rate_headers(headers: Mapping[str, str]) -> RateInfo | None`
  - `ChatGPTOAuthAdapter(auth, client_factory: Callable[[float], httpx.AsyncClient] | None = None, base_url=CODEX_BASE_URL, models_cache: Path | None = None)`: `name="chatgpt"`, `list_models()`, `complete(req)`, `health()`

- [ ] **Step 1: Write the failing tests**

`Agent/tests/test_chatgpt_adapter.py`:
```python
import json

import httpx
import pytest

from tf_agent.models.chatgpt_adapter import ChatGPTOAuthAdapter, build_payload, parse_rate_headers
from tf_agent.models.errors import AuthRequired, InvalidRequest, TransientProviderError, UsageLimited
from tf_agent.models.types import CompletionRequest, ImagePart, Message, ToolCall, ToolSpec

RATE_HEADERS = {
    "x-codex-primary-used-percent": "5",
    "x-codex-primary-window-minutes": "300",
    "x-codex-primary-reset-at": "1791247829",
}
ADD = ToolSpec("add", "Add two ints", {"type": "object", "properties": {"a": {"type": "integer"}}})


class StubAuth:
    def __init__(self):
        self.forced = 0

    async def headers(self, accept="text/event-stream"):
        return {"authorization": "Bearer t", "chatgpt-account-id": "acc_1", "accept": accept}

    async def ensure_fresh(self, force=False):
        self.forced += int(force)
        return {}

    def status(self):
        return {"connected": True, "email": "nico@example.com", "plan": "plus"}


def sse(*events) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def make(handler, **kw):
    transport = httpx.MockTransport(handler)
    auth = StubAuth()
    return ChatGPTOAuthAdapter(auth, client_factory=lambda timeout: httpx.AsyncClient(transport=transport), **kw), auth


def req(**kw):
    return CompletionRequest(model="gpt-6-sol", system="sys", messages=[Message.user("hi")], **kw)


TOOL_EVENTS = (
    {"type": "response.created"},
    {"type": "response.output_item.done", "item": {"id": "fc_1", "type": "function_call", "status": "completed",
                                                    "arguments": "{\"a\":2,\"b\":3}", "call_id": "call_A",
                                                    "name": "add"}},
    {"type": "response.completed", "response": {"usage": {"input_tokens": 145, "output_tokens": 21}}},
)


def test_build_payload_maps_all_message_kinds(tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNGfake")
    call = ToolCall("call_A", "add", {"a": 2})
    r = CompletionRequest(
        model="gpt-6-sol", system="sys",
        messages=[Message.user("look", [ImagePart(str(img), "image/png")]),
                  Message.assistant("thinking", [call]),
                  Message.tool_result(call, "4")],
        tools=[ADD], require_tool=True)
    p = build_payload(r)
    assert p["instructions"] == "sys" and p["store"] is False and p["stream"] is True
    user = p["input"][0]
    assert user["content"][0] == {"type": "input_text", "text": "look"}
    assert user["content"][1]["image_url"].startswith("data:image/png;base64,")
    assert p["input"][1] == {"type": "message", "role": "assistant",
                             "content": [{"type": "output_text", "text": "thinking"}]}
    assert p["input"][2] == {"type": "function_call", "call_id": "call_A", "name": "add",
                             "arguments": json.dumps({"a": 2})}
    assert p["input"][3] == {"type": "function_call_output", "call_id": "call_A", "output": "4"}
    assert p["tools"][0]["type"] == "function" and p["tools"][0]["name"] == "add"
    assert p["tool_choice"] == "required"


def test_build_payload_structured_output_and_effort():
    p = build_payload(req(output_schema={"type": "object"}, schema_name="plan", reasoning_effort="high"))
    assert p["text"]["format"] == {"type": "json_schema", "name": "plan", "schema": {"type": "object"},
                                   "strict": False}
    assert p["tool_choice"] == "none" and p["tools"] == []
    assert p["reasoning"] == {"effort": "high"}


def test_parse_rate_headers():
    rate = parse_rate_headers(httpx.Headers(RATE_HEADERS))
    assert (rate.used_percent, rate.window_minutes, rate.resets_at) == (5.0, 300, 1791247829.0)
    assert parse_rate_headers(httpx.Headers({})) is None


async def test_complete_parses_tool_calls_usage_and_rate():
    def handler(request):
        assert request.url.path == "/backend-api/codex/responses"
        assert json.loads(request.content)["model"] == "gpt-6-sol"
        return httpx.Response(200, headers=RATE_HEADERS, content=sse(*TOOL_EVENTS))

    adapter, _ = make(handler)
    resp = await adapter.complete(req(tools=[ADD]))
    assert resp.tool_calls == [ToolCall("call_A", "add", {"a": 2, "b": 3})]
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (145, 21)
    assert resp.rate.used_percent == 5.0
    assert resp.provider == "chatgpt" and resp.model == "gpt-6-sol"


async def test_complete_structured_output():
    events = ({"type": "response.output_item.done", "item": {"type": "message", "content": [
        {"type": "output_text", "text": "{\"answer\": 4}"}]}},
              {"type": "response.completed", "response": {"usage": {}}})
    adapter, _ = make(lambda r: httpx.Response(200, content=sse(*events)))
    resp = await adapter.complete(req(output_schema={"type": "object"}))
    assert resp.structured == {"answer": 4}


async def test_429_maps_to_usage_limited_with_reset():
    adapter, _ = make(lambda r: httpx.Response(429, headers=RATE_HEADERS, json={"error": "usage_limit_reached"}))
    with pytest.raises(UsageLimited) as ei:
        await adapter.complete(req())
    assert ei.value.reset_at == 1791247829.0


async def test_401_refreshes_once_then_auth_required():
    seen = 0

    def handler(request):
        nonlocal seen
        seen += 1
        return httpx.Response(401, json={"error": "unauthorized"})

    adapter, auth = make(handler)
    with pytest.raises(AuthRequired):
        await adapter.complete(req())
    assert (seen, auth.forced) == (2, 1)


async def test_401_then_success_after_refresh():
    responses = iter([httpx.Response(401), httpx.Response(200, content=sse(*TOOL_EVENTS))])
    adapter, auth = make(lambda r: next(responses))
    resp = await adapter.complete(req(tools=[ADD]))
    assert resp.tool_calls[0].name == "add" and auth.forced == 1


@pytest.mark.parametrize("status,exc", [(500, TransientProviderError), (503, TransientProviderError),
                                        (400, InvalidRequest)])
async def test_status_mapping(status, exc):
    adapter, _ = make(lambda r: httpx.Response(status, text="nope"))
    with pytest.raises(exc):
        await adapter.complete(req())


async def test_sse_error_event_usage_limit():
    ev = {"type": "error", "error": {"code": "usage_limit_reached", "message": "You've hit your limit"}}
    adapter, _ = make(lambda r: httpx.Response(200, content=sse(ev)))
    with pytest.raises(UsageLimited):
        await adapter.complete(req())


async def test_network_error_is_transient():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    adapter, _ = make(handler)
    with pytest.raises(TransientProviderError):
        await adapter.complete(req())


async def test_list_models_keeps_largest_list_and_caches(tmp_path):
    def models(*slugs, hidden=()):
        return [{"slug": s, "display_name": s.upper(), "priority": i + 1, "context_window": 272000,
                 "input_modalities": ["text", "image"], "visibility": "hide" if s in hidden else "list",
                 "supported_in_api": True, "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]}
                for i, s in enumerate(slugs)]

    def handler(request):
        v = request.url.params["client_version"]
        if v == "1.0.0":
            return httpx.Response(200, json={"models": models("a", "b")})
        if v == "2.0.0":
            return httpx.Response(200, json={"models": models("a", "b", "c", hidden=("c",))})
        return httpx.Response(500)

    cache = tmp_path / "models.json"
    adapter, _ = make(handler, models_cache=cache)
    infos = await adapter.list_models()
    assert [m.model_id for m in infos] == ["a", "b", "c"]
    assert infos[2].hidden is True and infos[0].vision is True
    assert infos[0].reasoning_levels == ("low", "high") and infos[0].context_window == 272000
    offline, _ = make(lambda r: httpx.Response(500), models_cache=cache)
    assert [m.model_id for m in await offline.list_models()] == ["a", "b", "c"]


async def test_health_reports_account():
    adapter, _ = make(lambda r: httpx.Response(500))
    h = await adapter.health()
    assert h.connected is True and h.account == "nico@example.com (plus)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest Agent/tests/test_chatgpt_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tf_agent.models.chatgpt_adapter'`.

- [ ] **Step 3: Implement**

`Agent/tf_agent/models/chatgpt_adapter.py`:
```python
"""ChatGPT adapter: OpenAI Responses API over SSE on the Codex backend (D-29, D-37)."""
from __future__ import annotations

import base64
import json
import mimetypes
import time
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

import httpx

from tf_agent.models.chatgpt_auth import CODEX_BASE_URL, ChatGptAuthError
from tf_agent.models.errors import (
    AuthRequired,
    InvalidRequest,
    MalformedResponse,
    ProviderError,
    TransientProviderError,
    UsageLimited,
)
from tf_agent.models.types import (
    CompletionRequest,
    CompletionResponse,
    ImagePart,
    ModelInfo,
    ProviderHealth,
    RateInfo,
    TextPart,
    ToolCall,
    Usage,
)

PROVIDER = "chatgpt"
CLIENT_VERSIONS = ("1.0.0", "2.0.0", "0.99.0")
USAGE_LIMIT_CODES = ("usage_limit_reached", "rate_limit_exceeded", "insufficient_quota")


class ChatGptAuthLike(Protocol):
    async def headers(self, accept: str = "text/event-stream") -> dict[str, str]: ...

    async def ensure_fresh(self, force: bool = False) -> dict[str, Any]: ...

    def status(self) -> dict[str, Any]: ...


def _image_url(part: ImagePart) -> str:
    data = Path(part.path).read_bytes()
    mime = part.mime or mimetypes.guess_type(part.path)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def build_payload(req: CompletionRequest) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for m in req.messages:
        if m.role == "user":
            content: list[dict[str, Any]] = []
            for p in m.parts:
                if isinstance(p, TextPart):
                    content.append({"type": "input_text", "text": p.text})
                else:
                    content.append({"type": "input_image", "image_url": _image_url(p)})
            items.append({"type": "message", "role": "user", "content": content})
        elif m.role == "assistant":
            if m.text():
                items.append({"type": "message", "role": "assistant",
                              "content": [{"type": "output_text", "text": m.text()}]})
            for c in m.tool_calls:
                items.append({"type": "function_call", "call_id": c.id, "name": c.name,
                              "arguments": json.dumps(c.arguments)})
        else:
            items.append({"type": "function_call_output", "call_id": m.tool_call_id, "output": m.text()})

    payload: dict[str, Any] = {
        "model": req.model,
        "instructions": req.system,
        "input": items,
        "store": False,
        "stream": True,
        "include": [],
    }
    if req.tools:
        payload["tools"] = [{"type": "function", "name": t.name, "description": t.description,
                             "parameters": t.parameters, "strict": False} for t in req.tools]
        payload["tool_choice"] = "required" if req.require_tool else "auto"
        payload["parallel_tool_calls"] = True
    else:
        payload["tools"] = []
        payload["tool_choice"] = "none"
        payload["parallel_tool_calls"] = False
    if req.reasoning_effort:
        payload["reasoning"] = {"effort": req.reasoning_effort}
    if req.output_schema is not None:
        payload["text"] = {"format": {"type": "json_schema", "name": req.schema_name,
                                      "schema": req.output_schema, "strict": False}}
    return payload


def _num(headers: Mapping[str, str], key: str, cast: Callable[[str], Any]) -> Any:
    value = headers.get(key)
    if value is None:
        return None
    try:
        return cast(value)
    except ValueError:
        return None


def parse_rate_headers(headers: Mapping[str, str]) -> RateInfo | None:
    used = _num(headers, "x-codex-primary-used-percent", float)
    if used is None:
        return None
    return RateInfo(
        used_percent=used,
        window_minutes=_num(headers, "x-codex-primary-window-minutes", int),
        resets_at=_num(headers, "x-codex-primary-reset-at", float),
    )


def _reset_at(headers: Mapping[str, str]) -> float | None:
    reset = _num(headers, "x-codex-primary-reset-at", float)
    if reset is not None:
        return reset
    retry_after = _num(headers, "retry-after", float)
    return time.time() + retry_after if retry_after is not None else None


def _error_from_status(status: int, body: str, headers: Mapping[str, str]) -> ProviderError:
    lowered = body.lower()
    if status == 429 or any(code in lowered for code in USAGE_LIMIT_CODES):
        return UsageLimited(PROVIDER, f"usage limited ({status})", _reset_at(headers))
    if status in (401, 403):
        return AuthRequired(PROVIDER, f"authentication failed ({status}): run `tf login chatgpt`")
    if status >= 500:
        return TransientProviderError(PROVIDER, f"server error ({status})")
    return InvalidRequest(PROVIDER, f"request rejected ({status}): {body[:500]}")


async def _consume_sse(lines: AsyncIterator[str]) -> tuple[str, list[ToolCall], Usage]:
    texts: list[str] = []
    calls: list[ToolCall] = []
    usage = Usage()
    async for line in lines:
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "message":
                texts.extend(c.get("text", "") for c in item.get("content") or [] if c.get("type") == "output_text")
            elif item.get("type") == "function_call":
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {"_unparseable_arguments": item.get("arguments")}
                calls.append(ToolCall(id=item.get("call_id") or item.get("id") or "", name=item.get("name", ""),
                                      arguments=args if isinstance(args, dict) else {"value": args}))
        elif kind == "response.completed":
            u = (event.get("response") or {}).get("usage") or {}
            usage = Usage(u.get("input_tokens"), u.get("output_tokens"))
        elif kind in ("error", "response.failed"):
            err = event.get("error") or (event.get("response") or {}).get("error") or {}
            code = str(err.get("code") or err.get("type") or "")
            message = str(err.get("message") or json.dumps(event)[:300])
            if code in USAGE_LIMIT_CODES:
                raise UsageLimited(PROVIDER, message, None)
            if not code or code in ("server_error", "overloaded"):
                raise TransientProviderError(PROVIDER, message)
            raise InvalidRequest(PROVIDER, f"{code}: {message}")
    return "".join(texts), calls, usage


def _levels(model: dict[str, Any]) -> tuple[str, ...]:
    out = []
    for level in model.get("supported_reasoning_levels") or []:
        effort = level.get("effort") if isinstance(level, dict) else level
        if effort:
            out.append(str(effort))
    return tuple(out)


class ChatGPTOAuthAdapter:
    name = PROVIDER

    def __init__(
        self,
        auth: ChatGptAuthLike,
        client_factory: Callable[[float], httpx.AsyncClient] | None = None,
        base_url: str = CODEX_BASE_URL,
        models_cache: Path | None = None,
    ) -> None:
        self.auth = auth
        self.base_url = base_url
        self.models_cache = models_cache
        self._client_factory = client_factory or (
            lambda timeout: httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15)))

    async def _headers(self, accept: str, force_refresh: bool) -> dict[str, str]:
        try:
            if force_refresh:
                await self.auth.ensure_fresh(force=True)
            return await self.auth.headers(accept)
        except ChatGptAuthError as e:
            raise AuthRequired(PROVIDER, str(e)) from e

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        payload = build_payload(req)
        for attempt in (1, 2):
            headers = await self._headers("text/event-stream", force_refresh=attempt == 2)
            try:
                async with self._client_factory(req.timeout_s) as client:
                    async with client.stream("POST", f"{self.base_url}/responses", headers=headers,
                                             json=payload) as r:
                        if r.status_code == 401 and attempt == 1:
                            continue
                        if r.status_code >= 400:
                            body = (await r.aread()).decode(errors="replace")
                            raise _error_from_status(r.status_code, body, r.headers)
                        rate = parse_rate_headers(r.headers)
                        text, calls, usage = await _consume_sse(r.aiter_lines())
            except httpx.TransportError as e:
                raise TransientProviderError(PROVIDER, f"network error: {type(e).__name__}") from e
            structured = None
            if req.output_schema is not None:
                try:
                    structured = json.loads(text)
                except json.JSONDecodeError as e:
                    raise MalformedResponse(PROVIDER, "structured output was not valid JSON") from e
            return CompletionResponse(provider=PROVIDER, model=req.model, text=text, tool_calls=calls,
                                      structured=structured, usage=usage, rate=rate)
        raise AuthRequired(PROVIDER, "still unauthorized after token refresh: run `tf login chatgpt`")

    async def list_models(self) -> list[ModelInfo]:
        headers = await self._headers("application/json", force_refresh=False)
        best: list[dict[str, Any]] = []
        async with self._client_factory(30) as client:
            for version in CLIENT_VERSIONS:
                try:
                    r = await client.get(f"{self.base_url}/models", params={"client_version": version},
                                         headers=headers)
                except httpx.HTTPError:
                    continue
                if r.status_code >= 400:
                    continue
                body = r.json()
                models = body.get("models") or body.get("data") or []
                if len(models) > len(best):
                    best = models
        if best and self.models_cache:
            self.models_cache.parent.mkdir(parents=True, exist_ok=True)
            self.models_cache.write_text(json.dumps(best))
        elif not best and self.models_cache and self.models_cache.exists():
            best = json.loads(self.models_cache.read_text())
        infos = []
        for m in best:
            model_id = m.get("slug") or m.get("id")
            if not model_id:
                continue
            infos.append(ModelInfo(
                provider=PROVIDER,
                model_id=model_id,
                display_name=m.get("display_name"),
                context_window=m.get("context_window"),
                vision="image" in (m.get("input_modalities") or ["text"]),
                priority=int(m.get("priority") or 100),
                hidden=(m.get("visibility") or "list") != "list" or m.get("supported_in_api") is False,
                reasoning_levels=_levels(m),
            ))
        return infos

    async def health(self) -> ProviderHealth:
        st = self.auth.status()
        if not st.get("connected"):
            return ProviderHealth(PROVIDER, False, "not logged in: run `tf login chatgpt`")
        return ProviderHealth(PROVIDER, True, "connected", account=f"{st.get('email')} ({st.get('plan')})")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest Agent/tests/test_chatgpt_adapter.py -v`
Expected: `15 passed`.

- [ ] **Step 5: Commit**

```bash
git add Agent
git commit -m "feat(agent): ChatGPT Responses adapter (SSE, native tools, rate headers, model list)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Claude adapter (`claude -p` subprocess, isolated, emulated tools)

**Files:**
- Create: `Agent/tf_agent/models/claude_cli.py`
- Create: `Agent/tests/conftest.py`, `Agent/tests/fixtures/fake_claude.py`
- Test: `Agent/tests/test_claude_cli.py`

**Interfaces:**
- Consumes: Task 3 types/errors.
- Produces:
  - `clean_env() -> dict[str, str]` (environment without `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`)
  - `isolation_flags(empty_mcp_config: Path) -> list[str]`, `step_schema(tools) -> dict`, `render_prompt(req, image_ref: Callable[[ImagePart], str]) -> str`, `classify_failure(text, api_status) -> ProviderError`
  - `ClaudeCliAuth(bin="claude", log_path=None, stats_path=None)`: `available()`, `status() -> {"available","connected","email","subscription"}`, `async login_start(email=None) -> {"started","auth_url"}`, `async login_complete(raw) -> bool`, `logout()`, `discover_models() -> list[str]`
  - `ClaudeCLIAdapter(auth: ClaudeCliAuth, runtime_dir: Path | None = None)`: `name="claude"`, `list_models()`, `complete(req)`, `health()`
  - test fixture `fake_claude` → `.bin`, `.respond(payload, exit_code=0, sleep=0.0)`, `.calls()`, `.envelope(...)`, `.tmp`

- [ ] **Step 1: Write the fake CLI and fixture**

`Agent/tests/fixtures/fake_claude.py`:
```python
#!/usr/bin/env python3
"""Stand-in for the `claude` CLI in tests. Behaviour is driven by FAKE_CLAUDE_* env vars."""
import json
import os
import sys
import time

argv = sys.argv[1:]
if argv[:2] == ["auth", "status"]:
    print(os.environ.get("FAKE_CLAUDE_STATUS", '{"loggedIn": false}'))
    sys.exit(0)
stdin = sys.stdin.read()
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"argv": argv, "stdin": stdin, "env_keys": sorted(os.environ)}) + "\n")
time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "0")))
with open(os.environ["FAKE_CLAUDE_RESPONSE"]) as f:
    sys.stdout.write(f.read())
sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))
```

`Agent/tests/conftest.py`:
```python
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _envelope(structured=None, result="", is_error=False, subtype="success", api_error_status=None,
              model="claude-opus-5-5"):
    return {
        "type": "result", "subtype": subtype, "is_error": is_error, "api_error_status": api_error_status,
        "result": result, "structured_output": structured,
        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 0,
                  "output_tokens": 7},
        "modelUsage": {model: {}},
    }


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "claude"
    shutil.copy(FIXTURES / "fake_claude.py", script)
    script.chmod(0o755)
    log = tmp_path / "claude-calls.jsonl"
    response = tmp_path / "claude-response.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    def respond(payload, exit_code=0, sleep=0.0):
        response.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        monkeypatch.setenv("FAKE_CLAUDE_RESPONSE", str(response))
        monkeypatch.setenv("FAKE_CLAUDE_EXIT", str(exit_code))
        monkeypatch.setenv("FAKE_CLAUDE_SLEEP", str(sleep))

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return SimpleNamespace(bin=str(script), respond=respond, calls=calls, envelope=_envelope, tmp=tmp_path)
```

- [ ] **Step 2: Write the failing tests**

`Agent/tests/test_claude_cli.py`:
```python
import json
import time

import pytest

from tf_agent.models.claude_cli import ClaudeCLIAdapter, ClaudeCliAuth, render_prompt
from tf_agent.models.errors import AuthRequired, MalformedResponse, TransientProviderError, UsageLimited
from tf_agent.models.types import CompletionRequest, ImagePart, Message, ToolCall, ToolSpec

ADD = ToolSpec("add", "Add two ints", {"type": "object", "properties": {"a": {"type": "integer"}}})


def adapter(fake):
    return ClaudeCLIAdapter(ClaudeCliAuth(fake.bin, stats_path=fake.tmp / "stats.json"), runtime_dir=fake.tmp / "rt")


def req(text="hi", **kw):
    kw.setdefault("system", "You are a scout.")
    return CompletionRequest(model="opus", messages=[Message.user(text)], **kw)


def flag_value(argv, flag):
    return argv[argv.index(flag) + 1]


def test_render_prompt_includes_conversation_and_tools():
    call = ToolCall("c1", "add", {"a": 1})
    r = CompletionRequest(model="opus", system="s", tools=[ADD], messages=[
        Message.user("go"), Message.assistant("plan", [call]), Message.tool_result(call, "2")])
    text = render_prompt(r, lambda img: "@" + img.path)
    assert "[user]\ngo" in text
    assert '"name": "add"' in text and '[tool result id=c1 name=add]\n2' in text
    assert "<tools>" in text and '"calls"' in text


async def test_tool_step_uses_isolation_flags_and_stdin(fake_claude):
    fake_claude.respond(fake_claude.envelope(structured={"thought": "t", "calls": [
        {"name": "add", "arguments": {"a": 1}}]}))
    resp = await adapter(fake_claude).complete(req(tools=[ADD], require_tool=True))
    assert [(c.name, c.arguments) for c in resp.tool_calls] == [("add", {"a": 1})]
    assert resp.text == "t" and resp.provider == "claude" and resp.model == "claude-opus-5-5"
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (15, 7)
    call = fake_claude.calls()[0]
    argv = call["argv"]
    for flag in ("-p", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert flag_value(argv, "--tools") == "" and flag_value(argv, "--setting-sources") == ""
    assert flag_value(argv, "--output-format") == "json" and flag_value(argv, "--model") == "opus"
    assert flag_value(argv, "--system-prompt") == "You are a scout."
    schema = json.loads(flag_value(argv, "--json-schema"))
    assert schema["properties"]["calls"]["items"]["properties"]["name"]["enum"] == ["add"]
    assert "[user]\nhi" in call["stdin"] and "hi" not in argv


async def test_structured_output_mode(fake_claude):
    schema = {"type": "object", "properties": {"answer": {"type": "integer"}}}
    fake_claude.respond(fake_claude.envelope(structured={"answer": 4}))
    resp = await adapter(fake_claude).complete(req(output_schema=schema))
    assert resp.structured == {"answer": 4}
    assert json.loads(flag_value(fake_claude.calls()[0]["argv"], "--json-schema")) == schema


async def test_plain_text_mode(fake_claude):
    fake_claude.respond(fake_claude.envelope(result="hello there"))
    resp = await adapter(fake_claude).complete(req())
    assert resp.text == "hello there"
    assert "--json-schema" not in fake_claude.calls()[0]["argv"]


async def test_large_prompt_goes_through_stdin(fake_claude):
    fake_claude.respond(fake_claude.envelope(result="ok"))
    await adapter(fake_claude).complete(req("x" * 300_000))
    call = fake_claude.calls()[0]
    assert sum(len(a) for a in call["argv"]) < 20_000
    assert len(call["stdin"]) > 300_000


async def test_oversized_system_prompt_moves_to_stdin(fake_claude):
    fake_claude.respond(fake_claude.envelope(result="ok"))
    await adapter(fake_claude).complete(req(system="S" * 100_000))
    call = fake_claude.calls()[0]
    assert len(flag_value(call["argv"], "--system-prompt")) < 200
    assert call["stdin"].startswith("<system>\n" + "S" * 10)


async def test_image_with_spaces_is_staged(fake_claude):
    folder = fake_claude.tmp / "Trend Finder App"
    folder.mkdir()
    img = folder / "sheet one.jpg"
    img.write_bytes(b"\xff\xd8fakejpeg")
    fake_claude.respond(fake_claude.envelope(structured={"ok": True}))
    r = CompletionRequest(model="opus", system="s", output_schema={"type": "object"},
                          messages=[Message.user("look", [ImagePart(str(img))])])
    await adapter(fake_claude).complete(r)
    stdin = fake_claude.calls()[0]["stdin"]
    ref = next(tok for tok in stdin.split() if tok.startswith("@"))
    assert " " not in ref and "Trend Finder App" not in stdin
    from pathlib import Path
    assert Path(ref[1:]).read_bytes() == b"\xff\xd8fakejpeg"


async def test_api_key_env_is_stripped(fake_claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    fake_claude.respond(fake_claude.envelope(result="ok"))
    await adapter(fake_claude).complete(req())
    keys = fake_claude.calls()[0]["env_keys"]
    assert "ANTHROPIC_API_KEY" not in keys and "ANTHROPIC_AUTH_TOKEN" not in keys


async def test_usage_limit_error(fake_claude):
    fake_claude.respond(fake_claude.envelope(is_error=True, subtype="error_during_execution",
                                             result="Claude AI usage limit reached|1791247829"))
    with pytest.raises(UsageLimited) as ei:
        await adapter(fake_claude).complete(req())
    assert ei.value.reset_at == 1791247829.0


async def test_auth_error(fake_claude):
    fake_claude.respond(fake_claude.envelope(is_error=True, result="Invalid API key · Please run /login"))
    with pytest.raises(AuthRequired):
        await adapter(fake_claude).complete(req())


async def test_garbage_output(fake_claude):
    fake_claude.respond("garbage", exit_code=1)
    with pytest.raises(TransientProviderError):
        await adapter(fake_claude).complete(req())
    fake_claude.respond("garbage", exit_code=0)
    with pytest.raises(MalformedResponse):
        await adapter(fake_claude).complete(req())


async def test_timeout_kills_process(fake_claude):
    fake_claude.respond(fake_claude.envelope(result="late"), sleep=5)
    started = time.monotonic()
    with pytest.raises(TransientProviderError, match="timed out"):
        await adapter(fake_claude).complete(req(timeout_s=0.5))
    assert time.monotonic() - started < 3


async def test_empty_calls_is_malformed(fake_claude):
    fake_claude.respond(fake_claude.envelope(structured={"calls": []}))
    with pytest.raises(MalformedResponse):
        await adapter(fake_claude).complete(req(tools=[ADD]))


async def test_status_models_and_health(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_STATUS", json.dumps(
        {"loggedIn": True, "email": "nico@example.com", "subscriptionType": "max"}))
    (fake_claude.tmp / "stats.json").write_text(json.dumps({"modelUsage": {
        "claude-opus-5-5": {}, "claude-opus-4-1-20250805": {}, "claude-haiku-4-5-20251001": {}}}))
    a = adapter(fake_claude)
    assert a.auth.status()["connected"] is True
    assert sorted(a.auth.discover_models()) == ["claude-haiku-4-5-20251001", "claude-opus-5-5"]
    ids = [m.model_id for m in await a.list_models()]
    assert ids[:3] == ["opus", "sonnet", "haiku"] and "claude-opus-5-5" in ids
    h = await a.health()
    assert h.connected is True and h.account == "nico@example.com (max)"


async def test_missing_binary_is_auth_required(tmp_path):
    a = ClaudeCLIAdapter(ClaudeCliAuth(str(tmp_path / "nope" / "claude")), runtime_dir=tmp_path / "rt")
    with pytest.raises(AuthRequired, match="not found"):
        await a.complete(req())
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest Agent/tests/test_claude_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tf_agent.models.claude_cli'`.

- [ ] **Step 4: Implement**

`Agent/tf_agent/models/claude_cli.py`:
```python
"""Claude adapter: the official `claude -p` CLI as an isolated subprocess (D-29, D-30, D-36).

Ported login/status/model-discovery logic from Docs/Code docs/claude-codex-auth-reference.md §3.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from tf_agent.models.errors import (
    AuthRequired,
    MalformedResponse,
    ProviderError,
    TransientProviderError,
    UsageLimited,
)
from tf_agent.models.types import (
    CompletionRequest,
    CompletionResponse,
    ImagePart,
    ModelInfo,
    ProviderHealth,
    ToolCall,
    ToolSpec,
    Usage,
)

PROVIDER = "claude"
URL_RE = re.compile(r"https?://[^\s)>\"]+")
MODEL_RE = re.compile(r"^claude-(haiku|sonnet|opus)-(\d+)-(\d+)(?:-(\d{8}))?$")
CLAUDE_ALIASES = ("opus", "sonnet", "haiku")
STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
MAX_SYSTEM_ARG_BYTES = 64_000

LIMIT_RE = re.compile(r"usage limit|rate limit|hit your limit|limit reached|too many requests", re.I)
AUTH_RE = re.compile(r"not logged in|run /login|invalid api key|authenticat|oauth token|unauthorized", re.I)
TRANSIENT_RE = re.compile(r"overloaded|internal server error|bad gateway|service unavailable|timed? ?out|"
                          r"connection (reset|refused|error)", re.I)
RESET_EPOCH_RE = re.compile(r"\|(\d{10})\b")


def clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}


def isolation_flags(empty_mcp_config: Path) -> list[str]:
    return ["-p", "--output-format", "json", "--permission-mode", "dontAsk", "--tools", "",
            "--no-session-persistence", "--strict-mcp-config", "--mcp-config", str(empty_mcp_config),
            "--setting-sources", ""]


def step_schema(tools: list[ToolSpec]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "thought": {"type": "string"},
            "calls": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": [t.name for t in tools]},
                        "arguments": {"type": "object"},
                    },
                    "required": ["name", "arguments"],
                },
            },
        },
        "required": ["calls"],
    }


def render_prompt(req: CompletionRequest, image_ref: Callable[[ImagePart], str]) -> str:
    out = ["<conversation>"]
    for m in req.messages:
        if m.role == "user":
            out.append("[user]")
            out.append(m.text())
            out.extend(image_ref(img) for img in m.images())
        elif m.role == "assistant":
            out.append("[assistant]")
            if m.text():
                out.append(m.text())
            if m.tool_calls:
                out.append(json.dumps({"calls": [{"id": c.id, "name": c.name, "arguments": c.arguments}
                                                 for c in m.tool_calls]}, ensure_ascii=False))
        else:
            out.append(f"[tool result id={m.tool_call_id} name={m.tool_name}]")
            out.append(m.text())
    out.append("</conversation>")
    if req.tools:
        out.append("<tools>")
        out.extend(json.dumps({"name": t.name, "description": t.description, "parameters": t.parameters},
                              ensure_ascii=False) for t in req.tools)
        out.append("</tools>")
        out.append('Decide your next step. Reply ONLY with JSON matching the schema: one or more tool calls in '
                   '"calls", each with the tool "name" and "arguments" matching that tool\'s parameters.')
    elif req.output_schema is not None:
        out.append("Reply ONLY with JSON matching the required schema.")
    return "\n".join(out)


def classify_failure(text: str, api_status: Any) -> ProviderError:
    t = (text or "").strip()[:800]
    try:
        status = int(api_status) if api_status is not None else None
    except (TypeError, ValueError):
        status = None
    if status == 429 or LIMIT_RE.search(t):
        m = RESET_EPOCH_RE.search(t)
        return UsageLimited(PROVIDER, t or "usage limited", float(m.group(1)) if m else None)
    if status in (401, 403) or AUTH_RE.search(t):
        return AuthRequired(PROVIDER, t or "authentication required: run `tf login claude`")
    if (status or 0) >= 500 or TRANSIENT_RE.search(t):
        return TransientProviderError(PROVIDER, t or "transient failure")
    return ProviderError(PROVIDER, t or "unknown claude CLI failure")


class ClaudeCliAuth:
    def __init__(self, bin: str = "claude", log_path: Path | None = None, stats_path: Path | None = None) -> None:
        self.bin = shutil.which(bin) or bin
        self.log_path = log_path or Path(tempfile.gettempdir()) / "tf-claude" / "login.log"
        self.stats_path = stats_path or Path.home() / ".claude" / "stats-cache.json"
        self._proc: subprocess.Popen[str] | None = None

    def available(self) -> bool:
        return Path(self.bin).is_file() and os.access(self.bin, os.X_OK)

    def status(self) -> dict[str, Any]:
        if not self.available():
            return {"available": False, "connected": False, "email": None, "subscription": None}
        try:
            r = subprocess.run([self.bin, "auth", "status"], capture_output=True, text=True, timeout=15,
                               stdin=subprocess.DEVNULL, env=clean_env())
            d = json.loads(r.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            d = {}
        return {"available": True, "connected": bool(d.get("loggedIn")), "email": d.get("email"),
                "subscription": d.get("subscriptionType")}

    async def login_start(self, email: str | None = None) -> dict[str, Any]:
        if self._proc and self._proc.poll() is None:
            return {"started": True, "pending": True, "auth_url": None}
        cmd = [self.bin, "auth", "login", "--claudeai"] + (["--email", email] if email else [])
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("w") as log:
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                          text=True, start_new_session=True, env=clean_env())
        for _ in range(10):  # the CLI prints the auth URL shortly after start
            await asyncio.sleep(0.4)
            m = URL_RE.search(self.log_path.read_text(errors="ignore"))
            if m:
                return {"started": True, "auth_url": m.group(0)}
        return {"started": True, "auth_url": None}

    async def login_complete(self, raw: str) -> bool:
        code = raw.strip()
        if code.startswith("http"):
            code = (parse_qs(urlparse(code).query).get("code") or [code])[0]
        p = self._proc
        if not p or p.poll() is not None or not p.stdin:
            raise RuntimeError("no pending Claude login: start it again")
        p.stdin.write(code + "\n")
        p.stdin.flush()
        p.stdin.close()
        for _ in range(30):
            await asyncio.sleep(1)
            if (await asyncio.to_thread(self.status))["connected"]:
                self._proc = None
                return True
            if p.poll() is not None:
                break
        self._proc = None
        raise RuntimeError(self.log_path.read_text(errors="ignore")[-2000:])

    def logout(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None
        subprocess.run([self.bin, "auth", "logout"], capture_output=True, text=True, timeout=20,
                       stdin=subprocess.DEVNULL, env=clean_env())

    def discover_models(self) -> list[str]:
        try:
            usage = json.loads(self.stats_path.read_text()).get("modelUsage", {})
        except Exception:
            usage = {}
        latest: dict[str, tuple[tuple[int, int, int], str]] = {}
        for model_id in usage:
            m = MODEL_RE.match(model_id)
            if m:
                key = (int(m[2]), int(m[3]), int(m[4] or 0))
                if m[1] not in latest or key > latest[m[1]][0]:
                    latest[m[1]] = (key, model_id)
        return [v[1] for v in latest.values()]


class ClaudeCLIAdapter:
    name = PROVIDER

    def __init__(self, auth: ClaudeCliAuth, runtime_dir: Path | None = None) -> None:
        self.auth = auth
        self.runtime_dir = Path(runtime_dir or Path(tempfile.gettempdir()) / "tf-claude").resolve()
        if " " in str(self.runtime_dir):
            raise ValueError("runtime_dir must not contain spaces (Claude @-mentions break on spaces)")
        self.cwd = self.runtime_dir / "cwd"
        self.image_dir = self.runtime_dir / "images"
        self.mcp_config = self.runtime_dir / "empty-mcp.json"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        if not self.mcp_config.exists():
            self.mcp_config.write_text('{"mcpServers": {}}')

    def _image_ref(self, img: ImagePart) -> str:
        src = Path(img.path).resolve()
        if " " not in str(src):
            return f"@{src}"
        data = src.read_bytes()
        dest = self.image_dir / f"{hashlib.sha256(data).hexdigest()[:16]}{src.suffix or '.jpg'}"
        if not dest.exists():
            dest.write_bytes(data)
        return f"@{dest}"

    async def _run(self, cmd: list[str], stdin_text: str, timeout_s: float) -> dict[str, Any]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=self.cwd, env=clean_env())
        except (FileNotFoundError, PermissionError) as e:
            raise AuthRequired(PROVIDER, f"claude CLI not found at {self.auth.bin!r}") from e
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin_text.encode()), timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise TransientProviderError(PROVIDER, f"claude -p timed out after {timeout_s}s") from None
        stdout = out.decode(errors="replace").strip()
        stderr = err.decode(errors="replace").strip()
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            failure = classify_failure(f"{stdout}\n{stderr}", None)
            if proc.returncode != 0 and type(failure) is ProviderError:
                raise TransientProviderError(PROVIDER, f"claude -p exited {proc.returncode}: {stderr[:300]}")
            if proc.returncode != 0:
                raise failure
            raise MalformedResponse(PROVIDER, "claude -p did not return JSON") from None
        if envelope.get("is_error") or envelope.get("subtype") not in (None, "success"):
            raise classify_failure(f"{envelope.get('result') or ''} {stderr}", envelope.get("api_error_status"))
        return envelope

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        prompt = render_prompt(req, self._image_ref)
        system = req.system
        if len(system.encode()) > MAX_SYSTEM_ARG_BYTES:
            prompt = f"<system>\n{system}\n</system>\n{prompt}"
            system = "Follow the instructions in the <system> block of the user message."
        cmd = [self.auth.bin, *isolation_flags(self.mcp_config), "--model", req.model, "--system-prompt", system]
        if req.tools:
            cmd += ["--json-schema", json.dumps(step_schema(req.tools))]
        elif req.output_schema is not None:
            cmd += ["--json-schema", json.dumps(req.output_schema)]
        envelope = await self._run(cmd, prompt, req.timeout_s)

        raw_usage = envelope.get("usage") or {}
        usage = Usage(
            input_tokens=sum(int(raw_usage.get(k) or 0) for k in
                             ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
            output_tokens=raw_usage.get("output_tokens"),
        )
        model = next(iter(envelope.get("modelUsage") or {}), req.model)
        structured = envelope.get("structured_output")

        if req.tools:
            calls_raw = structured.get("calls") if isinstance(structured, dict) else None
            calls = [ToolCall(f"call_{uuid.uuid4().hex[:12]}", c["name"], c.get("arguments") or {})
                     for c in (calls_raw or []) if isinstance(c, dict) and c.get("name")]
            if not calls:
                raise MalformedResponse(PROVIDER, "tool step returned no calls")
            return CompletionResponse(provider=PROVIDER, model=model, text=str(structured.get("thought") or ""),
                                      tool_calls=calls, usage=usage)
        if req.output_schema is not None:
            if not isinstance(structured, dict):
                try:
                    structured = json.loads(envelope.get("result") or "")
                except json.JSONDecodeError:
                    raise MalformedResponse(PROVIDER, "structured output missing") from None
            return CompletionResponse(provider=PROVIDER, model=model, text=json.dumps(structured),
                                      structured=structured, usage=usage)
        return CompletionResponse(provider=PROVIDER, model=model, text=str(envelope.get("result") or ""),
                                  usage=usage)

    async def list_models(self) -> list[ModelInfo]:
        infos = [ModelInfo(PROVIDER, alias, display_name=f"latest {alias}", priority=i + 1)
                 for i, alias in enumerate(CLAUDE_ALIASES)]
        infos += [ModelInfo(PROVIDER, mid, display_name=mid, priority=10)
                  for mid in await asyncio.to_thread(self.auth.discover_models)]
        return infos

    async def health(self) -> ProviderHealth:
        st = await asyncio.to_thread(self.auth.status)
        if not st["available"]:
            return ProviderHealth(PROVIDER, False, f"claude CLI not found at {self.auth.bin!r}")
        if not st["connected"]:
            return ProviderHealth(PROVIDER, False, "not logged in: run `tf login claude`")
        return ProviderHealth(PROVIDER, True, "connected", account=f"{st['email']} ({st['subscription']})")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest Agent/tests/test_claude_cli.py -v`
Expected: `15 passed`.

- [ ] **Step 6: Commit**

```bash
git add Agent
git commit -m "feat(agent): isolated claude -p adapter (stdin prompts, staged images, emulated tools)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Model registry, aliases, role router (+ config files)

**Files:**
- Create: `config/model_aliases.yaml`, `config/roles.yaml`
- Create: `Agent/tf_agent/models/registry.py`, `Agent/tf_agent/models/router.py`
- Test: `Agent/tests/test_registry_router.py`

**Interfaces:**
- Consumes: `ProviderAdapter`, `ModelInfo`, `FakeAdapter`, `CLAUDE_ALIASES` (Task 6).
- Produces:
  - `load_aliases(path) -> dict[str, dict[str, str]]`, `load_roles(path) -> dict[str, list[dict]]`
  - `ModelRegistry(adapters, aliases, on_models: Callable[[str, list[ModelInfo]], Awaitable[None]] | None = None)`: `async refresh() -> dict[str, list[ModelInfo]]`, `async refresh_provider(provider) -> list[ModelInfo]`, `models(provider) -> list[ModelInfo]`, `resolve(provider, alias_or_id) -> str | None`
  - `RouteCandidate(provider, model, effort)`; `RoleRouter(roles, registry)`: `candidates(role, only=None) -> list[RouteCandidate]`

- [ ] **Step 1: Write the config files**

`config/model_aliases.yaml`:
```yaml
# alias -> concrete model id per provider (Docs/Code docs/02-model-layer.md).
# If a configured id disappears from the provider's model list, the registry falls back to the
# provider's highest-priority visible model.
claude:
  best: opus
  fast: sonnet
chatgpt:
  best: gpt-6-astra
  fast: gpt-6-luna
```

`config/roles.yaml`:
```yaml
# role -> ordered candidates; the first available provider wins (usage governor decides availability).
default:
  - {provider: claude, model: best}
  - {provider: chatgpt, model: best, effort: medium}
master:
  - {provider: claude, model: best}
  - {provider: chatgpt, model: best, effort: high}
scout:
  - {provider: chatgpt, model: best, effort: medium}
  - {provider: claude, model: best}
deep_dive:
  - {provider: claude, model: best}
  - {provider: chatgpt, model: best, effort: medium}
radar:
  - {provider: chatgpt, model: fast, effort: low}
  - {provider: claude, model: fast}
seed_study:
  - {provider: claude, model: best}
  - {provider: chatgpt, model: best, effort: medium}
analyst:
  - {provider: claude, model: best}
  - {provider: chatgpt, model: best, effort: medium}
cross_check:
  - {provider: chatgpt, model: best, effort: medium}
  - {provider: claude, model: best}
curator:
  - {provider: claude, model: best}
  - {provider: chatgpt, model: best, effort: high}
brief:
  - {provider: claude, model: best}
  - {provider: chatgpt, model: best, effort: medium}
learner:
  - {provider: claude, model: best}
  - {provider: chatgpt, model: best, effort: high}
demo:
  - {provider: claude, model: fast}
  - {provider: chatgpt, model: fast, effort: low}
```

- [ ] **Step 2: Write the failing tests**

`Agent/tests/test_registry_router.py`:
```python
from pathlib import Path

from tf_agent.models.fake import FakeAdapter
from tf_agent.models.registry import ModelRegistry, load_aliases
from tf_agent.models.router import RoleRouter, RouteCandidate, load_roles
from tf_agent.models.types import ModelInfo

CONFIG = Path(__file__).resolve().parents[2] / "config"


def chat_adapter():
    return FakeAdapter("chatgpt", models=[
        ModelInfo("chatgpt", "gpt-6-sol", priority=1),
        ModelInfo("chatgpt", "gpt-6-astra", priority=2),
        ModelInfo("chatgpt", "gpt-reserve", priority=0, hidden=True),
    ])


async def test_refresh_and_resolve_alias():
    reg = ModelRegistry({"chatgpt": chat_adapter()}, {"chatgpt": {"best": "gpt-6-astra"}})
    await reg.refresh()
    assert reg.resolve("chatgpt", "best") == "gpt-6-astra"
    assert reg.resolve("chatgpt", "gpt-6-sol") == "gpt-6-sol"


async def test_missing_configured_model_falls_back_to_top_visible():
    reg = ModelRegistry({"chatgpt": chat_adapter()}, {"chatgpt": {"best": "gpt-9-gone"}})
    await reg.refresh()
    assert reg.resolve("chatgpt", "best") == "gpt-6-sol"


def test_config_is_trusted_before_first_refresh():
    reg = ModelRegistry({"chatgpt": chat_adapter()}, {"chatgpt": {"best": "gpt-6-astra"}})
    assert reg.resolve("chatgpt", "best") == "gpt-6-astra"


def test_claude_aliases_always_resolve():
    reg = ModelRegistry({}, {"claude": {"best": "opus"}})
    reg._models["claude"] = [ModelInfo("claude", "claude-opus-5-5")]
    assert reg.resolve("claude", "best") == "opus"


async def test_refresh_failure_keeps_previous_list_and_hook_sees_success():
    seen = []

    async def hook(provider, models):
        seen.append((provider, [m.model_id for m in models]))

    adapter = chat_adapter()
    reg = ModelRegistry({"chatgpt": adapter}, {}, on_models=hook)
    await reg.refresh()
    adapter.models_error = RuntimeError("offline")
    await reg.refresh()
    assert [m.model_id for m in reg.models("chatgpt")] == ["gpt-6-sol", "gpt-6-astra", "gpt-reserve"]
    assert len(seen) == 1


async def test_router_order_effort_and_only_filter():
    reg = ModelRegistry({"chatgpt": chat_adapter()}, {"chatgpt": {"best": "gpt-6-astra"}, "claude": {"best": "opus"}})
    await reg.refresh()
    router = RoleRouter({"scout": [{"provider": "chatgpt", "model": "best", "effort": "medium"},
                                   {"provider": "claude", "model": "best"}]}, reg)
    assert router.candidates("scout") == [RouteCandidate("chatgpt", "gpt-6-astra", "medium"),
                                          RouteCandidate("claude", "opus", None)]
    assert router.candidates("scout", only="claude") == [RouteCandidate("claude", "opus", None)]


def test_router_unknown_role_uses_default():
    reg = ModelRegistry({}, {"claude": {"best": "opus"}})
    router = RoleRouter({"default": [{"provider": "claude", "model": "best"}]}, reg)
    assert router.candidates("whatever") == [RouteCandidate("claude", "opus", None)]


def test_repo_config_files_load():
    roles = load_roles(CONFIG / "roles.yaml")
    aliases = load_aliases(CONFIG / "model_aliases.yaml")
    assert {"default", "master", "scout", "analyst", "demo"} <= set(roles)
    assert aliases["claude"]["best"] == "opus" and "best" in aliases["chatgpt"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest Agent/tests/test_registry_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tf_agent.models.registry'`.

- [ ] **Step 4: Implement**

`Agent/tf_agent/models/registry.py`:
```python
"""Runtime model discovery and alias resolution (Docs/Code docs/02-model-layer.md)."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

import yaml

from tf_agent.models.base import ProviderAdapter
from tf_agent.models.claude_cli import CLAUDE_ALIASES
from tf_agent.models.types import ModelInfo

log = logging.getLogger(__name__)
ModelsHook = Callable[[str, list[ModelInfo]], Awaitable[None]]


def load_aliases(path: Path) -> dict[str, dict[str, str]]:
    return yaml.safe_load(Path(path).read_text()) or {}


class ModelRegistry:
    def __init__(self, adapters: Mapping[str, ProviderAdapter], aliases: dict[str, dict[str, str]],
                 on_models: ModelsHook | None = None) -> None:
        self.adapters = dict(adapters)
        self.aliases = aliases
        self.on_models = on_models
        self._models: dict[str, list[ModelInfo]] = {}

    async def refresh_provider(self, provider: str) -> list[ModelInfo]:
        try:
            models = await self.adapters[provider].list_models()
        except Exception as e:  # keep the previous list; a provider being offline must not break routing
            log.warning("model list refresh failed for %s: %s", provider, e)
            return self._models.get(provider, [])
        self._models[provider] = models
        if self.on_models is not None:
            await self.on_models(provider, models)
        return models

    async def refresh(self) -> dict[str, list[ModelInfo]]:
        for provider in self.adapters:
            await self.refresh_provider(provider)
        return dict(self._models)

    def models(self, provider: str) -> list[ModelInfo]:
        return list(self._models.get(provider, []))

    def resolve(self, provider: str, alias_or_id: str) -> str | None:
        target = self.aliases.get(provider, {}).get(alias_or_id, alias_or_id)
        if provider == "claude" and target in CLAUDE_ALIASES:
            return target
        known = self._models.get(provider)
        if not known:
            return target  # not refreshed yet: trust the configuration
        if any(m.model_id == target for m in known):
            return target
        visible = sorted((m for m in known if not m.hidden), key=lambda m: m.priority)
        return visible[0].model_id if visible else None
```

`Agent/tf_agent/models/router.py`:
```python
"""Role → ordered provider/model candidates (config/roles.yaml)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tf_agent.models.registry import ModelRegistry


@dataclass(frozen=True)
class RouteCandidate:
    provider: str
    model: str
    effort: str | None = None


def load_roles(path: Path) -> dict[str, list[dict[str, Any]]]:
    return yaml.safe_load(Path(path).read_text()) or {}


class RoleRouter:
    def __init__(self, roles: dict[str, list[dict[str, Any]]], registry: ModelRegistry) -> None:
        self.roles = roles
        self.registry = registry

    def candidates(self, role: str, only: str | None = None) -> list[RouteCandidate]:
        entries = self.roles.get(role) or self.roles.get("default") or []
        out: list[RouteCandidate] = []
        for entry in entries:
            provider = entry["provider"]
            if only is not None and provider != only:
                continue
            model = self.registry.resolve(provider, str(entry.get("model", "best")))
            if model:
                out.append(RouteCandidate(provider, model, entry.get("effort")))
        return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest Agent/tests/test_registry_router.py -v`
Expected: `8 passed`.

- [ ] **Step 6: Commit**

```bash
git add config Agent
git commit -m "feat(agent): model registry with aliases and role router" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Usage governor, ModelClient (routing + retries + fallback), DB ledger & state store

**Files:**
- Create: `Agent/tf_agent/models/governor.py`, `Agent/tf_agent/models/client.py`, `Agent/tf_agent/models/persistence.py`
- Modify: `Agent/tf_agent/testing.py` (add `ListLedger`, `MemoryStateStore`, `make_client`)
- Test: `Agent/tests/test_governor_client.py`, `Agent/tests/test_persistence.py`

**Interfaces:**
- Consumes: Tasks 3, 7; `tf_db.models.ModelCallRow`, `ProviderStateRow`, `ModelRow`.
- Produces:
  - `ProviderStatus(status="ok"|"cooling"|"auth_error", cooling_until: float | None, used_percent: float | None, last_error: str | None)`
  - `ProviderStateStore` protocol: `async save(provider, status)`, `async load_all() -> dict[str, ProviderStatus]`
  - `UsageGovernor(providers, concurrency=4, *, clock=time.time, store=None, cool_at_percent=98.0, probe_after_s=900.0)`: `available(p)`, `slot(p) -> asyncio.Semaphore`, `status(p)`, `async mark_cooling(p, reset_at, reason)`, `async mark_auth_error(p, reason)`, `async mark_ok(p)`, `async observe_rate(p, rate)`, `earliest_recovery() -> float | None`, `async load()`
  - `CallContext(role, run_id=None, task_id=None)`; `CallRecord(run_id, task_id, role, provider, model, input_tokens, output_tokens, images, latency_ms, status, error_class)`; `CallLedger` protocol `async record(rec)`
  - `ModelClient(adapters, router, governor, ledger=None, *, max_attempts=3, backoff_s=1.0, sleep=asyncio.sleep)`: `async complete(req, ctx, only=None) -> CompletionResponse` (raises `InvalidRequest` immediately; `AllProvidersUnavailable` when nothing is left)
  - `DbCallLedger(sessionmaker)`, `DbProviderStateStore(sessionmaker)`, `async save_models(sessionmaker, provider, models)`
  - `tf_agent.testing.ListLedger`, `MemoryStateStore`, `make_client(adapters: dict[str, FakeAdapter], roles=None, concurrency=4, clock=None) -> tuple[ModelClient, UsageGovernor, ListLedger]`

- [ ] **Step 1: Write the failing tests**

`Agent/tests/test_governor_client.py`:
```python
import asyncio

import pytest

from tf_agent.models.client import CallContext
from tf_agent.models.errors import (
    AllProvidersUnavailable,
    AuthRequired,
    InvalidRequest,
    MalformedResponse,
    TransientProviderError,
    UsageLimited,
)
from tf_agent.models.fake import FakeAdapter, text_response
from tf_agent.models.governor import ProviderStatus, UsageGovernor
from tf_agent.models.types import CompletionRequest, Message, RateInfo
from tf_agent.testing import MemoryStateStore, make_client


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def req():
    return CompletionRequest(model="", system="s", messages=[Message.user("hi")])


CTX = CallContext(role="scout")


async def test_first_available_provider_answers_and_ledger_records():
    a, b = FakeAdapter("a", [text_response("a", "from a")]), FakeAdapter("b")
    client, _, ledger = make_client({"a": a, "b": b})
    resp = await client.complete(req(), CTX)
    assert resp.text == "from a"
    assert a.requests[0].model == "a-model"
    assert [(r.provider, r.status) for r in ledger.records] == [("a", "ok")]


async def test_usage_limit_falls_back_and_cools():
    clock = Clock()
    a = FakeAdapter("a", [UsageLimited("a", "limit", reset_at=5000.0)])
    b = FakeAdapter("b", [text_response("b", "from b")])
    client, gov, ledger = make_client({"a": a, "b": b}, clock=clock)
    assert (await client.complete(req(), CTX)).text == "from b"
    assert gov.status("a").status == "cooling" and gov.status("a").cooling_until == 5000.0
    assert gov.available("a") is False
    clock.t = 5000.0
    assert gov.available("a") is True
    assert [r.error_class for r in ledger.records] == ["UsageLimited", None]


async def test_all_cooling_reports_earliest_reset():
    clock = Clock()
    a = FakeAdapter("a", [UsageLimited("a", "limit", reset_at=7000.0)])
    b = FakeAdapter("b", [UsageLimited("b", "limit", reset_at=6000.0)])
    client, _, _ = make_client({"a": a, "b": b}, clock=clock)
    with pytest.raises(AllProvidersUnavailable) as ei:
        await client.complete(req(), CTX)
    assert ei.value.earliest_reset == 6000.0


async def test_unknown_reset_cools_for_probe_window():
    clock = Clock()
    gov = UsageGovernor(["a"], clock=clock, probe_after_s=900)
    await gov.mark_cooling("a", None, "limit")
    assert gov.status("a").cooling_until == 1900.0


async def test_transient_is_retried_then_falls_back():
    a = FakeAdapter("a", [TransientProviderError("a", "x")] * 3)
    b = FakeAdapter("b", [text_response("b", "ok")])
    client, gov, ledger = make_client({"a": a, "b": b})
    assert (await client.complete(req(), CTX)).provider == "b"
    assert len(a.requests) == 3 and gov.status("a").status == "ok"


async def test_malformed_is_retried_like_transient():
    a = FakeAdapter("a", [MalformedResponse("a", "bad"), text_response("a", "ok")])
    client, _, _ = make_client({"a": a})
    assert (await client.complete(req(), CTX)).text == "ok"


async def test_invalid_request_raises_immediately():
    a = FakeAdapter("a", [InvalidRequest("a", "bad schema")])
    b = FakeAdapter("b", [text_response("b", "never")])
    client, _, _ = make_client({"a": a, "b": b})
    with pytest.raises(InvalidRequest):
        await client.complete(req(), CTX)
    assert b.requests == []


async def test_auth_error_skips_provider_until_ok():
    a = FakeAdapter("a", [AuthRequired("a", "login")])
    b = FakeAdapter("b", [text_response("b", "ok"), text_response("b", "ok2")])
    client, gov, _ = make_client({"a": a, "b": b})
    await client.complete(req(), CTX)
    assert gov.status("a").status == "auth_error"
    await client.complete(req(), CTX)
    assert len(a.requests) == 1
    await gov.mark_ok("a")
    assert gov.available("a") is True


async def test_high_usage_cools_preemptively():
    clock = Clock()
    a = FakeAdapter("a", [text_response("a", "ok")])
    a._script[0].rate = RateInfo(used_percent=99.0, window_minutes=300, resets_at=9000.0)
    client, gov, _ = make_client({"a": a}, clock=clock)
    await client.complete(req(), CTX)
    st = gov.status("a")
    assert st.status == "cooling" and st.cooling_until == 9000.0 and st.used_percent == 99.0


async def test_only_restricts_providers():
    a = FakeAdapter("a")
    b = FakeAdapter("b", [text_response("b", "ok")])
    client, _, _ = make_client({"a": a, "b": b})
    assert (await client.complete(req(), CTX, only="b")).provider == "b"
    assert a.requests == []


async def test_concurrency_slot_limits_in_flight_calls():
    a = FakeAdapter("a", [text_response("a", "ok")] * 6, delay_s=0.05)
    client, _, _ = make_client({"a": a}, concurrency=2)
    await asyncio.gather(*(client.complete(req(), CTX) for _ in range(6)))
    assert a.max_in_flight == 2


async def test_load_ignores_stale_auth_error():
    store = MemoryStateStore({"a": ProviderStatus("auth_error", None, None, "old"),
                              "b": ProviderStatus("cooling", 99999.0, 97.0, "limit")})
    gov = UsageGovernor(["a", "b"], store=store, clock=Clock())
    await gov.load()
    assert gov.status("a").status == "ok"
    assert gov.status("b").status == "cooling"
```

`Agent/tests/test_persistence.py`:
```python
from sqlalchemy import select

from tf_agent.models.client import CallRecord
from tf_agent.models.governor import ProviderStatus
from tf_agent.models.persistence import DbCallLedger, DbProviderStateStore, save_models
from tf_agent.models.types import ModelInfo
from tf_db.models import ModelCallRow, ModelRow


async def test_ledger_writes_rows(db_sessionmaker):
    await DbCallLedger(db_sessionmaker).record(CallRecord(
        run_id=None, task_id=None, role="scout", provider="claude", model="opus", input_tokens=5,
        output_tokens=3, images=1, latency_ms=900, status="ok", error_class=None))
    async with db_sessionmaker() as s:
        row = (await s.execute(select(ModelCallRow))).scalar_one()
    assert (row.provider, row.images, row.latency_ms) == ("claude", 1, 900)


async def test_state_store_upserts_and_loads(db_sessionmaker):
    store = DbProviderStateStore(db_sessionmaker)
    await store.save("claude", ProviderStatus("cooling", 1_800_000_000.0, 98.5, "limit"))
    await store.save("claude", ProviderStatus("ok", None, 12.0, None))
    loaded = await store.load_all()
    assert loaded["claude"] == ProviderStatus("ok", None, 12.0, None)


async def test_save_models_marks_missing_unavailable(db_sessionmaker):
    await save_models(db_sessionmaker, "chatgpt", [ModelInfo("chatgpt", "a"), ModelInfo("chatgpt", "b")])
    await save_models(db_sessionmaker, "chatgpt", [ModelInfo("chatgpt", "b", display_name="B", vision=False)])
    async with db_sessionmaker() as s:
        rows = {r.model_id: r for r in (await s.execute(select(ModelRow))).scalars()}
    assert rows["a"].available is False and rows["b"].available is True
    assert rows["b"].display_name == "B" and rows["b"].capabilities["vision"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest Agent/tests/test_governor_client.py Agent/tests/test_persistence.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tf_agent.models.client'`.

- [ ] **Step 3: Implement the governor**

`Agent/tf_agent/models/governor.py`:
```python
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
```

- [ ] **Step 4: Implement the client**

`Agent/tf_agent/models/client.py`:
```python
"""ModelClient: role routing + usage governor + retries + provider fallback + call ledger."""
from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Protocol

from tf_agent.models.base import ProviderAdapter
from tf_agent.models.errors import (
    AllProvidersUnavailable,
    AuthRequired,
    MalformedResponse,
    ProviderError,
    TransientProviderError,
    UsageLimited,
)
from tf_agent.models.governor import UsageGovernor
from tf_agent.models.router import RoleRouter, RouteCandidate
from tf_agent.models.types import CompletionRequest, CompletionResponse

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallContext:
    role: str
    run_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None


@dataclass(frozen=True)
class CallRecord:
    run_id: uuid.UUID | None
    task_id: uuid.UUID | None
    role: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    images: int
    latency_ms: int
    status: str
    error_class: str | None


class CallLedger(Protocol):
    async def record(self, rec: CallRecord) -> None: ...


class ModelClient:
    def __init__(self, adapters: Mapping[str, ProviderAdapter], router: RoleRouter, governor: UsageGovernor,
                 ledger: CallLedger | None = None, *, max_attempts: int = 3, backoff_s: float = 1.0,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.adapters = dict(adapters)
        self.router = router
        self.governor = governor
        self.ledger = ledger
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self._sleep = sleep

    async def _record(self, ctx: CallContext, cand: RouteCandidate, req: CompletionRequest, started: float,
                      resp: CompletionResponse | None, error: Exception | None) -> None:
        if self.ledger is None:
            return
        rec = CallRecord(
            run_id=ctx.run_id, task_id=ctx.task_id, role=ctx.role, provider=cand.provider,
            model=resp.model if resp else cand.model,
            input_tokens=resp.usage.input_tokens if resp else None,
            output_tokens=resp.usage.output_tokens if resp else None,
            images=sum(len(m.images()) for m in req.messages),
            latency_ms=int((time.monotonic() - started) * 1000),
            status="ok" if error is None else "error",
            error_class=type(error).__name__ if error else None,
        )
        try:
            await self.ledger.record(rec)
        except Exception as e:  # the ledger must never break a model call
            log.warning("call ledger write failed: %s", e)

    async def complete(self, req: CompletionRequest, ctx: CallContext, only: str | None = None) -> CompletionResponse:
        last_error: Exception | None = None
        for cand in self.router.candidates(ctx.role, only=only):
            if cand.provider not in self.adapters or not self.governor.available(cand.provider):
                continue
            adapter = self.adapters[cand.provider]
            concrete = replace(req, model=cand.model, reasoning_effort=cand.effort or req.reasoning_effort)
            for attempt in range(1, self.max_attempts + 1):
                started = time.monotonic()
                try:
                    async with self.governor.slot(cand.provider):
                        resp = await adapter.complete(concrete)
                except (TransientProviderError, MalformedResponse) as e:
                    await self._record(ctx, cand, concrete, started, None, e)
                    last_error = e
                    if attempt < self.max_attempts:
                        await self._sleep(self.backoff_s * 2 ** (attempt - 1) + random.uniform(0, self.backoff_s / 4))
                        continue
                    break
                except UsageLimited as e:
                    await self._record(ctx, cand, concrete, started, None, e)
                    await self.governor.mark_cooling(cand.provider, e.reset_at, e.message)
                    last_error = e
                    break
                except AuthRequired as e:
                    await self._record(ctx, cand, concrete, started, None, e)
                    await self.governor.mark_auth_error(cand.provider, e.message)
                    last_error = e
                    break
                except ProviderError as e:
                    await self._record(ctx, cand, concrete, started, None, e)
                    raise
                await self._record(ctx, cand, concrete, started, resp, None)
                await self.governor.mark_ok(cand.provider)
                await self.governor.observe_rate(cand.provider, resp.rate)
                return resp
        raise AllProvidersUnavailable(ctx.role, self.governor.earliest_recovery()) from last_error
```

- [ ] **Step 5: Implement persistence and test helpers**

`Agent/tf_agent/models/persistence.py`:
```python
"""DB-backed call ledger, provider state store and model catalog (tables from Plan 1 Task 2)."""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.models.client import CallRecord
from tf_agent.models.governor import ProviderStatus
from tf_agent.models.types import ModelInfo
from tf_db.models import ModelCallRow, ModelRow, ProviderStateRow

Sessionmaker = async_sessionmaker[AsyncSession]


def _to_dt(ts: float | None) -> datetime | None:
    return datetime.fromtimestamp(ts, tz=UTC) if ts is not None else None


class DbCallLedger:
    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sm = sessionmaker

    async def record(self, rec: CallRecord) -> None:
        async with self._sm() as s:
            s.add(ModelCallRow(**asdict(rec)))
            await s.commit()


class DbProviderStateStore:
    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sm = sessionmaker

    async def save(self, provider: str, status: ProviderStatus) -> None:
        values = {"status": status.status, "cooling_until": _to_dt(status.cooling_until),
                  "used_percent": status.used_percent, "last_error": status.last_error}
        stmt = pg_insert(ProviderStateRow).values(provider=provider, **values)
        stmt = stmt.on_conflict_do_update(index_elements=[ProviderStateRow.provider],
                                          set_={**values, "updated_at": func.now()})
        async with self._sm() as s:
            await s.execute(stmt)
            await s.commit()

    async def load_all(self) -> dict[str, ProviderStatus]:
        async with self._sm() as s:
            rows = (await s.execute(select(ProviderStateRow))).scalars().all()
        return {r.provider: ProviderStatus(r.status, r.cooling_until.timestamp() if r.cooling_until else None,
                                           r.used_percent, r.last_error) for r in rows}


async def save_models(sessionmaker: Sessionmaker, provider: str, models: list[ModelInfo]) -> None:
    async with sessionmaker() as s:
        await s.execute(update(ModelRow).where(ModelRow.provider == provider).values(available=False))
        for m in models:
            caps = {"vision": m.vision, "context_window": m.context_window, "priority": m.priority,
                    "hidden": m.hidden, "reasoning_levels": list(m.reasoning_levels)}
            stmt = pg_insert(ModelRow).values(provider=provider, model_id=m.model_id, display_name=m.display_name,
                                              capabilities=caps, available=True)
            stmt = stmt.on_conflict_do_update(
                index_elements=[ModelRow.provider, ModelRow.model_id],
                set_={"display_name": m.display_name, "capabilities": caps, "available": True,
                      "last_seen_at": func.now()})
            await s.execute(stmt)
        await s.commit()
```

Append to `Agent/tf_agent/testing.py`:
```python


# ---- model-client helpers (Task 8) ----
from tf_agent.models.client import CallRecord, ModelClient  # noqa: E402
from tf_agent.models.fake import FakeAdapter  # noqa: E402
from tf_agent.models.governor import ProviderStatus, UsageGovernor  # noqa: E402
from tf_agent.models.registry import ModelRegistry  # noqa: E402
from tf_agent.models.router import RoleRouter  # noqa: E402


class ListLedger:
    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    async def record(self, rec: CallRecord) -> None:
        self.records.append(rec)


class MemoryStateStore:
    def __init__(self, initial: dict[str, ProviderStatus] | None = None) -> None:
        self.saved: dict[str, ProviderStatus] = dict(initial or {})

    async def save(self, provider: str, status: ProviderStatus) -> None:
        self.saved[provider] = status

    async def load_all(self) -> dict[str, ProviderStatus]:
        return dict(self.saved)


async def _no_sleep(_: float) -> None:
    return None


def make_client(adapters: dict[str, FakeAdapter], roles: dict | None = None, concurrency: int = 4,
                clock=None) -> tuple[ModelClient, UsageGovernor, ListLedger]:
    """ModelClient over fake adapters; default role order = dict order of `adapters`."""
    registry = ModelRegistry(adapters, {})
    roles = roles or {"default": [{"provider": name, "model": f"{name}-model"} for name in adapters]}
    governor = UsageGovernor(adapters.keys(), concurrency, **({"clock": clock} if clock else {}))
    ledger = ListLedger()
    client = ModelClient(adapters, RoleRouter(roles, registry), governor, ledger, sleep=_no_sleep)
    return client, governor, ledger
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest Agent/tests/test_governor_client.py Agent/tests/test_persistence.py -v`
Expected: `15 passed` (`12` governor/client + `3` persistence; persistence tests need Docker running).

- [ ] **Step 7: Commit**

```bash
git add Agent
git commit -m "feat(agent): usage governor, model client with fallback/retries, DB ledger and state store" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Agent loop: tools, `submit_result`, repair, budget, compaction

**Files:**
- Create: `Agent/tf_agent/loop/__init__.py` (empty), `Agent/tf_agent/loop/tools.py`, `Agent/tf_agent/loop/compaction.py`, `Agent/tf_agent/loop/agent.py`
- Test: `Agent/tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `ModelClient.complete(req, ctx, only)`, `CallContext` (Task 8); types (Task 3).
- Produces (Plan 3 builds every role on these):
  - `Tool(name, description, params: type[BaseModel], handler: async (params) -> Any, compact: Callable[[Any], str] = default_compact)` with `.spec() -> ToolSpec`; `truncate(text, max_chars) -> str`; `default_compact(result) -> str`
  - `ELIDED`, `compact_transcript(messages, max_chars, keep_last=6) -> list[Message]`
  - `SUBMIT = "submit_result"`, `BUDGET_MSG`, `NUDGE_MSG`
  - `AgentBudget(max_steps=30, max_tool_output_chars=4000, max_transcript_chars=150_000)`
  - `AgentEvent(kind: "step"|"tool"|"submitted"|"failed", step: int, detail: str)`
  - `AgentResult(status: "succeeded"|"failed", result, steps, error, transcript, provider, model)`
  - `async run_agent(*, client, role, system, task, result_model, tools=(), images=(), budget=None, run_id=None, task_id=None, provider=None, on_event=None) -> AgentResult`

- [ ] **Step 1: Write the failing tests**

`Agent/tests/test_agent_loop.py`:
```python
import asyncio
import time

import pytest
from pydantic import BaseModel

from tf_agent.loop.agent import BUDGET_MSG, NUDGE_MSG, AgentBudget, run_agent
from tf_agent.loop.compaction import ELIDED, compact_transcript
from tf_agent.loop.tools import Tool, truncate
from tf_agent.models.fake import FakeAdapter, text_response, tool_call_response
from tf_agent.models.types import Message, ToolCall
from tf_agent.testing import make_client


class AddParams(BaseModel):
    a: int
    b: int


class Answer(BaseModel):
    answer: int


async def _add(p: AddParams) -> int:
    return p.a + p.b


ADD = Tool("add", "Add two integers.", AddParams, _add)


def setup(*script):
    fa = FakeAdapter("a", list(script))
    client, _, _ = make_client({"a": fa})
    return client, fa


async def run(client, tools=(ADD,), **kw):
    return await run_agent(client=client, role="demo", system="sys", task="compute", result_model=Answer,
                           tools=list(tools), **kw)


def call(name, args):
    return tool_call_response("a", (name, args))


async def test_happy_path_tool_then_submit():
    client, fa = setup(call("add", {"a": 2, "b": 3}), call("submit_result", {"answer": 5}))
    res = await run(client)
    assert (res.status, res.result, res.steps, res.provider) == ("succeeded", Answer(answer=5), 2, "a")
    first = fa.requests[0]
    assert [t.name for t in first.tools] == ["add", "submit_result"] and first.require_tool is True
    last_msg = fa.requests[1].messages[-1]
    assert (last_msg.role, last_msg.text()) == ("tool", "5")


async def test_invalid_submit_gets_one_repair():
    client, fa = setup(call("submit_result", {"answer": "x"}), call("submit_result", {"answer": 7}))
    res = await run(client)
    assert res.status == "succeeded" and res.result.answer == 7
    assert fa.requests[1].messages[-1].text().startswith("INVALID RESULT")


async def test_invalid_submit_twice_fails():
    client, _ = setup(call("submit_result", {"answer": "x"}), call("submit_result", {}))
    res = await run(client)
    assert res.status == "failed" and "invalid result" in res.error


async def test_unknown_tool_bad_args_and_exceptions_are_reported_to_model():
    async def boom(_):
        raise ValueError("kaboom")

    boom_tool = Tool("boom", "Always fails.", AddParams, boom)
    client, fa = setup(call("nope", {}), call("add", {"a": "x", "b": 1}), call("boom", {"a": 1, "b": 1}),
                       call("submit_result", {"answer": 1}))
    res = await run(client, tools=(ADD, boom_tool))
    assert res.status == "succeeded"
    outputs = [m.text() for m in res.transcript if m.role == "tool"]
    assert outputs[0].startswith("ERROR: unknown tool 'nope'")
    assert outputs[1].startswith("ERROR: invalid arguments for add")
    assert outputs[2] == "ERROR: ValueError: kaboom"


async def test_last_step_only_offers_submit():
    client, fa = setup(call("add", {"a": 1, "b": 1}), call("submit_result", {"answer": 2}))
    res = await run(client, budget=AgentBudget(max_steps=2))
    assert res.status == "succeeded"
    final = fa.requests[1]
    assert [t.name for t in final.tools] == ["submit_result"]
    assert final.messages[-1].text() == BUDGET_MSG


async def test_budget_exhausted_without_submit_fails():
    client, _ = setup(call("add", {"a": 1, "b": 1}), call("add", {"a": 1, "b": 1}))
    res = await run(client, budget=AgentBudget(max_steps=2))
    assert res.status == "failed" and "budget" in res.error


async def test_missing_tool_call_gets_nudged():
    client, fa = setup(text_response("a", "hmm"), call("submit_result", {"answer": 3}))
    res = await run(client)
    assert res.status == "succeeded"
    assert fa.requests[1].messages[-1].text() == NUDGE_MSG


async def test_submit_with_other_calls_skips_the_others():
    both = tool_call_response("a", ("add", {"a": 1, "b": 1}), ("submit_result", {"answer": 9}))
    client, _ = setup(both)
    res = await run(client)
    assert res.result.answer == 9
    assert any(m.text().startswith("SKIPPED") for m in res.transcript if m.role == "tool")


async def test_events_are_emitted():
    events = []

    async def sink(ev):
        events.append(ev.kind)

    client, _ = setup(call("add", {"a": 2, "b": 3}), call("submit_result", {"answer": 5}))
    await run(client, on_event=sink)
    assert events == ["step", "tool", "step", "submitted"]


async def test_parallel_tool_calls_run_concurrently():
    async def slow(p: AddParams) -> int:
        await asyncio.sleep(0.2)
        return p.a

    slow_tool = Tool("slow", "Sleeps.", AddParams, slow)
    two = tool_call_response("a", ("slow", {"a": 1, "b": 0}), ("slow", {"a": 2, "b": 0}))
    client, _ = setup(two, call("submit_result", {"answer": 3}))
    started = time.monotonic()
    await run(client, tools=(slow_tool,))
    assert time.monotonic() - started < 0.35


def test_reserved_tool_name_rejected():
    bad = Tool("submit_result", "x", AddParams, _add)
    client, _ = setup()
    with pytest.raises(ValueError, match="reserved"):
        asyncio.run(run(client, tools=(bad,)))


def test_truncate():
    assert truncate("abc", 5) == "abc"
    assert truncate("abcdefgh", 3).startswith("abc\n…[truncated 5 chars]")


def test_compaction_elides_old_tool_outputs_only():
    c = ToolCall("c", "t", {})
    msgs = [Message.user("TASK")]
    for _ in range(10):
        msgs += [Message.assistant("", [c]), Message.tool_result(c, "x" * 1000)]
    out = compact_transcript(msgs, max_chars=4000, keep_last=6)
    assert out[0].text() == "TASK"
    assert all(m.text() != ELIDED for m in out[-6:])
    assert sum(1 for m in out if m.text() == ELIDED) >= 5
    assert compact_transcript(msgs[:3], max_chars=10_000) == msgs[:3]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest Agent/tests/test_agent_loop.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tf_agent.loop'`.

- [ ] **Step 3: Implement**

`Agent/tf_agent/loop/__init__.py`: empty file.

`Agent/tf_agent/loop/tools.py`:
```python
"""Tool definition for agents: pydantic-validated params, async handler, compact output."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from tf_agent.models.types import ToolSpec


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n…[truncated {len(text) - max_chars} chars]"


def default_compact(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, BaseModel):
        return result.model_dump_json()
    return json.dumps(result, ensure_ascii=False, default=str)


@dataclass
class Tool:
    name: str
    description: str
    params: type[BaseModel]
    handler: Callable[[Any], Awaitable[Any]]
    compact: Callable[[Any], str] = default_compact

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.params.model_json_schema())
```

`Agent/tf_agent/loop/compaction.py`:
```python
"""Keep re-sent transcripts bounded by eliding old tool outputs (Claude calls are stateless, D-31)."""
from __future__ import annotations

import json

from tf_agent.models.types import Message, TextPart

ELIDED = "[elided to save context]"


def transcript_chars(messages: list[Message]) -> int:
    return sum(len(m.text()) + sum(len(json.dumps(c.arguments)) for c in m.tool_calls) for m in messages)


def compact_transcript(messages: list[Message], max_chars: int, keep_last: int = 6) -> list[Message]:
    out = list(messages)  # always a copy: a request must never alias the live, growing transcript
    total = transcript_chars(out)
    if total <= max_chars:
        return out
    limit = max(0, len(out) - keep_last)
    for i in range(limit):
        if total <= max_chars:
            break
        m = out[i]
        if m.role == "tool" and m.text() != ELIDED:
            total -= len(m.text()) - len(ELIDED)
            out[i] = Message(role="tool", parts=[TextPart(ELIDED)], tool_call_id=m.tool_call_id,
                             tool_name=m.tool_name)
    return out
```

`Agent/tf_agent/loop/agent.py`:
```python
"""run_agent(): provider-agnostic tool-calling loop ending in a validated submit_result (Code docs/01-agents.md)."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from tf_agent.loop.compaction import compact_transcript
from tf_agent.loop.tools import Tool, truncate
from tf_agent.models.client import CallContext, ModelClient
from tf_agent.models.types import CompletionRequest, ImagePart, Message, ToolCall, ToolSpec

SUBMIT = "submit_result"
BUDGET_MSG = "Step budget exhausted. Call submit_result now with your best result."
NUDGE_MSG = "You must respond with a tool call. Call submit_result when you are done."

T = TypeVar("T", bound=BaseModel)


@dataclass
class AgentBudget:
    max_steps: int = 30
    max_tool_output_chars: int = 4000
    max_transcript_chars: int = 150_000


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["step", "tool", "submitted", "failed"]
    step: int
    detail: str


@dataclass
class AgentResult(Generic[T]):
    status: Literal["succeeded", "failed"]
    result: T | None
    steps: int
    error: str | None
    transcript: list[Message]
    provider: str | None
    model: str | None


EventSink = Callable[[AgentEvent], Awaitable[None]]


def _short(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, err['loc'])) or 'root'}: {err['msg']}" for err in e.errors()[:8])


async def _execute(by_name: dict[str, Tool], call: ToolCall, max_chars: int) -> str:
    tool = by_name.get(call.name)
    if tool is None:
        names = ", ".join(sorted(by_name)) or "none"
        return f"ERROR: unknown tool {call.name!r}. Available tools: {names}, {SUBMIT}."
    try:
        params = tool.params.model_validate(call.arguments)
    except ValidationError as e:
        return f"ERROR: invalid arguments for {call.name}: {_short(e)}"
    try:
        result: Any = await tool.handler(params)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    return truncate(tool.compact(result), max_chars)


async def run_agent(
    *,
    client: ModelClient,
    role: str,
    system: str,
    task: str,
    result_model: type[T],
    tools: Sequence[Tool] = (),
    images: Sequence[ImagePart] = (),
    budget: AgentBudget | None = None,
    run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    provider: str | None = None,
    on_event: EventSink | None = None,
) -> AgentResult[T]:
    budget = budget or AgentBudget()
    by_name = {t.name: t for t in tools}
    if SUBMIT in by_name:
        raise ValueError(f"{SUBMIT!r} is a reserved tool name")
    submit_spec = ToolSpec(SUBMIT, "Submit your final result. Call it exactly once, when you are done.",
                           result_model.model_json_schema())
    ctx = CallContext(role=role, run_id=run_id, task_id=task_id)
    messages: list[Message] = [Message.user(task, list(images))]
    repairs_left = 1
    used_provider: str | None = None
    used_model: str | None = None

    async def emit(kind: Literal["step", "tool", "submitted", "failed"], step: int, detail: str) -> None:
        if on_event is not None:
            await on_event(AgentEvent(kind, step, detail))

    for step in range(1, budget.max_steps + 1):
        last = step == budget.max_steps
        if last:
            messages.append(Message.user(BUDGET_MSG))
        specs = [submit_spec] if last else [t.spec() for t in tools] + [submit_spec]
        req = CompletionRequest(model="", system=system, tools=specs, require_tool=True,
                                messages=compact_transcript(messages, budget.max_transcript_chars))
        resp = await client.complete(req, ctx, only=provider)
        used_provider, used_model = resp.provider, resp.model
        messages.append(Message.assistant(resp.text, resp.tool_calls))
        await emit("step", step, ", ".join(c.name for c in resp.tool_calls) or "no tool call")

        if not resp.tool_calls:
            messages.append(Message.user(NUDGE_MSG))
            continue

        submits = [c for c in resp.tool_calls if c.name == SUBMIT]
        others = [c for c in resp.tool_calls if c.name != SUBMIT]
        if submits:
            for c in others:
                messages.append(Message.tool_result(c, "SKIPPED: submit_result was called in the same step."))
            submit = submits[0]
            try:
                value = result_model.model_validate(submit.arguments)
            except ValidationError as e:
                if repairs_left > 0:
                    repairs_left -= 1
                    messages.append(Message.tool_result(
                        submit, f"INVALID RESULT: {_short(e)}. Fix the problems and call submit_result again."))
                    continue
                await emit("failed", step, "invalid result after one repair")
                return AgentResult("failed", None, step, f"invalid result: {_short(e)}", messages,
                                   used_provider, used_model)
            messages.append(Message.tool_result(submit, "ACCEPTED"))
            await emit("submitted", step, "result accepted")
            return AgentResult("succeeded", value, step, None, messages, used_provider, used_model)

        outputs = await asyncio.gather(*(_execute(by_name, c, budget.max_tool_output_chars) for c in others))
        for c, out in zip(others, outputs, strict=True):
            messages.append(Message.tool_result(c, out))
            await emit("tool", step, f"{c.name}: {out[:120]}")

    await emit("failed", budget.max_steps, "step budget exhausted")
    return AgentResult("failed", None, budget.max_steps, "step budget exhausted without a valid submit_result",
                       messages, used_provider, used_model)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest Agent/tests/test_agent_loop.py -v`
Expected: `13 passed`.

- [ ] **Step 5: Commit**

```bash
git add Agent
git commit -m "feat(agent): provider-agnostic tool-calling loop with submit_result, repair, budget, compaction" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Services wiring, provider API, `tf` CLI (doctor, login, models, migrate, demo-agent)

**Files:**
- Create: `Agent/tf_agent/config.py`
- Create: `Backend/tf_backend/services.py`, `Backend/tf_backend/main.py`, `Backend/tf_backend/doctor.py`, `Backend/tf_backend/api/__init__.py` (empty), `Backend/tf_backend/api/health.py`, `Backend/tf_backend/api/providers.py`
- Modify: `Backend/tf_backend/cli.py` (replace Task 1 placeholder)
- Test: `Backend/tests/test_providers_api.py`, `Backend/tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 2–9.
- Produces:
  - `tf_agent.config.AppSettings` (`database_url`, `config_dir`, `claude_bin`, `claude_runtime_dir`, `chatgpt_auth_file`, `chatgpt_models_cache`, `per_provider_concurrency`)
  - `tf_backend.services.Services` dataclass (`settings, chatgpt_auth, claude_auth, adapters, registry, router, governor, client, engine=None, sessionmaker=None`), `async build_services(settings=None, *, with_db=True)`, `async close_services(sv)`
  - `tf_backend.main.create_app(services=None) -> FastAPI`, module-level `app`
  - HTTP: `GET /api/health`, `GET /api/providers`, `GET /api/providers/{p}/models`, `POST /api/providers/chatgpt/login/start {device_code}`, `POST /api/providers/claude/login/start`, `POST /api/providers/claude/login/complete {code}`, `POST /api/providers/{p}/logout`
  - CLI: `tf version | migrate | doctor | models | demo-agent | login chatgpt [--device] | login claude`
  - `tf_backend.doctor.Check(name, level, detail)`, `run_checks(probes)`, `disk_check(free_gb)`, `default_probes(settings)`

- [ ] **Step 1: Write the failing tests**

`Backend/tests/test_providers_api.py`:
```python
import httpx
import pytest

from tf_agent.models.fake import FakeAdapter
from tf_agent.testing import make_client
from tf_backend.main import create_app
from tf_backend.services import Services


class StubChatAuth:
    def __init__(self):
        self.logged_out = False

    async def browser_login_start(self):
        return "https://auth.openai.com/oauth/authorize?x=1"

    async def device_login_start(self):
        return {"verification_url": "https://auth.openai.com/codex/device", "user_code": "ABCD"}

    def logout(self):
        self.logged_out = True


class StubClaudeAuth:
    def __init__(self):
        self.logged_out = False

    async def login_start(self, email=None):
        return {"started": True, "auth_url": "https://claude.ai/oauth/authorize?x=1"}

    async def login_complete(self, raw):
        if raw == "bad":
            raise RuntimeError("bad code")
        return True

    def logout(self):
        self.logged_out = True


@pytest.fixture
def services():
    adapters = {"claude": FakeAdapter("claude"), "chatgpt": FakeAdapter("chatgpt")}
    client, governor, _ = make_client(adapters)
    return Services(settings=None, chatgpt_auth=StubChatAuth(), claude_auth=StubClaudeAuth(), adapters=adapters,
                    registry=client.router.registry, router=client.router, governor=governor, client=client)


@pytest.fixture
async def http(services):
    app = create_app(services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_health(http):
    assert (await http.get("/api/health")).json() == {"ok": True}


async def test_list_providers(http):
    body = (await http.get("/api/providers")).json()
    assert {p["provider"] for p in body} == {"claude", "chatgpt"}
    assert all(p["connected"] and p["status"] == "ok" for p in body)


async def test_models_and_unknown_provider(http):
    models = (await http.get("/api/providers/chatgpt/models")).json()
    assert models[0]["model_id"] == "chatgpt-model"
    assert (await http.get("/api/providers/nope/models")).status_code == 404


async def test_chatgpt_login_start_browser_and_device(http):
    assert (await http.post("/api/providers/chatgpt/login/start", json={})).json()["auth_url"].startswith("https://")
    dev = (await http.post("/api/providers/chatgpt/login/start", json={"device_code": True})).json()
    assert dev["user_code"] == "ABCD"


async def test_claude_login_flow(http, services):
    await services.governor.mark_auth_error("claude", "expired")
    assert (await http.post("/api/providers/claude/login/start")).json()["auth_url"].startswith("https://")
    assert (await http.post("/api/providers/claude/login/complete", json={"code": "good"})).json() == {
        "connected": True}
    assert services.governor.status("claude").status == "ok"
    assert (await http.post("/api/providers/claude/login/complete", json={"code": "bad"})).status_code == 409


async def test_logout(http, services):
    assert (await http.post("/api/providers/chatgpt/logout")).json() == {"ok": True}
    assert services.chatgpt_auth.logged_out is True


async def test_connected_provider_clears_stale_auth_error(http, services):
    await services.governor.mark_auth_error("chatgpt", "old")
    body = {p["provider"]: p for p in (await http.get("/api/providers")).json()}
    assert body["chatgpt"]["status"] == "ok"
```

`Backend/tests/test_cli.py`:
```python
from pathlib import Path

from typer.testing import CliRunner

from tf_backend.cli import alembic_config, app
from tf_backend.doctor import Check, disk_check, run_checks


def test_help_lists_commands():
    out = CliRunner().invoke(app, ["--help"]).output
    for name in ("doctor", "migrate", "login", "models", "demo-agent", "version"):
        assert name in out


def test_alembic_config_points_at_database_ini():
    ini = Path(alembic_config().config_file_name)
    assert ini.name == "alembic.ini" and ini.parent.name == "Database" and ini.exists()


def test_run_checks_turns_exceptions_into_fail():
    def boom():
        raise RuntimeError("no db")

    checks = run_checks({"ok": lambda: Check("ok", "OK", "fine"), "db": boom})
    assert [c.level for c in checks] == ["OK", "FAIL"]
    assert "RuntimeError" in checks[1].detail


def test_disk_check_levels():
    assert disk_check(3).level == "FAIL"
    assert disk_check(8).level == "WARN"
    assert disk_check(50).level == "OK"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest Backend/tests -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tf_backend.main'` / `tf_backend.doctor`.

- [ ] **Step 3: Implement settings and services**

`Agent/tf_agent/config.py`:
```python
import tempfile
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Runtime settings; read from the environment and `.env` in the app root (run commands from there)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://tf:tf@localhost:5433/trendfinder"
    config_dir: Path = Path("config")
    claude_bin: str = "claude"
    claude_runtime_dir: Path = Path(tempfile.gettempdir()) / "tf-claude"
    chatgpt_auth_file: Path = Path("secrets/chatgpt-auth.json")
    chatgpt_models_cache: Path = Path("secrets/chatgpt-models.json")
    per_provider_concurrency: int = 4
```

`Backend/tf_backend/services.py`:
```python
"""Builds the long-lived service graph shared by the API and the CLI."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tf_agent.config import AppSettings
from tf_agent.models.base import ProviderAdapter
from tf_agent.models.chatgpt_adapter import ChatGPTOAuthAdapter
from tf_agent.models.chatgpt_auth import ChatGptAuth
from tf_agent.models.claude_cli import ClaudeCLIAdapter, ClaudeCliAuth
from tf_agent.models.client import ModelClient
from tf_agent.models.governor import UsageGovernor
from tf_agent.models.persistence import DbCallLedger, DbProviderStateStore, save_models
from tf_agent.models.registry import ModelRegistry, load_aliases
from tf_agent.models.router import RoleRouter, load_roles
from tf_db.session import make_engine, make_sessionmaker


@dataclass
class Services:
    settings: AppSettings | None
    chatgpt_auth: Any
    claude_auth: Any
    adapters: dict[str, ProviderAdapter]
    registry: ModelRegistry
    router: RoleRouter
    governor: UsageGovernor
    client: ModelClient
    engine: AsyncEngine | None = None
    sessionmaker: async_sessionmaker[AsyncSession] | None = None


async def build_services(settings: AppSettings | None = None, *, with_db: bool = True) -> Services:
    s = settings or AppSettings()
    chatgpt_auth = ChatGptAuth(s.chatgpt_auth_file)
    claude_auth = ClaudeCliAuth(s.claude_bin, log_path=s.claude_runtime_dir / "login.log")
    adapters: dict[str, ProviderAdapter] = {
        "claude": ClaudeCLIAdapter(claude_auth, runtime_dir=s.claude_runtime_dir),
        "chatgpt": ChatGPTOAuthAdapter(chatgpt_auth, models_cache=s.chatgpt_models_cache),
    }
    engine = sm = ledger = store = on_models = None
    if with_db:
        engine = make_engine(s.database_url)
        sm = make_sessionmaker(engine)
        ledger = DbCallLedger(sm)
        store = DbProviderStateStore(sm)

        async def on_models(provider, models):
            await save_models(sm, provider, models)

    registry = ModelRegistry(adapters, load_aliases(s.config_dir / "model_aliases.yaml"), on_models=on_models)
    router = RoleRouter(load_roles(s.config_dir / "roles.yaml"), registry)
    governor = UsageGovernor(adapters.keys(), s.per_provider_concurrency, store=store)
    if store is not None:
        await governor.load()
    client = ModelClient(adapters, router, governor, ledger)
    return Services(s, chatgpt_auth, claude_auth, adapters, registry, router, governor, client, engine, sm)


async def close_services(sv: Services) -> None:
    if sv.engine is not None:
        await sv.engine.dispose()
```

- [ ] **Step 4: Implement the API**

`Backend/tf_backend/api/health.py`:
```python
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}
```

`Backend/tf_backend/api/providers.py`:
```python
"""Provider status, model lists and subscription logins (Docs/Code docs/02-model-layer.md)."""
import asyncio
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from tf_agent.models.chatgpt_auth import ChatGptAuthError
from tf_backend.services import Services

router = APIRouter(prefix="/providers", tags=["providers"])


def _sv(request: Request) -> Services:
    return request.app.state.services


def _require(sv: Services, provider: str) -> None:
    if provider not in sv.adapters:
        raise HTTPException(404, f"unknown provider {provider!r}")


@router.get("")
async def list_providers(request: Request) -> list[dict[str, Any]]:
    sv = _sv(request)
    out = []
    for name, adapter in sv.adapters.items():
        h = await adapter.health()
        if h.connected and sv.governor.status(name).status == "auth_error":
            await sv.governor.mark_ok(name)
        st = sv.governor.status(name)
        out.append({"provider": name, "connected": h.connected, "detail": h.detail, "account": h.account,
                    "status": st.status, "cooling_until": st.cooling_until, "used_percent": st.used_percent,
                    "last_error": st.last_error})
    return out


@router.get("/{provider}/models")
async def list_models(provider: str, request: Request) -> list[dict[str, Any]]:
    sv = _sv(request)
    _require(sv, provider)
    models = sv.registry.models(provider) or await sv.registry.refresh_provider(provider)
    return [asdict(m) for m in models]


class ChatGptLoginStart(BaseModel):
    device_code: bool = False


@router.post("/chatgpt/login/start")
async def chatgpt_login_start(body: ChatGptLoginStart, request: Request) -> dict[str, Any]:
    sv = _sv(request)
    try:
        if body.device_code:
            return await sv.chatgpt_auth.device_login_start()
        return {"auth_url": await sv.chatgpt_auth.browser_login_start()}
    except ChatGptAuthError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/claude/login/start")
async def claude_login_start(request: Request) -> dict[str, Any]:
    return await _sv(request).claude_auth.login_start()


class ClaudeLoginComplete(BaseModel):
    code: str


@router.post("/claude/login/complete")
async def claude_login_complete(body: ClaudeLoginComplete, request: Request) -> dict[str, bool]:
    sv = _sv(request)
    try:
        connected = await sv.claude_auth.login_complete(body.code)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    await sv.governor.mark_ok("claude")
    return {"connected": connected}


@router.post("/{provider}/logout")
async def logout(provider: str, request: Request) -> dict[str, bool]:
    sv = _sv(request)
    _require(sv, provider)
    auth = sv.claude_auth if provider == "claude" else sv.chatgpt_auth
    await asyncio.to_thread(auth.logout)
    return {"ok": True}
```

`Backend/tf_backend/main.py`:
```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tf_backend.api import health, providers
from tf_backend.services import Services, build_services, close_services


def create_app(services: Services | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if services is not None:
            yield
            return
        sv = await build_services()
        app.state.services = sv
        await sv.registry.refresh()
        try:
            yield
        finally:
            await close_services(sv)

    app = FastAPI(title="Trend Finder", version="0.1.0", lifespan=lifespan)
    if services is not None:
        app.state.services = services
    app.include_router(health.router, prefix="/api")
    app.include_router(providers.router, prefix="/api")
    return app


app = create_app()
```

- [ ] **Step 5: Implement doctor and the CLI**

`Backend/tf_backend/doctor.py`:
```python
"""Environment checks for `tf doctor`."""
import asyncio
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from tf_agent.config import AppSettings
from tf_agent.models.chatgpt_auth import ChatGptAuth
from tf_agent.models.claude_cli import ClaudeCliAuth
from tf_db.session import ping


@dataclass(frozen=True)
class Check:
    name: str
    level: Literal["OK", "WARN", "FAIL"]
    detail: str


Probe = Callable[[], Check]


def run_checks(probes: dict[str, Probe]) -> list[Check]:
    out = []
    for name, probe in probes.items():
        try:
            out.append(probe())
        except Exception as e:
            out.append(Check(name, "FAIL", f"{type(e).__name__}: {e}"))
    return out


def disk_check(free_gb: float) -> Check:
    if free_gb < 5:
        return Check("disk", "FAIL", f"only {free_gb:.1f} GB free; media needs ~5 GB")
    if free_gb < 10:
        return Check("disk", "WARN", f"{free_gb:.1f} GB free; consider freeing space")
    return Check("disk", "OK", f"{free_gb:.1f} GB free")


def default_probes(settings: AppSettings) -> dict[str, Probe]:
    def claude() -> Check:
        st = ClaudeCliAuth(settings.claude_bin).status()
        if not st["available"]:
            return Check("claude", "FAIL", f"claude CLI not found ({settings.claude_bin})")
        if not st["connected"]:
            return Check("claude", "FAIL", "not logged in: run `tf login claude`")
        return Check("claude", "OK", f"{st['email']} ({st['subscription']})")

    def chatgpt() -> Check:
        st = ChatGptAuth(settings.chatgpt_auth_file).status()
        if not st["connected"]:
            return Check("chatgpt", "WARN", "not logged in: run `tf login chatgpt`")
        return Check("chatgpt", "OK", f"{st['email']} ({st['plan']})")

    def database() -> Check:
        if asyncio.run(ping(settings.database_url)):
            return Check("database", "OK", "reachable")
        return Check("database", "FAIL", "unreachable: docker compose -f Database/docker-compose.yml up -d")

    def ffmpeg() -> Check:
        missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
        return Check("ffmpeg", "FAIL", f"missing: {', '.join(missing)}") if missing else Check("ffmpeg", "OK", "found")

    def disk() -> Check:
        return disk_check(shutil.disk_usage(".").free / 1e9)

    return {"claude": claude, "chatgpt": chatgpt, "database": database, "ffmpeg": ffmpeg, "disk": disk}
```

`Backend/tf_backend/cli.py` (replace the whole file):
```python
"""`tf` command line (Docs/Code docs/08-infrastructure-and-repo.md)."""
import asyncio
import subprocess
import time
import webbrowser
from pathlib import Path

import typer
from alembic import command
from alembic.config import Config
from pydantic import BaseModel

import tf_db
from tf_agent.config import AppSettings
from tf_agent.loop.agent import AgentBudget, AgentEvent, run_agent
from tf_agent.loop.tools import Tool
from tf_agent.models.chatgpt_auth import ChatGptAuth
from tf_agent.models.claude_cli import ClaudeCliAuth
from tf_backend.doctor import default_probes, run_checks
from tf_backend.services import build_services, close_services

app = typer.Typer(no_args_is_help=True, help="Trend Finder command line")
login_app = typer.Typer(no_args_is_help=True, help="Log in to a model provider")
app.add_typer(login_app, name="login")


def alembic_config() -> Config:
    return Config(str(Path(tf_db.__file__).resolve().parent.parent / "alembic.ini"))


@app.command()
def version() -> None:
    """Print the app version."""
    from tf_backend import __version__

    typer.echo(__version__)


@app.command()
def migrate() -> None:
    """Upgrade the database to the latest migration."""
    command.upgrade(alembic_config(), "head")
    typer.echo("database is at the latest migration")


@app.command()
def doctor() -> None:
    """Check providers, database, ffmpeg and disk space."""
    checks = run_checks(default_probes(AppSettings()))
    for c in checks:
        typer.echo(f"[{c.level:<4}] {c.name:<9} {c.detail}")
    if any(c.level == "FAIL" for c in checks):
        raise typer.Exit(1)


@login_app.command("chatgpt")
def login_chatgpt(device: bool = typer.Option(False, "--device", help="Use device-code login instead of a local callback")) -> None:
    """Log in with your ChatGPT subscription."""
    asyncio.run(_login_chatgpt(device))


async def _login_chatgpt(device: bool) -> None:
    auth = ChatGptAuth(AppSettings().chatgpt_auth_file)
    started = time.time()
    if device:
        info = await auth.device_login_start()
        typer.echo(f"Open {info['verification_url']} and enter the code: {info['user_code']}")
    else:
        url = await auth.browser_login_start()
        typer.echo(f"Opening your browser. If it doesn't open, visit:\n{url}")
        webbrowser.open(url)
    try:
        ok = await auth.wait_connected(since=started, timeout_s=900)
    finally:
        await auth.stop_callback_server()
    if not ok:
        typer.echo("Login timed out.")
        raise typer.Exit(1)
    st = auth.status()
    typer.echo(f"ChatGPT connected: {st['email']} ({st['plan']})")


@login_app.command("claude")
def login_claude() -> None:
    """Log in with your Claude subscription (runs `claude auth login`)."""
    settings = AppSettings()
    st = ClaudeCliAuth(settings.claude_bin).status()
    if st["connected"]:
        typer.echo(f"Claude already connected: {st['email']} ({st['subscription']})")
        return
    raise typer.Exit(subprocess.call([settings.claude_bin, "auth", "login", "--claudeai"]))


@app.command()
def models() -> None:
    """List the models each provider currently offers."""
    asyncio.run(_models())


async def _models() -> None:
    sv = await build_services(with_db=False)
    try:
        for provider, infos in (await sv.registry.refresh()).items():
            typer.echo(f"{provider}:")
            for m in sorted(infos, key=lambda m: m.priority):
                flag = " (hidden)" if m.hidden else ""
                typer.echo(f"  {m.model_id:<28} {m.display_name or ''}{flag}")
            for alias in ("best", "fast"):
                typer.echo(f"  {alias} -> {sv.registry.resolve(provider, alias)}")
    finally:
        await close_services(sv)


class _AddParams(BaseModel):
    a: int
    b: int


class _DemoResult(BaseModel):
    answer: int
    explanation: str


@app.command("demo-agent")
def demo_agent(
    provider: str = typer.Option("claude", help="claude or chatgpt"),
    question: str = typer.Option("What is (17 + 25) + 8? Use the add tool for every addition."),
) -> None:
    """Run a tiny tool-using agent end-to-end on one provider."""
    asyncio.run(_demo(provider, question))


async def _demo(provider: str, question: str) -> None:
    sv = await build_services(with_db=False)

    async def add(p: _AddParams) -> int:
        return p.a + p.b

    async def show(ev: AgentEvent) -> None:
        typer.echo(f"  step {ev.step} [{ev.kind}] {ev.detail}")

    try:
        await sv.registry.refresh_provider(provider)
        res = await run_agent(
            client=sv.client, role="demo", provider=provider, result_model=_DemoResult,
            system="You are a careful assistant. Use the add tool for every addition, then call submit_result.",
            task=question, tools=[Tool("add", "Add two integers and return the sum.", _AddParams, add)],
            budget=AgentBudget(max_steps=8), on_event=show)
    finally:
        await close_services(sv)
    typer.echo(f"{res.status} via {res.provider}/{res.model} in {res.steps} steps: {res.result or res.error}")
    if res.status != "succeeded":
        raise typer.Exit(1)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest Backend/tests -v`
Expected: `12 passed` (1 workspace + 7 API + 4 CLI).

- [ ] **Step 7: Run the full suite and commit**

Run: `uv run pytest -v`
Expected: all tests pass (`106 passed` with Docker running).

```bash
git add Agent Backend
git commit -m "feat(backend): service wiring, provider API, tf CLI (doctor, login, models, migrate, demo-agent)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Live verification on both subscriptions + docs

**Files:**
- Create: `Agent/tests/live/test_live_providers.py`
- Create: `Docs/Plans/README.md`
- Modify: `Docs/Code docs/08-infrastructure-and-repo.md` (Dev workflow section)

**Interfaces:**
- Consumes: everything above. Produces: verified, documented Plan 1 baseline for Plan 2.

- [ ] **Step 1: Write the live tests**

`Agent/tests/live/test_live_providers.py`:
```python
"""Real calls on the owner's subscriptions. Run explicitly: uv run pytest -m live Agent/tests/live -v"""
import pytest

from tf_agent.config import AppSettings
from tf_agent.models.chatgpt_adapter import ChatGPTOAuthAdapter
from tf_agent.models.chatgpt_auth import ChatGptAuth
from tf_agent.models.claude_cli import ClaudeCLIAdapter, ClaudeCliAuth
from tf_agent.models.types import CompletionRequest, Message, ToolSpec

pytestmark = pytest.mark.live

ADD = ToolSpec("add", "Add two integers.", {"type": "object", "properties": {"a": {"type": "integer"},
                                                                          "b": {"type": "integer"}},
                                            "required": ["a", "b"]})
ANSWER = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]}


def claude() -> ClaudeCLIAdapter:
    return ClaudeCLIAdapter(ClaudeCliAuth(AppSettings().claude_bin))


def chatgpt() -> ChatGPTOAuthAdapter:
    settings = AppSettings()
    auth = ChatGptAuth(settings.chatgpt_auth_file)
    if not auth.status()["connected"]:
        pytest.skip("run `uv run tf login chatgpt` first")
    return ChatGPTOAuthAdapter(auth, models_cache=settings.chatgpt_models_cache)


async def test_claude_structured_output_is_isolated():
    resp = await claude().complete(CompletionRequest(
        model="haiku", system="Answer per the schema.", messages=[Message.user("What is 2+2?")],
        output_schema=ANSWER))
    assert resp.structured == {"answer": 4}
    assert resp.usage.input_tokens < 20_000  # D-36: isolated calls stay ~3k, not ~70k


async def test_claude_tool_step():
    resp = await claude().complete(CompletionRequest(
        model="haiku", system="Use the add tool.", messages=[Message.user("Add 2 and 3.")],
        tools=[ADD], require_tool=True))
    assert resp.tool_calls[0].name == "add" and resp.tool_calls[0].arguments == {"a": 2, "b": 3}


async def test_chatgpt_models_and_tool_step():
    adapter = chatgpt()
    visible = [m for m in await adapter.list_models() if not m.hidden]
    assert visible
    model = next((m.model_id for m in visible if m.model_id == "gpt-6-luna"), visible[-1].model_id)
    resp = await adapter.complete(CompletionRequest(
        model=model, system="Use the add tool.", messages=[Message.user("Add 2 and 3.")],
        tools=[ADD], require_tool=True, reasoning_effort="low"))
    assert resp.tool_calls[0].name == "add"
    assert resp.rate is not None and resp.rate.window_minutes == 300
```

- [ ] **Step 2: Bring everything up and log in**

```bash
docker compose -f Database/docker-compose.yml up -d
uv run tf migrate
uv run tf login chatgpt          # browser opens; if port 1455 is busy: uv run tf login chatgpt --device
uv run tf doctor
```
Expected: `tf doctor` prints `[OK  ]` for claude, chatgpt, database, ffmpeg; disk is `WARN` or `OK` (≈19 GB free).

- [ ] **Step 3: Run the live tests**

Run: `uv run pytest -m live Agent/tests/live -v`
Expected: `3 passed`.

- [ ] **Step 4: Run the demo agent on both providers**

```bash
uv run tf demo-agent --provider claude
uv run tf demo-agent --provider chatgpt
uv run tf models
```
Expected for each demo: step lines showing `add` calls, then `succeeded via <provider>/<model> in N steps: answer=50 …`. `tf models` lists both providers with `best -> …` / `fast -> …`.

- [ ] **Step 5: Check the API**

```bash
uv run uvicorn tf_backend.main:app --port 8000 &
sleep 4
curl -s localhost:8000/api/providers | python3 -m json.tool
kill %1
```
Expected: two providers, both `"connected": true`, `"status": "ok"`; ChatGPT shows `used_percent`.

- [ ] **Step 6: Update docs**

`Docs/Plans/README.md`:
```markdown
# Implementation Plans

| Plan | Scope | Status |
|---|---|---|
| [Plan 1](2026-10-05-plan-1-foundation-and-model-layer.md) | Workspace, DB foundation, both subscriptions behind one adapter interface, router/governor, agent loop, provider API, `tf` CLI | Done |
| Plan 2 | Tool layer (anonymous mode, D-34) + video pipeline | Next |
| Plan 3 | Orchestrator, Master/worker roles, scoring, clustering | Planned |
| Plan 4 | Feedback & learning, run API + SSE, React frontend | Planned |
```

In `Docs/Code docs/08-infrastructure-and-repo.md`, replace the `## Dev workflow` section body with:
````markdown
```
docker compose -f Database/docker-compose.yml up -d     # Postgres (pgvector); SearXNG arrives in Plan 2
uv sync
uv run tf migrate
uv run tf login chatgpt        # once; `--device` if port 1455 is busy
uv run tf doctor
uv run uvicorn tf_backend.main:app --reload
uv run pytest                  # unit + DB tests; live tests: uv run pytest -m live Agent/tests/live
```
````

- [ ] **Step 7: Commit**

```bash
git add Agent/tests/live Docs
git commit -m "test: live provider checks; docs: plan index and dev workflow" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-Review Notes (author)

- **Spec coverage (Plan 1 scope):** D-02/D-03 → Tasks 4–6, 10; D-23 groundwork (DB) → Task 2; D-29/D-30/D-31/D-36 → Tasks 5, 6, 9; D-32 → Tasks 1–2, 10; D-37 → Tasks 5, 8; model registry/router/governor/ledger (02-model-layer.md) → Tasks 7–8; worker loop + submit_result + repair + forced submit (01-agents.md) → Task 9; `models`/`provider_state`/`model_calls` (06-data-model.md) → Task 2; provider login endpoints → Task 10; `tf doctor/login/migrate` (08) → Task 10. Everything else (tools, pipeline, orchestrator, scoring, learning, UI) is explicitly Plans 2–4.
- **Deferred from the spec on purpose:** `config/model_capabilities.yaml` (ChatGPT's model list already reports modalities/context; add only if a gap appears), SearXNG container (Plan 2, its first user).
