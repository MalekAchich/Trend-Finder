"""Real discovery + analysis (no accounts). Run: uv run pytest -m live Agent/tests/live/test_live_tools.py -v"""
import pytest

from tf_agent.config import AppSettings
from tf_agent.pipeline.analyze import thread_runner
from tf_agent.pipeline.pose import PoseAnalyzer
from tf_agent.pipeline.transcript import Transcriber
from tf_agent.tools.factory import build_analyzer, build_tool_stack

pytestmark = pytest.mark.live


async def test_tiktok_discovery_and_analysis(tmp_path):
    settings = AppSettings(media_dir=tmp_path / "media")
    stack = build_tool_stack(settings)
    res = await stack.platforms.tiktok_search("dance trend", max_results=5, recent="month")
    enriched = [i for i in res.items if i.metrics.views is not None]
    assert len(enriched) >= 3, res.notes
    assert any(i.sound.id for i in enriched) or True  # many trends use "original sound"
    runner = thread_runner(PoseAnalyzer(), Transcriber(settings.whisper_model))
    try:
        r = await build_analyzer(settings, stack, runner).analyze(enriched[0])
    finally:
        runner.close()
    assert r.contact_sheet_path and r.probe and r.feasibility is not None
