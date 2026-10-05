"""run_agent(): provider-agnostic tool-calling loop ending in a validated submit_result (Code docs/01-agents.md)."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from tf_agent.loop.compaction import compact_transcript
from tf_agent.loop.tools import Tool, truncate
from tf_agent.models.client import CallContext, ModelClient
from tf_agent.models.types import CompletionRequest, ImagePart, Message, ToolCall, ToolSpec

SUBMIT = "submit_result"
BUDGET_MSG = "Step budget exhausted. Call submit_result now with your best result."
NUDGE_MSG = "You must respond with a tool call. Call submit_result when you are done."

T = TypeVar("T", bound=BaseModel)


@dataclass
class AgentBudget:
    max_steps: int = 30
    max_tool_output_chars: int = 4000
    max_transcript_chars: int = 150_000


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["step", "tool", "submitted", "failed"]
    step: int
    detail: str


@dataclass
class AgentResult(Generic[T]):
    status: Literal["succeeded", "failed"]
    result: T | None
    steps: int
    error: str | None
    transcript: list[Message]
    provider: str | None
    model: str | None


EventSink = Callable[[AgentEvent], Awaitable[None]]


def _short(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, err['loc'])) or 'root'}: {err['msg']}" for err in e.errors()[:8])


async def _execute(by_name: dict[str, Tool], call: ToolCall, max_chars: int) -> str:
    tool = by_name.get(call.name)
    if tool is None:
        names = ", ".join(sorted(by_name)) or "none"
        return f"ERROR: unknown tool {call.name!r}. Available tools: {names}, {SUBMIT}."
    try:
        params = tool.params.model_validate(call.arguments)
    except ValidationError as e:
        return f"ERROR: invalid arguments for {call.name}: {_short(e)}"
    try:
        result: Any = await tool.handler(params)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    return truncate(tool.compact(result), max_chars)


async def run_agent(
    *,
    client: ModelClient,
    role: str,
    system: str,
    task: str,
    result_model: type[T],
    tools: Sequence[Tool] = (),
    images: Sequence[ImagePart] = (),
    budget: AgentBudget | None = None,
    run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    provider: str | None = None,
    on_event: EventSink | None = None,
) -> AgentResult[T]:
    budget = budget or AgentBudget()
    by_name = {t.name: t for t in tools}
    if SUBMIT in by_name:
        raise ValueError(f"{SUBMIT!r} is a reserved tool name")
    submit_spec = ToolSpec(SUBMIT, "Submit your final result. Call it exactly once, when you are done.",
                           result_model.model_json_schema())
    ctx = CallContext(role=role, run_id=run_id, task_id=task_id)
    messages: list[Message] = [Message.user(task, list(images))]
    repairs_left = 1
    used_provider: str | None = None
    used_model: str | None = None

    async def emit(kind: Literal["step", "tool", "submitted", "failed"], step: int, detail: str) -> None:
        if on_event is not None:
            await on_event(AgentEvent(kind, step, detail))

    for step in range(1, budget.max_steps + 1):
        last = step == budget.max_steps
        if last:
            messages.append(Message.user(BUDGET_MSG))
        specs = [submit_spec] if last else [t.spec() for t in tools] + [submit_spec]
        req = CompletionRequest(model="", system=system, tools=specs, require_tool=True,
                                messages=compact_transcript(messages, budget.max_transcript_chars))
        resp = await client.complete(req, ctx, only=provider)
        used_provider, used_model = resp.provider, resp.model
        messages.append(Message.assistant(resp.text, resp.tool_calls))
        await emit("step", step, ", ".join(c.name for c in resp.tool_calls) or "no tool call")

        if not resp.tool_calls:
            messages.append(Message.user(NUDGE_MSG))
            continue

        submits = [c for c in resp.tool_calls if c.name == SUBMIT]
        others = [c for c in resp.tool_calls if c.name != SUBMIT]
        if submits:
            for c in others:
                messages.append(Message.tool_result(c, "SKIPPED: submit_result was called in the same step."))
            submit = submits[0]
            try:
                value = result_model.model_validate(submit.arguments)
            except ValidationError as e:
                if repairs_left > 0:
                    repairs_left -= 1
                    messages.append(Message.tool_result(
                        submit, f"INVALID RESULT: {_short(e)}. Fix the problems and call submit_result again."))
                    continue
                await emit("failed", step, "invalid result after one repair")
                return AgentResult("failed", None, step, f"invalid result: {_short(e)}", messages,
                                   used_provider, used_model)
            messages.append(Message.tool_result(submit, "ACCEPTED"))
            await emit("submitted", step, "result accepted")
            return AgentResult("succeeded", value, step, None, messages, used_provider, used_model)

        outputs = await asyncio.gather(*(_execute(by_name, c, budget.max_tool_output_chars) for c in others))
        for c, out in zip(others, outputs, strict=True):
            messages.append(Message.tool_result(c, out))
            await emit("tool", step, f"{c.name}: {out[:120]}")

    await emit("failed", budget.max_steps, "step budget exhausted")
    return AgentResult("failed", None, budget.max_steps, "step budget exhausted without a valid submit_result",
                       messages, used_provider, used_model)
