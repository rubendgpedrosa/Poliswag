# Lures Revival Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the `!lures` / `!uselure` commands so admins can list free, healthy Dragonite accounts (with credentials) and track a per-account lure budget.

**Architecture:** A new read-only `DatabaseConnector` against the `dragonite` schema finds available+healthy accounts; the existing `poliswag.account_lure` table tracks remaining lures. A `LureManager` service (instantiated on the bot like the other services) holds both, and a new admin-only `cogs/lures.py` exposes the commands. Dragonite's `account` table is never written — Poliswag only writes its own `account_lure` table.

**Tech Stack:** Python 3.11, discord.py, pymysql via `modules/database_connector.py`, pytest + pytest-mock (`asyncio_mode = auto`), MariaDB.

**Spec:** `docs/superpowers/specs/2026-06-22-lures-revival-design.md`

**Conventions to follow (verified against the codebase):**
- DB access: `db.get_data_from_database(query, params=...)` returns `list[dict]`; `db.execute_query_to_database(query, params=...)` returns affected-row count. Always parameterize user input with `%s` + `params=(...)`.
- Cogs: admin gate via `def cog_check(self, ctx): return str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS`. Command bodies are invoked in tests as `CogClass.command_name.callback(cog, ctx, ...)`.
- Embeds: `from modules.embeds import build_embed` → `build_embed(title, description="", footer=None)`.
- Logging: `self.poliswag.utility.log_to_file(msg, "ERROR")` (level optional, defaults to info).
- Run a single test file: `pytest tests/<path> -v` (coverage opts apply automatically).

---

## File Structure

- **Create** `modules/lure_manager.py` — `LureManager` service: dragonite availability query + `account_lure` seed/select/update.
- **Create** `cogs/lures.py` — admin-only `Lures` cog with `!lures` and `!uselure`.
- **Create** `migrations/004_add_account_lure.sql` — idempotent `account_lure` table for any env missing it.
- **Create** `tests/modules/test_lure_manager.py` — unit tests (mocked connectors).
- **Create** `tests/cogs/test_lures.py` — cog tests (mocked `poliswag.lure_manager`).
- **Modify** `modules/config.py` — add `DB_DRAGONITE`.
- **Modify** `.env.example` — add `DB_DRAGONITE="dragonite"`.
- **Modify** `main.py` — instantiate `LureManager`, load `cogs.lures`.
- **Modify** `mock_database/init.sql` — add `dragonite.account` + `poliswag.account_lure` for dev parity.

---

## Task 1: Config + env var for the dragonite schema

**Files:**
- Modify: `modules/config.py:28` (Database block)
- Modify: `.env.example` (Database section)

- [ ] **Step 1: Add the config var**

In `modules/config.py`, in the `# Database` block, after the `DB_SCANNER_NAME` line, add:

```python
    DB_DRAGONITE = os.environ.get("DB_DRAGONITE", "dragonite")
```

- [ ] **Step 2: Add it to `.env.example`**

In `.env.example`, next to the other `DB_*` vars (after `DB_POLISWAG="DB_POLISWAG"`), add:

```
DB_DRAGONITE="dragonite"
```

- [ ] **Step 3: Verify import still works**

Run: `python -c "from modules.config import Config; print(Config.DB_DRAGONITE)"`
Expected: prints `dragonite` (or the value of `DB_DRAGONITE` if set in `.env`).

- [ ] **Step 4: Commit**

```bash
git add modules/config.py .env.example
git commit -m "feat(P152): add DB_DRAGONITE config for dragonite schema access"
```

---

## Task 2: Migration + mock-DB parity for `account_lure` and `dragonite.account`

**Files:**
- Create: `migrations/004_add_account_lure.sql`
- Modify: `mock_database/init.sql` (poliswag block ~line 838; add a new dragonite block)

- [ ] **Step 1: Create the migration**

Create `migrations/004_add_account_lure.sql`:

```sql
CREATE TABLE IF NOT EXISTS account_lure (
  username VARCHAR(50) NOT NULL PRIMARY KEY,
  nb_lures INT NOT NULL DEFAULT 12
);
```

- [ ] **Step 2: Add `account_lure` to the mock DB poliswag block**

In `mock_database/init.sql`, inside the `poliswag` database section (after `USE poliswag;`, near the other poliswag-owned tables like `tracked_quest_reward`), add:

```sql
DROP TABLE IF EXISTS `account_lure`;
CREATE TABLE `account_lure` (
  `username` varchar(50) NOT NULL,
  `nb_lures` int(11) NOT NULL DEFAULT 12,
  PRIMARY KEY (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

LOCK TABLES `account_lure` WRITE;
INSERT INTO `account_lure` (`username`, `nb_lures`) VALUES
  ('free_low', 2),
  ('free_mid', 7),
  ('free_zero', 0);
UNLOCK TABLES;
```

- [ ] **Step 3: Add a minimal `dragonite` schema block to the mock DB**

In `mock_database/init.sql`, at the end of the file, add a new database block (mirrors the existing `CREATE DATABASE ... USE ...` style). Seed rows cover: free+healthy (`free_low`, `free_mid`, `free_zero`, `free_new`), in-cooldown (`busy_cooldown`), in-use (`busy_selected`), and unhealthy (`bad_invalid`):

```sql
DROP DATABASE IF EXISTS dragonite;
CREATE DATABASE IF NOT EXISTS dragonite;
USE dragonite;

DROP TABLE IF EXISTS `account`;
CREATE TABLE `account` (
  `username` varchar(64) NOT NULL,
  `password` varchar(64) NOT NULL,
  `warn` tinyint(1) unsigned DEFAULT 0,
  `suspended` tinyint(1) unsigned DEFAULT 0,
  `banned` tinyint(1) unsigned DEFAULT 0,
  `invalid` tinyint(1) NOT NULL DEFAULT 0,
  `auth_banned` int(11) unsigned NOT NULL DEFAULT 0,
  `last_selected` int(11) DEFAULT NULL,
  `last_released` int(11) DEFAULT NULL,
  `next_available_time` int(11) DEFAULT NULL,
  PRIMARY KEY (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

LOCK TABLES `account` WRITE;
INSERT INTO `account`
  (`username`,`password`,`warn`,`suspended`,`banned`,`invalid`,`auth_banned`,`last_selected`,`last_released`,`next_available_time`)
VALUES
  ('free_low','pw_low',0,0,0,0,0,100,200,NULL),
  ('free_mid','pw_mid',0,0,0,0,0,100,200,NULL),
  ('free_zero','pw_zero',0,0,0,0,0,100,200,NULL),
  ('free_new','pw_new',0,0,0,0,0,NULL,NULL,NULL),
  ('busy_cooldown','pw_cd',0,0,0,0,0,100,200,2000000000),
  ('busy_selected','pw_sel',0,0,0,0,0,300,200,NULL),
  ('bad_invalid','pw_bad',0,0,0,1,0,100,200,NULL);
UNLOCK TABLES;
```

- [ ] **Step 4: Commit**

```bash
git add migrations/004_add_account_lure.sql mock_database/init.sql
git commit -m "feat(P152): add account_lure migration + dragonite/account_lure mock-db parity"
```

---

## Task 3: `LureManager` service (TDD)

**Files:**
- Create: `modules/lure_manager.py`
- Test: `tests/modules/test_lure_manager.py`

The service uses two databases: `self.db` (poliswag, for `account_lure`) and `self.dragonite_db` (read-only dragonite `account`). In tests we patch `DatabaseConnector` at the import site so `__init__` doesn't open a real connection, then replace both db attributes with mocks.

- [ ] **Step 1: Write the failing tests**

Create `tests/modules/test_lure_manager.py`:

```python
"""Tests for modules.lure_manager.LureManager.

LureManager opens a dragonite DatabaseConnector in __init__; we patch it at
the import site and use poliswag.db (a MagicMock) for the account_lure table.
After construction we replace dragonite_db with a MagicMock too.
"""

from unittest.mock import MagicMock, patch

import pytest

from modules.lure_manager import LureManager, DEFAULT_LURE_COUNT, MAX_LISTED


@pytest.fixture
def manager():
    poliswag = MagicMock()
    poliswag.db = MagicMock()
    with patch("modules.lure_manager.DatabaseConnector") as conn_cls:
        m = LureManager(poliswag)
    m.dragonite_db = MagicMock()
    # default: no rows unless a test sets them
    poliswag.db.get_data_from_database.return_value = []
    m.dragonite_db.get_data_from_database.return_value = []
    return m


class TestAvailableQuery:
    def test_query_filters_health_cooldown_and_selection(self, manager):
        manager.list_available_with_lures()
        sql = manager.dragonite_db.get_data_from_database.call_args.args[0]
        assert "FROM account" in sql
        assert "banned = 0" in sql
        assert "suspended = 0" in sql
        assert "invalid = 0" in sql
        assert "warn = 0" in sql
        assert "auth_banned = 0" in sql
        assert "next_available_time" in sql
        assert "last_released >= last_selected" in sql


class TestSeeding:
    def test_seeds_missing_usernames_at_default_count(self, manager):
        manager.dragonite_db.get_data_from_database.return_value = [
            {"username": "free_new", "password": "pw"},
        ]
        # account_lure currently empty (first poliswag call), then select returns nothing
        manager.db.get_data_from_database.side_effect = [[], []]
        manager.list_available_with_lures()
        insert_call = manager.db.execute_query_to_database.call_args
        assert "INSERT INTO account_lure" in insert_call.args[0]
        assert insert_call.kwargs["params"] == ("free_new", DEFAULT_LURE_COUNT)

    def test_does_not_seed_existing_usernames(self, manager):
        manager.dragonite_db.get_data_from_database.return_value = [
            {"username": "free_low", "password": "pw"},
        ]
        manager.db.get_data_from_database.side_effect = [
            [{"username": "free_low"}],  # existing account_lure rows
            [{"username": "free_low", "nb_lures": 2}],  # selection
        ]
        manager.list_available_with_lures()
        manager.db.execute_query_to_database.assert_not_called()


class TestListing:
    def test_merges_password_and_count_sorted_and_capped(self, manager):
        # 6 available accounts; all already seeded
        avail = [
            {"username": f"u{i}", "password": f"p{i}"} for i in range(6)
        ]
        manager.dragonite_db.get_data_from_database.return_value = avail
        existing = [{"username": f"u{i}"} for i in range(6)]
        # selection returns up to MAX_LISTED rows, fewest-first, nb_lures > 0
        selected = [
            {"username": "u3", "nb_lures": 1},
            {"username": "u0", "nb_lures": 4},
            {"username": "u5", "nb_lures": 6},
            {"username": "u1", "nb_lures": 8},
            {"username": "u2", "nb_lures": 12},
        ]
        manager.db.get_data_from_database.side_effect = [existing, selected]
        result = manager.list_available_with_lures()
        assert [r["username"] for r in result] == ["u3", "u0", "u5", "u1", "u2"]
        assert result[0] == {"username": "u3", "password": "p3", "nb_lures": 1}
        # selection SQL caps at MAX_LISTED and sorts ascending
        sel_sql = manager.db.get_data_from_database.call_args.args[0]
        assert "nb_lures > 0" in sel_sql
        assert "ORDER BY nb_lures ASC" in sel_sql
        assert f"LIMIT {MAX_LISTED}" in sel_sql

    def test_returns_empty_when_no_available_accounts(self, manager):
        manager.dragonite_db.get_data_from_database.return_value = []
        result = manager.list_available_with_lures()
        assert result == []
        # no poliswag selection query issued when nothing is available
        manager.db.get_data_from_database.assert_not_called()


class TestAdjust:
    def test_update_floors_at_zero_and_returns_rowcount(self, manager):
        manager.db.execute_query_to_database.return_value = 1
        affected = manager.adjust_lure_count("free_low", -3)
        assert affected == 1
        call = manager.db.execute_query_to_database.call_args
        assert "UPDATE account_lure" in call.args[0]
        assert "GREATEST(nb_lures + %s, 0)" in call.args[0]
        assert call.kwargs["params"] == (-3, "free_low")

    def test_unknown_username_returns_zero(self, manager):
        manager.db.execute_query_to_database.return_value = 0
        assert manager.adjust_lure_count("ghost", 5) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/modules/test_lure_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.lure_manager'` (or ImportError for the symbols).

- [ ] **Step 3: Write the implementation**

Create `modules/lure_manager.py`:

```python
from modules.config import Config
from modules.database_connector import DatabaseConnector

DEFAULT_LURE_COUNT = 12
MAX_LISTED = 5

# An account is "available for lures" when it is healthy (no ban/suspend/
# warn/invalid/auth-ban flags), past any cooldown, and not currently selected
# by dragonite (released at or after its last selection). Dragonite's true
# in-use set is in-memory only, so this is a close DB approximation.
_AVAILABLE_ACCOUNTS_SQL = """
    SELECT username, password
    FROM account
    WHERE banned = 0 AND suspended = 0 AND invalid = 0
      AND warn = 0 AND auth_banned = 0
      AND (next_available_time IS NULL OR next_available_time <= UNIX_TIMESTAMP())
      AND (last_selected IS NULL OR last_released >= last_selected)
"""


class LureManager:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.db = poliswag.db  # poliswag DB — owns account_lure (read/write)
        self.dragonite_db = DatabaseConnector(Config.DB_DRAGONITE)  # read-only

    def _get_available_accounts(self):
        return self.dragonite_db.get_data_from_database(_AVAILABLE_ACCOUNTS_SQL)

    def _seed_missing(self, usernames):
        existing_rows = self.db.get_data_from_database(
            "SELECT username FROM account_lure"
        )
        existing = {row["username"] for row in existing_rows}
        for username in usernames:
            if username not in existing:
                self.db.execute_query_to_database(
                    "INSERT INTO account_lure (username, nb_lures) VALUES (%s, %s)",
                    params=(username, DEFAULT_LURE_COUNT),
                )

    def list_available_with_lures(self):
        accounts = self._get_available_accounts()
        passwords = {a["username"]: a["password"] for a in accounts}
        if not passwords:
            return []

        self._seed_missing(list(passwords.keys()))

        placeholders = ", ".join(["%s"] * len(passwords))
        rows = self.db.get_data_from_database(
            "SELECT username, nb_lures FROM account_lure "
            f"WHERE username IN ({placeholders}) AND nb_lures > 0 "
            f"ORDER BY nb_lures ASC LIMIT {MAX_LISTED}",
            params=tuple(passwords.keys()),
        )
        return [
            {
                "username": row["username"],
                "password": passwords[row["username"]],
                "nb_lures": row["nb_lures"],
            }
            for row in rows
        ]

    def adjust_lure_count(self, username, delta):
        return self.db.execute_query_to_database(
            "UPDATE account_lure SET nb_lures = GREATEST(nb_lures + %s, 0) "
            "WHERE username = %s",
            params=(delta, username),
        )
```

Note: `_seed_missing` runs `get_data_from_database` first (the "existing" call) and then `INSERT`s — matching the test's `side_effect` ordering (existing-rows call, then the selection call happens after seeding). In `test_returns_empty_when_no_available_accounts`, `passwords` is empty so we return before any poliswag query, satisfying `assert_not_called`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/modules/test_lure_manager.py -v`
Expected: PASS (all tests green).

- [ ] **Step 5: Commit**

```bash
git add modules/lure_manager.py tests/modules/test_lure_manager.py
git commit -m "feat(P152): add LureManager service for available accounts + lure tracking"
```

---

## Task 4: `Lures` cog (TDD)

**Files:**
- Create: `cogs/lures.py`
- Test: `tests/cogs/test_lures.py`

The cog reads `self.poliswag.lure_manager` (instantiated on the bot in Task 5). In tests, `poliswag` is a MagicMock so `lure_manager` is auto-mocked.

- [ ] **Step 1: Write the failing tests**

Create `tests/cogs/test_lures.py`:

```python
"""Tests for cogs.lures.Lures.

The cog uses poliswag.lure_manager (a MagicMock here). build_embed is patched
at the import site inside cogs.lures so we assert on the args, not discord.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.lures import Lures


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.ADMIN_USERS_IDS = ["42"]
    poliswag.lure_manager = MagicMock()
    return Lures(poliswag)


def make_ctx(author_id="42"):
    ctx = MagicMock()
    ctx.author.id = author_id
    ctx.author.name = "tester"
    ctx.send = AsyncMock()
    return ctx


class TestCogCheck:
    def test_admin_passes(self, cog):
        assert cog.cog_check(make_ctx()) is True

    def test_non_admin_fails(self, cog):
        assert cog.cog_check(make_ctx(author_id="nope")) is False


class TestLures:
    async def test_empty_sends_no_accounts_message(self, cog):
        ctx = make_ctx()
        cog.lure_manager.list_available_with_lures.return_value = []
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.lures.callback(cog, ctx)
        ctx.send.assert_awaited_once_with(embed="EMBED")
        title, desc = be.call_args.args[0], be.call_args.args[1]
        assert "DISPONÍVEIS" in title
        assert "Não há contas" in desc

    async def test_lists_accounts_with_credentials_and_counts(self, cog):
        ctx = make_ctx()
        cog.lure_manager.list_available_with_lures.return_value = [
            {"username": "free_low", "password": "pw_low", "nb_lures": 2},
            {"username": "free_mid", "password": "pw_mid", "nb_lures": 7},
        ]
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.lures.callback(cog, ctx)
        desc = be.call_args.args[1]
        assert "free_low / pw_low — 2 lures" in desc
        assert "free_mid / pw_mid — 7 lures" in desc
        ctx.send.assert_awaited_once_with(embed="EMBED")


class TestUseLure:
    async def test_missing_args_sends_usage(self, cog):
        ctx = make_ctx()
        await Lures.uselure.callback(cog, ctx, username=None, number=None)
        cog.lure_manager.adjust_lure_count.assert_not_called()
        assert "Utilização" in ctx.send.call_args.args[0]

    async def test_non_integer_number_sends_usage(self, cog):
        ctx = make_ctx()
        await Lures.uselure.callback(cog, ctx, username="free_low", number="abc")
        cog.lure_manager.adjust_lure_count.assert_not_called()
        assert "inteiro" in ctx.send.call_args.args[0]

    async def test_unknown_username_reports_not_found(self, cog):
        ctx = make_ctx()
        cog.lure_manager.adjust_lure_count.return_value = 0
        await Lures.uselure.callback(cog, ctx, username="ghost", number="-2")
        assert "não foi encontrada" in ctx.send.call_args.args[0]

    async def test_remove_lures_success_logs_and_confirms(self, cog):
        ctx = make_ctx()
        cog.lure_manager.adjust_lure_count.return_value = 1
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.uselure.callback(cog, ctx, username="free_low", number="-3")
        cog.lure_manager.adjust_lure_count.assert_called_once_with("free_low", -3)
        cog.poliswag.utility.log_to_file.assert_called_once()
        desc = be.call_args.args[1]
        assert "3 lures removidas" in desc
        assert "free_low" in desc

    async def test_add_single_lure_uses_singular(self, cog):
        ctx = make_ctx()
        cog.lure_manager.adjust_lure_count.return_value = 1
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.uselure.callback(cog, ctx, username="free_low", number="1")
        cog.lure_manager.adjust_lure_count.assert_called_once_with("free_low", 1)
        desc = be.call_args.args[1]
        assert "1 lure adicionada" in desc
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/cogs/test_lures.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cogs.lures'`.

- [ ] **Step 3: Write the implementation**

Create `cogs/lures.py`:

```python
from discord.ext import commands

from modules.embeds import build_embed


class Lures(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.lure_manager = poliswag.lure_manager

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        return str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS

    @commands.command(
        name="lures",
        brief="Lista as contas disponíveis com lures",
        help="Mostra até 5 contas livres e saudáveis com lures disponíveis, "
        "com username, password e número de lures restantes.",
    )
    async def lures(self, ctx):
        accounts = self.lure_manager.list_available_with_lures()
        if not accounts:
            await ctx.send(
                embed=build_embed(
                    "LISTA DE CONTAS DISPONÍVEIS",
                    "Não há contas disponíveis com lures neste momento.",
                )
            )
            return

        lines = "\n".join(
            f"{a['username']} / {a['password']} — {a['nb_lures']} lures"
            for a in accounts
        )
        await ctx.send(embed=build_embed("LISTA DE CONTAS DISPONÍVEIS", lines))

    @commands.command(
        name="uselure",
        brief="Ajusta o número de lures de uma conta",
        help="Utilização: uselure USERNAME NUMERO. NUMERO positivo adiciona "
        "lures, negativo remove (mínimo 0).",
    )
    async def uselure(self, ctx, username: str = None, number: str = None):
        if username is None or number is None:
            await ctx.send("Utilização: `!uselure USERNAME NUMERO`")
            return
        try:
            delta = int(number)
        except ValueError:
            await ctx.send(
                "NUMERO tem de ser um inteiro. Utilização: `!uselure USERNAME NUMERO`"
            )
            return

        affected = self.lure_manager.adjust_lure_count(username, delta)
        if not affected:
            await ctx.send(f"A conta `{username}` não foi encontrada.")
            return

        amount = abs(delta)
        action = "removida" if delta < 0 else "adicionada"
        plural = "" if amount == 1 else "s"
        self.poliswag.utility.log_to_file(
            f"[LURES] @{ctx.author} ({ctx.author.id}): "
            f"{amount} lure{plural} {action}{plural} -> {username}"
        )
        await ctx.send(
            embed=build_embed(
                "LURES ATUALIZADAS",
                f"{amount} lure{plural} {action}{plural} da conta **{username}**.",
            )
        )


async def setup(poliswag):
    await poliswag.add_cog(Lures(poliswag))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/cogs/test_lures.py -v`
Expected: PASS (all tests green).

- [ ] **Step 5: Commit**

```bash
git add cogs/lures.py tests/cogs/test_lures.py
git commit -m "feat(P152): add admin-only lures cog (!lures, !uselure)"
```

---

## Task 5: Wire the service and cog into the bot

**Files:**
- Modify: `main.py:43` (after `self.device_manager = ...`) and `main.py:62-71` (`setup_hook`)

- [ ] **Step 1: Import and instantiate the service**

In `main.py`, add the import near the other module imports (after `from modules.device_manager import DeviceManager`):

```python
from modules.lure_manager import LureManager
```

In `Poliswag.__init__`, after `self.device_manager = DeviceManager(self)`, add:

```python
        self.lure_manager = LureManager(self)
```

(`self.db` is already assigned earlier in `__init__`, so `LureManager`'s use of `poliswag.db` is safe.)

- [ ] **Step 2: Load the cog**

In `setup_hook`, after `await self.load_extension("cogs.scheduled")`, add:

```python
        await self.load_extension("cogs.lures")
```

- [ ] **Step 3: Verify the whole suite still passes**

Run: `pytest -q`
Expected: all tests pass (existing suite + the new `test_lure_manager.py` and `test_lures.py`).

- [ ] **Step 4: Verify lint/format clean**

Run: `ruff check modules/lure_manager.py cogs/lures.py && black --check modules/lure_manager.py cogs/lures.py main.py modules/config.py`
Expected: no errors; if black reports changes, run `black` on the files and re-stage.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(P152): wire LureManager + lures cog into the bot"
```

---

## Final verification

- [ ] Run `pytest -q` — full suite green.
- [ ] (Optional, dev stack) `make migrate` applies `004` cleanly; bring up the dev stack and run `!lures` to confirm `dragonite.account` seed rows surface (`free_low`, `free_mid` appear; `free_zero` excluded for 0 lures; `busy_*` and `bad_invalid` excluded).
- [ ] Confirm `dragonite.account` is never written: `grep -nE "INSERT|UPDATE|DELETE" modules/lure_manager.py` shows only `account_lure` statements.

---

## Notes on bugs fixed vs. the original (P-82)

- **SQL injection:** original built queries with f-strings around usernames; this version parameterizes all user/data input.
- **Broken 5-cap:** original appended freshly-seeded accounts *after* the cap loop, exceeding 5 and breaking the fewest-first order; this version seeds before selecting and lets SQL enforce `ORDER BY ... LIMIT`.
