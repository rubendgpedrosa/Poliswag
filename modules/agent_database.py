"""Owner job database queries, executed using the bot's existing credentials."""

import asyncio
import json
import re
import time

import pymysql

from modules.agent_jobs import read_json, write_json
from modules.config import Config
from modules.database_connector import connect


def databases():
    return {
        "poliswag": Config.DB_POLISWAG,
        "scanner": Config.DB_SCANNER_NAME,
        "dragonite": Config.DB_DRAGONITE,
        "pogoleiria": Config.DB_POGOLEIRIA,
        "poracle": "poracle",
    }


def query(arguments):
    alias = arguments.get("database", "poliswag")
    sql = arguments.get("sql", "")
    rows = arguments.get("max_rows", 100)
    params = arguments.get("params")
    if alias not in databases():
        raise ValueError("Unknown database; use list_databases first.")
    if (
        not isinstance(sql, str)
        or len(sql) > 12000
        or not re.match(r"^\s*(SELECT|SHOW|DESCRIBE|DESC|EXPLAIN|WITH)\b", sql, re.I)
        or re.search(r"\b(INTO\s+(OUTFILE|DUMPFILE)|LOAD_FILE)\b", sql, re.I)
    ):
        raise ValueError("Use a read-only SELECT, SHOW, DESCRIBE or EXPLAIN query.")
    if type(rows) is not int or not 1 <= rows <= 500:
        raise ValueError("max_rows must be between 1 and 500.")
    if params is not None and not isinstance(params, (list, dict)):
        raise ValueError("params must be an array or object.")
    connection = connect(
        databases()[alias],
        read_timeout=12,
        autocommit=False,
        cursorclass=pymysql.cursors.SSDictCursor,
    )
    try:
        cursor = connection.cursor()
        cursor.execute("SET SESSION max_statement_time=10")
        cursor.execute("START TRANSACTION READ ONLY")
        cursor.execute(sql, params)
        columns = [column[0] for column in cursor.description or ()]
        result = []
        size = 0
        truncated = False
        for _ in range(rows + 1):
            row = cursor.fetchone()
            if row is None:
                break
            # Convert dates, decimals and bytes to JSON without exposing credentials.
            row = json.loads(json.dumps(row, default=str, ensure_ascii=False))
            size += len(json.dumps(row, ensure_ascii=False))
            if len(result) == rows or size > 60000:
                truncated = True
                break
            result.append(row)
        return {
            "database": alias,
            "columns": columns,
            "rows": result,
            "truncated": truncated,
        }
    finally:
        # Closing a streaming cursor drains remaining rows; disconnect instead.
        # This also rolls back the read-only transaction.
        connection.close()


class AgentDatabase:
    def __init__(self, jobs, owner_id):
        self.jobs = jobs
        self.owner_id = owner_id

    async def poll(self):
        for job in self.jobs.list(self.owner_id):
            directory = self.jobs.directory(job["id"])
            if job["state"] != "running" or (directory / "cancel").exists():
                continue
            for path in sorted((directory / "database").glob("*.request.json"))[:4]:
                if (
                    self.jobs.get(job["id"], self.owner_id)["state"] != "running"
                    or (directory / "cancel").exists()
                ):
                    break
                response = path.with_name(
                    path.name.replace(".request.json", ".result.json")
                )
                if response.exists():
                    continue
                request = read_json(path)
                try:
                    if not 0 <= time.time() - request.get("created", 0) <= 35:
                        raise ValueError("Database request expired.")
                    if request.get("tool") == "list_databases":
                        data = {
                            "databases": list(databases()),
                            "access": "production read-only",
                        }
                    elif request.get("tool") == "query":
                        data = await asyncio.to_thread(
                            query, request.get("arguments", {})
                        )
                    else:
                        raise ValueError("Unknown database tool.")
                    result = {"data": data}
                except ValueError as error:
                    result = {"error": str(error)}
                except pymysql.MySQLError as error:
                    # SQL diagnostics help correct queries; connection errors can contain secrets.
                    code = error.args[0] if error.args else 0
                    diagnostic = (
                        str(error.args[1])[:500]
                        if code in {1054, 1064, 1146}
                        else "Database query unavailable or timed out."
                    )
                    result = {"error": diagnostic, "code": code}
                except Exception:
                    result = {"error": "Database query unavailable."}
                write_json(response, result)
