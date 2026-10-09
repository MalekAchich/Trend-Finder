"""Plan 5 Task 7: the single page's API, against the real test DB with fake runs and fake models."""
import asyncio
import json
import uuid

import httpx
import pytest
from sqlalchemy import select, update

from tf_agent.learning.learner import Learner
from tf_agent.models.fake import FakeAdapter
from tf_agent.orchestrator.blackboard import Blackboard
from tf_agent.orchestrator.run import InputError, RunSettings
from tf_agent.pipeline import PIPELINE_VERSION
from tf_agent.roles.runners import Roles
from tf_agent.testing import make_client
from tf_backend.app_context import AppContext
from tf_backend.main import create_app
from tf_backend.runs import RunManager
from tf_db.models import (
    Character,
    CharacterVersion,
    Finding,
    FindingScore,
    Run,
    Task,
    TrendCluster,
    TrendMember,
    Video,
    VideoAnalysis,
)

H = {"x-trendfinder-client": "test"}


class FakeOrchestrator:
    """create_run/execute against the real DB; execute waits for a release event (so tests can observe states)."""

    def __init__(self, sm):
        self._sm = sm
        self.blackboard = Blackboard(sm)
        self.release = asyncio.Event()
        self.curate_release = asyncio.Event()
        self.executed, self.stops, self.created = [], [], []

    async def create_run(self, slug, settings):
        if settings is not None:
            for url in settings.trend_urls:
                if "example.com" in url:
                    raise InputError(f"not a TikTok, Instagram or YouTube video URL: {url}")
        self.created.append(settings)
        async with self._sm() as s:
            ch = (await s.execute(select(Character).where(Character.slug == slug))).scalar_one()
            v = (await s.execute(select(CharacterVersion).where(CharacterVersion.character_id == ch.id))).scalars().first()
            run = Run(character_id=ch.id, character_version_id=v.id, state="created", settings={},
                      inputs={"trend_urls": list(settings.trend_urls) if settings else []})
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

    async def stop_and_curate(self, run_id):
        self.stops.append(run_id)
        async with self._sm() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(state="curating",
                                                                       stop_reason="stopped by the owner"))
            await s.commit()
        await self.curate_release.wait()
        async with self._sm() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(state="review_ready"))
            await s.commit()


@pytest.fixture
async def ctx(db_sessionmaker, tmp_path):
    chars = tmp_path / "chars" / "Testy"
    chars.mkdir(parents=True)
    (chars / "Testy.png").write_bytes(b"\x89PNG")
    (chars / "Testy face.png").write_bytes(b"\x89PNG face")
    media = tmp_path / "media"
    (media / "thumbs").mkdir(parents=True)
    (media / "thumbs" / "tiktok_1.jpg").write_bytes(b"\xff\xd8jpeg")
    client, _, _ = make_client({"a": FakeAdapter("a", [])})
    orch = FakeOrchestrator(db_sessionmaker)
    return AppContext(sessionmaker=db_sessionmaker, runs=RunManager(db_sessionmaker, lambda: orch),
                      learner=Learner(db_sessionmaker, Roles(client)), media_dir=media,
                      characters_dir=tmp_path / "chars", orchestrator=orch)


@pytest.fixture
async def http(ctx):
    app = create_app(services=None, context=ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers=H) as c:
        yield c
    await ctx.runs.shutdown()


async def a_run(http, ctx, state="running"):
    await http.get("/api/characters")  # syncs the folder
    run_id = await ctx.orchestrator.create_run("testy", RunSettings())
    async with ctx.sessionmaker() as s:
        await s.execute(update(Run).where(Run.id == run_id).values(state=state))
        s.add(Video(canonical_id="tiktok:1", platform="tiktok", url="https://www.tiktok.com/@u/video/1",
                    creator_handle="u", metrics={"views": 1000}))
        await s.flush()
        s.add(VideoAnalysis(canonical_id="tiktok:1", pipeline_version=PIPELINE_VERSION,
                            thumbnail_path=str(ctx.media_dir / "thumbs" / "tiktok_1.jpg"),
                            best_clean_segment={"start_s": 0, "end_s": 9}, feasibility=80))
        f = Finding(run_id=run_id, canonical_id="tiktok:1", status="analyzed", why="fits")
        s.add(f)
        await s.flush()
        s.add(FindingScore(finding_id=f.id, fit=70, feasibility=80, momentum=50, freshness=90, overall=72,
                           adaptation_idea="hallway", fit_justification="calm", feasibility_notes="one person"))
        await s.commit()
        return run_id, f.id


async def curate(ctx, run_id, finding_id):
    async with ctx.sessionmaker() as s:
        c = TrendCluster(run_id=run_id, best_finding_id=finding_id, member_count=1, rank=1, overall=72, label="calm")
        s.add(c)
        await s.flush()
        s.add(TrendMember(cluster_id=c.id, finding_id=finding_id))
        await s.execute(update(Run).where(Run.id == run_id).values(state="review_ready"))
        await s.commit()
        return c.id


async def test_characters_are_image_folders_synced_on_read(http):
    chars = (await http.get("/api/characters")).json()
    assert [(c["slug"], c["name"], c["runs"]) for c in chars] == [("testy", "Testy", 0)]
    assert chars[0]["image_url"] == "/api/media/characters/Testy/Testy.png" and len(chars[0]["images"]) == 2
    assert (await http.get(chars[0]["image_url"])).status_code == 200


async def test_start_a_run_with_inputs(http, ctx):
    await http.get("/api/characters")
    body = {"character": "testy", "platforms": ["tiktok"], "freshness": "day", "minutes": 30,
            "trend_urls": ["https://www.tiktok.com/@a/video/1"],
            "targets": [{"url": "https://youtu.be/TestShort01", "character": "testy"}]}
    r = await http.post("/api/runs", json=body)
    assert r.status_code == 200
    s = ctx.orchestrator.created[-1]
    assert (s.freshness, s.wall_clock_s, s.platforms, s.trend_urls) == ("day", 1800, ["tiktok"], body["trend_urls"])
    assert s.targets == body["targets"]
    bad = await http.post("/api/runs", json={**body, "trend_urls": ["https://example.com/x"]})
    assert bad.status_code == 422 and "https://example.com/x" in bad.json()["detail"]
    assert (await http.post("/api/runs", json={**body, "freshness": "decade"})).status_code == 422


async def test_active_run_detail_and_events(http, ctx):
    assert (await http.get("/api/runs/active")).json() is None
    await http.get("/api/characters")
    run_id = (await http.post("/api/runs", json={"character": "testy"})).json()["run_id"]
    for _ in range(50):
        if (await http.get(f"/api/runs/{run_id}")).json()["state"] == "running":
            break
        await asyncio.sleep(0.02)
    active = (await http.get("/api/runs/active")).json()
    assert active["id"] == run_id and active["character"] == {"slug": "testy", "name": "Testy",
                                                               "image_url": "/api/media/characters/Testy/Testy.png"}
    async with ctx.sessionmaker() as s:
        s.add(Task(run_id=uuid.UUID(run_id), task_type="scout", platform="tiktok", state="running", goal="find dances"))
        await s.commit()
    detail = (await http.get(f"/api/runs/{run_id}")).json()
    assert detail["active"] is True and detail["inputs"]["trend_urls"] == []
    assert [(a["role"], a["platform"], a["status"], a["goal"]) for a in detail["agents"]] == [
        ("scout", "tiktok", "working", "find dances")]
    ctx.orchestrator.release.set()
    events = []
    async with http.stream("GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "0"}) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert [e["payload"]["state"] for e in events] == ["created", "running", "review_ready"]
    replay = []
    async with http.stream("GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(events[0]["id"])}) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                replay.append(json.loads(line[6:])["id"])
    assert replay == [e["id"] for e in events[1:]]


async def test_character_runs_newest_first(http, ctx):
    first, _ = await a_run(http, ctx, state="review_ready")
    runs = (await http.get("/api/characters/testy/runs")).json()
    assert [r["id"] for r in runs] == [str(first)] and runs[0]["videos"] == 1 and runs[0]["satisfaction"] is None
    assert (await http.get("/api/characters/nobody/runs")).status_code == 404


async def test_videos_live_then_curated_and_rated(http, ctx):
    run_id, finding_id = await a_run(http, ctx)
    live = (await http.get(f"/api/runs/{run_id}/videos")).json()
    assert len(live) == 1 and live[0]["cluster_id"] is None and live[0]["score"] == 72
    assert live[0]["thumbnail_url"] == "/api/media/thumbs/tiktok_1.jpg" and live[0]["platform_id"] == "1"
    cluster_id = await curate(ctx, run_id, finding_id)
    curated = (await http.get(f"/api/runs/{run_id}/videos")).json()
    assert curated[0]["cluster_id"] == str(cluster_id) and curated[0]["feedback"] is None
    r = await http.put(f"/api/videos/{cluster_id}/feedback", json={"rating": "up", "note": "love it"})
    assert r.status_code == 200
    assert (await http.get(f"/api/runs/{run_id}/videos")).json()[0]["feedback"] == {"rating": "up", "note": "love it"}
    assert (await http.put(f"/api/videos/{cluster_id}/feedback", json={"rating": None, "note": None})).status_code == 200
    assert (await http.get(f"/api/runs/{run_id}/videos")).json()[0]["feedback"] is None
    assert (await http.put(f"/api/videos/{uuid.uuid4()}/feedback", json={"rating": "up"})).status_code == 404
    assert (await http.put(f"/api/videos/{cluster_id}/feedback", json={"rating": "skip"})).status_code == 422
    assert (await http.get("/api/media/thumbs/tiktok_1.jpg")).status_code == 200


async def test_run_score_only_after_it_ended(http, ctx):
    run_id, finding_id = await a_run(http, ctx)
    r = await http.put(f"/api/runs/{run_id}/feedback", json={"satisfaction": 7, "note": None})
    assert r.status_code == 409 and "running" in r.json()["detail"]
    await curate(ctx, run_id, finding_id)
    assert (await http.put(f"/api/runs/{run_id}/feedback", json={"satisfaction": 7, "note": "ok"})).status_code == 200
    assert (await http.get("/api/characters/testy/runs")).json()[0]["satisfaction"] == 7
    assert (await http.get(f"/api/runs/{run_id}")).json()["satisfaction"] == 7


async def test_stop_and_resume(http, ctx):
    await http.get("/api/characters")
    run_id = (await http.post("/api/runs", json={"character": "testy"})).json()["run_id"]
    await asyncio.sleep(0.05)
    assert (await http.post(f"/api/runs/{run_id}/stop")).status_code == 200
    await asyncio.sleep(0.05)
    run = (await http.get(f"/api/runs/{run_id}")).json()
    assert run["state"] == "curating" and run["active"] is True
    assert (await http.post(f"/api/runs/{run_id}/resume")).status_code == 200  # no-op while curating
    r = await http.post(f"/api/runs/{run_id}/stop")
    assert r.status_code == 409 and "already stopping" in r.json()["detail"]
    ctx.orchestrator.curate_release.set()
    for _ in range(50):
        if (await http.get(f"/api/runs/{run_id}")).json()["state"] == "review_ready":
            break
        await asyncio.sleep(0.02)
    assert (await http.post(f"/api/runs/{run_id}/resume")).status_code == 409


@pytest.mark.parametrize("path,status", [
    ("/api/media/thumbs/tiktok_1.jpg", 200),
    ("/api/media/characters/Testy/Testy.png", 200),
    ("/api/media/thumbs/../../secrets/chatgpt-auth.json", 404),
    ("/api/media/thumbs/%2e%2e/%2e%2e/etc/passwd", 404),
    ("/api/media/sheets/tiktok_1.jpg", 404),
    ("/api/media/videos/x.mp4", 404),
])
async def test_media_is_confined(http, path, status):
    assert (await http.get(path)).status_code == status


async def test_removed_routes_are_gone(http):
    for method, path in (("GET", "/api/settings"), ("GET", "/api/briefs"), ("GET", "/api/platforms"),
                         ("POST", "/api/characters/sync")):
        assert (await http.request(method, path)).status_code in (404, 405), path


async def test_mutations_need_the_client_header(ctx):
    app = create_app(services=None, context=ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as bare:
        assert (await bare.post("/api/runs", json={"character": "testy"})).status_code == 403
        assert (await bare.put(f"/api/videos/{uuid.uuid4()}/feedback", json={"rating": "up"})).status_code == 403
        assert (await bare.get("/api/characters")).status_code == 200


async def test_unfinished_runs_resume_on_startup(http, ctx):
    await http.get("/api/characters")
    run_id = await ctx.orchestrator.create_run("testy", None)
    async with ctx.sessionmaker() as s:
        await s.execute(update(Run).where(Run.id == run_id).values(state="reading_character"))
        s.add(Task(run_id=run_id, task_type="scout", state="running"))
        await s.commit()
    assert await ctx.runs.resume_unfinished() == [run_id]
    await asyncio.sleep(0.05)
    assert ctx.orchestrator.executed == [run_id]
    async with ctx.sessionmaker() as s:
        assert (await s.execute(select(Task.state).where(Task.run_id == run_id))).scalar_one() == "queued"


async def test_run_score_and_note_come_back_after_a_reload(http, ctx):
    run_id, finding_id = await a_run(http, ctx)
    await curate(ctx, run_id, finding_id)
    r = await http.put(f"/api/runs/{run_id}/feedback", json={"satisfaction": 6, "note": "more AI influencers"})
    assert r.status_code == 200
    detail = (await http.get(f"/api/runs/{run_id}")).json()
    listed = (await http.get("/api/characters/testy/runs")).json()[0]
    history = (await http.get("/api/runs")).json()[0]
    for row in (detail, listed, history):
        assert (row["satisfaction"], row["run_note"]) == (6, "more AI influencers")


async def test_videos_from_before_a_pipeline_bump_keep_their_thumbnail(http, ctx):
    run_id, _ = await a_run(http, ctx)
    async with ctx.sessionmaker() as s:
        await s.execute(update(VideoAnalysis).values(pipeline_version="old"))
        await s.commit()
    card = (await http.get(f"/api/runs/{run_id}/videos")).json()[0]
    assert card["thumbnail_url"] == "/api/media/thumbs/tiktok_1.jpg"


async def test_download_a_found_video_or_its_sound(http, ctx, tmp_path):
    from tf_backend.downloads import Downloads

    await a_run(http, ctx)
    asked = []

    async def save(url, folder, kind):
        asked.append((url, kind))
        folder.mkdir(parents=True)
        f = folder / ("1.mp3" if kind == "audio" else "1.mp4")
        f.write_bytes(b"media")
        return f

    ctx.downloads = Downloads(save, tmp_path / "downloads")
    video = await http.get("/api/download/tiktok:1")
    sound = await http.get("/api/download/tiktok:1?kind=audio")
    assert video.status_code == 200 and video.content == b"media" and video.headers["content-type"] == "video/mp4"
    assert 'filename="tiktok-u-1.mp4"' in video.headers["content-disposition"]
    assert sound.headers["content-type"] == "audio/mpeg" and "tiktok-u-1-sound.mp3" in sound.headers["content-disposition"]
    assert asked == [("https://www.tiktok.com/@u/video/1", "video"), ("https://www.tiktok.com/@u/video/1", "audio")]
    assert (await http.get("/api/download/tiktok:999")).status_code == 404  # only videos the app knows
    assert (await http.get("/api/download/tiktok:1?kind=gif")).status_code == 422


def png(color="red", size=(8, 12), fmt="PNG"):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


async def test_add_a_character_from_the_app(http, ctx):
    files = [("images", ("look.png", png("red"), "image/png")), ("images", ("face.jpg", png("blue", fmt="JPEG"), "image/jpeg"))]
    r = await http.post("/api/characters", data={"name": "Made Up Mia"}, files=files)
    assert r.status_code == 200 and r.json() == {"slug": "made-up-mia"}
    folder = ctx.characters_dir / "Made Up Mia"
    assert sorted(p.name for p in folder.iterdir()) == ["Made Up Mia.png", "face.jpg"]  # the first is the main one
    mia = next(c for c in (await http.get("/api/characters")).json() if c["slug"] == "made-up-mia")
    assert mia["image_url"].endswith("/Made%20Up%20Mia.png") or mia["image_url"].endswith("/Made Up Mia.png")
    again = await http.post("/api/characters", data={"name": "made up mia"}, files=files[:1])
    assert again.status_code == 409
    assert (await http.post("/api/characters", data={"name": "../escape"}, files=files[:1])).status_code == 422
    fake = [("images", ("x.png", b"not an image", "image/png"))]
    bad = await http.post("/api/characters", data={"name": "Nope"}, files=fake)
    assert bad.status_code == 422 and "isn't an image" in bad.json()["detail"]
    assert not (ctx.characters_dir / "Nope").exists()


async def test_add_images_to_a_character(http, ctx):
    await http.get("/api/characters")
    r = await http.post("/api/characters/testy/images", files=[("images", ("Testy face.png", png("green"), "image/png"))])
    assert r.status_code == 200
    names = sorted(p.name for p in (ctx.characters_dir / "Testy").iterdir())
    assert names == ["Testy face 2.png", "Testy face.png", "Testy.png"]  # never overwrites
    assert len((await http.get("/api/characters/testy")).json()["images"]) == 3
    assert (await http.post("/api/characters/nobody/images", files=[("images", ("a.png", png(), "image/png"))])
            ).status_code == 404


async def test_renaming_keeps_the_runs_and_renames_the_folder(http, ctx):
    run_id, _ = await a_run(http, ctx, state="review_ready")
    r = await http.patch("/api/characters/testy", json={"name": "Testy Two"})
    assert r.status_code == 200 and r.json() == {"slug": "testy-two"}
    folder = ctx.characters_dir / "Testy Two"
    assert sorted(p.name for p in folder.iterdir()) == ["Testy Two.png", "Testy face.png"]
    chars = (await http.get("/api/characters")).json()
    assert [(c["slug"], c["name"], c["runs"]) for c in chars] == [("testy-two", "Testy Two", 1)]
    assert [x["id"] for x in (await http.get("/api/characters/testy-two/runs")).json()] == [str(run_id)]
    assert (await http.get("/api/characters/testy")).status_code == 404


async def test_a_folder_renamed_by_hand_keeps_its_character(http, ctx):
    run_id, _ = await a_run(http, ctx, state="review_ready")
    (ctx.characters_dir / "Testy").rename(ctx.characters_dir / "Testo")
    chars = (await http.get("/api/characters")).json()
    assert [(c["slug"], c["runs"]) for c in chars] == [("testo", 1)]


async def test_no_rename_while_its_run_is_going(http, ctx, monkeypatch):
    await a_run(http, ctx, state="running")
    monkeypatch.setattr(ctx.runs, "is_active", lambda run_id: True)
    r = await http.patch("/api/characters/testy", json={"name": "Later"})
    assert r.status_code == 409 and "run is going" in r.json()["detail"]


async def test_videos_under_the_bar_are_listed_closest_first(http, ctx):
    run_id, finding_id = await a_run(http, ctx, state="review_ready")
    assert (await http.get(f"/api/runs/{run_id}/below-bar")).json() == []
    async with ctx.sessionmaker() as s:
        await s.execute(update(Finding).where(Finding.id == finding_id).values(status="below_bar"))
        await s.commit()
    under = (await http.get(f"/api/runs/{run_id}/below-bar")).json()
    assert [(v["canonical_id"], v["score"]) for v in under] == [("tiktok:1", 72)]
    assert (await http.get(f"/api/runs/{run_id}/videos")).json() == []  # never among the kept videos
    assert (await http.get(f"/api/runs/{uuid.uuid4()}/below-bar")).status_code == 404


async def test_the_live_counts_leave_out_the_owners_own_targets(http, ctx):
    run_id, _ = await a_run(http, ctx)
    async with ctx.sessionmaker() as s:
        s.add(Video(canonical_id="tiktok:2", platform="tiktok", url="https://www.tiktok.com/@u/video/2"))
        await s.flush()
        s.add(Finding(run_id=run_id, canonical_id="tiktok:2", status="analyzed", source="owner", why="your target"))
        await s.commit()
    assert (await http.get(f"/api/runs/{run_id}")).json()["findings"] == {"analyzed": 1}  # the run's own find only
