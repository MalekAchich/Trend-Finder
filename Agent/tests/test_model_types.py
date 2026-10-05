import pytest

from tf_agent.models.base import ProviderAdapter
from tf_agent.models.errors import AllProvidersUnavailable, ProviderError, UsageLimited
from tf_agent.models.fake import FakeAdapter, text_response, tool_call_response
from tf_agent.models.types import CompletionRequest, ImagePart, Message, ToolCall, ToolSpec


def _req(**kw):
    return CompletionRequest(model="m", system="s", messages=[Message.user("hi")], **kw)


def test_message_helpers():
    m = Message.user("hello", [ImagePart("/tmp/a.jpg")])
    assert m.text() == "hello"
    assert [i.path for i in m.images()] == ["/tmp/a.jpg"]
    call = ToolCall("c1", "add", {"a": 1})
    assert Message.assistant("", [call]).tool_calls == [call]
    tr = Message.tool_result(call, "2")
    assert (tr.role, tr.tool_call_id, tr.tool_name, tr.text()) == ("tool", "c1", "add", "2")


def test_request_rejects_tools_with_schema():
    with pytest.raises(ValueError, match="mutually exclusive"):
        _req(tools=[ToolSpec("t", "d", {})], output_schema={"type": "object"})


def test_request_require_tool_needs_tools():
    with pytest.raises(ValueError, match="require_tool"):
        _req(require_tool=True)


def test_request_needs_messages():
    with pytest.raises(ValueError, match="message"):
        CompletionRequest(model="m", system="s", messages=[])


def test_errors_carry_provider_and_reset():
    e = UsageLimited("chatgpt", "limit", reset_at=123.0)
    assert isinstance(e, ProviderError) and e.provider == "chatgpt" and e.reset_at == 123.0
    a = AllProvidersUnavailable("scout", 99.0)
    assert a.role == "scout" and a.earliest_reset == 99.0


async def test_fake_adapter_scripted_and_records():
    fa = FakeAdapter("claude", [tool_call_response("claude", ("add", {"a": 1, "b": 2}))])
    req = _req()
    resp = await fa.complete(req)
    assert resp.tool_calls[0].name == "add" and resp.tool_calls[0].arguments == {"a": 1, "b": 2}
    assert fa.requests == [req]


async def test_fake_adapter_raises_scripted_exception():
    fa = FakeAdapter("claude", [UsageLimited("claude", "x")])
    with pytest.raises(UsageLimited):
        await fa.complete(_req())


async def test_fake_adapter_callable_script_and_empty_script():
    fa = FakeAdapter("x", [lambda r: text_response("x", text=r.system)])
    assert (await fa.complete(_req())).text == "s"
    with pytest.raises(AssertionError, match="no scripted response"):
        await fa.complete(_req())


def test_fake_adapter_satisfies_protocol():
    assert isinstance(FakeAdapter(), ProviderAdapter)
