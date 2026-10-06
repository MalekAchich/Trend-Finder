from pathlib import Path

from typer.testing import CliRunner

from tf_backend.cli import alembic_config, app
from tf_backend.doctor import Check, disk_check, run_checks


def test_help_lists_commands():
    out = CliRunner().invoke(app, ["--help"]).output
    for name in ("doctor", "migrate", "login", "models", "demo-agent", "version"):
        assert name in out


def test_alembic_config_points_at_database_ini():
    ini = Path(alembic_config().config_file_name)
    assert ini.name == "alembic.ini" and ini.parent.name == "Database" and ini.exists()


def test_run_checks_turns_exceptions_into_fail():
    def boom():
        raise RuntimeError("no db")

    checks = run_checks({"ok": lambda: Check("ok", "OK", "fine"), "db": boom})
    assert [c.level for c in checks] == ["OK", "FAIL"]
    assert "RuntimeError" in checks[1].detail


def test_disk_check_levels():
    assert disk_check(3).level == "FAIL"
    assert disk_check(8).level == "WARN"
    assert disk_check(50).level == "OK"


def test_serve_runs_the_api_with_the_built_ui_on_loopback(monkeypatch, tmp_path):
    import uvicorn

    import tf_backend.cli as cli

    calls = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.update(app=app, **kw))
    (tmp_path / "index.html").write_text("x")
    monkeypatch.setattr(cli, "FRONTEND_DIST", tmp_path)
    out = CliRunner().invoke(app, ["serve", "--no-open"])
    assert out.exit_code == 0, out.output
    assert calls["app"] == "tf_backend.main:app" and calls["host"] == "127.0.0.1" and calls["port"] == 8000
    assert "http://127.0.0.1:8000" in out.output


def test_serve_refuses_a_public_host_without_opt_in(monkeypatch):
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    out = CliRunner().invoke(app, ["serve", "--host", "0.0.0.0", "--no-open"])
    assert out.exit_code != 0 and "no login" in out.output


def test_search_check_flags_blocked_engines():
    from tf_backend.doctor import search_check

    ok = search_check({"results": [{}] * 12, "unresponsive_engines": [["brave", "too many requests"]]})
    assert ok.level == "OK" and "12 results" in ok.detail and "brave" in ok.detail
    dead = search_check({"results": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"], ["google", "access denied"]]})
    assert dead.level == "FAIL" and "duckduckgo (CAPTCHA)" in dead.detail and "settings.yml" in dead.detail
