import json

import httpx
import pytest

from tf_agent.models.chatgpt_adapter import ChatGPTOAuthAdapter, build_payload, parse_rate_headers
from tf_agent.models.errors import AuthRequired, InvalidRequest, TransientProviderError, UsageLimited
from tf_agent.models.types import CompletionRequest, ImagePart, Message, ToolCall, ToolSpec

RATE_HEADERS = {
    "x-codex-primary-used-percent": "5",
    "x-codex-primary-window-minutes": "300",
    "x-codex-primary-reset-at": "1791247829",
}
ADD = ToolSpec("add", "Add two ints", {"type": "object", "properties": {"a": {"type": "integer"}}})


class StubAuth:
    def __init__(self):
        self.forced = 0

    async def headers(self, accept="text/event-stream"):
        return {"authorization": "Bearer t", "chatgpt-account-id": "acc_1", "accept": accept}

    async def ensure_fresh(self, force=False, stale_token=None):
        self.forced += int(force)
        return {}

    def status(self):
        return {"connected": True, "email": "nico@example.com", "plan": "plus"}


def sse(*events) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def make(handler, **kw):
    transport = httpx.MockTransport(handler)
    auth = StubAuth()
    return ChatGPTOAuthAdapter(auth, client_factory=lambda timeout: httpx.AsyncClient(transport=transport), **kw), auth


def req(**kw):
    return CompletionRequest(model="gpt-6-sol", system="sys", messages=[Message.user("hi")], **kw)


TOOL_EVENTS = (
    {"type": "response.created"},
    {"type": "response.output_item.done", "item": {"id": "fc_1", "type": "function_call", "status": "completed",
                                                    "arguments": "{\"a\":2,\"b\":3}", "call_id": "call_A",
                                                    "name": "add"}},
    {"type": "response.completed", "response": {"usage": {"input_tokens": 145, "output_tokens": 21}}},
)


def test_build_payload_maps_all_message_kinds(tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNGfake")
    call = ToolCall("call_A", "add", {"a": 2})
    r = CompletionRequest(
        model="gpt-6-sol", system="sys",
        messages=[Message.user("look", [ImagePart(str(img), "image/png")]),
                  Message.assistant("thinking", [call]),
                  Message.tool_result(call, "4")],
        tools=[ADD], require_tool=True)
    p = build_payload(r)
    assert p["instructions"] == "sys" and p["store"] is False and p["stream"] is True
    user = p["input"][0]
    assert user["content"][0] == {"type": "input_text", "text": "look"}
    assert user["content"][1]["image_url"].startswith("data:image/png;base64,")
    assert p["input"][1] == {"type": "message", "role": "assistant",
                             "content": [{"type": "output_text", "text": "thinking"}]}
    assert p["input"][2] == {"type": "function_call", "call_id": "call_A", "name": "add",
                             "arguments": json.dumps({"a": 2})}
    assert p["input"][3] == {"type": "function_call_output", "call_id": "call_A", "output": "4"}
    assert p["tools"][0]["type"] == "function" and p["tools"][0]["name"] == "add"
    assert p["tool_choice"] == "required"


def test_build_payload_structured_output_and_effort():
    p = build_payload(req(output_schema={"type": "object"}, schema_name="plan", reasoning_effort="high"))
    assert p["text"]["format"] == {"type": "json_schema", "name": "plan", "schema": {"type": "object"},
                                   "strict": False}
    assert p["tool_choice"] == "none" and p["tools"] == []
    assert p["reasoning"] == {"effort": "high", "summary": "auto"}  # summaries feed the live stream


def test_parse_rate_headers():
    rate = parse_rate_headers(httpx.Headers(RATE_HEADERS))
    assert (rate.used_percent, rate.window_minutes, rate.resets_at) == (5.0, 300, 1791247829.0)
    assert parse_rate_headers(httpx.Headers({})) is None


async def test_complete_parses_tool_calls_usage_and_rate():
    def handler(request):
        assert request.url.path == "/backend-api/codex/responses"
        assert json.loads(request.content)["model"] == "gpt-6-sol"
        return httpx.Response(200, headers=RATE_HEADERS, content=sse(*TOOL_EVENTS))

    adapter, _ = make(handler)
    resp = await adapter.complete(req(tools=[ADD]))
    assert resp.tool_calls == [ToolCall("call_A", "add", {"a": 2, "b": 3})]
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (145, 21)
    assert resp.rate.used_percent == 5.0
    assert resp.provider == "chatgpt" and resp.model == "gpt-6-sol"


async def test_complete_structured_output():
    events = ({"type": "response.output_item.done", "item": {"type": "message", "content": [
        {"type": "output_text", "text": "{\"answer\": 4}"}]}},
              {"type": "response.completed", "response": {"usage": {}}})
    adapter, _ = make(lambda r: httpx.Response(200, content=sse(*events)))
    resp = await adapter.complete(req(output_schema={"type": "object"}))
    assert resp.structured == {"answer": 4}


async def test_429_maps_to_usage_limited_with_reset():
    adapter, _ = make(lambda r: httpx.Response(429, headers=RATE_HEADERS, json={"error": "usage_limit_reached"}))
    with pytest.raises(UsageLimited) as ei:
        await adapter.complete(req())
    assert ei.value.reset_at == 1791247829.0


async def test_401_refreshes_once_then_auth_required():
    seen = 0

    def handler(request):
        nonlocal seen
        seen += 1
        return httpx.Response(401, json={"error": "unauthorized"})

    adapter, auth = make(handler)
    with pytest.raises(AuthRequired):
        await adapter.complete(req())
    assert (seen, auth.forced) == (2, 1)


async def test_401_then_success_after_refresh():
    responses = iter([httpx.Response(401), httpx.Response(200, content=sse(*TOOL_EVENTS))])
    adapter, auth = make(lambda r: next(responses))
    resp = await adapter.complete(req(tools=[ADD]))
    assert resp.tool_calls[0].name == "add" and auth.forced == 1


@pytest.mark.parametrize("status,exc", [(500, TransientProviderError), (503, TransientProviderError),
                                        (400, InvalidRequest)])
async def test_status_mapping(status, exc):
    adapter, _ = make(lambda r: httpx.Response(status, text="nope"))
    with pytest.raises(exc):
        await adapter.complete(req())


async def test_sse_error_event_usage_limit():
    ev = {"type": "error", "error": {"code": "usage_limit_reached", "message": "You've hit your limit"}}
    adapter, _ = make(lambda r: httpx.Response(200, content=sse(ev)))
    with pytest.raises(UsageLimited):
        await adapter.complete(req())


async def test_network_error_is_transient():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    adapter, _ = make(handler)
    with pytest.raises(TransientProviderError):
        await adapter.complete(req())


async def test_list_models_keeps_largest_list_and_caches(tmp_path):
    def models(*slugs, hidden=()):
        return [{"slug": s, "display_name": s.upper(), "priority": i + 1, "context_window": 272000,
                 "input_modalities": ["text", "image"], "visibility": "hide" if s in hidden else "list",
                 "supported_in_api": True, "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]}
                for i, s in enumerate(slugs)]

    def handler(request):
        v = request.url.params["client_version"]
        if v == "1.0.0":
            return httpx.Response(200, json={"models": models("a", "b")})
        if v == "2.0.0":
            return httpx.Response(200, json={"models": models("a", "b", "c", hidden=("c",))})
        return httpx.Response(500)

    cache = tmp_path / "models.json"
    adapter, _ = make(handler, models_cache=cache)
    infos = await adapter.list_models()
    assert [m.model_id for m in infos] == ["a", "b", "c"]
    assert infos[2].hidden is True and infos[0].vision is True
    assert infos[0].reasoning_levels == ("low", "high") and infos[0].context_window == 272000
    offline, _ = make(lambda r: httpx.Response(500), models_cache=cache)
    assert [m.model_id for m in await offline.list_models()] == ["a", "b", "c"]


async def test_health_reports_account():
    adapter, _ = make(lambda r: httpx.Response(500))
    h = await adapter.health()
    assert h.connected is True and h.account == "nico@example.com (plus)"


async def test_reasoning_summary_is_returned():
    """Plan 5 Task 4: the reasoning summary becomes the agent's visible thought."""
    events = (
        {"type": "response.created"},
        {"type": "response.reasoning_summary_text.delta", "delta": "Searching gym "},
        {"type": "response.reasoning_summary_text.delta", "delta": "trends first."},
        {"type": "response.output_item.done", "item": {"type": "reasoning", "summary": [
            {"type": "summary_text", "text": "Searching gym trends first."}]}},
        *TOOL_EVENTS[1:],
    )
    adapter, _ = make(lambda r: httpx.Response(200, headers=RATE_HEADERS, content=sse(*events)))
    resp = await adapter.complete(req(tools=[ADD], reasoning_effort="medium"))
    assert resp.reasoning == "Searching gym trends first." and resp.tool_calls[0].name == "add"


def test_rate_headers_keep_both_windows():
    rate = parse_rate_headers(httpx.Headers({
        "x-codex-primary-used-percent": "12", "x-codex-primary-window-minutes": "300",
        "x-codex-primary-reset-at": "1791300000", "x-codex-secondary-used-percent": "44",
        "x-codex-secondary-window-minutes": "10080", "x-codex-secondary-reset-at": "1791700000"}))
    assert rate.used_percent == 44.0
    assert [(w.name, w.used_percent, w.window_minutes) for w in rate.windows] == [
        ("five_hour", 12.0, 300), ("seven_day", 44.0, 10080)]
