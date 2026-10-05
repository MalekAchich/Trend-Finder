"""Shared run state agents coordinate through (D-07): scope ownership, seen videos, leads, events."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.tools.normalize import norm_handle, norm_hashtag, norm_query
from tf_agent.tools.platforms import SeenFilter
from tf_agent.tools.types import VideoItem
from tf_db.models import Event, Finding, Lead, ScopeClaim, SeenItem

ScopeElement = tuple[str, str, str]  # (platform, kind, value)
_KINDS = (("queries", "query", norm_query), ("hashtags", "hashtag", norm_hashtag),
          ("sounds", "sound", lambda v: v.strip().lower()), ("creators", "creator", norm_handle))


class Blackboard:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    @staticmethod
    def normalize_scope(platform: str, scope: dict[str, Any]) -> list[ScopeElement]:
        out: list[ScopeElement] = []
        for field, kind, norm in _KINDS:
            for raw in scope.get(field) or []:
                value = norm(str(raw))[:256]
                if value and (platform, kind, value) not in out:
                    out.append((platform, kind, value))
        for kind in ("seed", "radar", "lead"):
            if scope.get(kind):
                out.append((platform, kind, str(scope[kind]).strip().lower()[:256]))
        return out

    async def claim(self, run_id: uuid.UUID, task_id: uuid.UUID, elements: list[ScopeElement]) -> list[ScopeElement]:
        """Claim every element for the task, or none of them. Returns the elements already owned by others."""
        if not elements:
            return []
        async with self._sm() as s:
            stmt = pg_insert(ScopeClaim).values([
                {"run_id": run_id, "platform": p, "kind": k, "value": v, "task_id": task_id} for p, k, v in elements
            ]).on_conflict_do_nothing().returning(ScopeClaim.platform, ScopeClaim.kind, ScopeClaim.value)
            claimed = {tuple(r) for r in (await s.execute(stmt)).all()}
            rejected = [e for e in elements if e not in claimed]
            if rejected:
                await s.rollback()
                return rejected
            await s.commit()
            return []

    async def owner(self, run_id: uuid.UUID, element: ScopeElement) -> uuid.UUID | None:
        p, k, v = element
        async with self._sm() as s:
            return (await s.execute(select(ScopeClaim.task_id).where(
                ScopeClaim.run_id == run_id, ScopeClaim.platform == p, ScopeClaim.kind == k,
                ScopeClaim.value == v))).scalar_one_or_none()

    def seen_filter_for(self, run_id: uuid.UUID, character_id: uuid.UUID) -> SeenFilter:
        """Hide videos already submitted in this run or rated in an earlier run (D-07 blackboard)."""
        async def seen_filter(items: list[VideoItem]) -> tuple[list[VideoItem], int]:
            ids = [i.canonical_id for i in items]
            if not ids:
                return items, 0
            async with self._sm() as s:
                submitted = set((await s.execute(select(Finding.canonical_id).where(
                    Finding.run_id == run_id, Finding.canonical_id.in_(ids)))).scalars())
                rated = set((await s.execute(select(SeenItem.canonical_id).where(
                    SeenItem.character_id == character_id, SeenItem.rated.is_(True),
                    SeenItem.canonical_id.in_(ids)))).scalars())
            hidden = submitted | rated
            kept = [i for i in items if i.canonical_id not in hidden]
            return kept, len(items) - len(kept)

        return seen_filter

    async def add_leads(self, run_id: uuid.UUID, task_id: uuid.UUID, leads: list[dict[str, Any]]) -> int:
        rows = [{"run_id": run_id, "source_task_id": task_id, "platform": str(l.get("platform") or "web")[:16],
                 "type": str(l["type"])[:16], "value": str(l["value"]).strip()[:256], "why": l.get("why")}
                for l in leads if l.get("type") and l.get("value")]
        if not rows:
            return 0
        async with self._sm() as s:
            stmt = pg_insert(Lead).values(rows).on_conflict_do_nothing().returning(Lead.id)
            inserted = len((await s.execute(stmt)).all())
            await s.commit()
        return inserted

    async def open_leads(self, run_id: uuid.UUID, limit: int = 30) -> list[dict[str, Any]]:
        async with self._sm() as s:
            rows = (await s.execute(select(Lead).where(Lead.run_id == run_id, Lead.status == "new")
                                    .order_by(Lead.created_at).limit(limit))).scalars().all()
        return [{"id": r.id, "type": r.type, "value": r.value, "platform": r.platform, "why": r.why} for r in rows]

    async def assign_lead(self, lead_id: uuid.UUID, task_id: uuid.UUID) -> None:
        async with self._sm() as s:
            await s.execute(update(Lead).where(Lead.id == lead_id).values(status="assigned", assigned_task_id=task_id))
            await s.commit()

    async def record_event(self, run_id: uuid.UUID, type_: str, payload: dict[str, Any]) -> int:
        async with self._sm() as s:
            event = Event(run_id=run_id, type=type_, payload=payload)
            s.add(event)
            await s.commit()
            return event.id

    async def events_after(self, run_id: uuid.UUID, after_id: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        async with self._sm() as s:
            rows = (await s.execute(select(Event).where(Event.run_id == run_id, Event.id > after_id)
                                    .order_by(Event.id).limit(limit))).scalars().all()
        return [{"id": e.id, "type": e.type, "payload": e.payload, "created_at": e.created_at.isoformat()}
                for e in rows]
