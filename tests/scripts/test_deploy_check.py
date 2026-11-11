"""scripts/deploy_check.sh, run for real against a fake `docker` on PATH."""

import os
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy_check.sh"

# Answers the three `docker inspect -f <format> <name>` questions the script
# asks from environment variables, and prints a marker for `docker logs`.
FAKE_DOCKER = """#!/usr/bin/env bash
if [ "$1" = inspect ]; then
  [ "$4" = "$FAKE_NAME" ] || exit 1
  case "$3" in
    *Id*) echo abc123 ;;
    *RestartCount*) echo "$FAKE_RESTARTS" ;;
    *Running*) echo "$FAKE_RUNNING" ;;
    *StartedAt*) echo "$FAKE_STARTED" ;;
  esac
  exit 0
fi
[ "$1" = logs ] && echo "Traceback: the fake container log"
exit 0
"""


@pytest.fixture
def run(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    heartbeat = tmp_path / "heartbeat"

    def _run(*, started_ago=30, beat_ago=None, restarts=0, running="true", name="bot"):
        now = time.time()
        if beat_ago is not None:
            heartbeat.write_text("x")
            os.utime(heartbeat, (now - beat_ago, now - beat_ago))
        started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - started_ago))
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "HEARTBEAT_FILE": str(heartbeat),
            "DEPLOY_WAIT_SECONDS": "1",
            "FAKE_NAME": "bot",
            "FAKE_RESTARTS": str(restarts),
            "FAKE_RUNNING": running,
            "FAKE_STARTED": started,
        }
        return subprocess.run(
            ["bash", str(SCRIPT), name],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    return _run


def test_passes_once_the_heartbeat_is_newer_than_the_start(run):
    done = run(started_ago=30, beat_ago=5)
    assert done.returncode == 0
    assert "is up and its scheduler is passing" in done.stdout


def test_a_heartbeat_from_before_the_start_does_not_count(run):
    # The previous process's last beat is still on disk right after a restart.
    done = run(started_ago=30, beat_ago=90)
    assert done.returncode == 1
    assert "no scheduler heartbeat" in done.stderr
    assert "the fake container log" in done.stderr


def test_no_heartbeat_file_at_all_fails_after_the_wait(run):
    done = run(started_ago=30, beat_ago=None)
    assert done.returncode == 1
    assert "no scheduler heartbeat" in done.stderr


def test_a_restart_by_docker_is_a_crash_on_start(run):
    # Even with a fresh heartbeat: the count says the bot fell over first.
    done = run(started_ago=30, beat_ago=5, restarts=3)
    assert done.returncode == 1
    assert "crashed while starting" in done.stderr and "3 time(s)" in done.stderr
    assert "the fake container log" in done.stderr


def test_a_stopped_container_fails(run):
    done = run(running="false", beat_ago=5)
    assert done.returncode == 1
    assert "not running" in done.stderr


def test_an_unknown_container_fails_at_once(run):
    done = run(name="nosuch", beat_ago=5)
    assert done.returncode == 1
    assert "no container named nosuch" in done.stderr
