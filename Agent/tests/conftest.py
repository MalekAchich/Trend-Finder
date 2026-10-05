import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _envelope(structured=None, result="", is_error=False, subtype="success", api_error_status=None,
              model="claude-opus-5-5"):
    return {
        "type": "result", "subtype": subtype, "is_error": is_error, "api_error_status": api_error_status,
        "result": result, "structured_output": structured,
        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 0,
                  "output_tokens": 7},
        "modelUsage": {model: {}},
    }


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "claude"
    shutil.copy(FIXTURES / "fake_claude.py", script)
    script.chmod(0o755)
    log = tmp_path / "claude-calls.jsonl"
    response = tmp_path / "claude-response.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    def respond(payload, exit_code=0, sleep=0.0):
        response.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        monkeypatch.setenv("FAKE_CLAUDE_RESPONSE", str(response))
        monkeypatch.setenv("FAKE_CLAUDE_EXIT", str(exit_code))
        monkeypatch.setenv("FAKE_CLAUDE_SLEEP", str(sleep))

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return SimpleNamespace(bin=str(script), respond=respond, calls=calls, envelope=_envelope, tmp=tmp_path)
