import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from dotenv import dotenv_values

# Deterministic test environment, applied before Config is imported by any
# test module. Every env key modules/config.py reads ends up pinned: keys
# present in the repo-root .env.test come from that file, and every other
# Config key gets the explicit test-safe default below. Both are applied
# unconditionally (override), so ambient values can never leak in; automatic
# dotenv discovery is then disabled outright, so a production .env is never
# even searched for. Merely loading .env.test would NOT be enough: keys
# absent from it (e.g. SHINY_EVENT_DIGESTS_ENABLED, SHINY_ALERT_USER_IDS,
# OWNER_ALERT_ENDPOINT, AGENT_JOBS_DIR) would still inherit ambient values,
# and absence alone is not a safe default either, so each one is set
# explicitly ("" disables every consumer: fetch_data truthiness-checks the
# URL, and the legacy owner webhook reads the file, not Config). The only
# ambient values honoured are the explicit disposable-integration selectors
# (EVENT_SQL_TEST_PORT, HUNDO_SQL_TEST_PORT, HUNDO_SQL_TEST_PASSWORD,
# SPAWN_HISTORY_SQL_TEST_HOST), which opt a run into the throwaway MariaDB
# suites under tests/integration/. Production secrets are never read or
# printed here.
_TEST_ENV_DEFAULTS = {
    "SHINY_LEADERBOARD_ENABLED": "0",
    "SHINY_ALERT_USER_IDS": "",
    "SHINY_EVENT_DIGESTS_ENABLED": "false",
    "TRADES_CHANNEL_ID": "0",
    "TRAP_CHANNEL_ID": "0",
    "DB_DRAGONITE": "dragonite",
    "POKEDEX_URL": "https://pogoleiria.pt/pokedex",
    "WEBSTATS_URL": "https://pogoleiria.pt/webstats",
    "ADB_DEVICE": "",
    "UNOWNHASH_COMPOSE_FILE": "/root/unonwhash/docker-compose.yml",
    "RECREATE_SERVICES": "dragonite rotom-ng",
    "HEARTBEAT_FILE": "logs/heartbeat",
    "AGENT_JOBS_DIR": "logs/agent-jobs",
    "QUEST_MAP_URL": "http://tiles.test.invalid",
    "QUEST_MAP_STYLE": "osm-bright",
    "MEGA_JSON_OUTPUT": "/tmp/poliswag-test/megas.json",
    "MEGA_SPRITES_DIR": "/tmp/poliswag-test/sprites/mega",
    "OWNER_ALERT_ENDPOINT": "",
}
for _key, _value in _TEST_ENV_DEFAULTS.items():
    os.environ[_key] = _value
del _key, _value
# Repo-root .env.test regardless of the cwd pytest started from, applied with
# override semantics so any ambient runtime config (a real DISCORD_API_KEY
# included) is replaced. Parsed with dotenv_values instead of load_dotenv so
# the explicit load is immune to an inherited PYTHON_DOTENV_DISABLED flag,
# which would otherwise make load_dotenv skip the file entirely.
_TEST_ENV_FILE = Path(__file__).parent.parent / ".env.test"
_TEST_ENV_VALUES = {
    key: value
    for key, value in dotenv_values(_TEST_ENV_FILE).items()
    if value is not None
}
os.environ.update(_TEST_ENV_VALUES)
del _TEST_ENV_FILE, _TEST_ENV_VALUES
# Block every later automatic .env discovery (notably Config.load_dotenv(),
# which would otherwise read a production .env from the working directory).
# The pinned python-dotenv returns early from load_dotenv while this flag is
# truthy, before any find_dotenv search runs.
os.environ["PYTHON_DOTENV_DISABLED"] = "true"

_REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="session", autouse=True)
def _refresh_mock_data_timestamps():
    """Regenerate mock_data/ JSON timestamps before the test session runs.

    mock_data/scanner_status.json and device_status.json embed absolute
    timestamps used to decide whether workers/devices look "alive". Left
    untouched between test runs they silently age past the 1h freshness
    window a test asserts on, so refresh unconditionally here instead of
    relying on someone remembering to run `make mock-data` first.
    """
    subprocess.run(
        [sys.executable, "mock_data/refresh.py"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
    )


@pytest.fixture(autouse=True)
def _prevent_real_db_connections():
    """Block all pymysql.connect calls so tests never need a live database."""
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
