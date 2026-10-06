"""Plan 5 Task 4: the live stream catalogue (Docs/Specs/2026-10-06-single-page-redesign.md §3)."""
from tf_agent.models.client import CallContext
from tf_agent.orchestrator.blackboard import Blackboard

from .test_run import SETTINGS, World, build

AGENT_EVENTS = {"agent.started", "agent.thought", "agent.tool_call", "agent.tool_result", "agent.finished",
                "analysis.started", "analysis.finished", "video.saved", "candidate.rejected", "plan.created"}
FOUND_KEYS = {"id", "cluster_id", "run_id", "canonical_id", "platform", "platform_id", "url", "thumbnail_url",
              "creator", "caption", "views", "posted_at", "duration_s", "score", "scores", "why", "adaptation",
              "best_segment", "watch_out", "niche_guess", "feedback"}


async def events_of(sm, run_id):
    return await Blackboard(sm).events_after(run_id, 0, limit=5000)


async def test_a_run_narrates_every_agent_step(db_sessionmaker, tmp_path):
    orch = await build(db_sessionmaker, tmp_path, World(ghost=True))
    run_id = await orch.create_run("testy", SETTINGS)
    assert (await orch.execute(run_id)).state == "review_ready"
    events = await events_of(db_sessionmaker, run_id)
    types = {e["type"] for e in events}
    assert AGENT_EVENTS <= types, AGENT_EVENTS - types
    for e in events:
        if e["type"] in AGENT_EVENTS:
            agent = e["payload"]["agent"]
            assert agent["id"] and agent["role"] in ("lead", "scout", "radar", "deep_dive", "analyst"), e
    plan = next(e["payload"] for e in events if e["type"] == "plan.created")
    assert plan["round"] == 1 and plan["reasoning"] == "go" and plan["refused"] == []
    assert {"task_id", "role", "platform", "goal"} <= set(plan["tasks"][0])
    thought = next(e["payload"] for e in events if e["type"] == "agent.thought")
    assert thought["text"].startswith("Searching TikTok for")
    call = next(e["payload"] for e in events if e["type"] == "agent.tool_call")
    assert call["tool"] == "tiktok_search" and "query" in call["args"]
    result = next(e["payload"] for e in events if e["type"] == "agent.tool_result")
    assert result["ok"] is True and len(result["summary"]) <= 300
    ghost = next(e["payload"] for e in events if e["type"] == "candidate.rejected")
    assert ghost["canonical_id"] == "tiktok:999999999999" and "unknown video" in ghost["reason"]
    saved = next(e["payload"] for e in events if e["type"] == "video.saved")
    assert FOUND_KEYS <= set(saved["video"]) and saved["video"]["score"] is not None
    finished = [e["payload"] for e in events if e["type"] == "analysis.finished"]
    assert {f["verdict"] for f in finished} <= {"saved", "filtered", "failed"}


async def test_provider_switch_is_streamed(db_sessionmaker, tmp_path):
    orch = await build(db_sessionmaker, tmp_path, World())
    run_id = await orch.create_run("testy", SETTINGS)
    await orch.roles.client.on_switch(CallContext(role="scout", run_id=run_id), "claude", "chatgpt", "usage limit")
    ev = [e for e in await events_of(db_sessionmaker, run_id) if e["type"] == "provider.switched"]
    assert ev[0]["payload"]["from"] == "claude" and ev[0]["payload"]["to"] == "chatgpt"
    assert ev[0]["payload"]["agent"]["role"] == "scout"


async def test_workers_with_leads_finish_cleanly(db_sessionmaker, tmp_path):
    """Found live (Plan 5): add_leads returns a count; agent.finished must not crash on it."""
    world = World()
    orig = world.complete

    async def with_leads(req):
        resp = await orig(req)
        for c in resp.tool_calls:
            if c.name == "submit_result":
                c.arguments["leads"] = [{"type": "hashtag", "value": "deskdance", "platform": "tiktok",
                                         "why": "office dances are trending"}]
        return resp

    world.complete = with_leads
    orch = await build(db_sessionmaker, tmp_path, world)
    run_id = await orch.create_run("testy", SETTINGS)
    await orch.execute(run_id)
    finished = [e["payload"] for e in await events_of(db_sessionmaker, run_id) if e["type"] == "agent.finished"]
    assert finished and all(f["failed"] is None for f in finished) and any(f["leads"] >= 1 for f in finished)


async def test_a_crashing_worker_still_reports_finished(db_sessionmaker, tmp_path):
    from tf_agent.models.errors import ProviderError

    world = World()
    orig = world.complete

    async def boom(req):
        if req.tools:  # every worker step blows up
            raise ProviderError("a", "something unexpected")
        return await orig(req)

    world.complete = boom
    orch = await build(db_sessionmaker, tmp_path, world)
    run_id = await orch.create_run("testy", SETTINGS)
    await orch.execute(run_id)
    events = await events_of(db_sessionmaker, run_id)
    started = {e["payload"]["agent"]["id"] for e in events if e["type"] == "agent.started"}
    finished = {e["payload"]["agent"]["id"] for e in events if e["type"] == "agent.finished"}
    assert started and started == finished


async def test_a_refused_worker_is_reported_and_not_retried(db_sessionmaker, tmp_path):
    from sqlalchemy import select

    from tf_agent.models.errors import ContentRefused
    from tf_db.models import Task

    world = World()
    orig = world.complete

    async def refuse(req):
        if req.tools:
            raise ContentRefused("a", "safeguards flagged this message. Details: `[reasoning_extraction]` "
                                      "Request ID: req_abc123")
        return await orig(req)

    world.complete = refuse
    orch = await build(db_sessionmaker, tmp_path, world)
    run_id = await orch.create_run("testy", SETTINGS)
    assert (await orch.execute(run_id)).state == "review_ready"
    events = await events_of(db_sessionmaker, run_id)
    refused = [e["payload"] for e in events if e["type"] == "agent.refused"]
    assert refused and refused[0]["request_id"] == "req_abc123" and refused[0]["detail"] == "reasoning_extraction"
    assert refused[0]["agent"]["role"] == "scout"
    async with db_sessionmaker() as s:
        attempts = (await s.execute(select(Task.attempts).where(Task.task_type == "scout"))).scalars().all()
    assert attempts and max(attempts) == 1  # a refusal isn't retried: the prompt needs fixing
