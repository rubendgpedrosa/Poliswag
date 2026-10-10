import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from modules.agent_jobs import AgentJobs, write_json
from scripts.agent_worker import Worker, agent_environment, command_for


@pytest.fixture
def worker(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    (repo / "README.md").write_text("original\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Tests",
            "-c",
            "user.email=tests@example.com",
            "commit",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )
    fake = tmp_path / "fake-agent"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys, time\n"
        "prompt = sys.stdin.read()\n"
        "if 'SLEEP' in prompt: time.sleep(20)\n"
        "if 'EDIT' in prompt: pathlib.Path('README.md').write_text('changed')\n"
        "print(json.dumps({'result': 'finished', 'is_error': 'FAIL' in prompt}))\n"
    )
    fake.chmod(0o700)
    runner = Worker(
        queue=tmp_path / "queue",
        repo=repo,
        worktrees=tmp_path / "worktrees",
        owner_id=123,
        timeout=2,
        binaries={"claude": str(fake), "codex": str(fake)},
    )
    runner.jobs.root.mkdir()
    runner.heartbeat()
    return runner


def job(worker, prompt="hello", **changes):
    return worker.jobs.submit(
        owner_id=123, provider="claude", mode="code", prompt=prompt, context=changes
    )


def test_code_job_edits_worktree_without_touching_live_tree(worker):
    request = job(worker, "EDIT")
    worker.run_job(request)
    result = worker.jobs.get(request["id"], 123)
    assert result["state"] == "completed"
    assert Path(result["worktree"]).joinpath("README.md").read_text() == "changed"
    assert (worker.repo / "README.md").read_text() == "original\n"
    assert (
        worker.jobs.directory(request["id"]) / "answer.txt"
    ).read_text() == "finished"


def test_nonzero_payload_error_is_failure_even_with_exit_zero(worker):
    request = job(worker, "FAIL")
    worker.run_job(request)
    assert worker.jobs.get(request["id"], 123)["state"] == "failed"


def test_cancel_queued_job_does_not_start_agent(worker):
    request = job(worker)
    worker.jobs.cancel(request["id"], 123)
    worker.run_job(request)
    assert worker.jobs.get(request["id"], 123)["state"] == "cancelled"
    assert not worker.worktrees.exists()


def test_timeout_stops_agent_and_preserves_worktree(worker):
    request = job(worker, "SLEEP")
    worker.timeout = 0.05
    started = time.monotonic()
    worker.run_job(request)
    result = worker.jobs.get(request["id"], 123)
    assert result["state"] == "failed"
    assert time.monotonic() - started < 5
    assert Path(result["worktree"]).exists()


def test_cancellation_stops_running_process(worker):
    request = job(worker, "SLEEP")
    timer = threading.Timer(0.2, worker.jobs.cancel, args=(request["id"], 123))
    timer.start()
    try:
        worker.run_job(request)
    finally:
        timer.join()
    result = worker.jobs.get(request["id"], 123)
    assert result["state"] == "cancelled"
    assert Path(result["worktree"]).exists()


def test_restart_marks_running_job_interrupted_without_replay(worker):
    request = job(worker)
    write_json(
        worker.jobs.directory(request["id"]) / "result.json", {"state": "running"}
    )
    worker.recover()
    result = AgentJobs(worker.jobs.root).get(request["id"], 123)
    assert result["state"] == "interrupted"
    assert not worker.worktrees.exists()


def test_forged_owner_cannot_execute(worker):
    request = job(worker)
    request["owner_id"] = "999"
    worker.run_job(request)
    assert worker.jobs.get(request["id"], 123)["state"] == "failed"
    assert not worker.worktrees.exists()


def test_bot_credentials_do_not_enter_agent_environment(monkeypatch):
    monkeypatch.setenv("DISCORD_API_KEY", "secret")
    monkeypatch.setenv("DB_PASSWORD", "secret")
    monkeypatch.setenv("CODEX_THREAD_ID", "parent")
    assert (
        not {"DISCORD_API_KEY", "DB_PASSWORD", "CODEX_THREAD_ID"}
        & agent_environment().keys()
    )


def test_provider_modes_use_supported_headless_flags(tmp_path):
    binaries = {"claude": "claude", "codex": "codex"}
    request = {"provider": "codex", "mode": "ask"}
    cmd = command_for(request, binaries, tmp_path, tmp_path / "answer.txt")
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    request.update(provider="claude")
    cmd = command_for(request, binaries, tmp_path, tmp_path / "answer.txt")
    tools = cmd[cmd.index("--tools") + 1]
    assert "Bash" not in tools and "Edit" not in tools and "Write" not in tools
    assert json.loads(cmd[cmd.index("--mcp-config") + 1]) == {"mcpServers": {}}
