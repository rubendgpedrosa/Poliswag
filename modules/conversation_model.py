"""Public banter through the host's Claude/Codex login, without API calls."""

import asyncio
import json
import time
from pathlib import Path

from modules.agent_jobs import AgentJobs, TERMINAL

TOPICS = {
    "greeting": "Greet the community with a playful Pokemon frog joke.",
    "events": "Give a playful introduction to the community event calendar.",
    "statistics": "Give a playful introduction to aggregate Pokemon game numbers.",
}
INSTRUCTIONS = (
    "You are Poliswag, a friendly Pokemon frog mascot in a Portuguese community. "
    "Write one short playful sentence in European Portuguese, at most 180 characters. "
    "Only light Pokemon banter. Do not invent facts, dates, totals or event names. "
    "No numbers, links, mentions, commands, code, technical details, accounts, "
    "infrastructure, configuration, credentials, private information or explanations "
    "of how anything works. You have no tools and no private context. "
    'Return JSON with a single reply string, for example {"reply":"Olá! 🐸"}.'
)


class ConversationModel:
    def __init__(self, jobs_dir):
        self.jobs = AgentJobs(Path(jobs_dir) / "public")
        self.slots = asyncio.Semaphore(2)
        self.retry_after = 0

    async def line(self, topic):
        if (
            topic not in TOPICS
            or self.slots.locked()
            or time.monotonic() < self.retry_after
        ):
            return None
        job = None
        async with self.slots:
            try:
                status = self.jobs.worker_status()
                if status.get("active"):
                    return None
                job = self.jobs.submit(
                    owner_id=0,
                    provider="codex",
                    mode="ask",
                    prompt=topic,
                    context={},
                )
                async with asyncio.timeout(35):
                    while True:
                        result = self.jobs.get(job["id"], 0)
                        if result["state"] in TERMINAL:
                            if result["state"] != "completed":
                                raise ValueError("Unavailable")
                            return self.extract(result.get("reply"))
                        await asyncio.sleep(0.25)
            except Exception:
                self.retry_after = time.monotonic() + 60
                return None
            finally:
                if job is not None:
                    try:
                        self.jobs.cancel(job["id"], 0)
                    except (ValueError, OSError):
                        pass

    @staticmethod
    def extract(text):
        if not isinstance(text, str) or len(text) > 4096:
            return None
        try:
            reply = json.loads(text)
        except ValueError:
            return None
        if not isinstance(reply, dict) or set(reply) != {"reply"}:
            return None
        return reply["reply"] if isinstance(reply["reply"], str) else None
