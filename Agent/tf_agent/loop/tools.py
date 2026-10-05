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
