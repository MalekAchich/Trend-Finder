from pathlib import Path

from tf_agent.models.fake import FakeAdapter
from tf_agent.models.registry import ModelRegistry, load_aliases
from tf_agent.models.router import RoleRouter, RouteCandidate, load_roles
from tf_agent.models.types import ModelInfo

CONFIG = Path(__file__).resolve().parents[2] / "config"


def chat_adapter():
    return FakeAdapter("chatgpt", models=[
        ModelInfo("chatgpt", "gpt-6-sol", priority=1),
        ModelInfo("chatgpt", "gpt-6-astra", priority=2),
        ModelInfo("chatgpt", "gpt-reserve", priority=0, hidden=True),
    ])


async def test_refresh_and_resolve_alias():
    reg = ModelRegistry({"chatgpt": chat_adapter()}, {"chatgpt": {"best": "gpt-6-astra"}})
    await reg.refresh()
    assert reg.resolve("chatgpt", "best") == "gpt-6-astra"
    assert reg.resolve("chatgpt", "gpt-6-sol") == "gpt-6-sol"


async def test_missing_configured_model_falls_back_to_top_visible():
    reg = ModelRegistry({"chatgpt": chat_adapter()}, {"chatgpt": {"best": "gpt-9-gone"}})
    await reg.refresh()
    assert reg.resolve("chatgpt", "best") == "gpt-6-sol"


def test_config_is_trusted_before_first_refresh():
    reg = ModelRegistry({"chatgpt": chat_adapter()}, {"chatgpt": {"best": "gpt-6-astra"}})
    assert reg.resolve("chatgpt", "best") == "gpt-6-astra"


def test_claude_aliases_always_resolve():
    reg = ModelRegistry({}, {"claude": {"best": "opus"}})
    reg._models["claude"] = [ModelInfo("claude", "claude-opus-5-5")]
    assert reg.resolve("claude", "best") == "opus"


async def test_refresh_failure_keeps_previous_list_and_hook_sees_success():
    seen = []

    async def hook(provider, models):
        seen.append((provider, [m.model_id for m in models]))

    adapter = chat_adapter()
    reg = ModelRegistry({"chatgpt": adapter}, {}, on_models=hook)
    await reg.refresh()
    adapter.models_error = RuntimeError("offline")
    await reg.refresh()
    assert [m.model_id for m in reg.models("chatgpt")] == ["gpt-6-sol", "gpt-6-astra", "gpt-reserve"]
    assert len(seen) == 1


async def test_router_order_effort_and_only_filter():
    reg = ModelRegistry({"chatgpt": chat_adapter()}, {"chatgpt": {"best": "gpt-6-astra"}, "claude": {"best": "opus"}})
    await reg.refresh()
    router = RoleRouter({"scout": [{"provider": "chatgpt", "model": "best", "effort": "medium"},
                                   {"provider": "claude", "model": "best"}]}, reg)
    assert router.candidates("scout") == [RouteCandidate("chatgpt", "gpt-6-astra", "medium"),
                                          RouteCandidate("claude", "opus", None)]
    assert router.candidates("scout", only="claude") == [RouteCandidate("claude", "opus", None)]



async def test_owner_priority_puts_one_provider_first_everywhere():
    reg = ModelRegistry({"chatgpt": chat_adapter()}, {"chatgpt": {"best": "gpt-6-astra"}, "claude": {"best": "opus"}})
    await reg.refresh()
    router = RoleRouter({"scout": [{"provider": "chatgpt", "model": "best", "effort": "medium"},
                                   {"provider": "claude", "model": "best"}]}, reg)
    router.first = "claude"
    assert [c.provider for c in router.candidates("scout")] == ["claude", "chatgpt"]
    assert [c.provider for c in router.candidates("scout", prefer="chatgpt")] == ["claude", "chatgpt"]  # beats rotation
    router.first = None
    assert [c.provider for c in router.candidates("scout", prefer="claude")] == ["claude", "chatgpt"]
    assert [c.provider for c in router.candidates("scout")] == ["chatgpt", "claude"]

def test_router_unknown_role_uses_default():
    reg = ModelRegistry({}, {"claude": {"best": "opus"}})
    router = RoleRouter({"default": [{"provider": "claude", "model": "best"}]}, reg)
    assert router.candidates("whatever") == [RouteCandidate("claude", "opus", None)]


def test_repo_config_files_load():
    roles = load_roles(CONFIG / "roles.yaml")
    aliases = load_aliases(CONFIG / "model_aliases.yaml")
    assert {"default", "master", "scout", "analyst", "demo"} <= set(roles)
    assert aliases["claude"]["best"] == "opus" and "best" in aliases["chatgpt"]
