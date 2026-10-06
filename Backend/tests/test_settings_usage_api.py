"""Plan 6 Task 1: model choices per provider, live usage, runs history, character detail and videos."""
import uuid

import httpx
import pytest
from sqlalchemy import update

from tf_agent.learning.learner import Learner
from tf_agent.models.fake import FakeAdapter
from tf_agent.models.types import ModelInfo, RateInfo
from tf_agent.roles.runners import Roles
from tf_agent.testing import make_client
from tf_backend.app_context import AppContext
from tf_backend.main import create_app
from tf_backend.runs import RunManager
from tf_backend.services import Services
from tf_db.models import (
    Character,
    CharacterVersion,
    Finding,
    FindingScore,
    ModelCallRow,
    Run,
    TasteProfile,
    Video,
    VideoAnalysis,
)

H = {"x-trendfinder-client": "test"}
CLAUDE = [ModelInfo("claude", "fable", "Fable 5.1", priority=1, unavailable="not included in your plan"),
          ModelInfo("claude", "opus", "Opus 5.5", priority=2), ModelInfo("claude", "sonnet", "Sonnet 5.5", priority=3)]
GPT = [ModelInfo("chatgpt", "gpt-6-astra", "GPT-6-Astra", priority=1, reasoning_levels=("low", "medium", "high")),
       ModelInfo("chatgpt", "gpt-6-luna", "GPT-6-Luna", priority=2, reasoning_levels=("low", "medium"))]


@pytest.fixture
async def app_parts(db_sessionmaker, tmp_path):
    adapters = {"claude": FakeAdapter("claude", models=CLAUDE), "chatgpt": FakeAdapter("chatgpt", models=GPT)}
    client, governor, _ = make_client(adapters)
    registry = client.router.registry
    registry.aliases.update({"claude": {"best": "opus", "fast": "sonnet"},
                             "chatgpt": {"best": "gpt-6-astra", "fast": "gpt-6-luna"}})
    await registry.refresh()
    sv = Services(settings=None, chatgpt_auth=None, claude_auth=None, adapters=adapters, registry=registry,
                  router=client.router, governor=governor, client=client)
    chars = tmp_path / "chars" / "Testy"
    chars.mkdir(parents=True)
    (chars / "Testy.png").write_bytes(b"\x89PNG")
    ctx = AppContext(sessionmaker=db_sessionmaker, runs=RunManager(db_sessionmaker, lambda: None),
                     learner=Learner(db_sessionmaker, Roles(client)), media_dir=tmp_path / "media",
                     characters_dir=tmp_path / "chars")
    return sv, ctx


@pytest.fixture
async def http(app_parts):
    sv, ctx = app_parts
    app = create_app(services=sv, context=ctx)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers=H) as c:
        yield c


async def test_model_choices_list_and_apply(http, app_parts):
    sv, _ = app_parts
    m = (await http.get("/api/settings/models")).json()
    gpt = m["chatgpt"]
    assert (gpt["main"], gpt["fast"], gpt["effort"]) == ("gpt-6-astra", "gpt-6-luna", None)
    assert [x["id"] for x in gpt["models"]] == ["gpt-6-astra", "gpt-6-luna"] and gpt["efforts"] == ["low", "medium"]
    assert m["claude"]["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    fable = next(x for x in m["claude"]["models"] if x["id"] == "fable")
    assert fable["name"] == "Fable 5.1" and fable["unavailable"] == "not included in your plan"
    r = await http.put("/api/settings/models", json={"provider": "chatgpt", "main": "gpt-6-luna", "fast": "gpt-6-luna",
                                                     "effort": "medium"})
    assert r.status_code == 200
    assert sv.registry.resolve("chatgpt", "best") == "gpt-6-luna" and sv.client.effort_override["chatgpt"] == "medium"
    assert (await http.get("/api/settings/models")).json()["chatgpt"]["main"] == "gpt-6-luna"


@pytest.mark.parametrize("body,needle", [
    ({"provider": "chatgpt", "main": "gpt-9", "fast": "gpt-6-luna", "effort": None}, "gpt-9"),
    ({"provider": "chatgpt", "main": "gpt-6-astra", "fast": "gpt-6-luna", "effort": "high"}, "high"),
    ({"provider": "claude", "main": "opus", "fast": "sonnet", "effort": "ultra"}, "ultra"),
    ({"provider": "gemini", "main": "x", "fast": "y", "effort": None}, "gemini"),
    ({"provider": "claude", "main": "fable", "fast": "sonnet", "effort": None}, "not included in your plan"),
])
async def test_bad_model_choices_are_refused(http, body, needle):
    r = await http.put("/api/settings/models", json=body)
    assert r.status_code == 422 and needle in r.json()["detail"]


async def test_model_choices_survive_a_restart(app_parts, http):
    from tf_backend.model_settings import load_model_choices

    sv, ctx = app_parts
    await http.put("/api/settings/models", json={"provider": "claude", "main": "sonnet", "fast": "sonnet", "effort": "low"})
    sv.registry.aliases["claude"] = {"best": "opus", "fast": "sonnet"}
    sv.client.effort_override.clear()
    await load_model_choices(ctx.sessionmaker, sv)
    assert sv.registry.resolve("claude", "best") == "sonnet" and sv.client.effort_override["claude"] == "low"


async def run_with_tokens(ctx, tokens: list[tuple[str, int, int]], state="review_ready"):
    async with ctx.sessionmaker() as s:
        from tf_agent.characters.folders import sync_characters

        await sync_characters(ctx.characters_dir, ctx.sessionmaker)
        ch = (await s.execute(Character.__table__.select())).first()
        v = (await s.execute(CharacterVersion.__table__.select())).first()
        run = Run(character_id=ch.id, character_version_id=v.id, state=state, settings={})
        s.add(run)
        await s.flush()
        for provider, inp, out in tokens:
            s.add(ModelCallRow(run_id=run.id, role="scout", provider=provider, model="m", input_tokens=inp,
                               output_tokens=out, latency_ms=10, status="ok"))
        await s.commit()
        return run.id


async def test_usage_reports_windows_and_tokens(http, app_parts):
    sv, ctx = app_parts
    from tf_agent.models.types import RateWindow

    await sv.governor.observe_rate("chatgpt", RateInfo(used_percent=44.0, window_minutes=300, resets_at=1791300000.0))
    await sv.governor.observe_rate("claude", RateInfo(used_percent=44.0, window_minutes=10080, resets_at=1791792000.0,
                                                      windows=(RateWindow("five_hour", 43.0, 300, 1791337200.0),
                                                               RateWindow("seven_day", 44.0, 10080, 1791792000.0))))
    await run_with_tokens(ctx, [("chatgpt", 1000, 200), ("claude", 3000, 100)])
    await run_with_tokens(ctx, [("chatgpt", 3000, 600)])
    u = {p["provider"]: p for p in (await http.get("/api/usage")).json()["providers"]}
    assert u["chatgpt"]["used_percent"] == 44.0 and u["chatgpt"]["window_minutes"] == 300
    assert u["claude"]["used_percent"] == 44.0 and u["claude"]["status"] == "ok"
    assert [(w["name"], w["used_percent"]) for w in u["claude"]["windows"]] == [("five_hour", 43.0), ("seven_day", 44.0)]
    assert u["chatgpt"]["tokens_today"] == 4800 and u["chatgpt"]["calls_today"] == 2
    assert u["chatgpt"]["avg_tokens_per_run"] == 2400 and u["claude"]["avg_tokens_per_run"] == 3100


async def test_runs_history(http, app_parts):
    _, ctx = app_parts
    rid = await run_with_tokens(ctx, [("chatgpt", 10, 5)])
    runs = (await http.get("/api/runs")).json()
    assert runs[0]["id"] == str(rid) and runs[0]["character"]["slug"] == "testy" and runs[0]["tokens"] == 15
    assert (await http.get("/api/runs?character=testy")).json()[0]["id"] == str(rid)
    assert (await http.get("/api/runs?character=nobody")).json() == []


async def test_character_detail_and_videos_by_found_date(http, app_parts):
    _, ctx = app_parts
    rid = await run_with_tokens(ctx, [])
    async with ctx.sessionmaker() as s:
        await s.execute(update(Run).where(Run.id == rid).values(character_read={
            "look": "robot butler", "vibe": "calm", "performance_angle": "composure",
            "possible_niches": ["a", "b", "c"], "kling_constraints": "full body", "avoid": ""}))
        ch = (await s.execute(Character.__table__.select())).first()
        s.add(TasteProfile(character_id=ch.id, version=1, body_md="## Loves\n- ceremony", author="learner"))
        for i, days_ago in ((1, 0), (2, 10)):
            s.add(Video(canonical_id=f"tiktok:{i}", platform="tiktok", url=f"https://www.tiktok.com/@u/video/{i}"))
            await s.flush()
            s.add(VideoAnalysis(canonical_id=f"tiktok:{i}", pipeline_version="1", feasibility=80))
            f = Finding(run_id=rid, canonical_id=f"tiktok:{i}", status="analyzed")
            s.add(f)
            await s.flush()
            await s.execute(update(Finding).where(Finding.id == f.id).values(
                created_at=Finding.created_at - __import__("datetime").timedelta(days=days_ago)))
            s.add(FindingScore(finding_id=f.id, fit=70, overall=70 - i))
        await s.commit()
    d = (await http.get("/api/characters/testy")).json()
    assert d["name"] == "Testy" and d["latest_read"]["look"] == "robot butler" and "ceremony" in d["taste_md"]
    assert d["runs"] == 1 and d["videos_found"] == 2 and d["images"]
    week = (await http.get("/api/characters/testy/videos?days=7")).json()
    assert [v["canonical_id"] for v in week] == ["tiktok:1"] and week[0]["found_at"]
    every = (await http.get("/api/characters/testy/videos")).json()
    assert [v["canonical_id"] for v in every] == ["tiktok:1", "tiktok:2"]
    assert (await http.get(f"/api/characters/{uuid.uuid4()}")).status_code == 404


async def test_usage_falls_back_to_what_the_adapter_last_saw(http, app_parts):
    """The startup model check talks to the Claude CLI directly; its usage numbers still reach Settings."""
    from tf_agent.models.types import RateWindow

    sv, _ = app_parts
    sv.adapters["claude"].last_rate = RateInfo(used_percent=12.0, windows=(RateWindow("five_hour", 12.0, 300, None),))
    u = {p["provider"]: p for p in (await http.get("/api/usage")).json()["providers"]}
    assert u["claude"]["windows"][0]["used_percent"] == 12.0


async def test_usage_asks_providers_that_can_report_live(http, app_parts):
    from tf_agent.models.types import RateWindow

    sv, _ = app_parts

    async def live():
        return RateInfo(used_percent=17.0, windows=(RateWindow("five_hour", 0.0, 300, 1.0), RateWindow("seven_day", 17.0, 10080, 2.0)))

    sv.adapters["chatgpt"].usage = live
    u = {p["provider"]: p for p in (await http.get("/api/usage")).json()["providers"]}
    assert [(w["name"], w["used_percent"]) for w in u["chatgpt"]["windows"]] == [("five_hour", 0.0), ("seven_day", 17.0)]
    assert u["chatgpt"]["used_percent"] == 17.0
