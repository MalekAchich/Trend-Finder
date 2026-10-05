"""Scoring weights (+ the learner's suggestion, never auto-applied) and platform health."""
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tf_agent.scoring.subscores import DEFAULT_WEIGHTS
from tf_backend.api.deps import ctx, require_client_header
from tf_backend.app_context import AppContext
from tf_db.models import Setting

router = APIRouter(tags=["settings"])


class Weights(BaseModel):
    fit: float
    feasibility: float
    momentum: float
    freshness: float

    @model_validator(mode="after")
    def check(self) -> "Weights":
        values = [self.fit, self.feasibility, self.momentum, self.freshness]
        if any(v < 0 or v > 1 for v in values) or abs(sum(values) - 1.0) > 0.01:
            raise ValueError("weights must each be between 0 and 1 and sum to 1")
        return self


class SettingsIn(BaseModel):
    weights: Weights


@router.get("/settings")
async def get_settings(c: AppContext = Depends(ctx)) -> dict[str, Any]:
    async with c.sessionmaker() as s:
        rows = dict((await s.execute(select(Setting.key, Setting.value))).all())
    return {"weights": rows.get("weights") or DEFAULT_WEIGHTS, "weight_suggestion": rows.get("weight_suggestion")}


@router.put("/settings", dependencies=[Depends(require_client_header)])
async def put_settings(body: SettingsIn, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    value = body.weights.model_dump()
    async with c.sessionmaker() as s:
        stmt = pg_insert(Setting).values(key="weights", value=value)
        await s.execute(stmt.on_conflict_do_update(index_elements=[Setting.key], set_={"value": value}))
        await s.commit()
    return {"weights": value}


@router.get("/platforms")
async def platforms(c: AppContext = Depends(ctx)) -> dict[str, str]:
    return c.registry.snapshot() if c.registry is not None else {}
