"""Private, durable queue shared by the Discord bot and a host-side worker.

Only standard-library imports: the worker runs outside the bot container.
Requests and worker results are separate files so neither overwrites the other.
"""

import json
import os
import re
import time
import uuid
from pathlib import Path

PROVIDERS = {"claude": "claude", "codex": "codex", "openai": "codex"}
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
MAX_PROMPT = 12000


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


class AgentJobs:
    def __init__(self, root):
        self.root = Path(root)

    def directory(self, job_id):
        if not re.fullmatch(r"[0-9a-f]{12}", job_id):
            raise ValueError("Código de tarefa inválido.")
        return self.root / job_id

    def worker_status(self):
        status = read_json(self.root / "worker.json")
        status["online"] = time.time() - status.get("heartbeat", 0) < 30
        return status

    def submit(
        self,
        *,
        owner_id,
        provider,
        prompt,
        context,
        destination=None,
        origin=None,
    ):
        provider = PROVIDERS.get(provider.lower())
        if not provider:
            raise ValueError("Usa `claude`, `codex` ou `openai`.")
        if not prompt.strip() or len(prompt) > MAX_PROMPT:
            raise ValueError(f"O pedido deve ter entre 1 e {MAX_PROMPT} caracteres.")
        worker = self.worker_status()
        if not worker["online"]:
            raise ValueError("O executor está offline. Consulta `!self`.")
        if not worker.get("providers", {}).get(provider):
            raise ValueError(f"{provider} não está disponível no executor.")
        if sum(j["state"] not in TERMINAL for j in self.list(owner_id)) >= 5:
            raise ValueError("Já tens 5 tarefas pendentes. Aguarda ou usa `!cancel`.")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        job_id = uuid.uuid4().hex[:12]
        directory = self.directory(job_id)
        directory.mkdir(mode=0o700)
        write_json(
            directory / "request.json",
            {
                "id": job_id,
                "owner_id": str(owner_id),
                "provider": provider,
                # Owner agents are read-only; the worker refuses any other mode.
                "mode": "ask",
                "prompt": prompt.strip(),
                "context": context,
                "destination": destination,
                "origin": origin,
                "created": time.time(),
            },
        )
        return self.get(job_id, owner_id)

    def get(self, job_id, owner_id):
        directory = self.directory(job_id)
        request = read_json(directory / "request.json")
        if not request or request.get("owner_id") != str(owner_id):
            raise ValueError("Tarefa não encontrada.")
        result = read_json(directory / "result.json")
        continuation = read_json(directory / "continuation.json")
        revision = max(result.get("revision", 0), continuation.get("revision", 0))
        queued = continuation.get("revision", 0) > result.get("revision", 0)
        return {
            **request,
            **result,
            "state": "queued" if queued else result.get("state", "queued"),
            "revision": revision,
            "turns": continuation.get("turns", []),
            "notified": self.notification_marker(job_id, revision).exists(),
        }

    def notification_marker(self, job_id, revision=0):
        name = f"notified-{revision}" if revision else "notified"
        return self.directory(job_id) / name

    def resume(self, job_id, owner_id, response):
        job = self.get(job_id, owner_id)
        if job["state"] != "waiting_input":
            raise ValueError("Esta tarefa não está à espera de uma decisão.")
        if not response.strip() or len(response) > MAX_PROMPT:
            raise ValueError(f"A resposta deve ter entre 1 e {MAX_PROMPT} caracteres.")
        write_json(
            self.directory(job_id) / "continuation.json",
            {
                "revision": job["revision"] + 1,
                "turns": [
                    *job["turns"],
                    {"question": job.get("reply", ""), "answer": response.strip()},
                ],
            },
        )
        return self.get(job_id, owner_id)

    def list(self, owner_id):
        jobs = []
        for path in self.root.glob("*/request.json"):
            try:
                jobs.append(self.get(path.parent.name, owner_id))
            except ValueError:
                continue
        return sorted(jobs, key=lambda j: j["created"], reverse=True)

    def cancel(self, job_id, owner_id):
        job = self.get(job_id, owner_id)
        if job["state"] in TERMINAL:
            raise ValueError("Esta tarefa já terminou.")
        (self.directory(job_id) / "cancel").touch(mode=0o600)


def parse_direct_request(content):
    """Explicit provider requests in tagged owner messages; other text is untouched.

    `codex <question>`, `ask codex <question>` and the legacy `code codex <...>`
    all return (provider, prompt). Every owner request is read-only.
    """
    parts = content.strip().removeprefix("!").split(maxsplit=2)
    if len(parts) >= 2 and parts[0].lower() in PROVIDERS:
        return parts[0].lower(), content.strip().split(maxsplit=1)[1]
    if len(parts) == 3 and parts[0].lower() in {"code", "ask"}:
        if parts[1].lower() in PROVIDERS:
            return parts[1].lower(), parts[2]
    return None
