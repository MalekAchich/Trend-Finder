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
