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
