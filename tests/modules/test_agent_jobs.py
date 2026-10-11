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


def waiting_job(queue):
    job = submit(queue)
    write_json(
        queue.directory(job["id"]) / "result.json",
        {
            "state": "waiting_input",
            "reply": "Qual cidade devo usar?",
            "revision": 0,
        },
    )
    queue.notification_marker(job["id"]).touch()
    return job


def test_decision_resumes_same_job_without_modifying_original_request(queue):
    job = waiting_job(queue)
    resumed = queue.resume(job["id"], 123, "Leiria")
    assert resumed["state"] == "queued" and resumed["revision"] == 1
    assert not resumed["notified"]
    assert resumed["prompt"] == "fix"
    assert resumed["provider"] == "codex" and resumed["mode"] == "code"
    assert resumed["turns"] == [
        {"question": "Qual cidade devo usar?", "answer": "Leiria"}
    ]
    assert len(queue.list(123)) == 1
    queue.notification_marker(job["id"], 1).touch()
    assert queue.get(job["id"], 123)["notified"]


def test_only_owner_can_answer_a_waiting_job(queue):
    job = waiting_job(queue)
    with pytest.raises(ValueError, match="não encontrada"):
        queue.resume(job["id"], 999, "Leiria")
    assert queue.get(job["id"], 123)["state"] == "waiting_input"


def test_pending_and_completed_jobs_cannot_be_resumed(queue):
    job = submit(queue)
    with pytest.raises(ValueError, match="decisão"):
        queue.resume(job["id"], 123, "Leiria")
    write_json(queue.directory(job["id"]) / "result.json", {"state": "completed"})
    with pytest.raises(ValueError, match="decisão"):
        queue.resume(job["id"], 123, "Leiria")


def test_decision_history_survives_multiple_blocks(queue):
    job = waiting_job(queue)
    queue.resume(job["id"], 123, "Leiria")
    write_json(
        queue.directory(job["id"]) / "result.json",
        {
            "state": "waiting_input",
            "reply": "Qual orçamento?",
            "revision": 1,
        },
    )
    resumed = queue.resume(job["id"], 123, "Cinco euros")
    assert resumed["revision"] == 2 and len(resumed["turns"]) == 2
    assert resumed["turns"][1] == {
        "question": "Qual orçamento?",
        "answer": "Cinco euros",
    }
