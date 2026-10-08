import json

import pytest

from tf_agent.loop.tools import Tool
from tf_agent.models.fake import FakeAdapter, text_response, tool_call_response
from tf_agent.roles.prompts import PromptLibrary
from tf_agent.roles.runners import Roles, TaskSpec
from tf_agent.roles.schemas import AnalystResult, WorkPlan
from tf_agent.testing import make_client
from tf_agent.tools.agent_tools import SearchParams

BRIEF = "# Character: Nicolaiz\n## Persona\nA deadpan 1970s gentleman."

PLAN = {"reasoning_summary": "start broad", "directions": [
    {"key": "deadpan-mundane-duty", "label": "Mundane tasks done with ceremony", "hypothesis": "contrast",
     "niche": "deadpan professional", "mode": "explore"}],
    "tasks": [{"task_type": "scout", "direction_key": "deadpan-mundane-duty", "platform": "tiktok",
               "scope": {"queries": ["serious man dance trend"]}, "goal": "single-person deadpan dances",
               "max_candidates": 5}], "stop": False}

ANALYSIS = {"fit_breakdown": {"look": 8, "vibe": 9, "energy": 7, "niche": 6, "adaptability": 8},
            "justification": "stiff dance suits him", "adaptation_idea": "Nicolaiz in a bank lobby",
            "feasibility_notes": "single person, static camera", "niche_guess": "deadpan professional"}


def roles_for(*adapters):
    client, _, _ = make_client({a.name: a for a in adapters})
    return Roles(client, PromptLibrary())


def test_every_prompt_renders_without_leftover_placeholders():
    lib = PromptLibrary()
    for role in ("master", "scout", "radar", "deep_dive", "seed_study", "analyst", "cross_check", "reader"):
        text = lib.render(role, brief=BRIEF)
        assert ("Nicolaiz" in text or role == "reader") and "$" not in text and lib.version(role)
        assert "deadpan" not in lib.raw(role).lower()  # prompts are character-agnostic
    assert "only your own scope" in lib.render("scout", brief=BRIEF).lower()


async def test_plan_returns_validated_workplan():
    a = FakeAdapter("a", [text_response("a", structured=PLAN)])
    plan = await roles_for(a).plan("ctx")
    assert isinstance(plan, WorkPlan) and plan.tasks[0].scope.queries == ["serious man dance trend"]
    req = a.requests[0]
    assert req.output_schema is not None and req.schema_name == "work_plan"


async def test_plan_repairs_invalid_output_once():
    bad = dict(PLAN, tasks=[dict(PLAN["tasks"][0], platform="myspace")])
    a = FakeAdapter("a", [text_response("a", structured=bad), text_response("a", structured=PLAN)])
    plan = await roles_for(a).plan("ctx")
    assert plan.tasks[0].platform == "tiktok"
    assert "invalid" in a.requests[1].messages[-1].text().lower()


async def test_worker_runs_tools_and_submits():
    calls = []

    async def search(p: SearchParams):
        calls.append(p.query)
        return {"items": [], "hidden_already_seen": 0, "platform_health": "ok", "notes": []}

    tools = [Tool("tiktok_search", "search", SearchParams, search)]
    result = {"candidates": [{"canonical_id": "tiktok:1", "why": "deadpan", "preliminary_fit": 7}],
              "leads": [{"type": "sound", "value": "sound:9", "platform": "tiktok", "why": "recurring"}], "notes": ""}
    a = FakeAdapter("a", [tool_call_response("a", ("tiktok_search", {"query": "deadpan dance"})),
                          tool_call_response("a", ("submit_result", result))])
    spec = TaskSpec(task_type="scout", platform="tiktok", scope={"queries": ["deadpan dance"]}, goal="find",
                    max_candidates=5, direction_label="Mundane", direction_hypothesis="contrast")
    res = await roles_for(a).work(spec, BRIEF, tools)
    assert res.status == "succeeded" and res.result.candidates[0].canonical_id == "tiktok:1"
    assert calls == ["deadpan dance"]
    assert "deadpan dance" in a.requests[0].messages[0].text()


async def test_analyst_sees_canonical_image_then_contact_sheet(tmp_path):
    canon, sheet = tmp_path / "canon.png", tmp_path / "sheet.jpg"
    canon.write_bytes(b"png")
    sheet.write_bytes(b"jpg")
    a = FakeAdapter("a", [text_response("a", structured=ANALYSIS)])
    out = await roles_for(a).analyze(BRIEF, str(canon), str(sheet), {"caption": "x", "views": 10})
    assert isinstance(out.result, AnalystResult) and out.result.fit == pytest.approx(72.0)  # closeness counts most
    assert out.provider == "a"
    assert [i.path for i in a.requests[0].messages[0].images()] == [str(canon), str(sheet)]
    assert json.dumps({"caption": "x", "views": 10}) in a.requests[0].messages[0].text()


async def test_cross_check_uses_the_other_provider(tmp_path):
    canon, sheet = tmp_path / "c.png", tmp_path / "s.jpg"
    canon.write_bytes(b"x")
    sheet.write_bytes(b"x")
    a = FakeAdapter("a")
    b = FakeAdapter("b", [text_response("b", structured={"fit": 55, "justification": "meh"})])
    out = await roles_for(a, b).cross_check(BRIEF, str(canon), str(sheet), {}, exclude_provider="a")
    assert out.result.fit == 55 and out.provider == "b" and a.requests == []
