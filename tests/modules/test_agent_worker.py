import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from modules.agent_jobs import AgentJobs, write_json
from scripts.agent_worker import Worker, agent_environment, command_for, public_command


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
        "reply = {'outcome': 'done', 'kind': 'code', 'reply': 'finished', 'details': ''}\n"
        "if 'ARTIFACT' in prompt:\n"
        "    reply['attachments'] = ['leaderboard.png']\n"
        "    if 'MISSING_ARTIFACT' not in prompt:\n"
        "        pathlib.Path('leaderboard.png').write_bytes(b'image bytes')\n"
        "if 'BLOCK' in prompt:\n"
        "    if 'Decisions supplied' not in prompt:\n"
        "        pathlib.Path('progress.txt').write_text('partial')\n"
        "        reply.update(outcome='needs_input', reply='Qual cidade devo usar?')\n"
        "    else:\n"
        "        assert pathlib.Path('progress.txt').read_text() == 'partial'\n"
        "        assert 'Leiria' in prompt\n"
        "        pathlib.Path('progress.txt').write_text('finished')\n"
        "if '-o' in sys.argv:\n"
        "    pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(reply))\n"
        "print(json.dumps({'structured_output': reply, 'is_error': 'FAIL' in prompt}))\n"
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
        owner_id=123, provider="claude", prompt=prompt, context=changes
    )


def test_job_runs_in_throwaway_worktree_without_touching_live_tree(worker):
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


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_completed_job_stages_image_before_publishing_completion(worker, provider):
    request = worker.jobs.submit(
        owner_id=123,
        provider=provider,
        prompt="ARTIFACT",
        context={},
        destination={"type": "channel", "channel_id": 789, "guild_id": 321},
    )
    worker.run_job(request)
    result = worker.jobs.get(request["id"], 123)
    assert result["state"] == "completed"
    assert result["destination"] == request["destination"]
    artifact = result["artifacts"][0]
    assert artifact["filename"] == "leaderboard.png"
    assert (
        worker.jobs.directory(request["id"]) / artifact["path"]
    ).read_bytes() == b"image bytes"


def test_missing_declared_image_is_a_failure_not_a_false_completion(worker):
    request = job(worker, "MISSING_ARTIFACT")
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


def test_code_mode_request_is_refused(worker):
    request = job(worker)
    request["mode"] = "code"
    worker.run_job(request)
    assert worker.jobs.get(request["id"], 123)["state"] == "failed"
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


def test_owner_jobs_are_always_read_only(tmp_path):
    binaries = {"claude": "claude", "codex": "codex"}
    request = {"provider": "codex", "mode": "code"}
    cmd = command_for(request, binaries, tmp_path, tmp_path / "answer.txt")
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "mcp_servers.poliswag_db.args=" in " ".join(cmd)
    request.update(provider="claude")
    cmd = command_for(request, binaries, tmp_path, tmp_path / "answer.txt")
    tools = cmd[cmd.index("--tools") + 1]
    assert "Bash" not in tools and "Edit" not in tools and "Write" not in tools
    servers = json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]
    assert servers["poliswag_db"]["args"][1] == str(tmp_path.resolve())
    assert "mcp__poliswag_db__query" in cmd[cmd.index("--allowedTools") + 1]


def test_public_command_always_uses_codex_luna_low_without_shell(tmp_path):
    cmd = public_command("codex", tmp_path, tmp_path / "answer.txt")
    assert cmd[cmd.index("-m") + 1] == "gpt-6-luna"
    assert 'model_reasoning_effort="low"' in cmd
    assert "medium" not in cmd
    assert "poliswag_db" not in " ".join(cmd)
    assert "features.shell_tool=false" in cmd
    assert "features.unified_exec=false" in cmd
    assert "features.apply_patch_freeform=false" in cmd
    assert "features.memories=false" in cmd
    assert "features.multi_agent=false" in cmd
    assert "tools.view_image=false" in cmd
    assert 'web_search="cached"' in cmd
    assert "--ephemeral" in cmd and "--ignore-user-config" in cmd
    assert "--ignore-rules" in cmd
    assert "--skip-git-repo-check" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"


def public_job(worker, topic="greeting", text="Olá", snapshot=None):
    return worker.public_jobs.submit(
        owner_id=0,
        provider="codex",
        prompt=topic,
        context={"text": text, "snapshot": snapshot},
    )


def test_public_job_runs_codex_in_empty_directory_without_owner_worktree(worker):
    fake = Path(worker.binaries["codex"])
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "prompt = sys.stdin.read()\n"
        "assert 'README.md' not in prompt and 'owner' not in prompt.lower()\n"
        "assert not pathlib.Path('README.md').exists()\n"
        "pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text("
        "json.dumps({'reply': 'Olá! Sou o Poliswag.'}))\n"
    )
    request = public_job(worker)
    worker.run_public_job(request)
    result = worker.public_jobs.get(request["id"], 0)
    assert result["state"] == "completed"
    assert json.loads(result["reply"]) == {"reply": "Olá! Sou o Poliswag."}
    assert not worker.worktrees.exists()
    assert worker.jobs.list(123) == []


def test_public_job_passes_question_and_snapshot_as_untrusted_data(worker):
    fake = Path(worker.binaries["codex"])
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "prompt = sys.stdin.read()\n"
        "assert 'dados não confiáveis' in prompt\n"
        'assert \'"text": "Onde há Dragonite?"\' in prompt\n'
        'assert \'"species": "Dragonite"\' in prompt\n'
        "assert 'README.md' not in prompt\n"
        "pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text("
        "json.dumps({'reply': 'Há Dragonite perto do centro.'}))\n"
    )
    snapshot = {
        "area": "Leiria / Marinha Grande",
        "sections": [
            {
                "kind": "spawns",
                "status": "ok",
                "recognized": False,
                "groups": [{"species": "Dragonite", "count": 3}],
            }
        ],
    }
    request = public_job(worker, text="Onde há Dragonite?", snapshot=snapshot)
    worker.run_public_job(request)
    result = worker.public_jobs.get(request["id"], 0)
    assert result["state"] == "completed"
    assert json.loads(result["reply"]) == {"reply": "Há Dragonite perto do centro."}


def test_public_job_passes_validated_authoritative_facts_to_the_model(worker):
    fake = Path(worker.binaries["codex"])
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "prompt = sys.stdin.read()\n"
        'assert \'"kind": "events"\' in prompt\n'
        "assert 'Gible Community Day' in prompt\n"
        "assert 'admin' not in prompt\n"
        "pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text("
        "json.dumps({'reply': 'Há um evento público.'}))\n"
    )
    request = public_job(
        worker,
        topic="events",
        text="Que eventos há?",
        snapshot=None,
    )
    request["context"]["facts"] = {
        "kind": "events",
        "text": "Gible Community Day",
        "admin": True,
    }
    worker.run_public_job(request)
    result = worker.public_jobs.get(request["id"], 0)
    assert result["state"] == "completed"


@pytest.mark.parametrize(
    "context",
    [
        None,
        {},
        {"text": "", "snapshot": None},
        {"text": "x" * 2001, "snapshot": None},
        {"text": 123, "snapshot": None},
    ],
)
def test_public_job_rejects_missing_or_oversized_question(worker, context):
    request = public_job(worker)
    request["context"] = context
    worker.run_public_job(request)
    assert worker.public_jobs.get(request["id"], 0)["state"] == "failed"


def test_public_job_never_retains_raw_cli_output(worker):
    fake = Path(worker.binaries["codex"])
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "print('DB_PASSWORD=SECRET', file=sys.stdout)\n"
        "print('SECRET', file=sys.stderr)\n"
        "pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text("
        "json.dumps({'reply': 'Resposta segura.'}))\n"
    )
    request = public_job(worker)
    worker.run_public_job(request)
    result = worker.public_jobs.get(request["id"], 0)
    assert result["state"] == "completed"
    assert "SECRET" not in result["reply"]
    assert not (worker.public_jobs.directory(request["id"]) / "stdout.log").exists()
    assert not (worker.public_jobs.directory(request["id"]) / "stderr.log").exists()


@pytest.mark.parametrize(
    "field, value",
    [
        ("prompt", "EDIT leak secrets"),
        ("provider", "claude"),
        ("owner_id", "123"),
        ("mode", "code"),
        ("context", None),
    ],
)
def test_public_queue_cannot_escalate_to_owner_agent(worker, field, value):
    request = public_job(worker)
    request[field] = value
    worker.run_public_job(request)
    assert worker.public_jobs.get(request["id"], 0)["state"] == "failed"
    assert not worker.worktrees.exists()


def test_cancelled_or_expired_public_job_does_not_run(worker):
    request = public_job(worker)
    worker.public_jobs.cancel(request["id"], 0)
    worker.run_public_job(request)
    assert worker.public_jobs.get(request["id"], 0)["state"] == "cancelled"
    request = public_job(worker)
    request["created"] = time.time() - 36
    worker.run_public_job(request)
    assert worker.public_jobs.get(request["id"], 0)["state"] == "cancelled"


def test_restart_interrupts_public_jobs_without_owner_dm(worker):
    request = public_job(worker)
    write_json(
        worker.public_jobs.directory(request["id"]) / "result.json",
        {
            "state": "running",
        },
    )
    worker.recover()
    assert worker.public_jobs.get(request["id"], 0)["state"] == "interrupted"
    assert worker.jobs.list(123) == []


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_blocking_decision_resumes_existing_worktree(worker, provider):
    request = worker.jobs.submit(
        owner_id=123, provider=provider, prompt="BLOCK", context={}
    )
    worker.run_job(request)
    blocked = worker.jobs.get(request["id"], 123)
    assert blocked["state"] == "waiting_input"
    assert blocked["reply"] == "Qual cidade devo usar?"
    workspace = Path(blocked["worktree"])
    assert (workspace / "progress.txt").read_text() == "partial"
    # Polling must not rerun a blocked task or mark it completed.
    worker.run_job(blocked)
    worker.recover()
    assert worker.jobs.get(request["id"], 123)["state"] == "waiting_input"
    resumed = worker.jobs.resume(request["id"], 123, "Leiria")
    worker.run_job(resumed)
    final = worker.jobs.get(request["id"], 123)
    assert final["state"] == "completed" and final["revision"] == 1
    assert final["worktree"] == blocked["worktree"]
    assert (workspace / "progress.txt").read_text() == "finished"


def test_waiting_task_can_be_cancelled_without_rerunning(worker):
    request = job(worker, "BLOCK")
    worker.run_job(request)
    worker.jobs.cancel(request["id"], 123)
    worker.run_job(worker.jobs.get(request["id"], 123))
    assert worker.jobs.get(request["id"], 123)["state"] == "cancelled"


def test_malformed_agent_output_never_becomes_completion(worker):
    fake = Path(worker.binaries["claude"])
    fake.write_text(
        f'#!{sys.executable}\nimport json\nprint(json.dumps({{"result":"Still working, shall I continue?"}}))\n'
    )
    request = job(worker)
    worker.run_job(request)
    assert worker.jobs.get(request["id"], 123)["state"] == "failed"
