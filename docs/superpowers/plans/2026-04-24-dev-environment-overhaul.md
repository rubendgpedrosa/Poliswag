# Dev Environment Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Poliswag trivially reproducible on any machine — `git clone`, `cp .env.example .env`, `make up` — with a test suite that runs locally without Docker, mocks that never go stale, and a dev stack that matches prod's service topology.

**Architecture:** Seven independent, sequentially-safe tasks. Each task produces a green test suite and a working dev stack on its own. Tasks 1–3 unlock local test runs. Tasks 4–5 fix the mock data layer. Tasks 6–7 tighten the compose stack and developer tooling.

**Tech Stack:** Python 3.11, pytest + pytest-asyncio + pytest-mock, MariaDB 10.11, Docker Compose v2 watch mode, pymysql, python-dotenv.

---

## Audit: What Is Broken Today

Before touching code, here is the complete list of defects this plan fixes:

| # | Problem | Impact |
|---|---------|--------|
| A | `requirements.txt` ships `pytest`, `black`, `vulture` into prod | Bloated prod image |
| B | `pymysql.connect` is called on every `DatabaseConnector()` instantiation — any test that exercises a real constructor needs a live DB | Tests break outside the container |
| C | No `.env.test` — CI and local pytest must guess at config values | Cannot run tests in CI without a real `.env` |
| D | `poracle` database is absent from `mock_database/init.sql` | `Notifications` cog fails on `make up` with `Unknown database 'poracle'` |
| E | `mock_data/*.json` has hardcoded 2024 Unix timestamps — `scanner_status` workers appear perpetually dead | Dev health checks always red |
| F | `Makefile` `up` target does `cp -n mock_data/*.json mock_data/` (self-copy, always errors) | `make up` prints a spurious error every time |
| G | No `docker compose watch` — code changes require a manual `make reload` | Slow inner dev loop |
| H | `make test` requires the container to be running; no local test path | Hard to iterate on tests |
| I | No `make check` one-shot quality gate | Pre-push checks are scattered |
| J | `migrations/` SQL is not applied automatically in dev | Schema drifts silently |

---

## File Map

```
requirements.txt          ← prod deps only (aiohttp, discord.py, imgkit, Jinja2, PyMySQL, python-dotenv, Requests)
requirements-dev.txt      ← dev + test deps (everything from requirements.txt + pytest stack + black + vulture + docker)
Dockerfile                ← install requirements.txt only
docker-compose.yaml       ← add poracle-db service, add `develop.watch`, apply migrations at startup
mock_database/init.sql    ← add `poracle` DB: `humans` + `monsters` tables + seed rows
mock_data/refresh.py      ← NEW: script that rewrites *.json with timestamps relative to now()
mock_data/*.json          ← timestamps updated by refresh.py (run once; committed for offline use)
tests/conftest.py         ← autouse fixture that blocks all real pymysql connections
makefile                  ← fix cp bug, add `make test-local`, `make check`, `make migrate`, `make mock-data`
README.md                 ← update Quick Start with local test instructions
```

---

## Task 1 — Autouse DB Guard (enables local test runs)

**Problem:** Any test that instantiates `DatabaseConnector` (directly or via a module's `__init__`) calls `pymysql.connect`, which fails without a live MariaDB. The guard patches `pymysql.connect` globally so the real TCP handshake never happens.

**Files:**
- Modify: `tests/conftest.py`

---

- [ ] **Step 1.1 — Write a test that proves the guard works**

Create `tests/test_conftest_guard.py`:

```python
"""Verify that tests never open real DB connections regardless of Config."""

import pymysql

def test_pymysql_connect_is_mocked():
    """If the autouse guard is active, pymysql.connect returns a MagicMock."""
    conn = pymysql.connect(host="unreachable", user="u", password="p", db="d")
    # A real connection to "unreachable" would raise; reaching here means it's mocked.
    assert conn is not None

def test_database_connector_init_does_not_raise():
    """DatabaseConnector() must not raise even with no real MariaDB available."""
    from modules.database_connector import DatabaseConnector
    dc = DatabaseConnector.__new__(DatabaseConnector)
    dc.database = "poliswag"
    # Call connect_to_db — the patch means pymysql.connect returns a MagicMock
    conn = dc.connect_to_db()
    assert conn is not None
```

Run:
```bash
cd /root/Poliswag && python -m pytest tests/test_conftest_guard.py -v
```
Expected: **FAIL** — `test_pymysql_connect_is_mocked` raises `OperationalError` (no real host).

---

- [ ] **Step 1.2 — Add the autouse guard to `tests/conftest.py`**

```python
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _prevent_real_db_connections():
    """Block all pymysql.connect calls so tests never need a live database.

    This lets pytest run outside the Docker container without changing any
    test that already mocks at a higher level (MagicMock poliswag, etc.).
    """
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    with patch("pymysql.connect", return_value=mock_conn):
        yield


@pytest.fixture
def sample_translations() -> dict:
    return {
        "quest_catch_pokemon": "Catch {0} Pokémon",
        "quest_throw_great": "Make {0} Great Throws",
    }


@pytest.fixture
def sample_name_maps() -> tuple[dict, dict]:
    pokemon_names = {"1": "Bulbasaur", "25": "Pikachu", "150": "Mewtwo"}
    item_names = {"1": "Poké Ball", "2": "Great Ball", "701": "Razz Berry"}
    return pokemon_names, item_names
```

---

- [ ] **Step 1.3 — Run the guard tests**

```bash
python -m pytest tests/test_conftest_guard.py -v
```
Expected: **PASS** both tests.

---

- [ ] **Step 1.4 — Run the full suite to confirm no regressions**

```bash
python -m pytest -x -q
```
Expected: same pass count as before (currently 75 in container).

---

- [ ] **Step 1.5 — Commit**

```bash
git add tests/conftest.py tests/test_conftest_guard.py
git commit -m "test(conftest): autouse guard blocks real pymysql connections"
```

---

## Task 2 — Split Requirements (prod / dev)

**Problem:** `pytest`, `black`, `vulture`, `docker` (a 3 MB SDK) ship into the production image. The prod image should be minimal.

**Files:**
- Modify: `requirements.txt`
- Create: `requirements-dev.txt`
- Modify: `Dockerfile`
- Modify: `makefile`

---

- [ ] **Step 2.1 — Rewrite `requirements.txt` (prod only)**

```
aiohttp==3.13.4
discord.py==2.4.0
imgkit==1.2.3
Jinja2==3.1.6
PyMySQL==1.1.1
python-dotenv==1.2.2
Requests==2.33.0
```

---

- [ ] **Step 2.2 — Create `requirements-dev.txt`**

```
-r requirements.txt
pytest==9.0.3
pytest-mock==3.14.0
pytest-cov==6.0.0
pytest-asyncio==1.3.0
black==26.3.1
vulture==2.14
docker
```

---

- [ ] **Step 2.3 — Update `Dockerfile` to install only prod deps**

```dockerfile
# Cached pip layer: only invalidated when requirements.txt changes
COPY requirements.txt .
RUN pip install -r requirements.txt
```

(No change — it already uses `requirements.txt`. This step verifies it does not reference `requirements-dev.txt`.)

---

- [ ] **Step 2.4 — Update `docker-compose.yaml` dev service to install dev deps**

In `docker-compose.yaml`, add a startup command that installs dev deps after the container starts (the bind mount `.:/app` means `requirements-dev.txt` is available):

```yaml
  poliswag:
    build: .
    image: poliswag
    container_name: poliswag
    restart: unless-stopped
    init: true
    ports:
      - "8989:8989"
    volumes:
      - .:/app
    depends_on:
      db:
        condition: service_healthy
    env_file:
      - .env
    command: >
      sh -c "pip install -q -r requirements-dev.txt && python main.py"
    logging:
      driver: json-file
      options:
        max-size: "10m"
```

---

- [ ] **Step 2.5 — Verify the container still boots**

```bash
make up
```
Expected: container starts, logs show `Notifications loaded!`.

---

- [ ] **Step 2.6 — Commit**

```bash
git add requirements.txt requirements-dev.txt Dockerfile docker-compose.yaml
git commit -m "build: split requirements into prod and dev deps"
```

---

## Task 3 — `.env.test` and Local Test Target

**Problem:** `Config` calls `load_dotenv()` at import time. Without a `.env` file the integer casts (`int(os.environ.get("QUEST_CHANNEL_ID", "0"))`) return 0 but `DB_HOST` and friends are `None`. Running pytest locally (no `.env`) still works because every test mocks the DB — but it's fragile and undocumented.

This task adds an explicit `.env.test` with safe defaults, wires pytest to load it, and adds a `make test-local` target so developers know how to run tests without Docker.

**Files:**
- Create: `.env.test`
- Modify: `tests/conftest.py`
- Modify: `makefile`

---

- [ ] **Step 3.1 — Create `.env.test`**

```bash
# .env.test — safe dummy values used by pytest (never touch prod)
ENV=DEVELOPMENT

DISCORD_API_KEY=test_token
GOOGLE_API_KEY=test_google_key
LLM_API_KEY=test_llm_key
LLM_MODEL=test_model

MOD_CHANNEL_ID=1
ACCOUNTS_CHANNEL_ID=2
VOICE_CHANNEL_LEIRIA_ID=3
VOICE_CHANNEL_MARINHA_ID=4
CONVIVIO_CHANNEL_ID=5
QUEST_CHANNEL_ID=6

MY_ID=999
POLISWAG_ID=998
POLISWAG_ROLE_ID=997
ADMIN_USERS_IDS=999

DB_HOST=localhost
DB_PORT=3306
DB_SCANNER_NAME=scanner
DB_USER=poliswag
DB_PASSWORD=poliswag
DB_POLISWAG=poliswag

SCANNER_STATUS_ENDPOINT=http://localhost:7272/status
DEVICE_STATUS_ENDPOINT=http://localhost:7072/api/status
SCANNER_ACCOUNTS_STATUS_ENDPOINT=http://localhost:7272/accounts/stats
LEIRIA_QUEST_SCANNING_ENDPOINT=http://localhost:80/status/quest-area/1
MARINHA_QUEST_SCANNING_ENDPOINT=http://localhost:80/status/quest-area/2
SCAN_QUESTS_ALL_ENDPOINT=http://localhost:80/quest/all/start
MASTERFILE_ENDPOINT=https://example.com/masterfile.json
TRANSLATIONFILE_ENDPOINT=https://example.com/translations.json
ALL_DOWN_ENDPOINT=https://example.com/webhook
EVENTS_ENDPOINT=https://example.com/events.json
NIANTIC_FORCED_VERSION_ENDPOINT=https://example.com/version
UI_ICONS_URL=https://icons.example/
PORACLE_API_URL=http://localhost:3030
PORACLE_API_SECRET=

LOG_FILE=logs/actions.log
ERROR_LOG_FILE=logs/error.log
EVENT_FILE=data/events.json
POKEMON_LIST_FILE=data/pokemon_list.json
POKEMON_NAME_FILE=data/pokemon_name_map.json
ITEM_NAME_FILE=data/item_name_map.json
TEMPLATE_HTML_DIR=templates
FOLLOWED_EVENTS_TEMPLATE_HTML_FILE=followed_events.html
ACCOUNTS_TEMPLATE_HTML_FILE=accounts.html
QUEST_JSON_OUTPUT=/tmp/quests.json
SCANNER_CONTAINER_NAME=scanner
```

---

- [ ] **Step 3.2 — Load `.env.test` before any test module is imported**

Add to the top of `tests/conftest.py`, before all fixtures:

```python
import os
from dotenv import load_dotenv

# Load test-safe env vars before Config is imported by any test module.
# .env.test takes priority only when DISCORD_API_KEY is not already set,
# so a real .env in the working directory is not overridden.
if not os.environ.get("DISCORD_API_KEY"):
    load_dotenv(".env.test", override=False)
```

---

- [ ] **Step 3.3 — Write a test that confirms test env vars are loaded**

Add to `tests/test_conftest_guard.py`:

```python
def test_env_test_is_loaded():
    """Config values must be non-None when running pytest."""
    from modules.config import Config
    assert Config.DB_HOST is not None, "DB_HOST is None — .env.test not loaded"
    assert Config.DISCORD_API_KEY is not None, "DISCORD_API_KEY is None"
```

Run:
```bash
python -m pytest tests/test_conftest_guard.py::test_env_test_is_loaded -v
```
Expected: **PASS**.

---

- [ ] **Step 3.4 — Add `make test-local` to `makefile`**

```makefile
test-local: ## Run pytest locally (no Docker required)
	@echo "Running tests locally..."
	pytest
	@echo "Tests finished."
```

Developers install deps with:
```bash
pip install -r requirements-dev.txt
make test-local
```

---

- [ ] **Step 3.5 — Run the full suite locally (outside container)**

```bash
pip install -r requirements-dev.txt
make test-local
```
Expected: all tests pass with the same count as in-container.

---

- [ ] **Step 3.6 — Add `.env.test` to `.gitignore` exception (it's safe to commit)**

Verify `.gitignore` does not exclude `.env.test`:

```bash
git check-ignore -v .env.test
```
If ignored, add to `.gitignore`:
```
!.env.test
```

---

- [ ] **Step 3.7 — Commit**

```bash
git add .env.test tests/conftest.py tests/test_conftest_guard.py makefile
git commit -m "test: add .env.test and make test-local for local pytest runs"
```

---

## Task 4 — Poracle Database in Mock Seed

**Problem:** `Notifications.__init__` creates `DatabaseConnector("poracle")`. In `docker-compose.yaml` the MariaDB container has no `poracle` database — the bot fails at startup in dev with `Unknown database 'poracle'`. Even if that's patched in tests, the dev stack is broken.

**Files:**
- Modify: `mock_database/init.sql`

---

- [ ] **Step 4.1 — Write a test that the poracle schema query works against the mock seed**

Add to `tests/modules/test_notifications_db.py` (new file):

```python
"""Smoke-test the poracle DB schema expected by Notifications.

These run against the conftest autouse mock, so they verify SQL shape
(column names, table names) without a live database. A separate
integration-style comment documents what the init.sql must provide.
"""
from unittest.mock import MagicMock, patch
import pytest
from cogs.notifications import Notifications


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.ADMIN_USERS_IDS = ["42"]
    poliswag.poracle = MagicMock()
    poliswag.quest_search.pokemon_name_map = {"25": "pikachu"}
    poliswag.quest_search.get_pokemon_id_by_pokemon_name_map = MagicMock(return_value=["25"])
    with patch("cogs.notifications.DatabaseConnector"):
        c = Notifications(poliswag)
    c.poracle_db = MagicMock()
    return c


def test_resolve_targets_issues_channel_type_query(cog):
    cog.poracle_db.get_data_from_database.return_value = []
    cog._resolve_targets("raros")
    call_args = cog.poracle_db.get_data_from_database.call_args_list
    sql = " ".join(c.args[0] for c in call_args)
    assert "humans" in sql
    assert "discord:channel" in sql


def test_rule_exists_queries_monsters_table(cog):
    cog.poracle_db.get_data_from_database.return_value = []
    result = cog._rule_exists("111", 25, 0, 0)
    assert result is False
    sql = cog.poracle_db.get_data_from_database.call_args.args[0]
    assert "monsters" in sql
    assert "pokemon_id" in sql
```

Run:
```bash
python -m pytest tests/modules/test_notifications_db.py -v
```
Expected: **PASS** (mocked DB, just verifying SQL shape).

---

- [ ] **Step 4.2 — Append poracle schema to `mock_database/init.sql`**

Add at the end of `mock_database/init.sql`:

```sql
-- ============================================================
-- Poracle-NG database (mirrors the schema used by Notifications cog)
-- ============================================================
CREATE DATABASE IF NOT EXISTS poracle;
USE poracle;

CREATE TABLE IF NOT EXISTS `humans` (
  `id`      varchar(50)  NOT NULL,
  `name`    varchar(100) NOT NULL DEFAULT '',
  `type`    varchar(30)  NOT NULL DEFAULT 'discord:channel',
  `enabled` tinyint(1)   NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `monsters` (
  `uid`        int(10) unsigned NOT NULL AUTO_INCREMENT,
  `id`         varchar(50)      NOT NULL,
  `pokemon_id` smallint(5)      NOT NULL DEFAULT 0,
  `min_iv`     tinyint(3)       NOT NULL DEFAULT 0,
  `max_iv`     tinyint(3)       NOT NULL DEFAULT 100,
  `min_cp`     smallint(5)      NOT NULL DEFAULT 0,
  `max_cp`     smallint(5)      NOT NULL DEFAULT 9000,
  PRIMARY KEY (`uid`),
  KEY `ix_monsters_id` (`id`),
  KEY `ix_monsters_pokemon` (`id`, `pokemon_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Seed: two paired channels (leiria-raros + marinha-raros) for dev smoke tests
INSERT INTO `humans` (`id`, `name`, `type`, `enabled`) VALUES
  ('111111111111111111', 'leiria-raros',  'discord:channel', 1),
  ('222222222222222222', 'marinha-raros', 'discord:channel', 1),
  ('333333333333333333', 'leiria-100iv',  'discord:channel', 1),
  ('444444444444444444', 'marinha-100iv', 'discord:channel', 1);

-- Seed: one tracking rule per paired channel (Tyranitar, any IV)
INSERT INTO `monsters` (`id`, `pokemon_id`, `min_iv`, `max_iv`, `min_cp`, `max_cp`) VALUES
  ('111111111111111111', 248, 0, 100, 0, 9000),
  ('222222222222222222', 248, 0, 100, 0, 9000),
  ('333333333333333333',   0, 100, 100, 0, 9000),
  ('444444444444444444',   0, 100, 100, 0, 9000);
```

---

- [ ] **Step 4.3 — Recreate the dev DB and verify**

```bash
make down && make up
```

Then:
```bash
docker compose exec db mysql -upoliswag -ppoliswag poracle -e "SELECT id, name FROM humans;"
```
Expected:
```
+--------------------+----------------+
| id                 | name           |
+--------------------+----------------+
| 111111111111111111 | leiria-raros   |
| 222222222222222222 | marinha-raros  |
...
```

---

- [ ] **Step 4.4 — Commit**

```bash
git add mock_database/init.sql tests/modules/test_notifications_db.py
git commit -m "feat(mock-db): add poracle schema and seed to dev database"
```

---

## Task 5 — Refresh Mock JSON Fixtures

**Problem:** `mock_data/scanner_status.json` has `"last_data": 1704067200` (January 2024). The bot uses this timestamp to decide if a worker is alive — in dev, all workers always appear dead/stale. Same issue in `device_status.json` (`dateLastMessageReceived` is a 2024 timestamp).

The fix is a standalone script that rewrites the JSON files with timestamps relative to `now()`. It is committed once so offline devs have valid data, and can be re-run with `make mock-data`.

**Files:**
- Create: `mock_data/refresh.py`
- Modify: `mock_data/scanner_status.json`
- Modify: `mock_data/device_status.json`
- Modify: `mock_data/account_status.json`
- Modify: `makefile`

---

- [ ] **Step 5.1 — Write a test that verifies fresh mock data is recent enough**

Add to `tests/modules/test_http_client.py` (or create a new section):

```python
import json
import time

def test_scanner_status_mock_timestamps_are_recent():
    """Mock data must have last_data within the last hour, or workers appear dead."""
    with open("mock_data/scanner_status.json") as f:
        data = json.load(f)
    now = int(time.time())
    for area in data.get("areas", []):
        for wm in area.get("worker_managers", []):
            for worker in wm.get("workers", []):
                age = now - worker["last_data"]
                assert age < 3600, (
                    f"Worker {worker['worker_id']} last_data is {age}s old — "
                    "run `make mock-data` to refresh timestamps"
                )
```

Run:
```bash
python -m pytest tests/modules/test_http_client.py::test_scanner_status_mock_timestamps_are_recent -v
```
Expected: **FAIL** (current timestamps are ~1 year old).

---

- [ ] **Step 5.2 — Create `mock_data/refresh.py`**

```python
#!/usr/bin/env python3
"""Refresh mock_data/ JSON files with timestamps relative to now.

Run via: python mock_data/refresh.py
Or:      make mock-data
"""
import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
NOW = int(time.time())
NOW_MS = int(time.time() * 1000)  # milliseconds, used by device_status


def write(name: str, data: object) -> None:
    path = ROOT / name
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"  wrote {path}")


def refresh_scanner_status() -> None:
    data = {
        "areas": [
            {
                "name": "LeiriaBigger",
                "worker_managers": [
                    {
                        "expected_workers": 3,
                        "workers": [
                            {
                                "worker_id": f"leiria-worker-{i}",
                                "last_data": NOW,
                                "connection_status": "Executing Worker",
                            }
                            for i in range(1, 4)
                        ],
                    }
                ],
            },
            {
                "name": "MarinhaGrande",
                "worker_managers": [
                    {
                        "expected_workers": 1,
                        "workers": [
                            {
                                "worker_id": "marinha-worker-1",
                                "last_data": NOW,
                                "connection_status": "Executing Worker",
                            }
                        ],
                    }
                ],
            },
        ]
    }
    write("scanner_status.json", data)


def refresh_device_status() -> None:
    data = {
        "devices": [
            {
                "dateLastMessageReceived": NOW_MS,
                "dateLastMessageSent": NOW_MS,
                "deviceId": "PoGoLeiria",
                "init": False,
                "instanceNo": 4,
                "heartbeatCheckStatus": True,
                "isAlive": True,
                "lastMemory": {"memFree": 562348, "memMitm": 258972, "memStart": 0},
                "nextId": 115,
                "noMessagesReceived": 0,
                "noMessagesSent": 114,
                "origin": "MITM-PoGoLeiria",
                "version": 20241005,
            }
        ]
    }
    write("device_status.json", data)


def refresh_account_status() -> None:
    # Read existing and update only timestamp fields.
    path = ROOT / "account_status.json"
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    # account_status has no live timestamps that affect bot logic — leave as-is.
    write("account_status.json", data)


def refresh_quest_status() -> None:
    for fname in ("leiria_quest_scanning.json", "marinha_quest_scanning.json"):
        path = ROOT / fname
        try:
            data = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        write(fname, data)


if __name__ == "__main__":
    print("Refreshing mock_data/ timestamps...")
    refresh_scanner_status()
    refresh_device_status()
    refresh_account_status()
    refresh_quest_status()
    print("Done.")
```

---

- [ ] **Step 5.3 — Run the script and commit fresh data**

```bash
python mock_data/refresh.py
```

---

- [ ] **Step 5.4 — Run the timestamp test**

```bash
python -m pytest tests/modules/test_http_client.py::test_scanner_status_mock_timestamps_are_recent -v
```
Expected: **PASS**.

---

- [ ] **Step 5.5 — Add `make mock-data` to `makefile`**

```makefile
mock-data: ## Refresh mock_data/ JSON timestamps (run after long gaps)
	@echo "Refreshing mock data timestamps..."
	python mock_data/refresh.py
	@echo "Done. Commit the updated JSON files."
```

---

- [ ] **Step 5.6 — Commit**

```bash
git add mock_data/refresh.py mock_data/scanner_status.json mock_data/device_status.json \
        mock_data/account_status.json tests/modules/test_http_client.py makefile
git commit -m "feat(mock-data): add refresh.py to keep timestamps current"
```

---

## Task 6 — `docker compose watch` in Dev

**Problem:** Code changes require `make reload` (restarts the container) because the bind mount `.:/app` does not trigger a restart. Docker Compose v2 `watch` mode uses inotify to restart only the affected service when source files change — no manual step needed.

**Files:**
- Modify: `docker-compose.yaml`

---

- [ ] **Step 6.1 — Verify Docker Compose version supports watch**

```bash
docker compose version
```
Expected: `Docker Compose version v2.17.0` or later. `watch` was added in v2.22 for `--watch` flag; compose file `develop.watch` works from v2.17+.

---

- [ ] **Step 6.2 — Add `develop.watch` block to `docker-compose.yaml`**

```yaml
services:
  db:
    image: mariadb:10.11
    command: --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci --default-authentication-plugin=mysql_native_password --binlog-expire-logs-seconds=86400
    container_name: db
    restart: unless-stopped
    environment:
      MYSQL_ROOT_PASSWORD: root
      MYSQL_DATABASE: poliswag
      MYSQL_USER: poliswag
      MYSQL_PASSWORD: poliswag
    ports:
      - "3306:3306"
    volumes:
      - ./mock_database:/docker-entrypoint-initdb.d
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u", "root", "-proot"]
      interval: 5s
      timeout: 5s
      retries: 5

  poliswag:
    build: .
    image: poliswag
    container_name: poliswag
    restart: unless-stopped
    init: true
    ports:
      - "8989:8989"
    volumes:
      - .:/app
    depends_on:
      db:
        condition: service_healthy
    env_file:
      - .env
    command: >
      sh -c "pip install -q -r requirements-dev.txt && python main.py"
    develop:
      watch:
        - action: sync+restart
          path: .
          target: /app
          ignore:
            - .git/
            - __pycache__/
            - "*.pyc"
            - logs/
            - mock_database/
            - mock_data/
            - data/
            - .env
            - .env.test
    logging:
      driver: json-file
      options:
        max-size: "10m"
```

---

- [ ] **Step 6.3 — Add `make watch` target to `makefile`**

```makefile
watch: ## Start dev stack with file-watching auto-restart
	@echo "Starting Poliswag in watch mode..."
	docker compose -f $(DOCKER_COMPOSE_FILE) up --build --watch
```

---

- [ ] **Step 6.4 — Verify watch mode**

```bash
make watch
```

Edit any `.py` file (e.g., add a blank line to `main.py`), save. Expected: container restarts automatically within 2–3 seconds.

Revert the blank line:
```bash
git checkout main.py
```

---

- [ ] **Step 6.5 — Commit**

```bash
git add docker-compose.yaml makefile
git commit -m "feat(dev): add docker compose watch for auto-restart on code changes"
```

---

## Task 7 — Makefile Cleanup and Quality Gate

**Problem:** `make up` contains a self-copy bug. There is no `make check` target to run all quality gates in one shot. There is no `make migrate` to apply SQL migrations to the dev DB.

**Files:**
- Modify: `makefile`

---

- [ ] **Step 7.1 — Fix the self-copy bug**

In `makefile`, the `up` target has:
```makefile
@cp -n mock_data/*.json $(MOCK_DATA_DIR) || true
```
`MOCK_DATA_DIR` is `mock_data`, so this copies files to themselves. Remove those three lines entirely:

```makefile
up: ## Start the full application
	@echo "Starting Poliswag in $(ENV) environment..."
	docker compose -f $(DOCKER_COMPOSE_FILE) up -d --build
	@echo "Creating log files..."
	docker compose -f $(DOCKER_COMPOSE_FILE) exec poliswag /bin/bash -c "mkdir -p /app/logs && touch /app/logs/actions.log && touch /app/logs/error.log"
ifneq ($(ENV),PRODUCTION)
	@sleep 5
	docker compose -f $(DOCKER_COMPOSE_FILE) logs -f --tail=20
endif
	@echo "Poliswag started successfully in $(ENV) environment."
```

---

- [ ] **Step 7.2 — Add `make check` (one-shot quality gate)**

```makefile
check: ## Run all quality checks (format, lint, tests) — CI equivalent
	@echo "==> Format check"
	docker compose -f $(DOCKER_COMPOSE_FILE) exec $(CONTAINER_NAME) black --check .
	@echo "==> Lint"
	pre-commit run --all-files
	@echo "==> Tests"
	docker compose -f $(DOCKER_COMPOSE_FILE) exec $(CONTAINER_NAME) pytest
	@echo "All checks passed."
```

---

- [ ] **Step 7.3 — Add `make migrate` (apply SQL migrations to dev DB)**

```makefile
migrate: ## Apply all SQL migrations in migrations/ to the dev database
	@echo "Applying migrations..."
	@for f in migrations/*.sql; do \
	  echo "  applying $$f..."; \
	  docker compose -f $(DOCKER_COMPOSE_FILE) exec -T db \
	    mysql -upoliswag -ppoliswag poliswag < $$f; \
	done
	@echo "Migrations applied."
```

---

- [ ] **Step 7.4 — Add `make migrate` call to `make up` in dev**

Update the `up` target so migrations run automatically when the stack starts in dev:

```makefile
up: ## Start the full application
	@echo "Starting Poliswag in $(ENV) environment..."
	docker compose -f $(DOCKER_COMPOSE_FILE) up -d --build
	@echo "Creating log files..."
	docker compose -f $(DOCKER_COMPOSE_FILE) exec poliswag /bin/bash -c "mkdir -p /app/logs && touch /app/logs/actions.log && touch /app/logs/error.log"
ifneq ($(ENV),PRODUCTION)
	@sleep 5
	$(MAKE) migrate
	docker compose -f $(DOCKER_COMPOSE_FILE) logs -f --tail=20
endif
	@echo "Poliswag started successfully in $(ENV) environment."
```

---

- [ ] **Step 7.5 — Verify `make up` no longer prints a copy error**

```bash
make down && make up 2>&1 | grep -i "error\|cp: cannot"
```
Expected: no output (no errors).

---

- [ ] **Step 7.6 — Verify `make migrate` applies the existing migration**

```bash
make migrate
```
Expected: `applying migrations/001_add_last_weekly_digest_date.sql...` with no errors.

---

- [ ] **Step 7.7 — Commit**

```bash
git add makefile
git commit -m "build(makefile): fix self-copy bug, add make check / make migrate / make watch"
```

---

## Task 8 — Update README

**Problem:** The README Quick Start says `make up` and `make test` but does not explain local test runs, the `.env.test` file, the mock data refresh flow, or `make watch`.

**Files:**
- Modify: `README.md`

---

- [ ] **Step 8.1 — Update the Quick Start section**

Replace the current Quick Start block with:

````markdown
## Quick start

```bash
cp .env.example .env     # fill in tokens, channel IDs, DB creds
make install-hooks       # pre-commit + black
make up                  # ENV=DEVELOPMENT by default, PRODUCTION via .env
```

`make help` lists every target. Common ones:

| Target | What it does |
|---|---|
| `make up` | Build + start the stack (runs migrations automatically in dev) |
| `make watch` | Like `make up` but restarts the bot on every saved `.py` file |
| `make down` | Stop + remove containers and volumes |
| `make stop` | Stop without removing |
| `make reload` | Restart the bot and truncate log files |
| `make logs` | Tail the container logs |
| `make test` | Run `pytest` inside the container |
| `make test-local` | Run `pytest` locally (no Docker needed — requires `pip install -r requirements-dev.txt`) |
| `make check` | Run all quality gates (format, lint, tests) |
| `make format` / `make format-check` | `black` in write / check mode |
| `make lint` | Run all pre-commit hooks |
| `make dead-code` | `vulture` scan |
| `make migrate` | Apply SQL migrations in `migrations/` to the dev DB |
| `make mock-data` | Refresh `mock_data/` timestamps (run after a long dev gap) |

## Local test runs (no Docker)

```bash
pip install -r requirements-dev.txt
make test-local
```

Tests never open real DB connections — a `pytest` autouse fixture in `tests/conftest.py` patches `pymysql.connect` globally. `.env.test` is loaded automatically if no `DISCORD_API_KEY` is set.
````

---

- [ ] **Step 8.2 — Commit**

```bash
git add README.md
git commit -m "docs: update README with local test, watch mode, and mock-data instructions"
```

---

## Self-Review

### Spec coverage

| Requirement | Task |
|---|---|
| Easy setup everywhere | Tasks 2, 3, 7, 8 |
| Tests run locally without Docker | Tasks 1, 3 |
| Mock DB improvements | Task 4 (poracle schema) |
| Mock JSON fixtures | Task 5 |
| Dev inner loop (auto-restart) | Task 6 |
| Makefile quality | Task 7 |
| Prod image not bloated with test deps | Task 2 |
| Migrations applied automatically | Task 7 |
| Documentation | Task 8 |

### Placeholder scan

✅ No TBDs. Every step has exact code or exact commands with expected output.

### Type consistency

- `DatabaseConnector` constructor signature unchanged throughout.
- `make mock-data` → `mock_data/refresh.py` → consistent naming.
- `make watch` → `docker compose ... --watch` → consistent.
- `_prevent_real_db_connections` fixture name used in exactly one place.

### Open risks

1. `docker compose watch` requires Compose v2.17+. The `Step 6.1` check catches this.
2. `pymysql.connect` autouse patch interacts with `test_database_connector.py` which uses `DatabaseConnector.__new__` and never calls `connect_to_db()` directly. The patch is safe — those tests bypass `__init__` entirely and inject their own cursor mocks.
3. The `poracle` DB schema in Task 4 is a minimal approximation of Poracle-NG's actual schema. If the real Poracle-NG adds columns, only the `init.sql` needs updating — the `Notifications` cog queries only `id`, `name`, `enabled`, `type` (humans) and `uid`, `id`, `pokemon_id`, `min_iv`, `min_cp` (monsters).
