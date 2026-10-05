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
