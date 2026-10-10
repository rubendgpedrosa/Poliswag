"""Stateless, tool-free public banter. Only a fixed topic enters the API."""

import asyncio
import json
import time

import aiohttp

from modules.http_client import get_session

_TOPICS = {
    "greeting": "Greet the community with a playful Pokemon frog joke.",
    "events": "Give a playful introduction to the community event calendar.",
    "statistics": "Give a playful introduction to aggregate Pokemon game numbers.",
}
_INSTRUCTIONS = (
    "You are Poliswag, a friendly Pokemon frog mascot in a Portuguese community. "
    "Write one short playful sentence in European Portuguese, at most 180 characters. "
    "Only light Pokemon banter. Do not invent facts, dates, totals or event names. "
    "No numbers, links, mentions, commands, code, technical details, accounts, "
    "infrastructure, configuration, credentials, private information or explanations "
    "of how anything works. You have no tools and no private context. "
    "Return JSON with a single reply string."
)


class ConversationModel:
    def __init__(self, api_key, effort="medium"):
        self.api_key = api_key
        self.effort = effort if effort in {"low", "medium"} else "medium"
        self.slots = asyncio.Semaphore(2)
        self.retry_after = 0

    async def line(self, topic):
        if (
            not self.api_key
            or topic not in _TOPICS
            or self.slots.locked()
            or time.monotonic() < self.retry_after
        ):
            return None
        payload = {
            "model": "gpt-6-luna",
            "reasoning": {"effort": self.effort},
            "instructions": _INSTRUCTIONS,
            "input": _TOPICS[topic],
            "tools": [],
            "store": False,
            "max_output_tokens": 2048,
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "public_banter",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"reply": {"type": "string"}},
                        "required": ["reply"],
                        "additionalProperties": False,
                    },
                },
            },
        }
        async with self.slots:
            try:
                async with get_session().post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=18),
                ) as response:
                    if response.status != 200:
                        raise ValueError("Unavailable")
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        body.extend(chunk)
                        if len(body) > 65536:
                            raise ValueError("Response too large")
                    return self.extract(json.loads(body))
            except Exception:
                # No request, key, response, or exception is logged or displayed.
                self.retry_after = time.monotonic() + 60
                return None

    @staticmethod
    def extract(data):
        if data.get("status") != "completed":
            return None
        messages = []
        for item in data.get("output", []):
            if item.get("type") == "reasoning":
                continue
            if (
                item.get("type") != "message"
                or item.get("role") != "assistant"
                or item.get("status") != "completed"
            ):
                return None
            messages.append(item)
        if len(messages) != 1:
            return None
        content = messages[0].get("content", [])
        if len(content) != 1 or content[0].get("type") != "output_text":
            return None
        reply = json.loads(content[0]["text"])
        if not isinstance(reply, dict) or set(reply) != {"reply"}:
            return None
        return reply["reply"] if isinstance(reply["reply"], str) else None
