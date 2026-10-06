"""Provider-neutral model types shared by every adapter and the agent loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class TextPart:
    text: str
    kind: Literal["text"] = "text"


@dataclass(frozen=True)
class ImagePart:
    path: str
    mime: str | None = None  # guessed from the file extension when None
    kind: Literal["image"] = "image"


Part = TextPart | ImagePart


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    parts: list[Part] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None

    @classmethod
    def user(cls, text: str, images: list[ImagePart] | tuple[ImagePart, ...] = ()) -> Message:
        return cls(role="user", parts=[TextPart(text), *images])

    @classmethod
    def assistant(cls, text: str = "", tool_calls: list[ToolCall] | None = None) -> Message:
        return cls(role="assistant", parts=[TextPart(text)] if text else [], tool_calls=list(tool_calls or []))

    @classmethod
    def tool_result(cls, call: ToolCall, content: str) -> Message:
        return cls(role="tool", parts=[TextPart(content)], tool_call_id=call.id, tool_name=call.name)

    def text(self) -> str:
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    def images(self) -> list[ImagePart]:
        return [p for p in self.parts if isinstance(p, ImagePart)]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class CompletionRequest:
    model: str
    system: str
    messages: list[Message]
    tools: list[ToolSpec] = field(default_factory=list)
    require_tool: bool = False
    output_schema: dict[str, Any] | None = None
    schema_name: str = "result"
    reasoning_effort: str | None = None
    timeout_s: float = 180.0  # D-31 per-call default

    def __post_init__(self) -> None:
        if self.tools and self.output_schema is not None:
            raise ValueError("tools and output_schema are mutually exclusive")
        if self.require_tool and not self.tools:
            raise ValueError("require_tool needs at least one tool")
        if not self.messages:
            raise ValueError("at least one message is required")


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class RateWindow:
    name: str  # "five_hour" | "seven_day"
    used_percent: float
    window_minutes: int | None = None
    resets_at: float | None = None


@dataclass(frozen=True)
class RateInfo:
    """The binding (fullest) usage window, plus every window the provider reported."""
    used_percent: float | None = None
    window_minutes: int | None = None
    resets_at: float | None = None
    windows: tuple[RateWindow, ...] = ()


@dataclass
class CompletionResponse:
    provider: str
    model: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    structured: dict[str, Any] | None = None
    usage: Usage = field(default_factory=Usage)
    rate: RateInfo | None = None
    reasoning: str = ""  # the model's reasoning summary, when the provider exposes one


@dataclass(frozen=True)
class ModelInfo:
    provider: str
    model_id: str
    display_name: str | None = None
    context_window: int | None = None
    vision: bool = True
    priority: int = 100
    hidden: bool = False
    reasoning_levels: tuple[str, ...] = ()
    unavailable: str | None = None  # e.g. "not included in your plan"


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    connected: bool
    detail: str = ""
    account: str | None = None
