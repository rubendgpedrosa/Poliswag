import json
import sys
import time
from pathlib import Path

import pytest

import scripts.agent_worker as agent_worker
from modules.agent_jobs import write_json
from scripts.agent_worker import (
    Worker,
    agent_environment,
    public_claude_command,
    public_claude_environment,
    public_claude_reply,
    public_command,
    public_provider,
)


@pytest.fixture
def worker(tmp_path):
    # Each test replaces the fake CLI's body with what it needs to check.
    fake = tmp_path / "fake-agent"
    fake.write_text(f"#!{sys.executable}\n")
    fake.chmod(0o700)
    runner = Worker(
        queue=tmp_path / "queue",
        timeout=2,
        binaries={"claude": str(fake), "codex": str(fake)},
    )
    runner.root.mkdir()
    runner.heartbeat()
    return runner


def only_public_queue_touched(worker):
    """A public job writes nothing outside the public queue: no worktree,
    no other job directory, no copy of the status elsewhere."""
    return [path.name for path in worker.root.iterdir()] == ["public"]


def test_bot_credentials_do_not_enter_agent_environment(monkeypatch):
    monkeypatch.setenv("DISCORD_API_KEY", "secret")
    monkeypatch.setenv("DB_PASSWORD", "secret")
    monkeypatch.setenv("CODEX_THREAD_ID", "parent")
    assert (
        not {"DISCORD_API_KEY", "DB_PASSWORD", "CODEX_THREAD_ID"}
        & agent_environment().keys()
    )


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
    assert only_public_queue_touched(worker)


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
    assert only_public_queue_touched(worker)


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
    assert only_public_queue_touched(worker)


def test_public_claude_command_is_sonnet_medium_with_web_search_only(tmp_path):
    cmd = public_claude_command("claude", tmp_path)
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5-5"
    assert cmd[cmd.index("--effort") + 1] == "medium"
    assert cmd[cmd.index("--tools") + 1] == "WebSearch,WebFetch"
    allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
    assert allowed[0] == "WebSearch"
    assert "WebFetch(domain:leekduck.com)" in allowed
    # WebFetch only ever with a domain: never a bare, unrestricted grant
    assert all(a == "WebSearch" or a.startswith("WebFetch(domain:") for a in allowed)
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd
    assert "--disable-slash-commands" in cmd
    assert cmd[cmd.index("--system-prompt-file") + 1] == str(
        tmp_path / "instructions.txt"
    )
    assert "poliswag_db" not in " ".join(cmd)


@pytest.mark.parametrize(
    "value, expected",
    [(None, "codex"), ("claude", "claude"), (" Claude ", "claude"), ("x", "codex")],
)
def test_public_provider_defaults_to_codex(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("POLISWAG_PUBLIC_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("POLISWAG_PUBLIC_PROVIDER", value)
    assert public_provider() == expected


def test_public_claude_reply_keeps_only_the_reply():
    ok = json.dumps({"is_error": False, "structured_output": {"reply": "Olá!"}})
    assert json.loads(public_claude_reply(ok)) == {"reply": "Olá!"}
    assert public_claude_reply(json.dumps({"is_error": True, "result": "x"})) is None
    assert public_claude_reply(json.dumps({"structured_output": {"r": 1}})) is None
    assert public_claude_reply("not json") is None


def test_public_job_runs_claude_when_selected(worker, monkeypatch):
    monkeypatch.setenv("POLISWAG_PUBLIC_PROVIDER", "claude")
    fake = Path(worker.binaries["claude"])
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "prompt = sys.stdin.read()\n"
        "assert 'dados não confiáveis' in prompt\n"
        "assert '--model' in sys.argv and 'claude-sonnet-5-5' in sys.argv\n"
        "assert pathlib.Path('instructions.txt').exists()\n"
        "print('SECRET', file=sys.stderr)\n"
        "print(json.dumps({'is_error': False, 'result': 'SECRET',"
        " 'structured_output': {'reply': 'Poli-poli!'}}))\n"
    )
    request = public_job(worker)
    worker.run_public_job(request)
    result = worker.public_jobs.get(request["id"], 0)
    assert result["state"] == "completed"
    assert json.loads(result["reply"]) == {"reply": "Poli-poli!"}
    assert "SECRET" not in result["reply"]


def test_public_key_is_used_only_for_public_claude_runs(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "owner-login")
    monkeypatch.setenv("POLISWAG_PUBLIC_ANTHROPIC_API_KEY", "public-key")
    monkeypatch.setenv("POLISWAG_PUBLIC_ANTHROPIC_WORKSPACE", "wrkspc_1")
    env = public_claude_environment()
    assert env["ANTHROPIC_API_KEY"] == "public-key"
    assert env["ANTHROPIC_CUSTOM_HEADERS"] == "anthropic-workspace-id: wrkspc_1"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    owner = agent_environment()
    assert owner.get("CLAUDE_CODE_OAUTH_TOKEN") == "owner-login"
    assert "public-key" not in owner.values()
    assert not any(k.startswith("POLISWAG_PUBLIC") for k in owner)


def test_heartbeat_reports_what_the_bot_reads(worker):
    status = worker.public_jobs.worker_status()
    assert status["online"] and status["active"] is None
    assert status["providers"] == {"claude": True, "codex": True}


def test_starts_with_the_installed_units_command_line(monkeypatch, tmp_path):
    # poliswag-agent.service runs: agent_worker.py --owner-id <id>
    started = []
    monkeypatch.setattr(
        Worker, "run", lambda self: started.append((self.root, self.timeout))
    )
    monkeypatch.setattr(agent_worker.signal, "signal", lambda *_: None)
    monkeypatch.setattr(agent_worker.os, "umask", lambda _: 0o022)
    monkeypatch.setattr(
        sys,
        "argv",
        ["agent_worker.py", "--owner-id", "123", "--queue", str(tmp_path / "q")],
    )
    agent_worker.main()
    assert started == [(tmp_path / "q", 1200)]
