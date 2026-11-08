#!/usr/bin/env python3
"""Hermiswag's read-only window on the PoGoLeiria map data (MCP over stdio).

Connects as the MariaDB user `hermiswag`, which can only SELECT the views of
the `hermiswag` schema (public map data: live Pokémon, 48 h spawn history,
Pokéstops/Quests/Lures, Gyms/Raids, Power Spots/Max Battles, Team GO Rocket,
weather, nests, daily stats, Pokémon names, events, area fences). The grant is
the real boundary; this server adds one-statement, read-only, 10-second and
500-row limits on top. No accounts, workers, proxies or players exist in it.
"""

import datetime
import decimal
import json
import re
import sys
from pathlib import Path

import pymysql

ENV = Path.home() / ".hermes" / "mapdb.env"
# Knowledge files, read-only here: community.md (distilled from the server's
# own channels, no personal data) and poliswag.md (the bot's commands and the
# site, written by the owner's tooling). Every "## " heading is one section.
KNOWLEDGE = Path.home() / "knowledge"
MAX_ROWS = 500
MAX_CELL = 400
_READ = re.compile(r"^\s*(select|with|show|describe|desc|explain)\b", re.I)
_COMMENT = re.compile(r"/\*.*?\*/|--[^\n]*|#[^\n]*", re.S)

HINTS = (
    "Schema hermiswag (MariaDB). Timestamps are Unix epoch seconds (UTC) unless "
    "named *_at in spawn_history (also epoch); the area's local time is "
    "Europe/Lisbon: use CONVERT_TZ(FROM_UNIXTIME(x), @@session.time_zone, "
    "'Europe/Lisbon') or compare with UNIX_TIMESTAMP(). Names: join "
    "pokemon_name ON pokemon_id (form_id 0 = base form). Areas: area.geofence "
    "is the fence polygon JSON per area_key; use lat/lon boxes or ST_Contains "
    "with ST_GeomFromGeoJSON when exact. pokemon_live = current spawns "
    "(expire_timestamp > UNIX_TIMESTAMP()); spawn_history = last 48 h, one row "
    "per encounter (first_seen_at); seen_type 'encounter' has IVs. pokestop "
    "quests: quest_* and alternative_quest_* (AR), valid while *_expiry > now; "
    "reward_type 2 item, 3 stardust, 4 candy, 7 Pokémon encounter, 12 mega "
    "energy. gym raids: raid_end_timestamp > now; raid_battle_timestamp is the "
    "hatch time. station = Power Spots (battle_* = current Max Battle). "
    "incident = Team GO Rocket at a Pokéstop (expiration). *_stats = daily "
    "counts per area/fence. Always add a LIMIT and filter by time."
)


def _env():
    values = {}
    for line in ENV.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _connect():
    env = _env()
    return pymysql.connect(
        host=env["MAPDB_HOST"],
        port=int(env.get("MAPDB_PORT", "3306")),
        user=env["MAPDB_USER"],
        password=env["MAPDB_PASSWORD"],
        database=env.get("MAPDB_NAME", "hermiswag"),
        charset="utf8mb4",
        connect_timeout=5,
        read_timeout=15,
        autocommit=True,
    )


def _cell(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str) and len(value) > MAX_CELL:
        return value[:MAX_CELL] + "…"
    return value


def run_query(sql):
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("Empty query.")
    code = _COMMENT.sub(" ", sql).strip().rstrip(";").strip()
    if ";" in code:
        raise ValueError("One statement only.")
    if not _READ.match(code):
        raise ValueError("Read-only: SELECT, WITH, SHOW, DESCRIBE or EXPLAIN.")
    statement = code
    if re.match(r"^\s*(select|with)\b", code, re.I):
        statement = f"SET STATEMENT max_statement_time=10 FOR {code}"
    with _connect() as db:
        with db.cursor() as cursor:
            cursor.execute(statement)
            columns = [c[0] for c in cursor.description or ()]
            rows = cursor.fetchmany(MAX_ROWS + 1)
    truncated = len(rows) > MAX_ROWS
    return {
        "columns": columns,
        "rows": [[_cell(v) for v in row] for row in rows[:MAX_ROWS]],
        "row_count": min(len(rows), MAX_ROWS),
        "truncated": truncated,
    }


def schema():
    with _connect() as db:
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT table_name, GROUP_CONCAT(column_name ORDER BY "
                "ordinal_position SEPARATOR ', ') FROM information_schema.columns "
                "WHERE table_schema = 'hermiswag' GROUP BY table_name"
            )
            tables = {name: cols for name, cols in cursor.fetchall()}
    return {"hints": HINTS, "views": tables}


def _sections():
    out = []
    for path in sorted(KNOWLEDGE.glob("*.md")):
        try:
            text = path.read_text()
        except OSError:
            continue
        for part in re.split(r"(?m)^## ", text):
            if part.strip() and not part.lstrip().startswith("#"):
                out.append(("## " + part).strip())
    return out


def knowledge(topic=""):
    """Every section title with no topic; else the sections that best match
    the topic's words (title words count double), at most three."""
    sections = _sections()
    if not sections:
        return {"sections": [], "note": "Sem base de conhecimento."}
    titles = [s.splitlines()[0][3:] for s in sections]
    words = [w for w in re.findall(r"\w{3,}", (topic or "").lower())]
    if not words:
        return {"sections": titles, "note": "Indica um tema para ler as secções."}
    scored = []
    for title, body in zip(titles, sections):
        low, head = body.lower(), title.lower()
        score = sum(low.count(w) + 2 * head.count(w) for w in words)
        if score:
            scored.append((score, body))
    scored.sort(key=lambda x: -x[0])
    return {"matches": [b for _, b in scored[:3]], "all_sections": titles}


TOOLS = [
    {
        "name": "community_knowledge",
        "description": (
            "PoGoLeiria community knowledge learned from the server's own "
            "channels: local Pokéstops/Gyms and nicknames, meeting points, how "
            "the server, roles, alerts and the site work, frequent technical "
            "problems and fixes, events and habits. Give a topic to get the "
            "best matching sections; no topic lists the sections."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
        },
    },
    {
        "name": "map_schema",
        "description": "Views and columns of the PoGoLeiria public map database, with query hints. Call it first.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "map_query",
        "description": (
            "Run one read-only SQL query (MariaDB) on the PoGoLeiria public map "
            "views: live and recent spawns, Pokéstops, Quests, Lures, Gyms, Raids, "
            "Power Spots and Max Battles, Team GO Rocket, weather, nests, daily "
            "stats, areas. Max 500 rows, 10 s."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
]


def handle(message):
    method = message.get("method")
    if method == "initialize":
        return {
            "protocolVersion": message.get("params", {}).get(
                "protocolVersion", "2025-06-18"
            ),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "pogoleiria-mapdb", "version": "1.0"},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            if name == "community_knowledge":
                result = knowledge(args.get("topic") or "")
            elif name == "map_schema":
                result = schema()
            elif name == "map_query":
                result = run_query(args.get("sql"))
            else:
                raise ValueError("Unknown tool.")
            text, error = json.dumps(result, ensure_ascii=False, default=str), False
        except (ValueError, pymysql.MySQLError) as exc:
            # The database's own message helps fix the query; it holds no secrets.
            text, error = f"Erro: {exc}", True
        return {"content": [{"type": "text", "text": text}], "isError": error}
    if method == "ping":
        return {}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if "id" not in message:
            continue  # notifications
        result = handle(message)
        if result is None:
            reply = {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {"code": -32601, "message": "Method not found"},
            }
        else:
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
