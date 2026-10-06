"""Real calls on the owner's subscriptions. Run explicitly: uv run pytest -m live Agent/tests/live -v"""
import pytest

from tf_agent.config import AppSettings
from tf_agent.models.chatgpt_adapter import ChatGPTOAuthAdapter
from tf_agent.models.chatgpt_auth import ChatGptAuth
from tf_agent.models.claude_cli import ClaudeCLIAdapter, ClaudeCliAuth
from tf_agent.models.types import CompletionRequest, Message, ToolSpec

pytestmark = pytest.mark.live

ADD = ToolSpec("add", "Add two integers.", {"type": "object", "properties": {"a": {"type": "integer"},
                                                                          "b": {"type": "integer"}},
                                            "required": ["a", "b"]})
ANSWER = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]}


def claude() -> ClaudeCLIAdapter:
    return ClaudeCLIAdapter(ClaudeCliAuth(AppSettings().claude_bin))


def chatgpt() -> ChatGPTOAuthAdapter:
    settings = AppSettings()
    auth = ChatGptAuth(settings.chatgpt_auth_file)
    if not auth.status()["connected"]:
        pytest.skip("run `uv run tf login chatgpt` first")
    return ChatGPTOAuthAdapter(auth, models_cache=settings.chatgpt_models_cache)


async def test_claude_structured_output_is_isolated():
    resp = await claude().complete(CompletionRequest(
        model="haiku", system="Answer per the schema.", messages=[Message.user("What is 2+2?")],
        output_schema=ANSWER))
    assert resp.structured == {"answer": 4}
    assert resp.usage.input_tokens < 20_000  # D-36: isolated calls stay ~3k, not ~70k


async def test_claude_tool_step():
    resp = await claude().complete(CompletionRequest(
        model="haiku", system="Use the add tool.", messages=[Message.user("Add 2 and 3.")],
        tools=[ADD], require_tool=True))
    assert resp.tool_calls[0].name == "add" and resp.tool_calls[0].arguments == {"a": 2, "b": 3}


async def test_chatgpt_models_and_tool_step():
    adapter = chatgpt()
    visible = [m for m in await adapter.list_models() if not m.hidden]
    assert visible
    model = next((m.model_id for m in visible if m.model_id == "gpt-6-luna"), visible[-1].model_id)
    resp = await adapter.complete(CompletionRequest(
        model=model, system="Use the add tool.", messages=[Message.user("Add 2 and 3.")],
        tools=[ADD], require_tool=True, reasoning_effort="low"))
    assert resp.tool_calls[0].name == "add"
    assert resp.rate is not None and resp.rate.window_minutes in (300, 10080)  # binding window (D-37)


async def test_both_providers_read_a_staged_image(tmp_path):
    """I10: real Claude (`@` staged image from a path with spaces) and real ChatGPT both read image content."""
    import subprocess

    from tf_agent.models.types import ImagePart

    folder = tmp_path / "Trend Finder App"
    folder.mkdir()
    img = folder / "number sheet.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=400x200",
                    "-vf", "drawtext=text='742':fontsize=120:fontcolor=black:x=(w-text_w)/2:y=(h-text_h)/2",
                    "-frames:v", "1", str(img)], check=True)
    schema = {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]}
    ask = Message.user("What number is shown in the image? Answer 0 if you cannot see an image.", [ImagePart(str(img))])
    c = await claude().complete(CompletionRequest(model="haiku", system="Answer per the schema.", messages=[ask],
                                                  output_schema=schema))
    assert c.structured == {"number": 742}
    adapter = chatgpt()
    g = await adapter.complete(CompletionRequest(model="gpt-6-luna", system="Answer per the schema.", messages=[ask],
                                                 output_schema=schema, reasoning_effort="low"))
    assert g.structured == {"number": 742}


async def test_claude_sees_a_large_real_image():
    """The canonical image is ~5 MB; above ~256 KB the CLI drops @-images silently and the model guesses."""
    from pathlib import Path

    from tf_agent.models.types import ImagePart

    img = Path(__file__).resolve().parents[3].parent / "AI Influencers Characters" / "Nicolaiz" / "Nicolaiz.png"
    schema = {"type": "object", "properties": {"blazer_color": {"type": "string"}, "trousers_color": {"type": "string"}},
              "required": ["blazer_color", "trousers_color"]}
    ask = Message.user("Name the man's blazer color and trouser color. Say 'none' if no image is attached.",
                       [ImagePart(str(img))])
    c = await claude().complete(CompletionRequest(model="haiku", system="Answer per the schema.", messages=[ask],
                                                  output_schema=schema))
    blazer, trousers = c.structured["blazer_color"].lower(), c.structured["trousers_color"].lower()
    assert any(w in blazer for w in ("cream", "beige", "ivory", "off-white", "tan", "white")), blazer
    assert "brown" in trousers, trousers
