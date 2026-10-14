"""The scheduled reply check, run against stub commands (never the real job)."""

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "replies-check.sh"


def stub(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)
    return path


@pytest.fixture
def stubs(tmp_path):
    marks = tmp_path / "marks"
    marks.mkdir()
    bin = stub(
        tmp_path / "mailerlm",
        f'echo "$1 $2" >> {marks}/calls\nvar="STUB_EXIT_$(echo "$1" | tr a-z A-Z)"\nexit "${{!var:-0}}"\n',
    )
    notify = stub(tmp_path / "notify", f'echo "$@" >> {marks}/alerts\n')
    env = {
        **os.environ,
        "MAILERLM_BIN": str(bin),
        "SUPERLM_NOTIFY": str(notify),
        "CAFFEINATE": str(
            stub(tmp_path / "caffeinate", 'shift 2\nexec "$@"\n')
        ),  # the real one holds the Mac awake, then runs the command
        "MAILERLM_LOG_DIR": str(tmp_path / "logs"),
        "HOME": str(tmp_path),
    }
    env.pop("MAILERLM_CAFFEINATED", None)
    return env, marks, tmp_path


def run(env, **extra):
    return subprocess.run(
        [str(SCRIPT)], env={**env, **extra}, capture_output=True, text=True, timeout=30
    )


def test_a_clean_run_is_silent_and_runs_both_steps(stubs):
    env, marks, tmp = stubs
    assert run(env).returncode == 0
    assert (marks / "calls").read_text().split("\n")[:2] == ["doctor ", "replies check"]
    assert not (marks / "alerts").exists()
    assert len(list((tmp / "logs").glob("replies-check-*.log"))) == 1


def test_a_failed_step_is_alerted_once_and_the_other_step_still_runs(stubs):
    env, marks, _ = stubs
    run(env, STUB_EXIT_DOCTOR="1")
    assert "replies check" in (marks / "calls").read_text()
    alerts = (marks / "alerts").read_text().strip().split("\n")
    assert (
        len(alerts) == 1
        and "doctor" in alerts[0]
        and "replies-check" not in alerts[0].split("Failed:")[1].split(".")[0]
    )


def test_both_failing_is_still_one_alert_naming_both(stubs):
    env, marks, _ = stubs
    run(env, STUB_EXIT_DOCTOR="1", STUB_EXIT_REPLIES="1")
    alerts = (marks / "alerts").read_text().strip().split("\n")
    assert len(alerts) == 1 and "doctor" in alerts[0] and "replies-check" in alerts[0]


def test_a_killed_run_says_the_run_itself_failed(stubs):
    env, marks, tmp = stubs
    stub(tmp / "mailerlm", "sleep 30\n")
    process = subprocess.Popen(
        [str(SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    time.sleep(1.5)
    os.killpg(process.pid, signal.SIGTERM)  # launchd stops a job's whole process group
    process.wait(timeout=20)
    time.sleep(0.5)
    assert "the run itself" in (marks / "alerts").read_text()
