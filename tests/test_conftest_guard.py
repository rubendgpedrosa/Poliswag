"""Verify that tests never open real DB connections regardless of Config.

Also pins the deterministic test environment: every env key
modules/config.py reads must resolve to a test-safe value even when the run
starts in another directory with hostile ambient values or a hostile .env
file, automatic dotenv discovery must never run, and only the explicit
disposable-integration selectors may survive from the ambient env.
Production secrets are never read or printed here.
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pymysql
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).parent.parent

# The only ambient values a test run honours: they opt into the throwaway
# MariaDB suites under tests/integration/.
INTEGRATION_SELECTORS = (
    "EVENT_SQL_TEST_PORT",
    "HUNDO_SQL_TEST_PORT",
    "HUNDO_SQL_TEST_PASSWORD",
    "SPAWN_HISTORY_SQL_TEST_HOST",
)

_HOSTILE_AMBIENT = {
    # Hostile stand-ins for ambient/production values. Every one of them
    # must be replaced by the deterministic test environment.
    "DISCORD_API_KEY": "unsafe-ambient-discord-key",
    "ENV": "PRODUCTION",
    "DB_HOST": "unsafe-prod-db.internal",
    "DB_USER": "unsafe-prod-user",
    "DB_PASSWORD": "unsafe-prod-secret",
    "SHINY_ALERT_USER_IDS": "999999",
    "SHINY_EVENT_DIGESTS_ENABLED": "true",
    "SHINY_LEADERBOARD_ENABLED": "1",
    "OWNER_ALERT_ENDPOINT": "https://unsafe.example/hook",
    "AGENT_JOBS_DIR": "/unsafe/jobs",
    "PORACLE_API_URL": "http://unsafe-poracle:3030",
    "QUEST_MAP_URL": "https://unsafe-tiles.example",
    "MEGA_JSON_OUTPUT": "/unsafe/megas.json",
    "MEGA_SPRITES_DIR": "/unsafe/sprites",
    # Inherited-disabled-flag case: the explicit .env.test load must work
    # even when a parent process already disabled dotenv discovery.
    "PYTHON_DOTENV_DISABLED": "true",
}

_SELECTOR_PROBE = {
    "EVENT_SQL_TEST_PORT": "33990",
    "HUNDO_SQL_TEST_PORT": "33991",
    "HUNDO_SQL_TEST_PASSWORD": "unsafe-probe-password",
    "SPAWN_HISTORY_SQL_TEST_HOST": "example.invalid",
}


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


def test_env_test_is_loaded():
    """Config values must be non-None when running pytest."""
    from modules.config import Config

    assert Config.DB_HOST is not None, "DB_HOST is None — .env.test not loaded"
    assert Config.DISCORD_API_KEY is not None, "DISCORD_API_KEY is None"


def _load_conftest_module(name, path):
    """Execute a conftest.py by path, as a fresh interpreter would."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config_env_keys():
    """Every env key modules/config.py reads from the process environment."""
    src = (REPO_ROOT / "modules" / "config.py").read_text()
    assert "os.getenv(" not in src, "config.py must read env via os.environ.get only"
    assert "environ[" not in src, "config.py must read env via os.environ.get only"
    return set(re.findall(r'os\.environ\.get\(\s*"([A-Z0-9_]+)"', src))


def test_every_config_env_key_has_a_deterministic_test_value():
    """No Config key may fall through to ambient values or a production .env.

    Fails when modules/config.py starts reading a key that neither the
    repo-root .env.test nor conftest's explicit test-safe defaults cover, so
    the deterministic environment cannot drift out of sync with Config.
    """
    conftest = _load_conftest_module(
        "poliswag_tests_conftest", REPO_ROOT / "tests" / "conftest.py"
    )
    pinned = set(dotenv_values(REPO_ROOT / ".env.test")) | set(
        conftest._TEST_ENV_DEFAULTS
    )
    missing = _config_env_keys() - pinned
    assert (
        not missing
    ), "Config env keys without deterministic test values: " + ", ".join(
        sorted(missing)
    )
    assert os.environ.get(
        "PYTHON_DOTENV_DISABLED"
    ), "automatic dotenv discovery must stay disabled during tests"


def test_config_values_are_test_safe():
    """Ambient values and a production .env must not reach Config."""
    from modules.config import Config

    assert Config.ENV == "DEVELOPMENT"
    assert Config.DISCORD_API_KEY == "test_token"
    assert Config.DB_HOST == "localhost"
    assert Config.DB_PORT == 3306
    assert Config.DB_USER == "poliswag"
    assert Config.DB_PASSWORD == "poliswag"
    assert Config.DB_POLISWAG == "poliswag"
    assert Config.SHINY_EVENT_DIGESTS_ENABLED is False
    assert Config.SHINY_LEADERBOARD_ENABLED is False
    assert Config.SHINY_ALERT_USER_IDS == [Config.MY_ID] == [111]
    assert not Config.ENDPOINTS["owner_alert"]
    assert Config.AGENT_JOBS_DIR == "logs/agent-jobs"
    assert Config.PORACLE_API_URL == "http://localhost:3030"
    assert Config.QUEST_MAP_URL == "http://tiles.test.invalid"
    assert Config.MEGA_JSON_OUTPUT == "/tmp/poliswag-test/megas.json"
    assert Config.MEGA_SPRITES_DIR == "/tmp/poliswag-test/sprites/mega"
    assert Config.HERMES_RUNS_DIR == "/tmp/poliswag-test/hermes-runs"
    assert Config.CLAUDE_RUNS_DIR == "/tmp/poliswag-test/claude-runs"
    assert Config.PRIVATE_NAMES == ("zephyr", "quill")
    for name, url in Config.ENDPOINTS.items():
        if url is not None:
            assert "192.168." not in url and "duckdns" not in url, name


def _assert_test_safe_config_lines():
    """Child-script lines asserting Config is test-safe without real DB/HTTP."""
    return [
        "from modules.config import Config",
        "assert Config.ENV == 'DEVELOPMENT'",
        "assert Config.DISCORD_API_KEY == 'test_token'",
        "assert (Config.DB_HOST, Config.DB_PORT) == ('localhost', 3306)",
        "assert (Config.DB_USER, Config.DB_PASSWORD) == ('poliswag', 'poliswag')",
        "assert Config.DB_POLISWAG == 'poliswag'",
        "assert Config.SHINY_EVENT_DIGESTS_ENABLED is False",
        "assert Config.SHINY_LEADERBOARD_ENABLED is False",
        "assert Config.SHINY_ALERT_USER_IDS == [111]",
        "assert not Config.ENDPOINTS['owner_alert']",
        "assert Config.AGENT_JOBS_DIR == 'logs/agent-jobs'",
        "assert Config.PORACLE_API_URL == 'http://localhost:3030'",
        "assert Config.QUEST_MAP_URL == 'http://tiles.test.invalid'",
        "assert Config.MEGA_JSON_OUTPUT == '/tmp/poliswag-test/megas.json'",
        "assert Config.MEGA_SPRITES_DIR == '/tmp/poliswag-test/sprites/mega'",
        "assert Config.HERMES_RUNS_DIR == '/tmp/poliswag-test/hermes-runs'",
        "assert Config.CLAUDE_RUNS_DIR == '/tmp/poliswag-test/claude-runs'",
        "assert Config.PRIVATE_NAMES == ('zephyr', 'quill')",
        "assert all(v is None or ('192.168.' not in v and 'duckdns' not in v)"
        " for v in Config.ENDPOINTS.values())",
    ]


def _assert_discovery_blocked_lines():
    """Child-script lines proving automatic dotenv discovery never runs.

    Spies on dotenv.main.find_dotenv without delegating: any discovery
    attempt is recorded (and neutered to "no file"), so the trailing assertion
    fails if Config's import-time load_dotenv ever searches for a .env.
    """
    return [
        "import dotenv.main",
        "discovery_calls = []",
        "def spy_find(*a, **k):",
        "    discovery_calls.append((a, k))",
        "    return ''",
        "dotenv.main.find_dotenv = spy_find",
    ]


def _run_child(script, cwd, extra_env):
    """Run a child probe; only its marker is read back, values stay inside."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(cwd) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(_SELECTOR_PROBE)
    env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-c", "\n".join(script)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "DETERMINISTIC_OK" in proc.stdout


def test_deterministic_env_from_foreign_cwd_with_hostile_ambient(tmp_path):
    """Another cwd plus unsafe ambient values must still yield test-safe Config.

    The child imports tests/conftest.py by path (as a fresh interpreter hits
    it before any Config import), spies on dotenv discovery to prove it never
    runs, then imports Config and reports only a marker -- values are
    asserted in-process and never printed.
    """
    script = [
        "import importlib.util, os",
        f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r})",
        "spec = importlib.util.spec_from_file_location(",
        "'poliswag_conftest_check', " f"{str(REPO_ROOT / 'tests' / 'conftest.py')!r})",
        "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)",
    ]
    script += _assert_discovery_blocked_lines()
    script += _assert_test_safe_config_lines()
    for name in INTEGRATION_SELECTORS:
        script.append(f"assert os.environ.get({name!r}) == {_SELECTOR_PROBE[name]!r}")
    script.append("assert discovery_calls == []")
    script.append("print('DETERMINISTIC_OK')")
    _run_child(script, tmp_path, _HOSTILE_AMBIENT)


def test_hostile_dotenv_in_working_dir_cannot_leak(tmp_path):
    """A hostile .env in the run directory must not reach Config either.

    Builds a fake repo (config + conftest env setup + .env.test + hostile
    .env), then imports the fake conftest by path and Config from it in a
    subprocess started inside that directory with hostile ambient values.
    Only a marker is printed; values are asserted in-process.
    """
    fake = tmp_path / "fakerepo"
    (fake / "tests").mkdir(parents=True)
    (fake / "modules").mkdir()
    (fake / "modules" / "__init__.py").write_text("")
    shutil.copy(REPO_ROOT / "modules" / "config.py", fake / "modules" / "config.py")
    shutil.copy(REPO_ROOT / "tests" / "conftest.py", fake / "tests" / "conftest.py")
    shutil.copy(REPO_ROOT / ".env.test", fake / ".env.test")
    (fake / ".env").write_text(
        "DISCORD_API_KEY=hostile-env-file-key\n"
        "ENV=PRODUCTION\n"
        "DB_HOST=hostile-file-db.internal\n"
        "DB_PASSWORD=hostile-file-secret\n"
        "OWNER_ALERT_ENDPOINT=https://hostile-file.example/hook\n"
        "SHINY_ALERT_USER_IDS=888888\n"
        "SHINY_EVENT_DIGESTS_ENABLED=true\n"
        "AGENT_JOBS_DIR=/hostile/file-jobs\n"
        "QUEST_MAP_URL=https://hostile-file-tiles.example\n"
        "MEGA_JSON_OUTPUT=/hostile/file-megas.json\n"
    )
    script = [
        "import importlib.util, os",
        "spec = importlib.util.spec_from_file_location("
        "'poliswag_fake_conftest', 'tests/conftest.py')",
        "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)",
    ]
    script += _assert_discovery_blocked_lines()
    script += _assert_test_safe_config_lines()
    for name in INTEGRATION_SELECTORS:
        script.append(f"assert os.environ.get({name!r}) == {_SELECTOR_PROBE[name]!r}")
    script.append("assert discovery_calls == []")
    script.append("print('DETERMINISTIC_OK')")
    _run_child(script, fake, _HOSTILE_AMBIENT)
