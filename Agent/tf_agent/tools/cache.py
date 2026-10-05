"""Postgres-backed tool response cache keyed by sha256(tool + normalized input) (03-tools.md: caching)."""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_db.models import ToolCacheRow


def cache_key(tool: str, key_input: Any) -> str:
    blob = json.dumps({"tool": tool, "input": key_input}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class ToolCache:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession],
                 clock: Callable[[], float] = time.time) -> None:
        self._sm = sessionmaker
        self._clock = clock

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), tz=UTC)

    async def get(self, tool: str, key_input: Any) -> dict[str, Any] | None:
        async with self._sm() as s:
            row = (await s.execute(select(ToolCacheRow).where(ToolCacheRow.key == cache_key(tool, key_input)))
                   ).scalar_one_or_none()
        if row is None or row.expires_at <= self._now():
            return None
        return row.response

    async def put(self, tool: str, key_input: Any, response: dict[str, Any], ttl_s: float) -> None:
        expires = datetime.fromtimestamp(self._clock() + ttl_s, tz=UTC)
        stmt = pg_insert(ToolCacheRow).values(key=cache_key(tool, key_input), tool=tool, response=response,
                                              expires_at=expires)
        stmt = stmt.on_conflict_do_update(index_elements=[ToolCacheRow.key],
                                          set_={"response": response, "expires_at": expires})
        async with self._sm() as s:
            await s.execute(stmt)
            await s.commit()
