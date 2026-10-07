"""run_agent(): provider-agnostic tool-calling loop ending in a validated submit_result (Code docs/01-agents.md)."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from tf_agent.loop.compaction import compact_transcript
from tf_agent.loop.tools import Tool, truncate
from tf_agent.models.client import CallContext, ModelClient
from tf_agent.models.errors import AllProvidersUnavailable
from tf_agent.models.types import (
    CompletionRequest,
    CompletionResponse,
    ImagePart,
    Message,
    ToolCall,
    ToolSpec,
)

log = logging.getLogger(__name__)
SUBMIT = "submit_result"
BUDGET_MSG = "Step budget exhausted. Call submit_result now with your best result."
NUDGE_MSG = "You must respond with a tool call. Call submit_result when you are done."

T = TypeVar("T", bound=BaseModel)


@dataclass
class AgentBudget:
    max_steps: int = 30
    max_tool_output_chars: int = 4000
    max_transcript_chars: int = 150_000


EventKind = Literal["thought", "tool_call", "tool_result", "submitted", "failed"]
THOUGHT_CHARS = 1200
RESULT_CHARS = 300
DIGEST_CHARS = 6000
RESTART_REASON = "usage limit mid-task: restarted the task fresh, with a summary of its progress"


@dataclass(frozen=True)
class AgentEvent:
    """One narrated moment of an agent's work, streamed live to the owner."""
    kind: EventKind
    step: int
    text: str = ""
    tool: str | None = None
    args: dict[str, Any] | None = None
    ok: bool | None = None


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


def _paired(messages: list[Message]) -> list[Message]:
    """Guarantee every assistant tool call has a tool result (ChatGPT rejects orphaned function calls)."""
    answered = {m.tool_call_id for m in messages if m.role == "tool"}
    out: list[Message] = []
    for m in messages:
        out.append(m)
        for c in m.tool_calls:
            if c.id not in answered:
                out.append(Message.tool_result(c, "SKIPPED: no result was recorded for this call."))
                answered.add(c.id)
    return out


def progress_digest(messages: list[Message], max_chars: int = DIGEST_CHARS) -> str:
    """What an interrupted agent did, as plain facts, so another model can restart fresh instead of continuing a
    conversation it didn't write (model handoffs mid-conversation cost accuracy)."""
    results = {m.tool_call_id: m.text() for m in messages if m.role == "tool"}
    lines: list[str] = []
    for m in messages:
        if m.role != "assistant":
            continue
        if m.text().strip():
            lines.append(f"- note: {m.text().strip()[:200]}")
        for c in m.tool_calls:
            if c.name == SUBMIT:
                continue
            out = " ".join((results.get(c.id) or "no result").split())[:400]
            lines.append(f"- {c.name}({json.dumps(c.arguments, ensure_ascii=False)[:200]}) -> {out}")
    kept: list[str] = []
    total = 0
    for line in reversed(lines):  # the most recent steps matter most
        if total + len(line) > max_chars:
            kept.append("- (earlier steps omitted)")
            break
        kept.append(line)
        total += len(line)
    return "\n".join(reversed(kept))


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
    prefer_provider: str | None = None,
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
    # one conversation = one model: the provider that answers step 1 keeps the task (no mid-conversation handoff)
    conversation = uuid.uuid4().hex
    pinned: str | None = None
    restarted = False

    async def emit(kind: EventKind, step: int, text: str = "", **extra: Any) -> None:
        if on_event is not None:
            await on_event(AgentEvent(kind, step, text, **extra))

    async def ask(last: bool) -> CompletionResponse:
        nonlocal messages, conversation, pinned, restarted, prefer_provider
        while True:
            if last and messages[-1].text() != BUDGET_MSG:
                messages.append(Message.user(BUDGET_MSG))
            specs = [submit_spec] if last else [t.spec() for t in tools] + [submit_spec]
            req = CompletionRequest(model="", system=system, tools=specs, require_tool=True, conversation=conversation,
                                    messages=_paired(compact_transcript(messages, budget.max_transcript_chars)))
            try:
                resp = await client.complete(req, ctx, only=provider or pinned, prefer=prefer_provider)
            except AllProvidersUnavailable:
                others = [p for p in client.adapters if p != pinned and client.governor.available(p)]
                if pinned is None or provider is not None or restarted or not others:
                    raise
                # the model on this task hit its limit: start over on another model from a clean brief
                restarted, old = True, pinned
                digest = progress_digest(messages)
                client.end_conversation(conversation)
                conversation, pinned, prefer_provider = uuid.uuid4().hex, None, others[0]
                messages = [Message.user(f"{task}\n\nProgress so far (an earlier attempt was interrupted; continue "
                                         f"from here and don't repeat what's done):\n{digest or '- nothing yet'}",
                                         list(images))]
                if client.on_switch is not None:
                    try:
                        await client.on_switch(ctx, old, others[0], RESTART_REASON)
                    except Exception as e:  # narration must never break the agent
                        log.warning("switch hook failed: %s", e)
                continue
            pinned = pinned or resp.provider
            return resp

    try:
        for step in range(1, budget.max_steps + 1):
            resp = await ask(step == budget.max_steps)
            used_provider, used_model = resp.provider, resp.model
            messages.append(Message.assistant(resp.text, resp.tool_calls, resp.provider_state))
            thought = (resp.reasoning or resp.text or "").strip()
            if thought:
                await emit("thought", step, thought[:THOUGHT_CHARS])

            if not resp.tool_calls:
                messages.append(Message.user(NUDGE_MSG))
                continue

            submits = [c for c in resp.tool_calls if c.name == SUBMIT]
            others = [c for c in resp.tool_calls if c.name != SUBMIT]
            if submits:
                for c in others:
                    messages.append(Message.tool_result(c, "SKIPPED: submit_result was called in the same step."))
                submit = submits[0]
                for dup in submits[1:]:
                    messages.append(Message.tool_result(dup, "SKIPPED: duplicate submit_result; only the first counts."))
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

            for c in others:
                await emit("tool_call", step, tool=c.name, args=c.arguments)
            outputs = await asyncio.gather(*(_execute(by_name, c, budget.max_tool_output_chars) for c in others))
            for c, out in zip(others, outputs, strict=True):
                messages.append(Message.tool_result(c, out))
                await emit("tool_result", step, out[:RESULT_CHARS], tool=c.name, ok=not out.startswith("ERROR"))

        await emit("failed", budget.max_steps, "step budget exhausted")
        return AgentResult("failed", None, budget.max_steps, "step budget exhausted without a valid submit_result",
                           messages, used_provider, used_model)
    finally:
        client.end_conversation(conversation)
