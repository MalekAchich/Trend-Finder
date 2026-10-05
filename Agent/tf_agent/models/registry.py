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
