"""Real role calls on both subscriptions. Run: uv run pytest -m live Agent/tests/live/test_live_roles.py -v"""
from pathlib import Path

import pytest

from tf_agent.characters.folders import discover
from tf_agent.characters.read import CharacterRead, render_brief
from tf_agent.roles.runners import Roles
from tf_backend.services import build_services

pytestmark = pytest.mark.live
ROOT = Path(__file__).resolve().parents[3]
CHARS = ROOT.parent / "AI Influencers Characters"
SHEET = ROOT / "media" / "sheets" / "tiktok_7692054548176702753.jpg"
READ = CharacterRead(look="Curly dark hair, moustache, tan 1970s suit and brown shirt",
                     vibe="calm, sincere, out of time", performance_angle="treats modern trends with total gravity",
                     possible_niches=["man out of time", "retro lifestyle", "deadpan professional"],
                     kling_constraints="full body, one person, slow moves", avoid="fast spins")


@pytest.fixture
async def roles():
    sv = await build_services(with_db=False)
    yield Roles(sv.client)


def nicolaiz():
    return next(c for c in discover(CHARS) if c.slug == "nicolaiz")


@pytest.mark.parametrize("provider", ["claude", "chatgpt"])
async def test_reader_describes_the_real_images(roles, provider):
    c = nicolaiz()
    out = await roles._structured("reader", roles.prompts.render("reader"), f"Character name: {c.name}.",
                                  [__import__("tf_agent.models.types", fromlist=["ImagePart"]).ImagePart(p)
                                   for p in c.images[:4]], CharacterRead, "character_read", only=provider)
    assert out.provider == provider and len(out.result.possible_niches) >= 3
    assert any(w in out.result.look.lower() for w in ("suit", "blazer", "jacket"))


@pytest.mark.parametrize("provider", ["claude", "chatgpt"])
async def test_analyst_on_real_sheet(roles, provider):
    if not SHEET.exists():
        pytest.skip("run `tf analyze` on the reference TikTok first")
    out = await roles.analyze(render_brief("Nicolaiz", READ, None), nicolaiz().images[0], str(SHEET),
                              {"caption": "no ups and downs #dance", "pose": {"single_person_ratio": 0.24}},
                              provider=provider)
    assert out.provider == provider and 0 <= out.result.fit <= 100 and out.result.adaptation_idea


@pytest.mark.parametrize("provider", ["claude", "chatgpt"])
async def test_master_plan_schema(roles, provider):
    from tf_agent.roles.schemas import WorkPlan

    ctx = ("Round 1. Create exactly 3 tasks: 3 explore, 0 exploit. Niche not decided yet. Platforms: tiktok ok, "
           "youtube ok, instagram degraded (discovery only). No existing directions, no owned scopes.")
    judged = await roles._structured("master", roles.prompts.render("master", brief=render_brief("Nicolaiz", READ, None)),
                                     ctx, (), WorkPlan, "work_plan", only=provider)
    plan = judged.result
    assert 2 <= len(plan.tasks) <= 4 and len({d.niche for d in plan.directions}) >= 2
