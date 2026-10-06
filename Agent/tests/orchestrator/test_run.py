"""End-to-end orchestration with a role-aware fake provider, fake tools and a fake analyzer."""
import asyncio
import re
import time
import uuid

import pytest
from sqlalchemy import select

from tf_agent.characters.folders import sync_characters
from tf_agent.loop.tools import Tool
from tf_agent.models.errors import UsageLimited
from tf_agent.models.types import CompletionResponse, ToolCall
from tf_agent.orchestrator.queue import TaskQueue
from tf_agent.orchestrator.run import Orchestrator, RunSettings
from tf_agent.pipeline.result import VideoAnalysisResult
from tf_agent.roles.runners import Roles
from tf_agent.testing import make_client
from tf_agent.tools.agent_tools import SearchParams
from tf_agent.tools.store import VideoStore
from tf_agent.tools.types import Metrics, VideoItem
from tf_db.models import Finding, Round, Run

ANALYSIS = {"fit_breakdown": {"look": 8, "vibe": 8, "energy": 8, "niche": 8, "adaptability": 8},
            "justification": "fits well", "adaptation_idea": "bank lobby", "feasibility_notes": "ok",
            "niche_guess": "deadpan professional"}


class World:
    """Fake provider that plays every role; behavior switches let each test shape the run."""

    def __init__(self, duplicate_scopes=False, empty=False, limit_first_analysis=False):
        self.name = "a"
        self.duplicate_scopes, self.empty = duplicate_scopes, empty
        self.limit_first_analysis = limit_first_analysis
        self.analysis_calls = 0
        self.rounds_planned = 0

    async def list_models(self):
        from tf_agent.models.types import ModelInfo
        return [ModelInfo("a", "a-model")]

    async def health(self):
        from tf_agent.models.types import ProviderHealth
        return ProviderHealth("a", True)

    async def complete(self, req):
        if req.schema_name == "work_plan":
            self.rounds_planned += 1
            n = int(re.search(r"Create exactly (\d+) tasks", req.messages[0].text()).group(1))
            r = self.rounds_planned
            tasks = [{"task_type": "scout", "direction_key": f"dir-{r}-{i}", "platform": "tiktok",
                      "scope": {"queries": ["same query" if self.duplicate_scopes else f"query {r}-{i}"]},
                      "goal": "find deadpan dances", "max_candidates": 2} for i in range(n)]
            dirs = [{"key": f"dir-{r}-{i}", "label": f"Direction {r}-{i}", "hypothesis": "contrast",
                     "niche": f"niche {i}", "mode": "explore"} for i in range(n)]
            return CompletionResponse("a", "a-model", structured={"reasoning_summary": "go", "directions": dirs,
                                                                  "tasks": tasks})
        if req.schema_name == "analysis":
            self.analysis_calls += 1
            if self.limit_first_analysis and self.analysis_calls == 1:
                raise UsageLimited("a", "limit", reset_at=time.time() + 1.0)
            return CompletionResponse("a", "a-model", structured=ANALYSIS)
        # worker step: search once, then submit what the tool returned
        tool_msgs = [m for m in req.messages if m.role == "tool"]
        if not tool_msgs:
            query = re.search(r"queries: (.+)", req.messages[0].text()).group(1)
            return CompletionResponse("a", "a-model", tool_calls=[
                ToolCall(f"c{uuid.uuid4().hex[:6]}", "tiktok_search", {"query": query})])
        ids = re.findall(r"tiktok:\d+", tool_msgs[-1].text())[:2]
        cands = [{"canonical_id": i, "why": "deadpan fit", "preliminary_fit": 7} for i in ids]
        return CompletionResponse("a", "a-model", tool_calls=[
            ToolCall(f"c{uuid.uuid4().hex[:6]}", "submit_result", {"candidates": cands, "leads": [], "notes": ""})])


class FakeStack:
    def __init__(self, store, empty=False):
        self.store, self.empty = store, empty

    def tools_for(self, platform, seen_filter=None):
        async def search(p: SearchParams):
            if self.empty:
                return {"items": [], "hidden_already_seen": 0, "platform_health": "ok", "notes": []}
            base = abs(hash(p.query)) % 10**9
            items = [VideoItem(canonical_id=f"tiktok:{base + i}", platform="tiktok",
                               url=f"https://www.tiktok.com/@u/video/{base + i}", metrics=Metrics(views=1000))
                     for i in range(3)]
            await self.store.upsert_videos(items)
            if seen_filter:
                items, _ = await seen_filter(items)
            return {"items": [i.model_dump(mode="json") for i in items], "hidden_already_seen": 0,
                    "platform_health": "ok", "notes": []}

        return [Tool("tiktok_search", "search", SearchParams, search)]


class FakeAnalyzer:
    def __init__(self, sheet):
        self.sheet = sheet

    async def analyze(self, item):
        return VideoAnalysisResult(canonical_id=item.canonical_id, feasibility=80.0, contact_sheet_path=str(self.sheet))


async def build(sm, tmp_path, world, empty=False):
    folder = tmp_path / "chars" / "Testy"
    folder.mkdir(parents=True)
    (folder / "testy.png").write_bytes(b"png")
    await sync_characters(tmp_path / "chars", sm)
    sheet = tmp_path / "sheet.jpg"
    sheet.write_bytes(b"jpg")
    client, _, _ = make_client({"a": world})
    store = VideoStore(sm)
    return Orchestrator(sm, Roles(client), FakeStack(store, empty), FakeAnalyzer(sheet), store, idle_poll=0.02)


SETTINGS = RunSettings(platforms=["tiktok"], rounds=3, tasks_per_round=2, target_findings=3, good_score=0,
                       workers=2, analysis_workers=2, wall_clock_s=60)


async def findings(sm, run_id):
    async with sm() as s:
        return (await s.execute(select(Finding).where(Finding.run_id == run_id))).scalars().all()


async def test_happy_path_reaches_review_ready(db_sessionmaker, tmp_path):
    orch = await build(db_sessionmaker, tmp_path, World())
    run_id = await orch.create_run("testy", SETTINGS)
    outcome = await orch.execute(run_id)
    assert outcome.state == "review_ready" and "target" in outcome.stop_reason
    fs = await findings(db_sessionmaker, run_id)
    assert len(fs) == 4 and all(f.status == "analyzed" for f in fs)
    types = [e["type"] for e in await orch.blackboard.events_after(run_id)]
    assert types[0] == "run.state" and "plan.created" in types and types[-1] == "run.state"


async def test_overlapping_scopes_are_rejected(db_sessionmaker, tmp_path):
    orch = await build(db_sessionmaker, tmp_path, World(duplicate_scopes=True))
    run_id = await orch.create_run("testy", RunSettings(**{**SETTINGS.__dict__, "rounds": 1}))
    await orch.execute(run_id)
    async with db_sessionmaker() as s:
        rnd = (await s.execute(select(Round).where(Round.run_id == run_id))).scalars().first()
    assert len(rnd.plan_rejections) == 1 and "already owned" in rnd.plan_rejections[0]["reason"]
    assert len(await TaskQueue(db_sessionmaker).tasks(run_id)) == 1 + len(await findings(db_sessionmaker, run_id))


async def test_two_empty_rounds_stop_the_run(db_sessionmaker, tmp_path):
    world = World()
    orch = await build(db_sessionmaker, tmp_path, world, empty=True)
    run_id = await orch.create_run("testy", SETTINGS)
    outcome = await orch.execute(run_id)
    assert outcome.state == "review_ready" and "no new findings" in outcome.stop_reason
    assert world.rounds_planned == 2


async def test_usage_limit_pauses_then_completes(db_sessionmaker, tmp_path):
    orch = await build(db_sessionmaker, tmp_path, World(limit_first_analysis=True))
    run_id = await orch.create_run("testy", SETTINGS)
    outcome = await orch.execute(run_id)
    assert outcome.state == "review_ready"
    assert all(f.status == "analyzed" for f in await findings(db_sessionmaker, run_id))


async def test_crash_then_resume_completes_without_duplicates(db_sessionmaker, tmp_path):
    orch = await build(db_sessionmaker, tmp_path, World())
    run_id = await orch.create_run("testy", SETTINGS)
    task = asyncio.create_task(orch.execute(run_id))
    q = TaskQueue(db_sessionmaker)
    for _ in range(200):
        await asyncio.sleep(0.02)
        if any(t.state == "succeeded" for t in await q.tasks(run_id)):
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with db_sessionmaker() as s:
        assert (await s.get(Run, run_id)).state not in ("review_ready",)
    outcome = await orch.execute(run_id)
    assert outcome.state == "review_ready"
    cids = [f.canonical_id for f in await findings(db_sessionmaker, run_id)]
    assert len(cids) == len(set(cids)) >= 3


async def test_last_satisfaction_drives_the_explore_ratio(db_sessionmaker, tmp_path):
    from tf_db.models import Direction, RunFeedback

    orch = await build(db_sessionmaker, tmp_path, World())
    first = await orch.create_run("testy", SETTINGS)
    async with db_sessionmaker() as s:
        run = await s.get(Run, first)
        run.state = "review_ready"
        s.add(RunFeedback(run_id=first, satisfaction=9))
        for i in range(3):
            s.add(Direction(character_id=run.character_id, key=f"d{i}", label="x", alpha=2, beta=1))
        await s.commit()
    second = await orch.create_run("testy", SETTINGS)
    async with db_sessionmaker() as s:
        assert (await s.get(Run, second)).explore_ratio_used == pytest.approx(0.25)
