"""Settings: which model each subscription uses, and how much of each subscription is being used."""
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from tf_backend.api.deps import ctx, require_client_header
from tf_backend.app_context import AppContext
from tf_backend.model_settings import ChoiceError, current, save_choice, validate
from tf_backend.services import Services
from tf_db.models import ModelCallRow, Run

router = APIRouter(tags=["settings"])


def _sv(request: Request) -> Services:
    return request.app.state.services


@router.get("/settings/models")
async def get_models(request: Request) -> dict[str, Any]:
    sv = _sv(request)
    return {p: current(sv, p) for p in sv.adapters}


class ModelChoiceIn(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    main: str = Field(min_length=1, max_length=128)
    fast: str = Field(min_length=1, max_length=128)
    effort: str | None = Field(None, max_length=16)


@router.put("/settings/models", dependencies=[Depends(require_client_header)])
async def put_models(body: ModelChoiceIn, request: Request, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    sv = _sv(request)
    try:
        validate(sv, body.provider, body.main, body.fast, body.effort)
    except ChoiceError as e:
        raise HTTPException(422, str(e)) from e
    await save_choice(c.sessionmaker, sv, body.provider,
                      {"main": body.main, "fast": body.fast, "effort": body.effort})
    return current(sv, body.provider)


@router.get("/usage")
async def usage(request: Request, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    sv = _sv(request)
    since = datetime.now(UTC) - timedelta(hours=24)
    total = func.coalesce(ModelCallRow.input_tokens, 0) + func.coalesce(ModelCallRow.output_tokens, 0)
    async with c.sessionmaker() as s:
        today = dict((p, (int(t or 0), int(n))) for p, t, n in (await s.execute(
            select(ModelCallRow.provider, func.sum(total), func.count()).where(ModelCallRow.created_at >= since)
            .group_by(ModelCallRow.provider))).all())
        recent = select(Run.id).order_by(Run.started_at.desc()).limit(20).subquery()
        per_run = (select(ModelCallRow.provider, ModelCallRow.run_id, func.sum(total).label("t"))
                   .where(ModelCallRow.run_id.in_(select(recent.c.id)))
                   .group_by(ModelCallRow.provider, ModelCallRow.run_id).subquery())
        avg = dict((p, int(round(a or 0))) for p, a in (await s.execute(
            select(per_run.c.provider, func.avg(per_run.c.t)).group_by(per_run.c.provider))).all())
    out = []
    for name in sv.adapters:
        st = sv.governor.status(name)
        rate = sv.governor.last_rate(name)
        cooling = st.cooling_until if st.cooling_until and st.cooling_until > time.time() else None
        out.append({
            "provider": name, "status": st.status if cooling or st.status == "auth_error" else "ok",
            "used_percent": st.used_percent, "window_minutes": rate.window_minutes if rate else None,
            "resets_at": rate.resets_at if rate else None, "cooling_until": cooling, "last_error": st.last_error,
            "tokens_today": today.get(name, (0, 0))[0], "calls_today": today.get(name, (0, 0))[1],
            "avg_tokens_per_run": avg.get(name),
        })
    return {"providers": out}
