#!/usr/bin/env python3
"""Private stdio MCP bridge to the bot's owner-only database query queue."""

import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.agent_jobs import read_json, write_json

TOOLS = [
    {
        "name": "spawn_history_report",
        "description": "Recompute an unfiltered shiny leaderboard for all linked map users from retained raw spawns, with recorded odds and coverage metadata. Ignores account notification, collection, area and IV preferences. Hourly averages are null when history is incomplete; never substitute filtered shiny_alert counts.",
        "inputSchema": {
            "type": "object",
            "properties": {"hours": {"type": "integer", "minimum": 1, "maximum": 48}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "list_databases",
        "description": "List the production databases accessible through Poliswag.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "query",
        "description": "Query live production data using read-only SQL; use SHOW TABLES and DESCRIBE to discover schema. Results are bounded; prefer SQL aggregates and LIMIT. Use %s placeholders with params for values. Scanner accounts, credentials, usernames, Friend Codes and SQL comments are refused.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "database": {"type": "string"},
                "sql": {"type": "string"},
                "params": {"type": ["array", "object"]},
                "max_rows": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["database", "sql"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
]


def call(job_dir, name, arguments, timeout=30):
    directory = job_dir / "database"
    directory.mkdir(mode=0o700, exist_ok=True)
    request = directory / f"{uuid.uuid4().hex}.request.json"
    response = request.with_name(request.name.replace(".request.json", ".result.json"))
    try:
        write_json(
            request, {"created": time.time(), "tool": name, "arguments": arguments}
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if response.exists():
                return read_json(response)
            if (job_dir / "cancel").exists() or read_json(job_dir / "result.json").get(
                "state"
            ) != "running":
                return {"error": "The owner task is no longer running."}
            time.sleep(0.1)
        return {
            "error": "Poliswag's database bridge did not respond; report the tool failure accurately."
        }
    finally:
        request.unlink(missing_ok=True)
        response.unlink(missing_ok=True)


def handle(message, job_dir):
    if "id" not in message:
        return None
    method = message.get("method")
    params = message.get("params", {})
    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "poliswag_db", "version": "1.0.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        data = (
            call(job_dir, name, params.get("arguments", {}))
            if name in {t["name"] for t in TOOLS}
            else {"error": "Unknown tool."}
        )
        result = {
            "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
            "isError": "error" in data,
        }
    else:
        return {
            "jsonrpc": "2.0",
            "id": message["id"],
            "error": {"code": -32601, "message": "Method not found"},
        }
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def main():
    job_dir = Path(sys.argv[1]).resolve()
    for line in sys.stdin:
        message = {}
        try:
            message = json.loads(line)
            response = handle(message, job_dir)
        except Exception:
            response = {
                "jsonrpc": "2.0",
                "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": -32603, "message": "Database bridge error"},
            }
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
