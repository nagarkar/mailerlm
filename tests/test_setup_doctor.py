"""setup and doctor: the pointer lives only in the managed block, backups and diffs, idempotent."""

import pytest

from mailerlm import api, paths


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / "env" / ".env"
    path.parent.mkdir()
    return path


def run_setup(env_file, tmp_path, **kwargs):
    return api.setup(env_file=env_file, workspace_pointer=str(tmp_path / "ws"), **kwargs)


def test_dry_run_writes_nothing(env_file, tmp_path):
    board = run_setup(env_file, tmp_path, dry_run=True)
    assert not env_file.exists() and not (tmp_path / "ws").exists()
    assert board["env_diff"]


def test_setup_writes_the_block_creates_workspace_and_database(env_file, tmp_path):
    env_file.write_text("RESEND_API_KEY=abc\n")
    board = run_setup(env_file, tmp_path)
    text = env_file.read_text()
    assert "RESEND_API_KEY=abc" in text
    assert f"{paths.BLOCK_BEGIN}\nMAILERLM_WORKSPACE={tmp_path / 'ws'}\n{paths.BLOCK_END}" in text
    assert (tmp_path / "ws" / paths.DATABASE_FILENAME).exists()
    backup = tmp_path / "state" / "mailerlm" / "env-backups" / board["backup"].split("/")[-1]
    assert backup.exists() and not list(env_file.parent.glob(".env.bak.*"))


def test_a_second_run_changes_nothing(env_file, tmp_path):
    run_setup(env_file, tmp_path)
    before = env_file.read_text()
    board = run_setup(env_file, tmp_path)
    assert env_file.read_text() == before and board["changed"] is False
    assert board["env"] == "unchanged"


def test_a_pointer_set_outside_the_block_is_left_alone(env_file, tmp_path):
    env_file.write_text(f"MAILERLM_WORKSPACE={tmp_path / 'mine'}\n")
    board = run_setup(env_file, tmp_path)
    assert env_file.read_text() == f"MAILERLM_WORKSPACE={tmp_path / 'mine'}\n"
    assert (tmp_path / "mine" / paths.DATABASE_FILENAME).exists()
    assert "outside" in board["env"]


def test_doctor_fails_without_a_workspace_and_names_the_fix():
    board = api.doctor(web_app_health=lambda: None)
    assert board["ok"] is False
    failed = [c for c in board["checks"] if c["state"] == "fail"]
    assert failed and "mailerlm setup" in failed[0]["fix"]


def test_doctor_passes_after_setup(env_file, tmp_path, monkeypatch):
    run_setup(env_file, tmp_path)
    monkeypatch.setenv("MAILERLM_WORKSPACE", str(tmp_path / "ws"))
    board = api.doctor(config_file=tmp_path / "none.toml", web_app_health=lambda: None)
    assert board["ok"] is True
    assert any(
        c["name"] == "env file" and "chosen by MAILERLM_ENV" in c["detail"] for c in board["checks"]
    )


def test_an_exported_workspace_pointer_wins_over_the_default(env_file, tmp_path, monkeypatch):
    monkeypatch.setenv("MAILERLM_WORKSPACE", str(tmp_path / "exported"))
    api.setup(env_file=env_file)
    assert f"MAILERLM_WORKSPACE={tmp_path / 'exported'}" in env_file.read_text()
    assert (tmp_path / "exported" / paths.DATABASE_FILENAME).exists()


def test_doctor_reports_a_web_app_older_than_the_code(monkeypatch):
    from datetime import datetime, timedelta

    monkeypatch.setattr(
        api, "_newest_source_commit", lambda: datetime.fromisoformat("2026-10-11T12:00:00+00:00")
    )
    stale = {
        "started_at": (
            datetime.fromisoformat("2026-10-11T12:00:00+00:00") - timedelta(hours=1)
        ).isoformat()
    }
    web = next(
        c for c in api.doctor(web_app_health=lambda: stale)["checks"] if c["name"] == "web app"
    )
    assert (
        web["state"] == "fail" and "older than HEAD" in web["detail"] and "kickstart" in web["fix"]
    )
    fresh = {"started_at": "2026-10-11T13:00:00+00:00"}
    web = next(
        c for c in api.doctor(web_app_health=lambda: fresh)["checks"] if c["name"] == "web app"
    )
    assert web["state"] == "ok"
