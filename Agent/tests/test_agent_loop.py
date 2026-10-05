import asyncio
import time

import pytest
from pydantic import BaseModel

from tf_agent.loop.agent import BUDGET_MSG, NUDGE_MSG, AgentBudget, run_agent
from tf_agent.loop.compaction import ELIDED, compact_transcript
from tf_agent.loop.tools import Tool, truncate
from tf_agent.models.fake import FakeAdapter, text_response, tool_call_response
from tf_agent.models.types import Message, ToolCall
from tf_agent.testing import make_client


class AddParams(BaseModel):
    a: int
    b: int


class Answer(BaseModel):
    answer: int


async def _add(p: AddParams) -> int:
    return p.a + p.b


ADD = Tool("add", "Add two integers.", AddParams, _add)


def setup(*script):
    fa = FakeAdapter("a", list(script))
    client, _, _ = make_client({"a": fa})
    return client, fa


async def run(client, tools=(ADD,), **kw):
    return await run_agent(client=client, role="demo", system="sys", task="compute", result_model=Answer,
                           tools=list(tools), **kw)


def call(name, args):
    return tool_call_response("a", (name, args))


async def test_happy_path_tool_then_submit():
    client, fa = setup(call("add", {"a": 2, "b": 3}), call("submit_result", {"answer": 5}))
    res = await run(client)
    assert (res.status, res.result, res.steps, res.provider) == ("succeeded", Answer(answer=5), 2, "a")
    first = fa.requests[0]
    assert [t.name for t in first.tools] == ["add", "submit_result"] and first.require_tool is True
    last_msg = fa.requests[1].messages[-1]
    assert (last_msg.role, last_msg.text()) == ("tool", "5")


async def test_invalid_submit_gets_one_repair():
    client, fa = setup(call("submit_result", {"answer": "x"}), call("submit_result", {"answer": 7}))
    res = await run(client)
    assert res.status == "succeeded" and res.result.answer == 7
    assert fa.requests[1].messages[-1].text().startswith("INVALID RESULT")


async def test_invalid_submit_twice_fails():
    client, _ = setup(call("submit_result", {"answer": "x"}), call("submit_result", {}))
    res = await run(client)
    assert res.status == "failed" and "invalid result" in res.error


async def test_unknown_tool_bad_args_and_exceptions_are_reported_to_model():
    async def boom(_):
        raise ValueError("kaboom")

    boom_tool = Tool("boom", "Always fails.", AddParams, boom)
    client, fa = setup(call("nope", {}), call("add", {"a": "x", "b": 1}), call("boom", {"a": 1, "b": 1}),
                       call("submit_result", {"answer": 1}))
    res = await run(client, tools=(ADD, boom_tool))
    assert res.status == "succeeded"
    outputs = [m.text() for m in res.transcript if m.role == "tool"]
    assert outputs[0].startswith("ERROR: unknown tool 'nope'")
    assert outputs[1].startswith("ERROR: invalid arguments for add")
    assert outputs[2] == "ERROR: ValueError: kaboom"


async def test_last_step_only_offers_submit():
    client, fa = setup(call("add", {"a": 1, "b": 1}), call("submit_result", {"answer": 2}))
    res = await run(client, budget=AgentBudget(max_steps=2))
    assert res.status == "succeeded"
    final = fa.requests[1]
    assert [t.name for t in final.tools] == ["submit_result"]
    assert final.messages[-1].text() == BUDGET_MSG


async def test_budget_exhausted_without_submit_fails():
    client, _ = setup(call("add", {"a": 1, "b": 1}), call("add", {"a": 1, "b": 1}))
    res = await run(client, budget=AgentBudget(max_steps=2))
    assert res.status == "failed" and "budget" in res.error


async def test_missing_tool_call_gets_nudged():
    client, fa = setup(text_response("a", "hmm"), call("submit_result", {"answer": 3}))
    res = await run(client)
    assert res.status == "succeeded"
    assert fa.requests[1].messages[-1].text() == NUDGE_MSG


async def test_submit_with_other_calls_skips_the_others():
    both = tool_call_response("a", ("add", {"a": 1, "b": 1}), ("submit_result", {"answer": 9}))
    client, _ = setup(both)
    res = await run(client)
    assert res.result.answer == 9
    assert any(m.text().startswith("SKIPPED") for m in res.transcript if m.role == "tool")


async def test_events_are_emitted():
    events = []

    async def sink(ev):
        events.append(ev.kind)

    client, _ = setup(call("add", {"a": 2, "b": 3}), call("submit_result", {"answer": 5}))
    await run(client, on_event=sink)
    assert events == ["step", "tool", "step", "submitted"]


async def test_parallel_tool_calls_run_concurrently():
    async def slow(p: AddParams) -> int:
        await asyncio.sleep(0.2)
        return p.a

    slow_tool = Tool("slow", "Sleeps.", AddParams, slow)
    two = tool_call_response("a", ("slow", {"a": 1, "b": 0}), ("slow", {"a": 2, "b": 0}))
    client, _ = setup(two, call("submit_result", {"answer": 3}))
    started = time.monotonic()
    await run(client, tools=(slow_tool,))
    assert time.monotonic() - started < 0.35


def test_reserved_tool_name_rejected():
    bad = Tool("submit_result", "x", AddParams, _add)
    client, _ = setup()
    with pytest.raises(ValueError, match="reserved"):
        asyncio.run(run(client, tools=(bad,)))


def test_truncate():
    assert truncate("abc", 5) == "abc"
    assert truncate("abcdefgh", 3).startswith("abc\n…[truncated 5 chars]")


def test_compaction_elides_old_tool_outputs_only():
    c = ToolCall("c", "t", {})
    msgs = [Message.user("TASK")]
    for _ in range(10):
        msgs += [Message.assistant("", [c]), Message.tool_result(c, "x" * 1000)]
    out = compact_transcript(msgs, max_chars=4000, keep_last=6)
    assert out[0].text() == "TASK"
    assert all(m.text() != ELIDED for m in out[-6:])
    assert sum(1 for m in out if m.text() == ELIDED) >= 5
    assert compact_transcript(msgs[:3], max_chars=10_000) == msgs[:3]
