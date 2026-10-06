import json
import time

import pytest

from tf_agent.models.claude_cli import ClaudeCLIAdapter, ClaudeCliAuth, render_prompt
from tf_agent.models.errors import AuthRequired, MalformedResponse, TransientProviderError, UsageLimited
from tf_agent.models.types import CompletionRequest, ImagePart, Message, ToolCall, ToolSpec

ADD = ToolSpec("add", "Add two ints", {"type": "object", "properties": {"a": {"type": "integer"}}})


def adapter(fake):
    return ClaudeCLIAdapter(ClaudeCliAuth(fake.bin, stats_path=fake.tmp / "stats.json"), runtime_dir=fake.tmp / "rt")


def req(text="hi", **kw):
    kw.setdefault("system", "You are a scout.")
    return CompletionRequest(model="opus", messages=[Message.user(text)], **kw)


def flag_value(argv, flag):
    return argv[argv.index(flag) + 1]


def test_render_prompt_includes_conversation_and_tools():
    call = ToolCall("c1", "add", {"a": 1})
    r = CompletionRequest(model="opus", system="s", tools=[ADD], messages=[
        Message.user("go"), Message.assistant("plan", [call]), Message.tool_result(call, "2")])
    text = render_prompt(r, lambda img: "@" + img.path)
    assert "[user]\ngo" in text
    assert '"name": "add"' in text and '[tool result id=c1 name=add]\n2' in text
    assert "<tools>" in text and '"calls"' in text


async def test_tool_step_uses_isolation_flags_and_stdin(fake_claude):
    fake_claude.respond(fake_claude.envelope(structured={"thought": "t", "calls": [
        {"name": "add", "arguments": {"a": 1}}]}))
    resp = await adapter(fake_claude).complete(req(tools=[ADD], require_tool=True))
    assert [(c.name, c.arguments) for c in resp.tool_calls] == [("add", {"a": 1})]
    assert resp.text == "t" and resp.provider == "claude" and resp.model == "claude-opus-5-5"
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (15, 7)
    call = fake_claude.calls()[0]
    argv = call["argv"]
    for flag in ("-p", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert flag_value(argv, "--tools") == "" and flag_value(argv, "--setting-sources") == ""
    assert flag_value(argv, "--output-format") == "stream-json" and flag_value(argv, "--model") == "opus"
    assert flag_value(argv, "--system-prompt") == "You are a scout."
    schema = json.loads(flag_value(argv, "--json-schema"))
    assert schema["properties"]["calls"]["items"]["properties"]["name"]["enum"] == ["add"]
    assert "[user]\nhi" in call["stdin"] and "hi" not in argv


async def test_structured_output_mode(fake_claude):
    schema = {"type": "object", "properties": {"answer": {"type": "integer"}}}
    fake_claude.respond(fake_claude.envelope(structured={"answer": 4}))
    resp = await adapter(fake_claude).complete(req(output_schema=schema))
    assert resp.structured == {"answer": 4}
    assert json.loads(flag_value(fake_claude.calls()[0]["argv"], "--json-schema")) == schema


async def test_plain_text_mode(fake_claude):
    fake_claude.respond(fake_claude.envelope(result="hello there"))
    resp = await adapter(fake_claude).complete(req())
    assert resp.text == "hello there"
    assert "--json-schema" not in fake_claude.calls()[0]["argv"]


async def test_large_prompt_goes_through_stdin(fake_claude):
    fake_claude.respond(fake_claude.envelope(result="ok"))
    await adapter(fake_claude).complete(req("x" * 300_000))
    call = fake_claude.calls()[0]
    assert sum(len(a) for a in call["argv"]) < 20_000
    assert len(call["stdin"]) > 300_000


async def test_oversized_system_prompt_moves_to_stdin(fake_claude):
    fake_claude.respond(fake_claude.envelope(result="ok"))
    await adapter(fake_claude).complete(req(system="S" * 100_000))
    call = fake_claude.calls()[0]
    assert len(flag_value(call["argv"], "--system-prompt")) < 200
    assert call["stdin"].startswith("<system>\n" + "S" * 10)


async def test_image_with_spaces_is_staged(fake_claude):
    folder = fake_claude.tmp / "Trend Finder App"
    folder.mkdir()
    img = folder / "sheet one.jpg"
    img.write_bytes(b"\xff\xd8fakejpeg")
    fake_claude.respond(fake_claude.envelope(structured={"ok": True}))
    r = CompletionRequest(model="opus", system="s", output_schema={"type": "object"},
                          messages=[Message.user("look", [ImagePart(str(img))])])
    await adapter(fake_claude).complete(r)
    stdin = fake_claude.calls()[0]["stdin"]
    ref = next(tok for tok in stdin.split() if tok.startswith("@"))
    assert " " not in ref and "Trend Finder App" not in stdin
    from pathlib import Path
    assert Path(ref[1:]).read_bytes() == b"\xff\xd8fakejpeg"


async def test_api_key_env_is_stripped(fake_claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    fake_claude.respond(fake_claude.envelope(result="ok"))
    await adapter(fake_claude).complete(req())
    keys = fake_claude.calls()[0]["env_keys"]
    assert "ANTHROPIC_API_KEY" not in keys and "ANTHROPIC_AUTH_TOKEN" not in keys


async def test_usage_limit_error(fake_claude):
    fake_claude.respond(fake_claude.envelope(is_error=True, subtype="error_during_execution",
                                             result="Claude AI usage limit reached|1791247829"))
    with pytest.raises(UsageLimited) as ei:
        await adapter(fake_claude).complete(req())
    assert ei.value.reset_at == 1791247829.0


async def test_auth_error(fake_claude):
    fake_claude.respond(fake_claude.envelope(is_error=True, result="Invalid API key · Please run /login"))
    with pytest.raises(AuthRequired):
        await adapter(fake_claude).complete(req())


async def test_garbage_output(fake_claude):
    fake_claude.respond("garbage", exit_code=1)
    with pytest.raises(TransientProviderError):
        await adapter(fake_claude).complete(req())
    fake_claude.respond("garbage", exit_code=0)
    with pytest.raises(MalformedResponse):
        await adapter(fake_claude).complete(req())


async def test_timeout_kills_process(fake_claude):
    fake_claude.respond(fake_claude.envelope(result="late"), sleep=5)
    started = time.monotonic()
    with pytest.raises(TransientProviderError, match="timed out"):
        await adapter(fake_claude).complete(req(timeout_s=0.5))
    assert time.monotonic() - started < 3


async def test_empty_calls_is_malformed(fake_claude):
    fake_claude.respond(fake_claude.envelope(structured={"calls": []}))
    with pytest.raises(MalformedResponse):
        await adapter(fake_claude).complete(req(tools=[ADD]))




async def test_status_and_health(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_STATUS", json.dumps(
        {"loggedIn": True, "email": "nico@example.com", "subscriptionType": "max"}))
    a = adapter(fake_claude)
    assert a.auth.status()["connected"] is True
    h = await a.health()
    assert h.connected is True and h.account == "nico@example.com (max)"


async def test_missing_binary_is_auth_required(tmp_path):
    a = ClaudeCLIAdapter(ClaudeCliAuth(str(tmp_path / "nope" / "claude")), runtime_dir=tmp_path / "rt")
    with pytest.raises(AuthRequired, match="not found"):
        await a.complete(req())


async def test_large_image_is_shrunk_under_the_cli_attach_limit(fake_claude):
    """claude -p silently drops @-images over ~256 KB (found live: the analyst was blind), so big ones are re-encoded."""
    from pathlib import Path

    import numpy as np
    from PIL import Image

    from tf_agent.models.claude_cli import CLAUDE_IMAGE_MAX_BYTES

    big = fake_claude.tmp / "canonical.png"
    Image.fromarray(np.random.default_rng(1).integers(0, 255, (1520, 2688, 3), dtype=np.uint8)).save(big)
    assert big.stat().st_size > CLAUDE_IMAGE_MAX_BYTES
    fake_claude.respond(fake_claude.envelope(structured={"ok": True}))
    r = CompletionRequest(model="opus", system="s", output_schema={"type": "object"},
                          messages=[Message.user("look", [ImagePart(str(big))])])
    await adapter(fake_claude).complete(r)
    ref = Path(next(t for t in fake_claude.calls()[0]["stdin"].split() if t.startswith("@"))[1:])
    assert ref != big and ref.stat().st_size <= CLAUDE_IMAGE_MAX_BYTES
    with Image.open(ref) as im:
        assert im.format == "JPEG" and max(im.size) <= 1568
        assert abs(im.size[0] / im.size[1] - 2688 / 1520) < 0.02


def test_safeguard_refusal_is_classified_as_refused():
    """Found live (Plan 5): a refusal must let the client try the other provider, not fail the task."""
    from tf_agent.models.claude_cli import classify_failure
    from tf_agent.models.errors import ContentRefused

    err = classify_failure("API Error: Opus 5.5's safeguards flagged this message (https://www.anthropic.com/legal/aup)."
                           " Details: `[reasoning_extraction]`", None)
    assert isinstance(err, ContentRefused)


async def test_tool_steps_carry_a_progress_note(fake_claude):
    """Plan 5: the step field is a one-line `note` (a 'thought' field + 'think out loud' tripped Claude's safeguards)."""
    fake_claude.respond(fake_claude.envelope(structured={"note": "Checking #deskdance", "calls": [
        {"name": "add", "arguments": {"a": 1}}]}))
    r = await adapter(fake_claude).complete(req(tools=[ADD]))
    schema = json.loads(flag_value(fake_claude.calls()[0]["argv"], "--json-schema"))
    assert "note" in schema["properties"] and "thought" not in schema["properties"]
    assert r.text == "Checking #deskdance"


async def test_effort_is_passed_to_the_cli(fake_claude):
    fake_claude.respond(fake_claude.envelope(structured={"ok": True}))
    r = CompletionRequest(model="opus", system="s", output_schema={"type": "object"}, reasoning_effort="high",
                          messages=[Message.user("hi")])
    await adapter(fake_claude).complete(r)
    assert flag_value(fake_claude.calls()[0]["argv"], "--effort") == "high"


def stream_lines(env, model="claude-opus-5-5", five=0.43, week=0.44):
    events = [
        {"type": "system", "subtype": "init", "model": model},
        {"type": "assistant", "message": {"model": model}},
        {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "resetsAt": 1791337200, "rateLimitType": "five_hour",
            "unifiedWindows": {"five_hour": {"utilization": five, "resetsAt": 1791337200},
                               "seven_day": {"utilization": week, "resetsAt": 1791792000}}}},
        env,
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


async def test_stream_output_gives_the_real_model_and_both_usage_windows(fake_claude):
    """Plan 6 fix: Claude reports its usage windows in stream-json mode; plain json mode dropped them."""
    fake_claude.respond(stream_lines(fake_claude.envelope(structured={"ok": True}, model="claude-opus-5-5")))
    r = await adapter(fake_claude).complete(req(output_schema={"type": "object"}))
    argv = fake_claude.calls()[0]["argv"]
    assert flag_value(argv, "--output-format") == "stream-json" and "--verbose" in argv
    assert r.model == "claude-opus-5-5" and r.structured == {"ok": True}
    assert r.rate.used_percent == 44.0
    assert [(w.name, w.used_percent, w.window_minutes, w.resets_at) for w in r.rate.windows] == [
        ("five_hour", 43.0, 300, 1791337200.0), ("seven_day", 44.0, 10080, 1791792000.0)]


async def test_out_of_credits_answer_is_an_error_not_a_reply(fake_claude):
    """Found live: on a model the plan doesn't include, the CLI 'succeeds' with an out-of-credits message."""
    from tf_agent.models.errors import ModelUnavailable

    fake_claude.respond(stream_lines(fake_claude.envelope(
        result="You're out of usage credits. Switch to another model, or manage usage credits at https://claude.ai"),
        model="claude-fable-5-1"))
    with pytest.raises(ModelUnavailable):
        await adapter(fake_claude).complete(req())


async def test_models_are_the_cli_aliases_with_their_real_names(fake_claude):
    """Plan 6 fix: not a stale local stats cache; each alias is resolved by the CLI itself (cached for a day)."""
    a = adapter(fake_claude)
    fake_claude.respond(stream_lines(fake_claude.envelope(result="ok"), model="claude-sonnet-5-5"))
    models = {m.model_id: m for m in await a.list_models()}
    assert list(models) == ["fable", "opus", "sonnet", "haiku"]
    assert models["sonnet"].display_name == "Sonnet 5.5" and models["sonnet"].unavailable is None
    calls = len(fake_claude.calls())
    assert calls == 4 and all(flag_value(c["argv"], "--model") in models for c in fake_claude.calls())
    await a.list_models()
    assert len(fake_claude.calls()) == calls  # cached: no new probes
    fake_claude.respond(stream_lines(fake_claude.envelope(result="You're out of usage credits."), "claude-fable-5-1"))
    a.resolved_path.unlink()
    models = {m.model_id: m for m in await a.list_models()}
    assert models["fable"].unavailable == "not included in your plan"


def test_display_names():
    from tf_agent.models.claude_cli import display_name

    assert display_name("claude-opus-5-5") == "Opus 5.5"
    assert display_name("claude-haiku-4-5-20251001") == "Haiku 4.5"
    assert display_name("claude-fable-5-1") == "Fable 5.1"


async def test_out_of_credits_with_429_is_unavailable_not_a_usage_limit(fake_claude):
    """Found live: the CLI also sends this as is_error + 429; that must not cool the whole Claude provider."""
    from tf_agent.models.errors import ModelUnavailable

    fake_claude.respond(stream_lines(fake_claude.envelope(
        result="You're out of usage credits. Switch to another model.", is_error=True, api_error_status=429),
        model="claude-fable-5-1"))
    with pytest.raises(ModelUnavailable):
        await adapter(fake_claude).complete(req())


async def test_last_usage_survives_a_restart(fake_claude):
    fake_claude.respond(stream_lines(fake_claude.envelope(structured={"ok": True})))
    await adapter(fake_claude).complete(req(output_schema={"type": "object"}))
    again = adapter(fake_claude)  # a new process
    assert again.last_rate is not None and [w.name for w in again.last_rate.windows] == ["five_hour", "seven_day"]


async def test_usage_refreshes_with_one_tiny_call_only_when_stale(fake_claude):
    """Settings: Claude has no usage endpoint; a minimal haiku call refreshes it at most every 10 minutes."""
    a = adapter(fake_claude)
    fake_claude.respond(stream_lines(fake_claude.envelope(result="ok"), model="claude-haiku-4-5-20251001"))
    first = await a.usage()
    assert first.used_percent == 44.0 and len(fake_claude.calls()) == 1
    assert flag_value(fake_claude.calls()[0]["argv"], "--model") == "haiku"
    await a.usage()
    assert len(fake_claude.calls()) == 1  # fresh: reused
    a._rate_at -= 601
    await a.usage()
    assert len(fake_claude.calls()) == 2
