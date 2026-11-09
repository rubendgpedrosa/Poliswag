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
    "SHINY_ALERTS_ENABLED": "1",
    "SHUNDO_LEADERBOARD_ENABLED": "0",
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
    "HERMES_API_URL": "http://hermes.test.invalid",
    "HERMES_API_KEY": "",
    "HERMES_SESSION_ID": "",
    "HERMES_RUNS_DIR": "/tmp/poliswag-test/hermes-runs",
    "OWNER_DM_DEFAULT": "hermes",
    "POLISWAG_ENV_FILE": "/tmp/poliswag-test/env",
    "HERMISWAG_API_URL": "",
    "HERMISWAG_API_KEY": "",
    "HERMISWAG_SESSION_CHANNELS": "",
    "HERMISWAG_LEARN_CHANNELS": "",
    "HERMISWAG_PROACTIVE_CHANNELS": "",
    "HERMISWAG_PROACTIVE_STATE": "/tmp/poliswag-test/hermiswag-proactive.json",
    "SCANNER_GAME_VERSION_FILE": "/tmp/poliswag-test/scanner-game-version.json",
    "SCANNER_CARD_FILE": "/tmp/poliswag-test/scanner-card.json",
    "HERMISWAG_LEARNING_STATE": "/tmp/poliswag-test/hermiswag-learning.json",
    "CLAUDE_API_URL": "http://claude.test.invalid",
    "CLAUDE_API_KEY": "",
    "CLAUDE_RUNS_DIR": "/tmp/poliswag-test/claude-runs",
    "POLISWAG_PRIVATE_NAMES": "zephyr,quill",
    "QUEST_MAP_URL": "http://tiles.test.invalid",
    "QUEST_MAP_STYLE": "osm-bright",
    # Dragonite's fence API is never reached in tests: every test runs with
    # the deterministic snapshot installed by _install_scan_areas below.
    "DRAGONITE_API_URL": "http://dragonite.test.invalid",
    "SCAN_AREA_CACHE": "/tmp/poliswag-test/scan_areas.json",
    "MEGA_JSON_OUTPUT": "/tmp/poliswag-test/megas.json",
    "MEGA_SPRITES_DIR": "/tmp/poliswag-test/sprites/mega",
    "OWNER_ALERT_ENDPOINT": "",
    # Levelling pool off by default; tests that draw it patch Config.ENDPOINTS.
    "SCANNER_LEVEL_ACCOUNTS_STATUS_ENDPOINT": "",
    "LEVEL_DEVICE_STATUS_ENDPOINT": "",
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


# The five seed rows of migrations/040_add_scan_areas.sql, and fences for the
# two active ones that reproduce the old longitude split (east of -8.9 is
# Leiria, at or west of it is Marinha Grande) for tests that predate the
# registry. Tests about fences themselves reset and install their own.
_SEED_AREAS = (
    {
        "area_key": "leiria",
        "label": "Leiria",
        "dragonite_area": "Leiria",
        "poracle_area": "leiria",
        "alert_role": "AlertasLeiria",
        "sort_order": 1,
        "active": 1,
    },
    {
        "area_key": "marinha",
        "label": "Marinha Grande",
        "dragonite_area": "MarinhaGrande",
        "poracle_area": "marinhagrande",
        "alert_role": "AlertasMarinha",
        "sort_order": 2,
        "active": 1,
    },
    {
        "area_key": "batalha",
        "label": "Batalha",
        "dragonite_area": "Batalha",
        "poracle_area": "batalha",
        "alert_role": "AlertasBatalha",
        "sort_order": 3,
        "active": 0,
    },
    {
        "area_key": "portodemos",
        "label": "Porto de Mós",
        "dragonite_area": "PortoDeMos",
        "poracle_area": "portodemos",
        "alert_role": "AlertasPortoDeMos",
        "sort_order": 4,
        "active": 0,
    },
    {
        "area_key": "moita",
        "label": "Moita",
        "dragonite_area": "Moita",
        "poracle_area": "moita",
        "alert_role": "AlertasMoita",
        "sort_order": 5,
        "active": 0,
    },
)


def _square(lat_min, lon_min, lat_max, lon_max):
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon_min, lat_min],
                [lon_max, lat_min],
                [lon_max, lat_max],
                [lon_min, lat_max],
                [lon_min, lat_min],
            ]
        ],
    }


_SEED_FENCES = {
    # The old public map box, split at -8.9: east of it (and up to -8.65) is
    # Leiria, at or west of it (down to -9.1) is Marinha Grande. The union is
    # what the public snapshot reports as its bbox.
    "leiria": _square(39.6, -8.899, 39.9, -8.65),
    "marinha": _square(39.6, -9.1, 39.9, -8.9),
}


@pytest.fixture(autouse=True)
def _install_scan_areas():
    """Deterministic registry/fence snapshot; no test reads the live values."""
    from modules import scan_areas

    scan_areas.install(_SEED_AREAS, _SEED_FENCES)
    try:
        yield
    finally:
        scan_areas.reset()


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
