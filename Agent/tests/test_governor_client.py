import asyncio

import pytest

from tf_agent.models.client import CallContext
from tf_agent.models.errors import (
    AllProvidersUnavailable,
    AuthRequired,
    InvalidRequest,
    MalformedResponse,
    TransientProviderError,
    UsageLimited,
)
from tf_agent.models.fake import FakeAdapter, text_response
from tf_agent.models.governor import ProviderStatus, UsageGovernor
from tf_agent.models.types import CompletionRequest, Message, RateInfo
from tf_agent.testing import MemoryStateStore, make_client


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def req():
    return CompletionRequest(model="", system="s", messages=[Message.user("hi")])


CTX = CallContext(role="scout")


async def test_first_available_provider_answers_and_ledger_records():
    a, b = FakeAdapter("a", [text_response("a", "from a")]), FakeAdapter("b")
    client, _, ledger = make_client({"a": a, "b": b})
    resp = await client.complete(req(), CTX)
    assert resp.text == "from a"
    assert a.requests[0].model == "a-model"
    assert [(r.provider, r.status) for r in ledger.records] == [("a", "ok")]


async def test_usage_limit_falls_back_and_cools():
    clock = Clock()
    a = FakeAdapter("a", [UsageLimited("a", "limit", reset_at=5000.0)])
    b = FakeAdapter("b", [text_response("b", "from b")])
    client, gov, ledger = make_client({"a": a, "b": b}, clock=clock)
    assert (await client.complete(req(), CTX)).text == "from b"
    assert gov.status("a").status == "cooling" and gov.status("a").cooling_until == 5000.0
    assert gov.available("a") is False
    clock.t = 5000.0
    assert gov.available("a") is True
    assert [r.error_class for r in ledger.records] == ["UsageLimited", None]


async def test_all_cooling_reports_earliest_reset():
    clock = Clock()
    a = FakeAdapter("a", [UsageLimited("a", "limit", reset_at=7000.0)])
    b = FakeAdapter("b", [UsageLimited("b", "limit", reset_at=6000.0)])
    client, _, _ = make_client({"a": a, "b": b}, clock=clock)
    with pytest.raises(AllProvidersUnavailable) as ei:
        await client.complete(req(), CTX)
    assert ei.value.earliest_reset == 6000.0


async def test_unknown_reset_cools_for_probe_window():
    clock = Clock()
    gov = UsageGovernor(["a"], clock=clock, probe_after_s=900)
    await gov.mark_cooling("a", None, "limit")
    assert gov.status("a").cooling_until == 1900.0


async def test_transient_is_retried_then_falls_back():
    a = FakeAdapter("a", [TransientProviderError("a", "x")] * 3)
    b = FakeAdapter("b", [text_response("b", "ok")])
    client, gov, ledger = make_client({"a": a, "b": b})
    assert (await client.complete(req(), CTX)).provider == "b"
    assert len(a.requests) == 3 and gov.status("a").status == "ok"


async def test_malformed_is_retried_like_transient():
    a = FakeAdapter("a", [MalformedResponse("a", "bad"), text_response("a", "ok")])
    client, _, _ = make_client({"a": a})
    assert (await client.complete(req(), CTX)).text == "ok"


async def test_invalid_request_raises_immediately():
    a = FakeAdapter("a", [InvalidRequest("a", "bad schema")])
    b = FakeAdapter("b", [text_response("b", "never")])
    client, _, _ = make_client({"a": a, "b": b})
    with pytest.raises(InvalidRequest):
        await client.complete(req(), CTX)
    assert b.requests == []


async def test_auth_error_skips_provider_until_ok():
    a = FakeAdapter("a", [AuthRequired("a", "login")])
    b = FakeAdapter("b", [text_response("b", "ok"), text_response("b", "ok2")])
    client, gov, _ = make_client({"a": a, "b": b})
    await client.complete(req(), CTX)
    assert gov.status("a").status == "auth_error"
    await client.complete(req(), CTX)
    assert len(a.requests) == 1
    await gov.mark_ok("a")
    assert gov.available("a") is True


async def test_high_usage_cools_preemptively():
    clock = Clock()
    a = FakeAdapter("a", [text_response("a", "ok")])
    a._script[0].rate = RateInfo(used_percent=99.0, window_minutes=300, resets_at=9000.0)
    client, gov, _ = make_client({"a": a}, clock=clock)
    await client.complete(req(), CTX)
    st = gov.status("a")
    assert st.status == "cooling" and st.cooling_until == 9000.0 and st.used_percent == 99.0


async def test_only_restricts_providers():
    a = FakeAdapter("a")
    b = FakeAdapter("b", [text_response("b", "ok")])
    client, _, _ = make_client({"a": a, "b": b})
    assert (await client.complete(req(), CTX, only="b")).provider == "b"
    assert a.requests == []


async def test_concurrency_slot_limits_in_flight_calls():
    a = FakeAdapter("a", [text_response("a", "ok")] * 6, delay_s=0.05)
    client, _, _ = make_client({"a": a}, concurrency=2)
    await asyncio.gather(*(client.complete(req(), CTX) for _ in range(6)))
    assert a.max_in_flight == 2


async def test_load_ignores_stale_auth_error():
    store = MemoryStateStore({"a": ProviderStatus("auth_error", None, None, "old"),
                              "b": ProviderStatus("cooling", 99999.0, 97.0, "limit")})
    gov = UsageGovernor(["a", "b"], store=store, clock=Clock())
    await gov.load()
    assert gov.status("a").status == "ok"
    assert gov.status("b").status == "cooling"


async def test_prefer_reorders_but_still_falls_back():
    a = FakeAdapter("a", [text_response("a", "from a")])
    b = FakeAdapter("b", [text_response("b", "from b"), UsageLimited("b", "limit")])
    client, _, _ = make_client({"a": a, "b": b})
    assert (await client.complete(req(), CTX, prefer="b")).provider == "b"
    assert (await client.complete(req(), CTX, prefer="b")).provider == "a"  # b limited → falls back to a


async def test_switch_hook_reports_fallback():
    """Plan 5 Task 4: the live stream says when an agent moves to the other subscription, and why."""
    a = FakeAdapter("a", [UsageLimited("a", "5-hour limit reached", reset_at=None)])
    b = FakeAdapter("b", [text_response("b", "from b"), text_response("b", "again")])
    client, _, _ = make_client({"a": a, "b": b})
    switches = []

    async def on_switch(ctx, from_provider, to_provider, reason):
        switches.append((ctx.role, from_provider, to_provider, reason))

    client.on_switch = on_switch
    assert (await client.complete(req(), CTX)).text == "from b"
    assert switches == [("scout", "a", "b", "5-hour limit reached")]
    # a is cooling: it's skipped before being called, and that is reported too (run 1 switched silently);
    # the orchestrator shows each pair once per run
    assert (await client.complete(req(), CTX)).provider == "b"
    assert switches[1][:3] == ("scout", "a", "b") and "usage limit" in switches[1][3]


async def test_refusal_is_reported_and_logged_not_handed_to_another_provider(tmp_path):
    """Owner (2026-10-06): fix refusals rather than fall back. The call fails loudly, with everything needed to fix it."""
    import json as _json

    from tf_agent.models.errors import ContentRefused

    text = ("API Error: Opus 5.5's safeguards flagged this message (https://www.anthropic.com/legal/aup). "
            "Details: `[reasoning_extraction]` Request ID: req_011CfmMEQCVbNDgQByPKp1CA")
    a = FakeAdapter("a", [ContentRefused("a", text)])
    b = FakeAdapter("b", [text_response("b", "from b")])
    client, gov, _ = make_client({"a": a, "b": b})
    client.refusal_log = tmp_path / "refusals.jsonl"
    seen = []

    async def on_refused(ctx, err):
        seen.append((ctx.role, err.request_id, err.detail))

    client.on_refused = on_refused
    with pytest.raises(ContentRefused):
        await client.complete(req(), CTX)
    assert b.requests == [] and gov.available("a") is True
    assert seen == [("scout", "req_011CfmMEQCVbNDgQByPKp1CA", "reasoning_extraction")]
    row = _json.loads(client.refusal_log.read_text().splitlines()[0])
    assert row["request_id"] == "req_011CfmMEQCVbNDgQByPKp1CA" and row["role"] == "scout"
    assert row["system"] == "s" and "hi" in row["messages"]


async def test_provider_effort_override_wins_over_the_role_effort():
    """Plan 6: the owner's per-provider effort applies to every call on that provider."""
    roles = {"default": [{"provider": "a", "model": "a-model", "effort": "low"}]}
    a = FakeAdapter("a", [text_response("a", "x"), text_response("a", "y")])
    client, _, _ = make_client({"a": a}, roles=roles)
    await client.complete(req(), CTX)
    client.effort_override["a"] = "high"
    await client.complete(req(), CTX)
    assert [r.reasoning_effort for r in a.requests] == ["low", "high"]


async def test_governor_keeps_the_last_rate_window():
    gov = UsageGovernor(["a"], 4)
    await gov.observe_rate("a", RateInfo(used_percent=44.0, window_minutes=300, resets_at=1791300000.0))
    assert gov.last_rate("a").window_minutes == 300 and gov.last_rate("b") is None


async def test_an_effort_the_resolved_model_cannot_take_is_dropped():
    """Review #2: an effort saved for one model must not break calls once the alias resolves to another."""
    from tf_agent.models.types import ModelInfo

    a = FakeAdapter("a", [text_response("a", "x"), text_response("a", "y")],
                    models=[ModelInfo("a", "a-model", reasoning_levels=("low", "medium"))])
    client, _, _ = make_client({"a": a})
    await client.router.registry.refresh()
    client.effort_override["a"] = "ultra"
    await client.complete(req(), CTX)
    client.effort_override["a"] = "medium"
    await client.complete(req(), CTX)
    assert [r.reasoning_effort for r in a.requests] == [None, "medium"]
