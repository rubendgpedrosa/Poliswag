#!/usr/bin/env python3
"""Standalone integration test, owns and removes a disposable MariaDB container.

Run from Poliswag: python3 scripts/test_webstats_mariadb.py
Requires the sibling PoGoLeiria checkout and its installed npm dependencies.
Never uses production database settings or records.
"""

from datetime import datetime
import os
from pathlib import Path
import json
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pymysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.page_view_stats import PageViewStats  # noqa: E402
from modules.page_view_report import render_export  # noqa: E402

WEB = ROOT.parent / "PoGoLeiria"
name = "webstats-test-" + uuid4().hex[:8]


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def run_web(*args, check=True):
    return subprocess.run(
        args, cwd=WEB, env=env, check=check, capture_output=True, text=True
    )


try:
    docker(
        "run",
        "-d",
        "--rm",
        "--name",
        name,
        "--memory",
        "384m",
        "--cpus",
        "1",
        "-p",
        "127.0.0.1::3306",
        "-e",
        "MARIADB_ALLOW_EMPTY_ROOT_PASSWORD=1",
        "mariadb:10.11",
    )
    port = int(docker("port", name, "3306").split(":")[-1])
    for attempt in range(60):
        try:
            db = pymysql.connect(
                host="127.0.0.1", port=port, user="root", password="", autocommit=True
            )
            break
        except pymysql.Error:
            time.sleep(0.5)
    else:
        raise RuntimeError("Disposable MariaDB did not start")
    with db.cursor() as cursor:
        cursor.execute("CREATE DATABASE pogoleiria")
    env = {
        **os.environ,
        "DB_HOST": "127.0.0.1",
        "DB_PORT": str(port),
        "DB_USER": "root",
        "DB_PASSWORD": "",
        "TRACK_SECRET": "integration-only",
        "ANALYTICS_INTEGRATION": "1",
    }
    command = ("node", "apps/landing/scripts/analytics-db.mjs")
    assert run_web(*command, "--check", check=False).returncode != 0
    for _ in range(2):
        result = run_web(*command, "--migrate")
        print(result.stdout.strip())
    run_web(*command, "--check")
    # Real collector HTTP handler -> mysql2 -> real unique constraint, and
    # original occurrence/receipt timestamps across duplicate delivery.
    result = run_web(
        "npm", "test", "--workspace=landing", "--", "tests/track-db.integration.test.ts"
    )
    print(result.stdout.strip())
    with db.cursor() as cursor:
        cursor.execute("TRUNCATE pogoleiria.page_view")
        insert = """INSERT INTO pogoleiria.page_view
          (created_at, view, path, is_load, load_id, visitor, device, standalone, referrer,
           model, arch, bitness, cpu_cores, device_memory, event_name, schema_version)
          VALUES (%s, %s, %s, %s, %s, %s, 'mobile', 0, %s, 'Pixel', 'arm', '64', 8, 4, %s, 2)"""
        for view, is_load, referrer, event in [
            ("home", 1, "discord.com", "tool_view"),
            ("trades", 0, None, "tool_view"),
            ("trades", 0, None, "friend_code_copied"),
        ]:
            cursor.execute(
                insert,
                (
                    "2026-09-20 08:00:00",
                    view,
                    "/trades/entrar/TEST-CREDENTIAL",
                    is_load,
                    "0123456789abcdef",
                    "test-visitor",
                    referrer,
                    event,
                ),
            )
    config = SimpleNamespace(
        DB_HOST="127.0.0.1",
        DB_PORT=port,
        DB_USER="root",
        DB_PASSWORD="",
        DB_POGOLEIRIA="pogoleiria",
    )
    with patch("modules.page_view_stats.Config", config):
        stats = PageViewStats(None)._collect_sync(
            datetime(2026, 9, 20), datetime(2026, 9, 21), "export"
        )
    assert stats["totals"][0]["sessions"] == 1
    assert stats["totals"][0]["views"] == 2
    assert stats["referrers"] == [{"referrer": "discord.com", "sessions": 1}]
    assert stats["actions"][0]["events"] == 1
    # The section renderers went with the text report; the export is now the
    # only thing here that turns these rows into something a human receives,
    # so it is what has to stay free of credentials.
    export = render_export(stats)
    assert "TEST-CREDENTIAL" not in export
    assert json.loads(export)["totals"][0]["sessions"] == 1
    result = run_web(*command, "--sanitize-preview")
    assert "TEST-CREDENTIAL" not in result.stdout
    run_web(*command, "--sanitize-apply")
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM pogoleiria.page_view WHERE path = '/trades/entrar/:code'"
        )
        assert cursor.fetchone()[0] == 3
    db.close()
    print(
        "MariaDB integration passed: migrations, replay protection, snapshot, attribution, coverage, sanitization."
    )
finally:
    subprocess.run(
        ["docker", "rm", "-f", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
