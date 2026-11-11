"""scripts/watchdog.sh, run for real against a fake docker, logger and notifier."""

import os
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "watchdog.sh"

FAKE_DOCKER = """#!/usr/bin/env bash
if [ "$1" = inspect ]; then
  [ "$FAKE_ID" = none ] && exit 1
  case "$3" in
    *RestartCount*) echo "$FAKE_RESTARTS" ;;
    *Running*) echo "$FAKE_RUNNING" ;;
    *StartedAt*) echo "$FAKE_STARTED" ;;
    *Id*) echo "$FAKE_ID" ;;
  esac
  exit 0
fi
[ "$1" = restart ] && echo "restart $2" >> "$FAKE_CALLS"
exit 0
"""
FAKE_LOGGER = '#!/usr/bin/env bash\necho "log $*" >> "$FAKE_CALLS"\n'
# Stands in for scripts/notify_owner.py: records the alert, fails on demand.
FAKE_NOTIFY = """import os, sys
with open(os.environ["FAKE_CALLS"], "a") as calls:
    calls.write("alert " + " | ".join(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("FAKE_NOTIFY_EXIT", "0")))
"""


@pytest.fixture
def run(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("logger", FAKE_LOGGER)):
        (fake_bin / name).write_text(body)
        (fake_bin / name).chmod(0o755)
    root = tmp_path / "root"
    (root / "scripts").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "scripts" / "notify_owner.py").write_text(FAKE_NOTIFY)
    calls = tmp_path / "calls"

    def _run(
        *,
        started_ago=3600,
        beat_ago=30,
        restarts=0,
        running="true",
        container="abc123",
        notify_exit=0,
    ):
        now = time.time()
        if beat_ago is not None:
            heartbeat = root / "logs" / "heartbeat"
            heartbeat.write_text("x")
            os.utime(heartbeat, (now - beat_ago, now - beat_ago))
        calls.write_text("")
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "POLISWAG_ROOT": str(root),
            "FAKE_CALLS": str(calls),
            "FAKE_ID": container,
            "FAKE_RESTARTS": str(restarts),
            "FAKE_RUNNING": running,
            "FAKE_STARTED": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - started_ago)
            ),
            "FAKE_NOTIFY_EXIT": str(notify_exit),
        }
        done = subprocess.run(
            ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
        )
        assert done.returncode == 0, done.stderr
        return calls.read_text().splitlines()

    _run.root = root
    return _run


class TestHang:
    def test_a_bot_that_passes_is_left_alone(self, run):
        assert run(started_ago=3600, beat_ago=30) == []

    def test_a_stale_heartbeat_restarts_it_and_tells_the_owner(self, run):
        calls = run(started_ago=3600, beat_ago=700)
        assert "restart poliswag" in calls
        alert = next(line for line in calls if line.startswith("alert "))
        assert "--discord" in alert and "O Poliswag parou" in alert
        assert "sem tick há 11 min" in alert

    def test_no_restart_in_the_first_ten_minutes_after_a_start(self, run):
        assert run(started_ago=300, beat_ago=700) == []

    def test_a_stopped_container_is_not_ours(self, run):
        assert run(running="false", beat_ago=700) == []

    def test_no_container_at_all_is_not_ours(self, run):
        assert run(container="none", beat_ago=700) == []

    def test_a_failed_alert_still_restarts(self, run):
        calls = run(started_ago=3600, beat_ago=700, notify_exit=1)
        assert "restart poliswag" in calls
        assert any("could not alert the owner" in line for line in calls)


class TestCrashLoop:
    def test_docker_restarting_it_over_and_over_tells_the_owner(self, run):
        # Between two crashes: not running, last start a moment ago, and the
        # heartbeat on disk is from the process before the loop.
        calls = run(restarts=7, running="false", started_ago=20, beat_ago=300)
        alert = next(line for line in calls if line.startswith("alert "))
        assert "O Poliswag não arranca" in alert and "7 vezes" in alert
        assert "make deploy" in alert
        # Restarting cannot fix a crash on start.
        assert not any(line.startswith("restart") for line in calls)

    def test_it_is_told_once_per_container(self, run):
        run(restarts=7, running="false", started_ago=20, beat_ago=300)
        again = run(restarts=12, running="false", started_ago=20, beat_ago=900)
        assert again == []

    def test_a_new_container_is_told_again(self, run):
        run(restarts=7, running="false", started_ago=20, beat_ago=300)
        calls = run(
            restarts=3,
            running="false",
            started_ago=20,
            beat_ago=900,
            container="def456",
        )
        assert any(line.startswith("alert ") for line in calls)

    def test_an_alert_that_failed_is_tried_again(self, run):
        first = run(
            restarts=7, running="false", started_ago=20, beat_ago=300, notify_exit=1
        )
        assert any("trying again next minute" in line for line in first)
        second = run(restarts=8, running="false", started_ago=20, beat_ago=360)
        assert any(line.startswith("alert ") for line in second)

    def test_a_start_under_two_minutes_old_gets_the_benefit_of_the_doubt(self, run):
        assert run(restarts=4, running="true", started_ago=40, beat_ago=300) == []

    def test_up_two_minutes_without_a_pass_counts_as_not_starting(self, run):
        calls = run(restarts=4, running="true", started_ago=150, beat_ago=300)
        assert any(line.startswith("alert ") for line in calls)

    def test_a_bot_that_came_up_after_all_is_not_reported(self, run):
        # Three restarts while the database was coming up, then a pass.
        assert run(restarts=3, running="true", started_ago=90, beat_ago=30) == []

    def test_and_a_later_loop_in_the_same_container_is_told_again(self, run):
        run(restarts=7, running="false", started_ago=20, beat_ago=300)
        run(restarts=7, running="true", started_ago=90, beat_ago=30)  # recovered
        calls = run(restarts=11, running="false", started_ago=20, beat_ago=300)
        assert any(line.startswith("alert ") for line in calls)

    def test_two_restarts_are_not_a_loop_yet(self, run):
        assert run(restarts=2, running="false", started_ago=20, beat_ago=300) == []

    def test_no_heartbeat_file_at_all_still_counts(self, run):
        calls = run(restarts=5, running="false", started_ago=20, beat_ago=None)
        assert any(line.startswith("alert ") for line in calls)
