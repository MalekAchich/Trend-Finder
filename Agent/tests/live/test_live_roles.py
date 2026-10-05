"""Real role calls on both subscriptions. Run: uv run pytest -m live Agent/tests/live/test_live_roles.py -v"""
from pathlib import Path

import pytest

from tf_agent.characters.brief import build_brief
from tf_agent.characters.profile import parse_profile
from tf_agent.roles.runners import Roles
from tf_backend.services import build_services

pytestmark = pytest.mark.live
ROOT = Path(__file__).resolve().parents[3]
CHAR = ROOT.parent / "AI Influencers Characters" / "Nicolaiz"
SHEET = ROOT / "media" / "sheets" / "tiktok_7692054548176702753.jpg"


@pytest.fixture
async def roles():
    sv = await build_services(with_db=False)
    yield Roles(sv.client)


@pytest.mark.parametrize("provider", ["claude", "chatgpt"])
async def test_analyst_on_real_sheet(roles, provider):
    if not SHEET.exists():
        pytest.skip("run `tf analyze` on the reference TikTok first")
    brief = build_brief(parse_profile((CHAR / "profile.md").read_text()))
    out = await roles.analyze(brief, str(CHAR / "Nicolaiz.png"), str(SHEET),
                              {"caption": "no ups and downs #dance", "pose": {"single_person_ratio": 0.24}},
                              provider=provider)
    assert out.provider == provider and 0 <= out.result.fit <= 100 and out.result.adaptation_idea


@pytest.mark.parametrize("provider", ["claude", "chatgpt"])
async def test_master_plan_schema(roles, provider):
    brief = build_brief(parse_profile((CHAR / "profile.md").read_text()))
    ctx = ("Round 1. Create exactly 3 tasks: 3 explore, 0 exploit. Niche is OPEN. Platforms: tiktok ok, youtube ok, "
           "instagram degraded (discovery only). No existing directions, no owned scopes.")
    judged = await roles._structured("master", roles.prompts.render("master", brief=brief), ctx, (),
                                     __import__("tf_agent.roles.schemas", fromlist=["WorkPlan"]).WorkPlan,
                                     "work_plan", only=provider)
    plan = judged.result
    assert 2 <= len(plan.tasks) <= 4 and len({d.niche for d in plan.directions}) >= 2
