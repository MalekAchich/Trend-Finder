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
