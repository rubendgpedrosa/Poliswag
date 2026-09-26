#!/usr/bin/env python3
"""Run bot + website event tests against a disposable UTC MariaDB.

Requires Docker, the poliswag image, and installed sibling website dependencies.
Run on the host: python3 scripts/test_events_mariadb.py
"""

import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
name = "events-test-" + uuid4().hex[:8]


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


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
        "-e",
        "MARIADB_DATABASE=event_disposable",
        "mariadb:10.11",
        "--default-time-zone=+00:00",
    )
    port = docker("port", name, "3306").split(":")[-1]
    for _ in range(60):
        ready = subprocess.run(
            [
                "docker",
                "exec",
                name,
                "mariadb",
                "-uroot",
                "-e",
                'SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME="event_disposable"',
            ],
            capture_output=True,
            text=True,
        )
        if ready.returncode == 0 and "1" in ready.stdout:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("Disposable MariaDB did not become ready")
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "host",
            "-v",
            f"{ROOT}:/app",
            "-w",
            "/app",
            "-e",
            f"EVENT_SQL_TEST_PORT={port}",
            "poliswag",
            "pytest",
            "tests/integration/test_events_sql.py",
            "-q",
            "-o",
            "addopts=",
        ],
        check=True,
    )
    subprocess.run(
        [
            "npm",
            "test",
            "--workspace=landing",
            "--",
            "tests/events-db.integration.test.ts",
        ],
        cwd=ROOT.parent / "PoGoLeiria",
        env={**os.environ, "EVENT_SQL_TEST_PORT": port},
        check=True,
    )
finally:
    subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
