from typer.testing import CliRunner

from tf_backend.cli import app


def test_search_and_analyze_commands_exist():
    out = CliRunner().invoke(app, ["--help"]).output
    assert "search" in out and "analyze" in out


def test_search_rejects_unknown_platform():
    result = CliRunner().invoke(app, ["search", "myspace", "deadpan"])
    assert result.exit_code == 2 and "tiktok, instagram, shorts" in result.output
