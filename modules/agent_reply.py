"""Structured owner replies shared by the bot and the host worker."""

import json
import re

from modules.agent_artifacts import MAX_ATTACHMENTS

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string", "enum": ["done", "needs_input"]},
        "kind": {"type": "string", "enum": ["chat", "code", "stats"]},
        "reply": {"type": "string"},
        "details": {"type": "string"},
        "attachments": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": MAX_ATTACHMENTS,
        },
    },
    "required": ["outcome", "kind", "reply", "details", "attachments"],
    "additionalProperties": False,
}


def short_sentence(text):
    text = str(text).strip().split("\n", 1)[0]
    text = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    if len(text) > 280:
        text = text[:277].rsplit(" ", 1)[0] + "…"
    return text


def parse_reply(value):
    if isinstance(value, str):
        value = json.loads(value)
    required = {"outcome", "kind", "reply", "details"}
    if (
        not isinstance(value, dict)
        or not required <= set(value)
        or set(value) - (required | {"attachments"})
    ):
        raise ValueError("Invalid agent reply")
    attachments = value.get("attachments", [])  # Accept saved pre-manifest replies.
    if (
        value["outcome"] not in {"done", "needs_input"}
        or value["kind"] not in {"chat", "code", "stats"}
        or not isinstance(value["reply"], str)
        or not value["reply"].strip()
        or not isinstance(value["details"], str)
        or not isinstance(attachments, list)
        or len(attachments) > MAX_ATTACHMENTS
        or any(not isinstance(path, str) or not path for path in attachments)
    ):
        raise ValueError("Invalid agent reply")
    return {
        **value,
        "reply": short_sentence(value["reply"]),
        "details": value["details"][:500000],
        "attachments": attachments,
    }
