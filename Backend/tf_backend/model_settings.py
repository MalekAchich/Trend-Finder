"""The owner's model choices per provider (main + fast model, effort), stored in `settings.models`."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.models.claude_cli import CLAUDE_EFFORTS
from tf_backend.services import Services
from tf_db.models import Setting

KEY = "models"


class ChoiceError(ValueError):
    pass


def efforts_for(sv: Services, provider: str, models: list[str]) -> list[str]:
    """Efforts every chosen model accepts (Claude's CLI takes the same levels for all models)."""
    if provider == "claude":
        return list(CLAUDE_EFFORTS)
    known = {m.model_id: m for m in sv.registry.models(provider)}
    levels = [set(known[m].reasoning_levels) for m in models if m in known]
    if not levels:
        return []
    order = [lvl for lvl in known[models[0]].reasoning_levels] if models[0] in known else []
    common = set.intersection(*levels)
    return [lvl for lvl in order if lvl in common]


def current(sv: Services, provider: str) -> dict[str, Any]:
    aliases = sv.registry.aliases.get(provider, {})
    main, fast = aliases.get("best"), aliases.get("fast")
    return {
        "models": [{"id": m.model_id, "name": m.display_name or m.model_id, "efforts": list(m.reasoning_levels),
                    "unavailable": m.unavailable}
                   for m in sorted(sv.registry.models(provider), key=lambda m: m.priority) if not m.hidden],
        "main": main, "fast": fast, "effort": sv.client.effort_override.get(provider),
        "efforts": efforts_for(sv, provider, [m for m in (main, fast) if m]),
    }


def validate(sv: Services, provider: str, main: str, fast: str, effort: str | None) -> None:
    if provider not in sv.adapters:
        raise ChoiceError(f"unknown provider {provider}")
    known = {m.model_id: m for m in sv.registry.models(provider)}
    for model in (main, fast):
        if known and model not in known:
            raise ChoiceError(f"{model} isn't offered by {provider} right now")
        if known and known[model].unavailable:
            raise ChoiceError(f"{known[model].display_name or model} is {known[model].unavailable}")
    if effort is not None and effort not in efforts_for(sv, provider, [main, fast]):
        raise ChoiceError(f"effort {effort} isn't available for {main} and {fast}")


def apply(sv: Services, provider: str, choice: dict[str, Any]) -> None:
    sv.registry.aliases.setdefault(provider, {})
    sv.registry.aliases[provider].update({"best": choice["main"], "fast": choice["fast"]})
    if choice.get("effort"):
        sv.client.effort_override[provider] = choice["effort"]
    else:
        sv.client.effort_override.pop(provider, None)


async def save_choice(sm: async_sessionmaker[AsyncSession], sv: Services, provider: str,
                      choice: dict[str, Any]) -> None:
    async with sm() as s:
        row = (await s.execute(select(Setting).where(Setting.key == KEY).with_for_update())).scalar_one_or_none()
        value = {**(row.value if row else {}), provider: choice}
        stmt = pg_insert(Setting).values(key=KEY, value=value)
        await s.execute(stmt.on_conflict_do_update(index_elements=[Setting.key], set_={"value": value}))
        await s.commit()
    apply(sv, provider, choice)


async def load_model_choices(sm: async_sessionmaker[AsyncSession], sv: Services) -> None:
    async with sm() as s:
        value = (await s.execute(select(Setting.value).where(Setting.key == KEY))).scalar_one_or_none() or {}
    for provider, choice in value.items():
        if provider in sv.adapters:
            apply(sv, provider, choice)
