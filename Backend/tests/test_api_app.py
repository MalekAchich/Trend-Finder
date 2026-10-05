"""API surface for the web app (07-api-and-frontend.md) against the real test DB with fake runs and fake models."""
import asyncio
import json
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, update

from tf_agent.characters.sync import sync_characters
from tf_agent.learning.briefs import BriefWriter
from tf_agent.learning.learner import Learner
from tf_agent.models.fake import FakeAdapter, text_response
from tf_agent.orchestrator.blackboard import Blackboard
from tf_agent.roles.runners import Roles
from tf_agent.testing import make_client
from tf_backend.app_context import AppContext
from tf_backend.main import create_app
from tf_backend.runs import RunManager
from tf_db.models import (
    Character,
    Finding,
    FindingScore,
    Run,
    TrendCluster,
    TrendMember,
    Video,
    VideoAnalysis,
)

H = {"x-trendfinder-client": "test"}
PROFILE = """---
name: Testy
slug: testy
canonical_image: testy.png
---

## Persona
A calm robot butler.

## Niche / context: OPEN
To discover.
"""
LEARNED = {"taste_profile_md": "## Loves\n- calm duties", "retrospective": ["ok run"], "primary_niche": None}
BRIEF = {"title": "Duty", "concept": "Testy does the dance", "record_yourself": "walk in", "character_orientation": "video",
         "kling_prompt": "robot butler in a plain hallway", "framing": "full body 9:16",
         "motion_window": {"start_s": 0, "end_s": 6}, "shots": [{"seconds": 6, "description": "dance"}], "risks": []}


class FakeOrchestrator:
    """create_run/execute against the real DB; execute waits for a release event (so tests can observe states)."""

    def __init__(self, sm):
        self._sm = sm
        self.blackboard = Blackboard(sm)
        self.release = asyncio.Event()
        self.executed = []

    async def create_run(self, slug, settings):
        async with self._sm() as s:
            from tf_db.models import CharacterVersion
            ch = (await s.execute(select(Character).where(Character.slug == slug))).scalar_one()
            v = (await s.execute(select(CharacterVersion).where(CharacterVersion.character_id == ch.id))).scalars().first()
            run = Run(character_id=ch.id, character_version_id=v.id, state="created", settings={})
            s.add(run)
            await s.commit()
            await self.blackboard.record_event(run.id, "run.state", {"state": "created"})
            return run.id

    async def execute(self, run_id):
        self.executed.append(run_id)
        async with self._sm() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(state="running"))
            await s.commit()
        await self.blackboard.record_event(run_id, "run.state", {"state": "running"})
        await self.release.wait()
        async with self._sm() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(state="review_ready"))
            await s.commit()
        await self.blackboard.record_event(run_id, "run.state", {"state": "review_ready"})


@pytest.fixture
async def ctx(db_sessionmaker, tmp_path):
    chars = tmp_path / "chars" / "Testy"
    chars.mkdir(parents=True)
    (chars / "profile.md").write_text(PROFILE)
    (chars / "testy.png").write_bytes(b"\x89PNG")
    await sync_characters(tmp_path / "chars", db_sessionmaker)
    media = tmp_path / "media"
    (media / "sheets").mkdir(parents=True)
    (media / "sheets" / "tiktok_1.jpg").write_bytes(b"\xff\xd8jpeg")
    fa = FakeAdapter("a", [text_response("a", structured=LEARNED), text_response("a", structured=BRIEF)])
    client, _, _ = make_client({"a": fa})
    roles = Roles(client)
    orch = FakeOrchestrator(db_sessionmaker)
    return AppContext(sessionmaker=db_sessionmaker, runs=RunManager(db_sessionmaker, lambda: orch),
                      learner=Learner(db_sessionmaker, roles), briefs=BriefWriter(db_sessionmaker, roles),
                      media_dir=media, characters_dir=tmp_path / "chars", orchestrator=orch)


@pytest.fixture
async def http(ctx):
    app = create_app(services=None, context=ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", headers=H) as c:
        yield c
    await ctx.runs.shutdown()


async def make_reviewable_run(ctx, rating=None):
    async with ctx.sessionmaker() as s:
        ch = (await s.execute(select(Character))).scalar_one()
    run_id = await ctx.orchestrator.create_run("testy", None)
    async with ctx.sessionmaker() as s:
        s.add(Video(canonical_id="tiktok:1", platform="tiktok", url="https://www.tiktok.com/@u/video/1",
                    metrics={"views": 1000}))
        await s.flush()
        s.add(VideoAnalysis(canonical_id="tiktok:1", pipeline_version="1",
                            contact_sheet_path=str(ctx.media_dir / "sheets" / "tiktok_1.jpg"),
                            best_clean_segment={"start_s": 0, "end_s": 9}, feasibility=80))
        f = Finding(run_id=run_id, canonical_id="tiktok:1", status="analyzed")
        s.add(f)
        await s.flush()
        s.add(FindingScore(finding_id=f.id, fit=70, feasibility=80, momentum=50, freshness=90, overall=72,
                           adaptation_idea="hallway", fit_justification="calm"))
        c = TrendCluster(run_id=run_id, best_finding_id=f.id, member_count=1, rank=1, overall=72, label="calm")
        s.add(c)
        await s.flush()
        s.add(TrendMember(cluster_id=c.id, finding_id=f.id))
        await s.execute(update(Run).where(Run.id == run_id).values(state="review_ready"))
        await s.commit()
        return run_id, c.id


async def test_characters_detail_taste_and_seeds(http):
    chars = (await http.get("/api/characters")).json()
    assert [c["slug"] for c in chars] == ["testy"]
    detail = (await http.get("/api/characters/testy")).json()
    assert "robot butler" in detail["brief"] and detail["niche_open"] is True and detail["taste_profile"] is None
    assert detail["canonical_image_url"] == "/api/media/characters/Testy/testy.png"
    r = await http.put("/api/characters/testy/taste-profile", json={"body_md": "## Loves\n- kitchens"})
    assert r.status_code == 200 and r.json()["version"] == 1
    r = await http.post("/api/characters/testy/seeds", json={"url": "https://www.youtube.com/shorts/OUZbZ8cz4j8"})
    assert r.status_code == 200
    assert (await http.get("/api/characters/testy")).json()["seeds"][0]["canonical_id"] == "youtube:OUZbZ8cz4j8"


async def test_start_list_detail_and_events_of_a_run(http, ctx):
    r = await http.post("/api/runs", json={"slug": "testy", "rounds": 1, "tasks_per_round": 2})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    for _ in range(50):
        if (await http.get(f"/api/runs/{run_id}")).json()["state"] == "running":
            break
        await asyncio.sleep(0.02)
    detail = (await http.get(f"/api/runs/{run_id}")).json()
    assert detail["state"] == "running" and detail["active"] is True and detail["character"] == "testy"
    assert [x["id"] for x in (await http.get("/api/runs")).json()] == [run_id]
    ctx.orchestrator.release.set()
    events = []
    async with http.stream("GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "0"}) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert [e["payload"]["state"] for e in events] == ["created", "running", "review_ready"]
    first_id = events[0]["id"]
    replay = []
    async with http.stream("GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(first_id)}) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                replay.append(json.loads(line[6:])["id"])
    assert replay == [e["id"] for e in events[1:]]


async def test_stop_and_resume(http, ctx):
    run_id = (await http.post("/api/runs", json={"slug": "testy"})).json()["run_id"]
    await asyncio.sleep(0.05)
    r = await http.post(f"/api/runs/{run_id}/stop")
    assert r.status_code == 200 and (await http.get(f"/api/runs/{run_id}")).json()["state"] == "stopped"
    assert (await http.post(f"/api/runs/{run_id}/resume")).status_code == 409  # stopped runs are final


async def test_trends_feedback_and_briefs(http, ctx):
    run_id, cluster_id = await make_reviewable_run(ctx)
    cards = (await http.get(f"/api/runs/{run_id}/trends")).json()["cards"]
    card = cards[0]
    assert card["rank"] == 1 and card["scores"]["fit"] == 70 and card["contact_sheet_url"] == "/api/media/sheets/tiktok_1.jpg"
    assert card["best"]["url"].startswith("https://www.tiktok.com") and card["feedback"] is None
    assert (await http.post(f"/api/trends/{cluster_id}/brief")).status_code == 409  # not 👍 yet
    fb = {"cards": [{"cluster_id": str(cluster_id), "rating": "up", "note": "yes!"}], "satisfaction": 8, "note": None}
    r = await http.post(f"/api/runs/{run_id}/feedback", json=fb)
    assert r.status_code == 200 and r.json()["taste_version"] == 1
    r = await http.post(f"/api/trends/{cluster_id}/brief")
    assert r.status_code == 200 and r.json()["body"]["title"] == "Duty" and "# Duty" in r.json()["body_md"]
    assert (await http.get(f"/api/trends/{cluster_id}/brief")).json()["body"]["title"] == "Duty"
    assert (await http.get(f"/api/runs/{run_id}/trends")).json()["cards"][0]["feedback"]["rating"] == "up"


async def test_feedback_on_a_running_run_is_rejected(http, ctx):
    run_id, cluster_id = await make_reviewable_run(ctx)
    async with ctx.sessionmaker() as s:
        await s.execute(update(Run).where(Run.id == run_id).values(state="running"))
        await s.commit()
    fb = {"cards": [{"cluster_id": str(cluster_id), "rating": "up"}], "satisfaction": 5}
    r = await http.post(f"/api/runs/{run_id}/feedback", json=fb)
    assert r.status_code == 409 and "review" in r.json()["detail"]


@pytest.mark.parametrize("path,status", [
    ("/api/media/sheets/tiktok_1.jpg", 200),
    ("/api/media/characters/Testy/testy.png", 200),
    ("/api/media/sheets/../../secrets/chatgpt-auth.json", 404),
    ("/api/media/sheets/%2e%2e/%2e%2e/etc/passwd", 404),
    ("/api/media/other/x.jpg", 404),
])
async def test_media_is_confined(http, path, status):
    assert (await http.get(path)).status_code == status


async def test_settings_weights(http):
    s = (await http.get("/api/settings")).json()
    assert s["weights"] == {"fit": 0.4, "feasibility": 0.3, "momentum": 0.2, "freshness": 0.1}
    bad = await http.put("/api/settings", json={"weights": {"fit": 0.9, "feasibility": 0.9, "momentum": 0, "freshness": 0}})
    assert bad.status_code == 422
    ok = await http.put("/api/settings", json={"weights": {"fit": 0.5, "feasibility": 0.3, "momentum": 0.1,
                                                           "freshness": 0.1}})
    assert ok.status_code == 200 and (await http.get("/api/settings")).json()["weights"]["fit"] == 0.5


async def test_mutations_need_the_client_header(ctx):
    app = create_app(services=None, context=ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as bare:
        assert (await bare.post("/api/runs", json={"slug": "testy"})).status_code == 403
        assert (await bare.put("/api/settings", json={"weights": {}})).status_code == 403
        assert (await bare.get("/api/characters")).status_code == 200


async def test_unfinished_runs_resume_on_startup(ctx):
    run_id = await ctx.orchestrator.create_run("testy", None)
    async with ctx.sessionmaker() as s:
        await s.execute(update(Run).where(Run.id == run_id).values(state="running"))
        await s.commit()
    resumed = await ctx.runs.resume_unfinished()
    assert resumed == [run_id]
    await asyncio.sleep(0.05)
    assert ctx.orchestrator.executed == [run_id]
    await ctx.runs.shutdown()


async def test_resume_on_startup_requeues_orphaned_tasks_and_includes_interrupted(ctx):
    from tf_db.models import Task

    run_id = await ctx.orchestrator.create_run("testy", None)
    async with ctx.sessionmaker() as s:
        await s.execute(update(Run).where(Run.id == run_id).values(state="interrupted"))
        s.add(Task(run_id=run_id, task_type="scout", state="running"))
        await s.commit()
    assert await ctx.runs.resume_unfinished() == [run_id]
    async with ctx.sessionmaker() as s:
        assert (await s.execute(select(Task.state).where(Task.run_id == run_id))).scalar_one() == "queued"
    await ctx.runs.shutdown()
