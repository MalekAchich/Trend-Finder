from typing import Protocol, runtime_checkable

from tf_agent.models.types import CompletionRequest, CompletionResponse, ModelInfo, ProviderHealth


@runtime_checkable
class ProviderAdapter(Protocol):
    name: str

    async def list_models(self) -> list[ModelInfo]: ...

    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...

    async def health(self) -> ProviderHealth: ...
