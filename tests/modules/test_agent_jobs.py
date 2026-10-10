import time

import pytest

from modules.agent_jobs import AgentJobs, parse_direct_request, write_json


@pytest.fixture
def queue(tmp_path):
    jobs = AgentJobs(tmp_path / "queue")
    jobs.root.mkdir()
    write_json(
        jobs.root / "worker.json",
        {"heartbeat": time.time(), "providers": {"claude": True, "codex": True}},
    )
    return jobs


def submit(queue, **changes):
    args = dict(owner_id=123, provider="openai", mode="code", prompt="fix", context={})
    return queue.submit(**{**args, **changes})


def test_durable_request_and_owner_isolation(queue):
    job = submit(queue)
    assert job["provider"] == "codex"
    assert AgentJobs(queue.root).get(job["id"], 123)["state"] == "queued"
    with pytest.raises(ValueError, match="não encontrada"):
        queue.get(job["id"], 999)
    with pytest.raises(ValueError, match="não encontrada"):
        queue.cancel(job["id"], 999)
    with pytest.raises(ValueError):
        queue.get("../../.env", 123)


def test_no_silent_enqueue_when_worker_offline(queue):
    write_json(queue.root / "worker.json", {"heartbeat": time.time() - 31})
    with pytest.raises(ValueError, match="offline"):
        submit(queue)
    assert not queue.list(123)


def test_queue_limit_and_cancel_preserve_request(queue):
    jobs = [submit(queue) for _ in range(5)]
    with pytest.raises(ValueError, match="5 tarefas"):
        submit(queue)
    queue.cancel(jobs[0]["id"], 123)
    assert (queue.directory(jobs[0]["id"]) / "cancel").exists()
    assert queue.get(jobs[0]["id"], 123)["prompt"] == "fix"


@pytest.mark.parametrize("prompt", [" ", "x" * 12001])
def test_invalid_prompt_never_enqueued(queue, prompt):
    with pytest.raises(ValueError):
        submit(queue, prompt=prompt)
    assert not queue.list(123)


@pytest.mark.parametrize(
    "content, expected",
    [
        ("Claude what broke?", ("ask", "claude", "what broke?")),
        ("code openai fix the bug", ("code", "openai", "fix the bug")),
        ("ask codex explain it", ("ask", "codex", "explain it")),
        ("restart the scanner", None),
        ("claude", None),
        ("!questleiria", None),
    ],
)
def test_only_explicit_provider_requests_are_routed(content, expected):
    assert parse_direct_request(content) == expected
