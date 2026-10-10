# Shiny Prediction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Members give Poliswag a friend code, a host script has a scanner account resolve it to the game's hidden player ID, and Poliswag DMs the owner when a live spawn will be shiny for him.

**Architecture:** Three pieces joined only by tables in the `poliswag` database. A Poliswag cog stores codes; a host-side script (`scripts/shiny_linker`, run by a systemd timer) drives one scanner account through a private raid lobby to read the ID; a Poliswag scheduler step computes the roll for new `golbat.pokemon` rows and sends DMs. The linker attaches a temporary hook to the game only while codes are pending.

**Tech Stack:** Python 3.11, discord.py, pymysql, pytest (asyncio_mode auto), MariaDB, systemd, adb, `websocket-client` (linker venv only), Frida-style injector already on the phone.

**Spec:** `docs/superpowers/specs/2026-10-10-shiny-prediction-design.md`. Background and the proven procedure: `/root/unonwhash/CLAUDE-HANDOVER.md`.

**Read before starting**

- The production bot runs from `/root/Poliswag` (bind-mounted into the container), so **never develop in that checkout**: a watchdog restart would load half-written code. All tasks up to Task 13 happen in a git worktree.
- Tests run on the host: `pytest tests/...` from the worktree root. `.env.test` supplies dummy settings; `tests/conftest.py` blocks real database connections.
- Migrations are replayed on every bot start, so every statement must be re-runnable (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`); `tests/modules/test_migrations.py` enforces it.
- User-facing text is Portuguese. Commit messages in this repo are plain sentences ("The bot no longer probes the site; …"), not `feat:` prefixes.
- Tasks 1–12 touch no phone, no scanner container and no live database. Task 13 is the only one that does, and each of its live steps needs the owner present.

## File structure

| File | Responsibility |
|---|---|
| `migrations/017_add_shiny.sql` | `shiny_link`, `shiny_alert`, state columns on `poliswag` |
| `modules/shiny_roll.py` | The roll; player-ID and friend-code validation. Stdlib only (the linker imports it too) |
| `modules/membership.py` | `is_member(bot, user_id)`, moved out of the Pokédex cog so two cogs share it |
| `modules/shiny_store.py` | Poliswag's reads and writes of the two tables and the watermark |
| `cogs/shiny.py` | `!shiny` command and the verdict-button listener |
| `modules/shiny_alerts.py` | Scheduler step: announce finished links, alert on roll-0 spawns |
| `scripts/shiny_linker/proto.py` | Minimal protobuf encode/decode |
| `scripts/shiny_linker/rotom.py` | Rotom status and one controller session (login, RPC) |
| `scripts/shiny_linker/phone.py` | adb helpers, `quiet_scanner`, the hook's attach/detach |
| `scripts/shiny_linker/native-method-redirect.js` | The hook script (copied from the proven tool) |
| `scripts/shiny_linker/lobby.py` | Search by code, open lobby, invite, read ID, leave |
| `scripts/shiny_linker/store.py` | The linker's reads and writes (queue, state, raid gym, account) |
| `scripts/shiny_linker/linker.py` | Guards, one run, failure handling, breaker |
| `scripts/shiny_linker/__main__.py` | CLI: default run, `--check`, `--validate` |
| `scripts/shiny-linker.service`, `scripts/shiny-linker.timer` | systemd units |

---

### Task 0: Worktree

**Files:** none in the repo.

- [ ] **Step 1: Create the worktree and branch**

```bash
cd /root/Poliswag
git worktree add /root/worktrees/poliswag-shiny -b shiny-prediction
cp docs/superpowers/specs/2026-10-10-shiny-prediction-design.md /root/worktrees/poliswag-shiny/docs/superpowers/specs/
cp docs/superpowers/plans/2026-10-10-shiny-prediction.md /root/worktrees/poliswag-shiny/docs/superpowers/plans/
cd /root/worktrees/poliswag-shiny
```

The spec and plan are untracked files in the main checkout; the copies put them on the branch.

- [ ] **Step 2: Confirm the baseline is green**

Run: `cd /root/worktrees/poliswag-shiny && pytest -q 2>&1 | tail -3`
Expected: all tests pass (note the count; it only grows from here).

- [ ] **Step 3: Commit the spec and plan**

```bash
git add docs/superpowers/specs/2026-10-10-shiny-prediction-design.md docs/superpowers/plans/2026-10-10-shiny-prediction.md
git commit -m "Shiny prediction: design and implementation plan"
```

---

### Task 1: Migration

**Files:**
- Create: `migrations/017_add_shiny.sql`
- Test: `tests/modules/test_migrations.py` (existing, parametrised over every file)

- [ ] **Step 1: Write the migration**

```sql
-- Shiny prediction (docs/superpowers/specs/2026-10-10-shiny-prediction-design.md).
--
-- One row per Discord user who sent a friend code with !shiny. The host
-- script scripts/shiny_linker turns `pending` into `linked` or `failed`;
-- the friend code is cleared once a row is linked.
CREATE TABLE IF NOT EXISTS shiny_link (
  discord_id   BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  discord_name VARCHAR(64) NOT NULL,
  friend_code  CHAR(12) NULL,
  trainer_name VARCHAR(32) NULL,
  player_id    VARCHAR(21) NULL,
  status       ENUM('pending','linked','failed') NOT NULL DEFAULT 'pending',
  fail_reason  VARCHAR(64) NULL,
  attempts     TINYINT UNSIGNED NOT NULL DEFAULT 0,
  requested_at DATETIME NOT NULL,
  resolved_at  DATETIME NULL,
  announced_at DATETIME NULL,
  KEY status_requested (status, requested_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- One row per spawn a player was told about, so nobody hears of it twice;
-- `verdict` is the player's answer to "was it shiny?".
CREATE TABLE IF NOT EXISTS shiny_alert (
  discord_id   BIGINT UNSIGNED NOT NULL,
  encounter_id VARCHAR(25) NOT NULL,
  pokemon_id   SMALLINT UNSIGNED NOT NULL,
  form_id      SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  sent_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  verdict      ENUM('shiny','not_shiny') NULL,
  verdict_at   DATETIME NULL,
  PRIMARY KEY (discord_id, encounter_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Linker switch and bookkeeping, and the alert watermark (a golbat
-- first_seen_timestamp; NULL means never run, and the first tick only sets it).
ALTER TABLE poliswag
  ADD COLUMN IF NOT EXISTS shiny_linker_enabled TINYINT(1) NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS shiny_linker_disabled_reason VARCHAR(128) NULL,
  ADD COLUMN IF NOT EXISTS shiny_linker_last_run_at DATETIME NULL,
  ADD COLUMN IF NOT EXISTS shiny_linker_alerted_version VARCHAR(16) NULL,
  ADD COLUMN IF NOT EXISTS shiny_alert_watermark INT UNSIGNED NULL;
```

- [ ] **Step 2: Run the migration tests**

Run: `pytest -q tests/modules/test_migrations.py 2>&1 | tail -3`
Expected: PASS, with one more parametrised case (`017_add_shiny.sql`) than before.

- [ ] **Step 3: Commit**

```bash
git add migrations/017_add_shiny.sql
git commit -m "Migration 017: shiny links, alerts and linker state"
```

---

### Task 2: The roll

**Files:**
- Create: `modules/shiny_roll.py`
- Test: `tests/modules/test_shiny_roll.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The roll must match the community's JavaScript bit for bit: the vectors
below were produced by that snippet under Node, not by this module."""

import pytest

from modules.shiny_roll import is_player_id, normalise_friend_code, shiny_die

HEX = "0123456789abcdef"
GOOGLE = "123456789012345678901"


@pytest.mark.parametrize(
    "encounter_id, player_id, roll",
    [
        ("1", HEX, 113),
        ("9007199254740993", HEX, 408),  # above 2**53
        ("18446744073709551615", HEX, 253),  # largest uint64
        ("12345678901234567890", GOOGLE, 265),
        ("4611686018427387904", GOOGLE, 442),
        ("5550001112223334445", "fedcba9876543210", 27),
        ("10000000000000000168", HEX, 0),
        ("10000000000000000398", HEX, 0),
        ("7000000000000000028", GOOGLE, 0),
    ],
)
def test_matches_the_reference_implementation(encounter_id, player_id, roll):
    assert shiny_die(encounter_id, player_id) == roll


def test_an_integer_encounter_id_rolls_the_same():
    assert shiny_die(10000000000000000168, HEX) == 0


def test_a_non_numeric_encounter_id_is_an_error():
    with pytest.raises(ValueError):
        shiny_die("abc", HEX)


@pytest.mark.parametrize("value", [HEX, GOOGLE, "fedcba9876543210"])
def test_player_ids_in_the_two_known_shapes(value):
    assert is_player_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        None,
        "0123456789ABCDEF",  # upper case
        "0123456789abcde",  # 15
        "0123456789abcdef0",  # 17
        "12345678901234567890",  # 20 digits
        "E:abc+/=",  # the viewer-specific social ID
        HEX + "\n",
    ],
)
def test_anything_else_is_not_a_player_id(value):
    assert not is_player_id(value)


@pytest.mark.parametrize(
    "raw", ["123456789012", "1234 5678 9012", "1234-5678-9012", " 1234 5678 9012 "]
)
def test_friend_codes_are_normalised_to_twelve_digits(raw):
    assert normalise_friend_code(raw) == "123456789012"


@pytest.mark.parametrize(
    "raw", ["", None, "12345678901", "1234567890123", "1234 5678 901a", "remover"]
)
def test_what_is_not_a_friend_code(raw):
    assert normalise_friend_code(raw) is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest -q tests/modules/test_shiny_roll.py 2>&1 | tail -3`
Expected: collection error, `ModuleNotFoundError: No module named 'modules.shiny_roll'`.

- [ ] **Step 3: Write the module**

```python
"""Will this wild spawn be shiny for this player?

The game decides it from two values: the spawn's encounter ID and the
player's internal ID (the one a raid-lobby creator sees in
`invited_player_ids`, never the friend-code or GET_PLAYER identifiers, which
differ per viewer). SHA-1 over the encounter ID as eight little-endian bytes
followed by the ID text seeds a `java.util.Random`; its `nextInt(512)` is the
roll, and 0 is shiny at the base 1/512 rate. Because 512 is a power of two,
0 is also shiny at 1/64, 1/128 and 1/256.

Confirmed on 2026-10-10 against three scanner accounts (3 of 3 recorded
shinies roll 0, 0 of 201 non-shinies do): /root/unonwhash/CLAUDE-HANDOVER.md.

Standard library only: scripts/shiny_linker imports this from its own venv.
"""

import hashlib
import re

_MASK = (1 << 48) - 1
_MULTIPLIER = 0x5DEECE66D
_UINT64 = (1 << 64) - 1
# 16 lowercase hex for Pokémon Trainer Club accounts, 21 digits for Google.
_PLAYER_ID = re.compile(r"(?:[0-9a-f]{16}|[0-9]{21})\Z")
_FRIEND_CODE = re.compile(r"[0-9]{12}\Z")


def shiny_die(encounter_id, player_id):
    """The 512-sided roll (0-511) for one spawn and one player."""
    encounter = (int(encounter_id) & _UINT64).to_bytes(8, "little")
    seed = hashlib.sha1(encounter + player_id.encode("latin-1")).digest()[:8]
    # new java.util.Random(seed).nextInt(512)
    state = (int.from_bytes(seed, "little") ^ _MULTIPLIER) & _MASK
    state = (state * _MULTIPLIER + 11) & _MASK
    return state >> 39


def is_player_id(value):
    return bool(value) and _PLAYER_ID.match(value) is not None


def normalise_friend_code(raw):
    """Twelve digits, however they were typed; None if that is not what it is."""
    digits = re.sub(r"[\s-]", "", raw or "")
    return digits if _FRIEND_CODE.match(digits) else None
```

- [ ] **Step 4: Run the tests**

Run: `pytest -q tests/modules/test_shiny_roll.py 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/shiny_roll.py tests/modules/test_shiny_roll.py
git commit -m "The shiny roll, checked against the reference JavaScript"
```

---

### Task 3: Shared membership check

`!shiny` needs the same "is this DM author still in the server?" check the Pokédex cog has. Move it to a module; the Pokédex cog keeps its method and delegates.

**Files:**
- Create: `modules/membership.py`
- Modify: `cogs/pokedex.py` (the `is_member` method, around line 191)
- Test: `tests/modules/test_membership.py`

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import MagicMock

from modules.membership import is_member


def guild_with(*member_ids):
    guild = MagicMock()
    guild.members = [MagicMock(id=i) for i in member_ids]
    guild.get_member = lambda user_id: user_id if user_id in member_ids else None
    return guild


def test_a_cached_member_is_a_member():
    assert is_member(MagicMock(guilds=[guild_with(1, 2)]), 2) is True


def test_someone_not_in_any_guild_is_not():
    assert is_member(MagicMock(guilds=[guild_with(1, 2)]), 3) is False


def test_an_empty_member_cache_fails_open():
    assert is_member(MagicMock(guilds=[guild_with()]), 3) is True


def test_no_guilds_at_all_fails_open():
    assert is_member(MagicMock(guilds=[]), 3) is True
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest -q tests/modules/test_membership.py 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'modules.membership'`.

- [ ] **Step 3: Create the module**

```python
"""Is this Discord user in a guild the bot serves?

Commands that work in DMs need it: a DM channel outlives the membership
that created it. Used by cogs/pokedex.py and cogs/shiny.py.
"""


def is_member(bot, user_id):
    """Reads the member cache rather than fetching: `Intents.all()` keeps it
    populated, and a command shouldn't wait on an API call.

    Fails open when no guild has any member cached at all. An empty cache is
    the bot's problem, and locking every member out over it is worse than
    the hole it would close.
    """
    guilds = list(getattr(bot, "guilds", None) or [])
    cached = False
    for guild in guilds:
        if guild.get_member(user_id) is not None:
            return True
        cached = cached or bool(getattr(guild, "members", None))
    return not cached
```

- [ ] **Step 4: Make the Pokédex cog delegate**

In `cogs/pokedex.py`, add the import beside the other `modules` imports:

```python
from modules.membership import is_member
```

and replace the whole `is_member` method body (keep the method, it is called from several places and from tests) with:

```python
    def is_member(self, user_id):
        """Is this user in a guild the bot serves? See modules/membership.py."""
        return is_member(self.poliswag, user_id)
```

- [ ] **Step 5: Run the new tests and the Pokédex tests**

Run: `pytest -q tests/modules/test_membership.py tests/cogs/test_pokedex.py 2>&1 | tail -3`
Expected: PASS, with the Pokédex test count unchanged.

- [ ] **Step 6: Commit**

```bash
git add modules/membership.py cogs/pokedex.py tests/modules/test_membership.py
git commit -m "The server-membership check moves to a module two cogs can share"
```

---

### Task 4: Poliswag's shiny store

**Files:**
- Create: `modules/shiny_store.py`
- Test: `tests/modules/test_shiny_store.py`

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.shiny_store import ShinyStore


@pytest.fixture
def db():
    d = MagicMock()
    d.get_data_from_database = AsyncMock(return_value=[])
    d.execute_query_to_database = AsyncMock(return_value=1)
    d.execute_transaction = AsyncMock()
    return d


@pytest.fixture
def store(db):
    return ShinyStore(db)


async def test_get_is_none_for_a_stranger(store):
    assert await store.get(7) is None


async def test_get_returns_the_row(store, db):
    db.get_data_from_database.return_value = [{"status": "linked"}]
    assert await store.get(7) == {"status": "linked"}
    assert db.get_data_from_database.call_args.kwargs["params"] == (7,)


async def test_request_queues_the_code_and_resets_an_old_result(store, db):
    await store.request(7, "rita", "123456789012")
    sql = db.execute_query_to_database.call_args.args[0]
    assert db.execute_query_to_database.call_args.kwargs["params"] == (
        7,
        "rita",
        "123456789012",
    )
    assert "ON DUPLICATE KEY UPDATE" in sql
    for reset in (
        "status = 'pending'",
        "player_id = NULL",
        "trainer_name = NULL",
        "attempts = 0",
        "announced_at = NULL",
    ):
        assert reset in sql


async def test_request_truncates_a_long_name(store, db):
    await store.request(7, "x" * 80, "123456789012")
    assert len(db.execute_query_to_database.call_args.kwargs["params"][1]) == 64


async def test_remove_deletes_alerts_and_link_together(store, db):
    await store.remove(7)
    statements = db.execute_transaction.call_args.args[0]
    assert [s[0].split()[2] for s in statements] == ["shiny_alert", "shiny_link"]
    assert all(s[1] == (7,) for s in statements)


async def test_recipients_asks_nothing_when_nobody_is_listed(store, db):
    assert await store.recipients([]) == []
    db.get_data_from_database.assert_not_awaited()


async def test_recipients_are_linked_rows_among_the_listed_users(store, db):
    db.get_data_from_database.return_value = [{"discord_id": 7, "player_id": "a" * 16}]
    assert await store.recipients([7, 8]) == [{"discord_id": 7, "player_id": "a" * 16}]
    sql = db.get_data_from_database.call_args.args[0]
    assert "status = 'linked'" in sql and "IN (%s,%s)" in sql
    assert db.get_data_from_database.call_args.kwargs["params"] == (7, 8)


async def test_watermark_is_none_before_the_first_tick(store, db):
    db.get_data_from_database.return_value = [{"shiny_alert_watermark": None}]
    assert await store.watermark() is None


async def test_record_alert_is_true_only_the_first_time(store, db):
    db.execute_query_to_database.return_value = 1
    assert await store.record_alert(7, "123", 25, 0) is True
    db.execute_query_to_database.return_value = 0
    assert await store.record_alert(7, "123", 25, 0) is False
    assert "INSERT IGNORE" in db.execute_query_to_database.call_args.args[0]


async def test_a_verdict_is_stored_once(store, db):
    db.execute_query_to_database.return_value = 1
    assert await store.set_verdict(7, "123", "shiny") is True
    assert "verdict IS NULL" in db.execute_query_to_database.call_args.args[0]
    db.execute_query_to_database.return_value = 0
    assert await store.set_verdict(7, "123", "not_shiny") is False
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest -q tests/modules/test_shiny_store.py 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'modules.shiny_store'`.

- [ ] **Step 3: Write the store**

```python
"""Poliswag's side of the shiny tables (migration 017).

shiny_link is shared with the host script scripts/shiny_linker, which is the
only writer of `linked`/`failed`. This module queues codes, announces
results, and owns shiny_alert and the alert watermark.
"""

_REQUEST_SQL = """
INSERT INTO shiny_link (discord_id, discord_name, friend_code, status, requested_at)
VALUES (%s, %s, %s, 'pending', UTC_TIMESTAMP())
ON DUPLICATE KEY UPDATE
  discord_name = VALUES(discord_name), friend_code = VALUES(friend_code),
  status = 'pending', trainer_name = NULL, player_id = NULL, fail_reason = NULL,
  attempts = 0, requested_at = UTC_TIMESTAMP(), resolved_at = NULL,
  announced_at = NULL
"""


class ShinyStore:
    def __init__(self, db):
        self.db = db

    async def get(self, discord_id):
        rows = await self.db.get_data_from_database(
            "SELECT status, trainer_name, fail_reason FROM shiny_link "
            "WHERE discord_id = %s",
            params=(discord_id,),
        )
        return rows[0] if rows else None

    async def request(self, discord_id, discord_name, friend_code):
        await self.db.execute_query_to_database(
            _REQUEST_SQL, params=(discord_id, discord_name[:64], friend_code)
        )

    async def remove(self, discord_id):
        await self.db.execute_transaction(
            [
                ("DELETE FROM shiny_alert WHERE discord_id = %s", (discord_id,)),
                ("DELETE FROM shiny_link WHERE discord_id = %s", (discord_id,)),
            ]
        )

    async def unannounced(self):
        """Links the linker finished that their owner has not been told about."""
        return await self.db.get_data_from_database(
            "SELECT discord_id, status, trainer_name, fail_reason FROM shiny_link "
            "WHERE status IN ('linked', 'failed') AND announced_at IS NULL"
        )

    async def mark_announced(self, discord_id):
        await self.db.execute_query_to_database(
            "UPDATE shiny_link SET announced_at = UTC_TIMESTAMP() WHERE discord_id = %s",
            params=(discord_id,),
        )

    async def recipients(self, user_ids):
        """[{discord_id, player_id}] for the listed users who are linked."""
        if not user_ids:
            return []
        placeholders = ",".join(["%s"] * len(user_ids))
        return await self.db.get_data_from_database(
            "SELECT discord_id, player_id FROM shiny_link "
            f"WHERE status = 'linked' AND discord_id IN ({placeholders})",
            params=tuple(user_ids),
        )

    async def watermark(self):
        rows = await self.db.get_data_from_database(
            "SELECT shiny_alert_watermark FROM poliswag"
        )
        return rows[0]["shiny_alert_watermark"] if rows else None

    async def set_watermark(self, value):
        await self.db.execute_query_to_database(
            "UPDATE poliswag SET shiny_alert_watermark = %s", params=(value,)
        )

    async def record_alert(self, discord_id, encounter_id, pokemon_id, form_id):
        """True when this is the first time this player hears of this spawn."""
        inserted = await self.db.execute_query_to_database(
            "INSERT IGNORE INTO shiny_alert (discord_id, encounter_id, pokemon_id, form_id) "
            "VALUES (%s, %s, %s, %s)",
            params=(discord_id, encounter_id, pokemon_id, form_id),
        )
        return inserted == 1

    async def set_verdict(self, discord_id, encounter_id, verdict):
        """True when the answer was stored; False if there already was one."""
        updated = await self.db.execute_query_to_database(
            "UPDATE shiny_alert SET verdict = %s, verdict_at = UTC_TIMESTAMP() "
            "WHERE discord_id = %s AND encounter_id = %s AND verdict IS NULL",
            params=(verdict, discord_id, encounter_id),
        )
        return updated == 1
```

- [ ] **Step 4: Run the tests**

Run: `pytest -q tests/modules/test_shiny_store.py 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/shiny_store.py tests/modules/test_shiny_store.py
git commit -m "Poliswag's store for shiny links, alerts and the watermark"
```

---

### Task 5: The `!shiny` command

**Files:**
- Create: `cogs/shiny.py`
- Modify: `modules/dm_policy.py` (the `DM_EXEMPT_COGS` set), `main.py` (the `setup_hook` extension list)
- Test: `tests/cogs/test_shiny.py`, `tests/modules/test_dm_policy.py` (one test appended)

- [ ] **Step 1: Write the failing cog tests**

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.shiny import Shiny


def guild_with(*member_ids):
    guild = MagicMock()
    guild.members = [MagicMock(id=i) for i in member_ids]
    guild.get_member = lambda user_id: user_id if user_id in member_ids else None
    return guild


@pytest.fixture
def cog():
    bot = MagicMock()
    bot.guilds = [guild_with(123)]
    c = Shiny(bot)
    c.store = MagicMock(
        get=AsyncMock(return_value=None), request=AsyncMock(), remove=AsyncMock()
    )
    return c


def ctx_for(author_id=123, name="rita", in_guild=True):
    ctx = MagicMock()
    ctx.author = MagicMock(id=author_id)
    ctx.author.name = name
    ctx.send = AsyncMock()
    ctx.guild = MagicMock() if in_guild else None
    return ctx


def said(ctx):
    return ctx.send.call_args.kwargs["embed"].description


async def test_a_code_is_queued_however_it_was_typed(cog):
    ctx = ctx_for()
    await Shiny.shiny.callback(cog, ctx, texto="1234 5678-9012")
    cog.store.request.assert_awaited_once_with(123, "rita", "123456789012")
    assert "raid ativa" in said(ctx) and "convite de raid" in said(ctx)


async def test_something_that_is_not_a_code_is_refused(cog):
    ctx = ctx_for()
    await Shiny.shiny.callback(cog, ctx, texto="12345")
    cog.store.request.assert_not_awaited()
    assert "12 dígitos" in said(ctx)


async def test_remover_deletes_the_link(cog):
    ctx = ctx_for()
    await Shiny.shiny.callback(cog, ctx, texto="Remover")
    cog.store.remove.assert_awaited_once_with(123)
    assert "Apaguei" in said(ctx)


@pytest.mark.parametrize(
    "row, expected",
    [
        (None, "Ainda não enviaste"),
        ({"status": "pending", "trainer_name": None, "fail_reason": None}, "em fila"),
        (
            {"status": "linked", "trainer_name": "Rita99", "fail_reason": None},
            "**Rita99**",
        ),
        (
            {
                "status": "failed",
                "trainer_name": None,
                "fail_reason": "código não encontrado",
            },
            "código não encontrado",
        ),
    ],
)
async def test_bare_command_reports_the_status(cog, row, expected):
    cog.store.get.return_value = row
    ctx = ctx_for()
    await Shiny.shiny.callback(cog, ctx, texto="")
    assert expected in said(ctx)


async def test_a_dm_from_someone_who_left_the_server_is_turned_away(cog):
    ctx = ctx_for(author_id=999, in_guild=False)
    await Shiny.shiny.callback(cog, ctx, texto="123456789012")
    cog.store.request.assert_not_awaited()
    assert "membros do servidor" in said(ctx)


async def test_a_dm_from_a_member_works(cog):
    ctx = ctx_for(author_id=123, in_guild=False)
    await Shiny.shiny.callback(cog, ctx, texto="123456789012")
    cog.store.request.assert_awaited_once()
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest -q tests/cogs/test_shiny.py 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'cogs.shiny'`.

- [ ] **Step 3: Write the cog**

```python
"""!shiny — a member hands over a friend code so shinies can be predicted.

The code is only the way in: the host script scripts/shiny_linker has a
scanner account look the trainer up by that code, invite them to a private
raid lobby and read the internal player ID the game's shiny roll uses
(modules/shiny_roll.py). This cog queues the code and reports status;
modules/shiny_alerts.py tells the member when it resolves.

Nothing proves a code belongs to whoever sent it. The resolution DM names
the trainer it matched, which is what makes a wrong code visible.
"""

from discord.ext import commands

from modules.embeds import build_embed
from modules.membership import is_member
from modules.shiny_roll import normalise_friend_code
from modules.shiny_store import ShinyStore

TITLE = "SHINY"
_WHEN = (
    "quando houver uma raid ativa na zona (durante o dia, normalmente em "
    "menos de uma hora)"
)


class Shiny(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.store = ShinyStore(poliswag.db)

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    @commands.command(
        name="shiny",
        brief="Associa o teu código de amigo às previsões de shiny",
        help="`!shiny 1234 5678 9012` envia o teu código de amigo; uma conta "
        "do scanner procura o teu treinador e eu aviso-te por DM quando "
        "estiver associado. `!shiny` mostra o estado e `!shiny remover` "
        "apaga tudo. Funciona aqui ou em DM ao bot.",
    )
    async def shiny(self, ctx, *, texto: str = ""):
        author = ctx.author
        if ctx.guild is None and not is_member(self.poliswag, author.id):
            await self._say(
                ctx, "As previsões de shiny são para membros do servidor PoGo Leiria."
            )
            return

        texto = texto.strip()
        if not texto:
            await self._say(ctx, self._status_text(await self.store.get(author.id)))
            return
        if texto.lower() == "remover":
            await self.store.remove(author.id)
            await self._say(ctx, "Apaguei o teu código e o histórico de alertas.")
            return

        code = normalise_friend_code(texto)
        if code is None:
            await self._say(
                ctx,
                "Isso não parece um código de amigo. São 12 dígitos, por "
                "exemplo `!shiny 1234 5678 9012`.",
            )
            return
        await self.store.request(author.id, author.name, code)
        await self._say(
            ctx,
            f"Código recebido. Uma conta do scanner vai procurar o teu treinador {_WHEN} "
            "e eu aviso-te por DM. Podes receber um convite de raid no jogo: ignora-o.",
        )

    @staticmethod
    def _status_text(row):
        if row is None:
            return (
                "Ainda não enviaste um código. Escreve `!shiny 1234 5678 9012` "
                "com o teu código de amigo."
            )
        if row["status"] == "pending":
            return f"O teu código está em fila. É associado {_WHEN}."
        if row["status"] == "linked":
            return f"Associado ao treinador **{row['trainer_name']}**."
        return (
            f"Não foi possível associar: {row['fail_reason']}. "
            "Envia `!shiny <código>` outra vez."
        )

    @staticmethod
    async def _say(ctx, text):
        await ctx.send(embed=build_embed(TITLE, text))


async def setup(poliswag):
    await poliswag.add_cog(Shiny(poliswag))
```

- [ ] **Step 4: Run the cog tests**

Run: `pytest -q tests/cogs/test_shiny.py 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 5: Let the cog answer DMs, with a test that pins it**

Append to `tests/modules/test_dm_policy.py`:

```python
def test_exemption_names_the_real_shiny_cog():
    # !shiny is meant for DMs and carries its own membership check.
    from cogs.shiny import Shiny
    from modules.dm_policy import DM_EXEMPT_COGS

    assert Shiny.__cog_name__ in DM_EXEMPT_COGS
```

Run: `pytest -q tests/modules/test_dm_policy.py 2>&1 | tail -3`
Expected: 1 failed (`'Shiny' in frozenset({'Pokedex'})`).

In `modules/dm_policy.py`, extend the module docstring's last paragraph with one sentence and change the set:

```python
The Shiny cog (cogs/shiny.py) is exempt the same way: `!shiny` takes a friend
code in a DM and checks membership itself.
```

```python
DM_EXEMPT_COGS = frozenset({"Pokedex", "Shiny"})
```

Then find the existing test `test_only_the_trades_cog_is_exempt` in `tests/modules/test_dm_policy.py`: it asserts that the `Accounts` cog is not exempt, which is still true, so it needs no change.

Run: `pytest -q tests/modules/test_dm_policy.py 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 6: Load the cog**

In `main.py` `setup_hook`, after the line `await self.load_extension("cogs.announcements")`, add:

```python
        await self.load_extension("cogs.shiny")
```

- [ ] **Step 7: Run the whole suite**

Run: `pytest -q 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add cogs/shiny.py modules/dm_policy.py main.py tests/cogs/test_shiny.py tests/modules/test_dm_policy.py
git commit -m "!shiny queues a member's friend code and reports its status"
```

---

### Task 6: Announcing finished links

**Files:**
- Create: `modules/shiny_alerts.py`
- Modify: `modules/config.py` (one setting, after `MY_ID`)
- Test: `tests/modules/test_shiny_alerts.py`

- [ ] **Step 1: Add the setting**

In `modules/config.py`, directly under the `MY_ID = ...` line:

```python
    # Who gets shiny-prediction DMs (modules/shiny_alerts.py). The owner alone
    # while the prediction is being checked in the field; widening the pilot
    # is this list.
    SHINY_ALERT_USER_IDS = [
        int(part)
        for part in os.environ.get("SHINY_ALERT_USER_IDS", "").split(",")
        if part.strip()
    ] or [MY_ID]
```

- [ ] **Step 2: Write the failing tests**

```python
"""Shiny alerts: the link announcements, the spawn alerts, the DM itself.
The database is faked; the roll is the real one, fed vectors that the
reference JavaScript rolls to 0 (see tests/modules/test_shiny_roll.py)."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules import shiny_alerts
from modules.config import Config
from modules.shiny_alerts import ShinyAlerts, link_message

OWNER = 111
PLAYER = "0123456789abcdef"
ZERO = "10000000000000000168"  # rolls 0 for PLAYER
OTHER = "1"  # rolls 113 for PLAYER


@pytest.fixture(autouse=True)
def only_the_owner(mocker):
    mocker.patch.object(Config, "SHINY_ALERT_USER_IDS", [OWNER])


@pytest.fixture
def alerts():
    bot = MagicMock()
    bot.db.get_data_from_database = AsyncMock(return_value=[])
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user.return_value = user
    a = ShinyAlerts(bot)
    a.store = MagicMock(
        unannounced=AsyncMock(return_value=[]),
        mark_announced=AsyncMock(),
        recipients=AsyncMock(return_value=[]),
        watermark=AsyncMock(return_value=None),
        set_watermark=AsyncMock(),
        record_alert=AsyncMock(return_value=True),
    )
    a.user = user
    return a


class TestLinkMessage:
    def test_linked_names_the_trainer(self):
        row = {"status": "linked", "trainer_name": "Rita99", "fail_reason": None}
        assert "**Rita99**" in link_message(row, alerts_on=True)

    def test_linked_but_outside_the_pilot_says_alerts_come_later(self):
        row = {"status": "linked", "trainer_name": "Rita99", "fail_reason": None}
        assert "ainda estão em teste" in link_message(row, alerts_on=False)
        assert "ainda estão em teste" not in link_message(row, alerts_on=True)

    def test_failed_gives_the_reason_and_the_way_back(self):
        row = {"status": "failed", "trainer_name": None, "fail_reason": "código não encontrado"}
        text = link_message(row, alerts_on=False)
        assert "código não encontrado" in text and "!shiny" in text


class TestAnnounceLinks:
    async def test_each_finished_link_is_told_once(self, alerts):
        alerts.store.unannounced.return_value = [
            {"discord_id": 5, "status": "linked", "trainer_name": "A", "fail_reason": None}
        ]
        await alerts.tick()
        alerts.user.send.assert_awaited_once()
        alerts.store.mark_announced.assert_awaited_once_with(5)

    async def test_closed_dms_are_still_marked(self, alerts):
        alerts.store.unannounced.return_value = [
            {"discord_id": 5, "status": "failed", "trainer_name": None, "fail_reason": "x"}
        ]
        alerts.user.send.side_effect = discord.Forbidden(MagicMock(status=403), "no")
        await alerts.tick()
        alerts.store.mark_announced.assert_awaited_once_with(5)
```

- [ ] **Step 3: Run them to see them fail**

Run: `pytest -q tests/modules/test_shiny_alerts.py 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'modules.shiny_alerts'`.

- [ ] **Step 4: Write the module (announcements only; Task 7 adds the spawn alerts)**

```python
"""Shiny predictions by direct message.

Two jobs each scheduler tick:

1. Tell a member their friend code resolved (or failed). The host script
   scripts/shiny_linker writes the result; this sends one DM and stamps
   announced_at.
2. For linked players in Config.SHINY_ALERT_USER_IDS, roll every new spawn
   (modules/shiny_roll.py) and DM the ones that come up 0.

Owns shiny_alert and poliswag.shiny_alert_watermark; reads shiny_link,
golbat's pokemon and pokemon_shiny_stats, and pokemon_name.
"""

import discord

from modules.config import Config
from modules.shiny_store import ShinyStore


def link_message(row, alerts_on):
    """The one DM a member gets when the linker is done with their code."""
    if row["status"] == "linked":
        text = f"✨ O teu código ficou associado ao treinador **{row['trainer_name']}**."
        if not alerts_on:
            text += (
                "\nOs alertas de shiny ainda estão em teste; aviso-te quando abrirem."
            )
        return text + "\n-# Não és tu? Escreve `!shiny remover` e envia o código certo."
    return (
        f"Não consegui associar o teu código: {row['fail_reason']}. "
        "Confirma-o no jogo e envia `!shiny <código>` outra vez."
    )


class ShinyAlerts:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.store = ShinyStore(poliswag.db)

    async def tick(self):
        await self._announce_links()

    async def _announce_links(self):
        for row in await self.store.unannounced():
            alerts_on = row["discord_id"] in Config.SHINY_ALERT_USER_IDS
            await self._dm(row["discord_id"], content=link_message(row, alerts_on))
            # Stamped even when the DM was refused, or it would be retried
            # every minute for ever; `!shiny` still shows the status.
            await self.store.mark_announced(row["discord_id"])

    async def _dm(self, discord_id, **message):
        try:
            user = self.poliswag.get_user(
                int(discord_id)
            ) or await self.poliswag.fetch_user(int(discord_id))
            await user.send(**message, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.NotFound) as e:
            self.poliswag.utility.log_to_file(
                f"Shiny DM to {discord_id} not delivered: {e}"
            )
```

- [ ] **Step 5: Run the tests**

Run: `pytest -q tests/modules/test_shiny_alerts.py tests/modules/test_config.py 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add modules/shiny_alerts.py modules/config.py tests/modules/test_shiny_alerts.py
git commit -m "A member is told once when their shiny link resolves or fails"
```

---

### Task 7: Spawn alerts, verdict buttons, scheduler step

**Files:**
- Modify: `modules/shiny_alerts.py`, `cogs/shiny.py`, `cogs/scheduled.py`
- Test: `tests/modules/test_shiny_alerts.py`, `tests/cogs/test_shiny.py`, `tests/cogs/test_scheduled.py`

- [ ] **Step 1: Append the failing alert tests to `tests/modules/test_shiny_alerts.py`**

```python
def spawn(encounter_id=ZERO, **over):
    row = {
        "id": encounter_id,
        "pokemon_id": 25,
        "form_id": 0,
        "lat": 39.7436,
        "lon": -8.8071,
        "expire_timestamp": 1791630000,
        "expire_timestamp_verified": 1,
        "seen_type": "encounter",
        "first_seen_timestamp": 1791629000,
    }
    row.update(over)
    return row


def with_spawns(alerts, spawns, name="Pikachu", seen_shiny=True):
    """Route the three reads by what they select."""

    async def read(sql, params=None, **_):
        if "pokemon_shiny_stats" in sql:
            return [{"seen": 1}] if seen_shiny else []
        if "pokemon_name" in sql:
            return [{"name": name, "form_name": None}] if name else []
        return spawns

    alerts.poliswag.db.get_data_from_database = AsyncMock(side_effect=read)
    alerts.store.watermark.return_value = 1791628000
    alerts.store.recipients.return_value = [{"discord_id": OWNER, "player_id": PLAYER}]


class TestAlertEmbed:
    def test_says_what_where_and_until_when(self):
        embed = shiny_alerts.alert_embed(spawn(), "Pikachu", seen_shiny=True)
        assert "Pikachu" in embed.title
        assert "https://www.google.com/maps?q=39.7436,-8.8071" in embed.description
        assert "Desaparece às" in embed.description
        assert "já viu este Pokémon shiny" in embed.description

    def test_an_unverified_despawn_is_called_an_estimate(self):
        embed = shiny_alerts.alert_embed(
            spawn(expire_timestamp_verified=0), "Pikachu", True
        )
        assert "estimado" in embed.description

    def test_no_despawn_time_is_said_plainly(self):
        embed = shiny_alerts.alert_embed(spawn(expire_timestamp=None), "Pikachu", True)
        assert "desconhecida" in embed.description

    def test_a_species_never_seen_shiny_is_flagged(self):
        embed = shiny_alerts.alert_embed(spawn(), "Pikachu", seen_shiny=False)
        assert "nunca viu" in embed.description

    def test_a_nearby_sighting_warns_about_the_location(self):
        embed = shiny_alerts.alert_embed(spawn(seen_type="nearby_stop"), "Pikachu", True)
        assert "aproximada" in embed.description


class TestVerdictView:
    async def test_the_buttons_carry_the_encounter_id(self):
        view = shiny_alerts.verdict_view(ZERO)
        assert [b.custom_id for b in view.children] == [
            f"shiny:s:{ZERO}",
            f"shiny:n:{ZERO}",
        ]
        assert view.timeout is None


class TestAlertSpawns:
    async def test_the_first_tick_only_sets_the_watermark(self, alerts):
        alerts.store.recipients.return_value = [{"discord_id": OWNER, "player_id": PLAYER}]
        await alerts.tick()
        alerts.store.set_watermark.assert_awaited_once()
        alerts.user.send.assert_not_awaited()

    async def test_nobody_linked_keeps_the_watermark_moving(self, alerts):
        alerts.store.watermark.return_value = 1791628000
        await alerts.tick()
        alerts.store.set_watermark.assert_awaited_once()
        alerts.poliswag.db.get_data_from_database.assert_not_awaited()

    async def test_a_roll_of_zero_is_sent_with_buttons(self, alerts):
        with_spawns(alerts, [spawn(), spawn(OTHER, first_seen_timestamp=1791629100)])
        await alerts.tick()
        alerts.store.record_alert.assert_awaited_once_with(OWNER, ZERO, 25, 0)
        sent = alerts.user.send.call_args.kwargs
        assert "Pikachu" in sent["embed"].title
        assert sent["view"].children[0].custom_id == f"shiny:s:{ZERO}"
        alerts.store.set_watermark.assert_awaited_once_with(1791629100)

    async def test_a_spawn_already_alerted_is_not_sent_again(self, alerts):
        with_spawns(alerts, [spawn()])
        alerts.store.record_alert.return_value = False
        await alerts.tick()
        alerts.user.send.assert_not_awaited()

    async def test_no_zero_roll_means_no_dm(self, alerts):
        with_spawns(alerts, [spawn(OTHER)])
        await alerts.tick()
        alerts.store.record_alert.assert_not_awaited()
        alerts.user.send.assert_not_awaited()

    async def test_an_unnamed_pokemon_falls_back_to_its_number(self, alerts):
        with_spawns(alerts, [spawn()], name=None)
        await alerts.tick()
        assert "#25" in alerts.user.send.call_args.kwargs["embed"].title

    async def test_a_malformed_encounter_id_is_skipped(self, alerts):
        with_spawns(alerts, [spawn("not-a-number"), spawn()])
        await alerts.tick()
        alerts.user.send.assert_awaited_once()

    async def test_the_query_leaves_out_lures_and_expiring_spawns(self, alerts):
        with_spawns(alerts, [])
        await alerts.tick()
        sql, = [
            c.args[0]
            for c in alerts.poliswag.db.get_data_from_database.call_args_list
            if "first_seen_timestamp" in c.args[0]
        ]
        assert "'wild', 'encounter', 'nearby_stop'" in sql
        assert "lure" not in sql
        watermark, expires_after = alerts.poliswag.db.get_data_from_database.call_args.kwargs[
            "params"
        ]
        assert watermark == 1791628000 and isinstance(expires_after, int)
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest -q tests/modules/test_shiny_alerts.py 2>&1 | tail -3`
Expected: failures on `alert_embed`, `verdict_view` and the spawn tests (`AttributeError: module 'modules.shiny_alerts' has no attribute 'alert_embed'`).

- [ ] **Step 3: Extend `modules/shiny_alerts.py`**

Replace the import block with:

```python
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from modules.config import Config
from modules.embeds import build_embed
from modules.shiny_roll import shiny_die
from modules.shiny_store import ShinyStore

_LISBON = ZoneInfo("Europe/Lisbon")
# A spawn with less than this left is not worth a DM.
MIN_SECONDS_LEFT = 120
# Lure and tappable spawns are left out (the roll is untested on them), and
# cell-only sightings have no location worth sending.
_SPAWNS_SQL = f"""
SELECT id, pokemon_id, COALESCE(form, 0) AS form_id, lat, lon, expire_timestamp,
       expire_timestamp_verified, seen_type, first_seen_timestamp
FROM {Config.DB_SCANNER_NAME}.pokemon
WHERE first_seen_timestamp >= %s
  AND (expire_timestamp IS NULL OR expire_timestamp >= %s)
  AND seen_type IN ('wild', 'encounter', 'nearby_stop')
ORDER BY first_seen_timestamp
"""
_SEEN_SHINY_SQL = f"""
SELECT 1 AS seen FROM {Config.DB_SCANNER_NAME}.pokemon_shiny_stats
WHERE pokemon_id = %s AND form_id = %s AND `count` > 0 LIMIT 1
"""
```

Add these functions under `link_message`:

```python
def _despawn_line(spawn):
    expires = spawn["expire_timestamp"]
    if not expires:
        return "⏳ Hora de desaparecer desconhecida"
    hour = datetime.fromtimestamp(expires, _LISBON).strftime("%H:%M")
    if spawn["expire_timestamp_verified"]:
        return f"⏳ Desaparece às {hour}"
    return f"⏳ Desaparece por volta das {hour} (estimado)"


def alert_embed(spawn, name, seen_shiny):
    lines = [
        f"📍 [Abrir no mapa](https://www.google.com/maps?q={spawn['lat']},{spawn['lon']})",
        _despawn_line(spawn),
        (
            "✅ O scanner já viu este Pokémon shiny."
            if seen_shiny
            else "⚠️ O scanner nunca viu este Pokémon shiny: pode não existir em shiny."
        ),
    ]
    if spawn["seen_type"] == "nearby_stop":
        lines.append("📡 Localização aproximada (a pokéstop mais próxima).")
    return build_embed(
        f"✨ {name} deve ser shiny para ti",
        "\n".join(lines),
        footer="Previsão em teste. Diz-me se acertei.",
    )


def verdict_view(encounter_id):
    """Era shiny / Não era. No callbacks: cogs/shiny.py answers the custom
    IDs in on_interaction, so the buttons outlive a bot restart."""
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Era shiny",
            style=discord.ButtonStyle.success,
            custom_id=f"shiny:s:{encounter_id}",
        )
    )
    view.add_item(
        discord.ui.Button(
            label="Não era",
            style=discord.ButtonStyle.secondary,
            custom_id=f"shiny:n:{encounter_id}",
        )
    )
    return view
```

Replace `tick` and add the spawn methods (keep `_announce_links` and `_dm` as they are):

```python
    async def tick(self):
        await self._announce_links()
        await self._alert_spawns()

    async def _alert_spawns(self):
        now = int(time.time())
        watermark = await self.store.watermark()
        recipients = await self.store.recipients(Config.SHINY_ALERT_USER_IDS)
        if watermark is None or not recipients:
            # Never run, or nobody to tell: start from now, so the first
            # linked player isn't sent everything already on the map.
            await self.store.set_watermark(now)
            return

        spawns = await self.poliswag.db.get_data_from_database(
            _SPAWNS_SQL, params=(watermark, now + MIN_SECONDS_LEFT)
        )
        for spawn in spawns:
            for recipient in recipients:
                try:
                    roll = shiny_die(spawn["id"], recipient["player_id"])
                except ValueError:
                    break  # not an encounter ID; no recipient can roll it
                if roll == 0:
                    await self._alert(recipient["discord_id"], spawn)
        if spawns:
            # `>=` above re-reads the newest second next tick, in case more
            # rows land in it; shiny_alert keeps those from going out twice.
            await self.store.set_watermark(
                max(s["first_seen_timestamp"] for s in spawns)
            )

    async def _alert(self, discord_id, spawn):
        # Recorded before sending: a spawn told twice is worse than one lost
        # to a failed send.
        if not await self.store.record_alert(
            discord_id, spawn["id"], spawn["pokemon_id"], spawn["form_id"]
        ):
            return
        key = (spawn["pokemon_id"], spawn["form_id"])
        names = await self.poliswag.db.get_data_from_database(
            "SELECT name, form_name FROM pokemon_name WHERE pokemon_id = %s AND form_id = %s",
            params=key,
        )
        name = f"#{spawn['pokemon_id']}"
        if names:
            name = names[0]["name"]
            if names[0]["form_name"]:
                name += f" ({names[0]['form_name']})"
        seen = await self.poliswag.db.get_data_from_database(
            _SEEN_SHINY_SQL, params=key
        )
        await self._dm(
            discord_id,
            embed=alert_embed(spawn, name, bool(seen)),
            view=verdict_view(spawn["id"]),
        )
```

- [ ] **Step 4: Run the alert tests**

Run: `pytest -q tests/modules/test_shiny_alerts.py 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 5: Append the failing verdict tests to `tests/cogs/test_shiny.py`**

```python
def press(custom_id, user_id=123):
    interaction = MagicMock()
    interaction.data = {"custom_id": custom_id}
    interaction.user = MagicMock(id=user_id)
    interaction.message.embeds = [MagicMock()]
    interaction.response.edit_message = AsyncMock()
    return interaction


async def test_a_verdict_is_stored_for_whoever_pressed(cog):
    cog.store.set_verdict = AsyncMock(return_value=True)
    interaction = press("shiny:s:10000000000000000168")
    await cog.on_interaction(interaction)
    cog.store.set_verdict.assert_awaited_once_with(123, "10000000000000000168", "shiny")
    edited = interaction.response.edit_message.call_args.kwargs
    assert edited["view"] is None
    edited["embed"].set_footer.assert_called_once_with(
        text="Resposta registada: era shiny ✅"
    )


async def test_nao_era_is_stored_as_not_shiny(cog):
    cog.store.set_verdict = AsyncMock(return_value=True)
    await cog.on_interaction(press("shiny:n:42"))
    cog.store.set_verdict.assert_awaited_once_with(123, "42", "not_shiny")


async def test_a_second_answer_changes_nothing(cog):
    cog.store.set_verdict = AsyncMock(return_value=False)
    interaction = press("shiny:n:42")
    await cog.on_interaction(interaction)
    interaction.response.edit_message.call_args.kwargs["embed"].set_footer.assert_called_once_with(
        text="Já tinhas respondido a este alerta."
    )


@pytest.mark.parametrize("custom_id", ["Leiria", "shiny:x:42", "shiny:s", "Alertas1"])
async def test_other_buttons_are_left_alone(cog, custom_id):
    cog.store.set_verdict = AsyncMock()
    interaction = press(custom_id)
    await cog.on_interaction(interaction)
    cog.store.set_verdict.assert_not_awaited()
    interaction.response.edit_message.assert_not_awaited()
```

Run: `pytest -q tests/cogs/test_shiny.py 2>&1 | tail -3`
Expected: the new tests fail (`AttributeError: 'Shiny' object has no attribute 'on_interaction'`).

- [ ] **Step 6: Add the listener to `cogs/shiny.py`**

Under the `_WHEN` constant:

```python
# custom_id "shiny:<key>:<encounter id>" from modules/shiny_alerts.verdict_view.
_VERDICTS = {"s": ("shiny", "era shiny ✅"), "n": ("not_shiny", "não era ❌")}
```

Inside the class, after the `shiny` command:

```python
    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        """The Era shiny / Não era buttons under an alert DM.

        The verdict is stored against whoever pressed, so it can only ever
        answer that person's own alert row.
        """
        parts = ((interaction.data or {}).get("custom_id") or "").split(":")
        if len(parts) != 3 or parts[0] != "shiny" or parts[1] not in _VERDICTS:
            return
        verdict, label = _VERDICTS[parts[1]]
        stored = await self.store.set_verdict(interaction.user.id, parts[2], verdict)
        embeds = interaction.message.embeds
        embed = embeds[0] if embeds else None
        if embed is not None:
            embed.set_footer(
                text=f"Resposta registada: {label}"
                if stored
                else "Já tinhas respondido a este alerta."
            )
        await interaction.response.edit_message(embed=embed, view=None)
```

Run: `pytest -q tests/cogs/test_shiny.py 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 7: Add the scheduler step**

In `cogs/scheduled.py`: beside `from modules.hundo_alerts import HundoAlerts` add

```python
from modules.shiny_alerts import ShinyAlerts
```

after `self._hundo_alerts = HundoAlerts(poliswag)` add

```python
        self._shiny_alerts = ShinyAlerts(poliswag)
```

and in `scheduled_tasks`, after `self._hundo_alerts.tick,` add

```python
            self._shiny_alerts.tick,
```

In `tests/cogs/test_scheduled.py`, `TestHundoAlertsStep._quiet_other_steps` silences every other step; add one line after `cog._trade_dm.tick = AsyncMock()`:

```python
        cog._shiny_alerts.tick = AsyncMock()
```

- [ ] **Step 8: Run the whole suite**

Run: `pytest -q 2>&1 | tail -3`
Expected: PASS. If a scheduler test fails because the new step ran against a `MagicMock` database, silence it in that test the same way (`cog._shiny_alerts.tick = AsyncMock()`); do not weaken `_run_tick_step`.

- [ ] **Step 9: Commit**

```bash
git add modules/shiny_alerts.py cogs/shiny.py cogs/scheduled.py tests/modules/test_shiny_alerts.py tests/cogs/test_shiny.py tests/cogs/test_scheduled.py
git commit -m "A roll of 0 on a new spawn DMs the owner, with was-it-shiny buttons"
```

---

### Task 8: Linker — protobuf helpers and the Rotom session

The linker's building blocks are lifted from `/root/unonwhash/tools/` (`shiny-capture.py` for decoding, `raid-id-session.py` for the session). Those files stay untouched.

**Files:**
- Create: `scripts/shiny_linker/__init__.py`, `scripts/shiny_linker/proto.py`, `scripts/shiny_linker/rotom.py`
- Test: `tests/scripts/test_shiny_linker_proto.py`, `tests/scripts/test_shiny_linker_rotom.py`

- [ ] **Step 1: Write the failing proto tests**

```python
import pytest

from scripts.shiny_linker.proto import (
    blob,
    fields,
    integer,
    one,
    packed_ints,
    real,
    text,
    uint,
)


def test_integers_strings_and_doubles_round_trip():
    message = fields(integer(1, 300) + blob(2, "olá") + real(3, 39.5) + integer(1, 7))
    assert message[1] == [300, 7]
    assert text(message, 2) == "olá"
    assert len(one(message, 3)) == 8
    assert one(message, 9, "absent") == "absent"


def test_one_returns_the_last_value():
    assert one(fields(integer(1, 1) + integer(1, 2)), 1) == 2


def test_a_64_bit_value_survives():
    seed = (1 << 64) - 5
    assert one(fields(integer(1, seed)), 1) == seed


def test_text_of_a_missing_or_numeric_field_is_empty():
    assert text(fields(integer(1, 5)), 1) == ""
    assert text(fields(b""), 4) == ""


def test_packed_and_unpacked_ints_read_the_same():
    assert packed_ints(fields(blob(1, uint(3) + uint(70000))), 1) == [3, 70000]
    assert packed_ints(fields(integer(1, 3) + integer(1, 70000)), 1) == [3, 70000]


@pytest.mark.parametrize("data", [b"\x0a\x05ab", b"\x80", b"\x00\x01", b"\x0b"])
def test_malformed_input_raises(data):
    with pytest.raises(ValueError):
        fields(data)
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest -q tests/scripts/test_shiny_linker_proto.py 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'scripts.shiny_linker'`.

- [ ] **Step 3: Create the package and `proto.py`**

`scripts/shiny_linker/__init__.py`:

```python
"""Resolve pending !shiny friend codes to player IDs through one scanner account.

Run as `python -m scripts.shiny_linker` from the repo root, by
scripts/shiny-linker.timer. Design:
docs/superpowers/specs/2026-10-10-shiny-prediction-design.md. The procedure
it automates was worked out by hand in /root/unonwhash/CLAUDE-HANDOVER.md.
"""
```

`scripts/shiny_linker/proto.py`:

```python
"""Just enough protobuf to build requests and read replies without schemas.

Messages decode to {field number: [values]}: ints for varints, bytes for
everything else. Field numbers come from the POGOProtos definitions.
"""

import collections
import struct


def uint(value):
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def integer(number, value):
    return uint(number << 3) + uint(value)


def blob(number, value):
    if isinstance(value, str):
        value = value.encode("utf8")
    return uint((number << 3) | 2) + uint(len(value)) + value


def real(number, value):
    return uint((number << 3) | 1) + struct.pack("<d", value)


def varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("truncated varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError("oversized varint")


def fields(data):
    result = collections.defaultdict(list)
    offset = 0
    while offset < len(data):
        tag, offset = varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise ValueError("invalid field")
        if wire == 0:
            value, offset = varint(data, offset)
        elif wire in (1, 5):
            length = 8 if wire == 1 else 4
            if offset + length > len(data):
                raise ValueError("truncated fixed field")
            value = data[offset : offset + length]
            offset += length
        elif wire == 2:
            length, offset = varint(data, offset)
            if offset + length > len(data):
                raise ValueError("truncated bytes")
            value = data[offset : offset + length]
            offset += length
        else:
            raise ValueError("unsupported wire type")
        result[number].append(value)
    return result


def one(message, number, default=None):
    return message.get(number, [default])[-1]


def text(message, number):
    value = one(message, number, b"")
    return value.decode("utf8") if isinstance(value, bytes) else ""


def packed_ints(message, number):
    """A repeated int field, whether the sender packed it or not."""
    out = []
    for value in message.get(number, []):
        if isinstance(value, int):
            out.append(value)
        else:
            offset = 0
            while offset < len(value):
                item, offset = varint(value, offset)
                out.append(item)
    return out
```

- [ ] **Step 4: Run the proto tests**

Run: `pytest -q tests/scripts/test_shiny_linker_proto.py 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 5: Write the failing Rotom tests**

```python
import zlib

import pytest

from scripts.shiny_linker import rotom
from scripts.shiny_linker.proto import blob, fields, integer, one


class Wire:
    """A controller socket that answers each request with a canned reply."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.closed = False

    def send_binary(self, data):
        self.sent.append(fields(data))

    def recv(self):
        request_id = one(self.sent[-1], 1)
        return self.replies.pop(0)(request_id)

    def abort(self):
        self.closed = True

    def close(self):
        self.closed = True


def session(replies):
    s = rotom.Session.__new__(rotom.Session)
    s.connection = Wire(replies)
    s.worker_id, s.lat, s.lon, s.host, s._next_id = "w1", 39.7, -8.8, b"", 1
    return s


def login_ok(request_id):
    return integer(1, request_id) + integer(2, 200) + blob(3, integer(2, 3))


def rpc_reply(method, payload, compressed=False):
    def build(request_id):
        if compressed:
            body = zlib.compress(payload)
            entry = integer(1, method) + blob(2, body) + integer(3, 1)
        else:
            entry = integer(1, method) + blob(2, payload)
        return integer(1, request_id) + integer(2, 200) + blob(4, integer(1, 1) + blob(2, entry))

    return build


def test_idle_workers_counts_only_connected_devices():
    state = {
        "devices": [
            {"is_connected": True, "worker_count": 16, "worker_in_use_count": 9},
            {"is_connected": False, "worker_count": 8, "worker_in_use_count": 0},
        ]
    }
    assert rotom.idle_workers(state) == 7


def test_accounts_in_use_ignores_idle_controllers():
    state = {"controllers": [{"account_username": "a"}, {"account_username": None}, {}]}
    assert rotom.accounts_in_use(state) == {"a"}


def test_login_sends_the_refresh_token_in_the_scanner_wrapper():
    s = session([login_ok])
    s.login("acct", "token")
    request = fields(one(s.connection.sent[0], 3))
    assert one(request, 1) == b"acct"
    assert one(fields(one(request, 3)), 4) == b"token"
    assert one(request, 4) == b"w1"


def test_login_that_does_not_reach_a_token_is_an_error():
    s = session([lambda i: integer(1, i) + integer(2, 200) + blob(3, integer(2, 5))])
    with pytest.raises(RuntimeError, match="login"):
        s.login("acct", "token")


def test_rpc_returns_the_payload_for_its_method():
    s = session([rpc_reply(163, b"details")])
    assert s.rpc(163, b"q") == b"details"


def test_rpc_inflates_a_compressed_payload():
    s = session([rpc_reply(2, b"x" * 500, compressed=True)])
    assert s.rpc(2) == b"x" * 500


def test_rpc_refuses_methods_outside_its_short_list():
    with pytest.raises(ValueError):
        session([]).rpc(165)  # battle start must be impossible to send


def test_a_failed_worker_request_is_an_error():
    s = session([lambda i: integer(1, i) + integer(2, 500)])
    with pytest.raises(RuntimeError, match="500"):
        s.rpc(2)
```

Run: `pytest -q tests/scripts/test_shiny_linker_rotom.py 2>&1 | tail -3`
Expected: `ImportError: cannot import name 'rotom'`.

- [ ] **Step 6: Write `scripts/shiny_linker/rotom.py`**

```python
"""Rotom-NG as the linker sees it: the status endpoint, and one controller
session on an idle worker.

The session speaks Rotom's controller protocol (UnownHash/RotomProtos): a
registration, a login with the account's stored refresh token, then game
RPCs. It is a second controller beside Dragonite's, on a worker Dragonite is
not using; it never disconnects anyone.
"""

import json
import threading
import time
import urllib.request
import zlib

from .proto import blob, fields, integer, one, real

STATUS_URL = "http://127.0.0.1:7072/api/status"
CONTROLLER_URL = "ws://127.0.0.1:7071/controller"
NAME = "poliswag-shiny-linker"
# Seconds a single request may take. WebSocket pings keep a plain read
# timeout alive for ever, so the socket is aborted on an absolute deadline.
DEADLINE = 40
# What this session may send as itself: GET_PLAYER, gym info, raid details.
# All three pass the scanner agent's own allowlist. Everything else the
# linker needs goes through the hook (phone.Hook.wrap) as a marked
# GET_PLAYER. Battle start (165) is in neither list.
DIRECT_METHODS = (2, 156, 163)


class NoWorker(RuntimeError):
    """Rotom had no idle worker to give."""


def status():
    with urllib.request.urlopen(STATUS_URL, timeout=5) as response:
        return json.load(response)


def idle_workers(state):
    return sum(
        d.get("worker_count", 0) - d.get("worker_in_use_count", 0)
        for d in state.get("devices", [])
        if d.get("is_connected")
    )


def accounts_in_use(state):
    return {
        c["account_username"]
        for c in state.get("controllers", [])
        if c.get("account_username")
    }


class Session:
    def __init__(self, lat, lon):
        import websocket  # only the linker's venv has it

        self.lat, self.lon = lat, lon
        self.host = b""  # the social proxy's assigned host, kept by lobby.py
        self._next_id = 1
        self.connection = websocket.create_connection(
            CONTROLLER_URL, timeout=45, header=[f"User-Agent: {NAME}"]
        )
        try:
            self.connection.send_binary(blob(1, NAME) + integer(2, 2) + integer(4, 1))
            reply = fields(self.connection.recv())
            if one(reply, 1) != 1:
                raise NoWorker("rotom gave no idle worker")
            self.worker_id = one(reply, 7, b"").decode("utf8")
            if not self.worker_id:
                raise NoWorker("registration returned no worker")
        except BaseException:
            self.connection.close()
            raise

    def _request(self, kind, payload):
        request_id = self._next_id
        self._next_id += 1
        self.connection.send_binary(
            integer(1, request_id)
            + integer(2, kind)
            + blob(3 if kind == 1 else 4, payload)
        )
        timer = threading.Timer(DEADLINE, self.connection.abort)
        timer.daemon = True
        timer.start()
        try:
            expires = time.monotonic() + DEADLINE
            while time.monotonic() < expires:
                data = self.connection.recv()
                if not isinstance(data, bytes):
                    raise RuntimeError("worker did not return a binary response")
                response = fields(data)
                if one(response, 1) != request_id:
                    continue
                if one(response, 2) != 200:
                    raise RuntimeError(
                        f"worker request failed with status {one(response, 2)}"
                    )
                return fields(one(response, 3 if kind == 1 else 4, b""))
            raise TimeoutError("worker response timeout")
        finally:
            timer.cancel()

    def login(self, account, refresh_token):
        # The scanner's auth wrapper carries the stored refresh token in
        # field 4; this is not the game's own PtcOAuthToken message.
        response = self._request(
            1,
            blob(1, account)
            + integer(2, 2)
            + blob(3, blob(4, refresh_token))
            + blob(4, self.worker_id),
        )
        if one(response, 2) != 3:
            raise RuntimeError(f"login did not reach a token ({one(response, 2)})")
        self.host = b""

    def rpc(self, method, payload=b""):
        if method not in DIRECT_METHODS:
            raise ValueError(f"method {method} is not one this session sends")
        response = self._request(
            2,
            blob(1, integer(1, method) + blob(2, payload))
            + real(2, self.lat)
            + real(3, self.lon),
        )
        if one(response, 1) != 1:
            raise RuntimeError(f"RPC dispatch failed with status {one(response, 1)}")
        matches = [
            fields(x) for x in response.get(2, []) if one(fields(x), 1) == method
        ]
        if len(matches) != 1:
            raise RuntimeError("RPC response method was not uniquely matched")
        data = one(matches[0], 2, b"")
        if one(matches[0], 3, 0):
            inflater = zlib.decompressobj(47)
            data = inflater.decompress(data, 4_000_001)
            if len(data) > 4_000_000 or not inflater.eof or inflater.unused_data:
                raise RuntimeError("compressed response exceeded bounds or was incomplete")
        return data

    def close(self):
        self.connection.close()
```

- [ ] **Step 7: Run the Rotom tests**

Run: `pytest -q tests/scripts/test_shiny_linker_rotom.py 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add scripts/shiny_linker/__init__.py scripts/shiny_linker/proto.py scripts/shiny_linker/rotom.py tests/scripts/test_shiny_linker_proto.py tests/scripts/test_shiny_linker_rotom.py
git commit -m "Shiny linker: protobuf helpers and a Rotom controller session"
```

---

### Task 9: Linker — the phone and the hook

**Files:**
- Create: `scripts/shiny_linker/native-method-redirect.js` (copied), `scripts/shiny_linker/phone.py`
- Test: `tests/scripts/shiny_hook_harness.cjs`, `tests/scripts/test_shiny_linker_phone.py`

- [ ] **Step 1: Copy the proven hook script**

```bash
cp /root/unonwhash/tools/android/native-method-redirect.js scripts/shiny_linker/native-method-redirect.js
grep -c "__OWNED_REDIRECT_CONFIG__\|OWNED_REDIRECT_READY\|OWNED_REDIRECT_DETACHED\|detachDelayMs\|CONFIG.logPath" scripts/shiny_linker/native-method-redirect.js
```

Expected: `5` (one line per marker). The file must be byte-identical to the one that ran clean on 2026-10-10; do not edit it.

- [ ] **Step 2: Write the hook harness (`tests/scripts/shiny_hook_harness.cjs`)**

It loads the hook with Frida's globals mocked and prints one JSON object.

```js
'use strict';
// Runs scripts/shiny_linker/native-method-redirect.js against a mocked
// native sender. Usage: node shiny_hook_harness.cjs <path-to-hook.js>
const fs = require('fs');

const config = {
  nonce: [1, 2, 3], apiFieldCount: 36, sendIndex: 28, logPath: '/log',
  methods: [5012, 159, 160, 815, 1504], socialActions: [10000],
  lifetimeMs: 60000, detachDelayMs: 10,
};
const source = fs.readFileSync(process.argv[2], 'utf8')
  .replace('__OWNED_REDIRECT_CONFIG__', JSON.stringify(config));

const log = [];
let hook;
global.File = function () { this.write = (line) => log.push(line.trim()); this.close = () => {}; };
global.Process = {
  arch: 'arm64', pointerSize: 8,
  getModuleByName: () => ({ getExportByName: (name) => name }),
  findRangeByAddress: () => ({ protection: 'r-x' }),
};
global.NativeFunction = function (name) {
  if (name !== 'malloc') return () => {};
  return () => ({ isNull: () => false, writeInt(v) { this.int = v; }, writeByteArray(b) { this.bytes = Array.from(b); } });
};
global.Memory = { alloc: () => ({ add: () => ({ readPointer: () => 'sender' }) }) };
global.Interceptor = { attach: (_, handlers) => { hook = handlers; return { detach() { log.push('UNPATCHED'); } }; }, flush() {} };
eval(source);

const varint = (n) => { const out = []; while (n >= 128) { out.push((n & 127) | 128); n = Math.floor(n / 128); } out.push(n); return out; };
const blob = (field, bytes) => [...varint(field * 8 + 2), ...varint(bytes.length), ...bytes];
const integer = (field, value) => [...varint(field * 8), ...varint(value)];
const marked = (method, body, nonce = [1, 2, 3]) => [...blob(4095, nonce), ...blob(4094, body), ...integer(4093, method)];

function call(outerMethod, envelope) {
  const data = Uint8Array.from(envelope);
  const args = [0, 0, 0, { toInt32: () => 1 }, { readInt: () => outerMethod }, { readByteArray: () => data.buffer }, { readInt: () => data.length }];
  hook.onEnter(args);
  // An untouched request still has the caller's own arguments: report null.
  return { method: args[4].int ?? null, length: args[6].int ?? null, bytes: args[5].bytes ?? null };
}

const social = (action) => [...integer(1, action), ...blob(2, []), ...blob(3, [1])];
const result = {
  lobbyJoin: call(2, marked(159, [9, 9])),
  invite: call(2, marked(1504, [7])).method,
  search: call(2, marked(5012, social(10000))).method,
  battleStart: call(2, marked(165, [1])).method,
  friendRemoval: call(2, marked(5012, social(10009))).method,
  wrongNonce: call(2, marked(159, [9], [1, 2, 4])).method,
  notGetPlayer: call(106, marked(159, [9])).method,
  plainGetPlayer: call(2, []).method,
};
call(2, marked(0, []));
// The hook's lifetime timer would keep Node alive, so exit explicitly.
setTimeout(() => { console.log(JSON.stringify({ ...result, log })); process.exit(0); }, 60);
```

- [ ] **Step 3: Write the failing phone tests (`tests/scripts/test_shiny_linker_phone.py`)**

```python
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.shiny_linker import phone
from scripts.shiny_linker.proto import fields, one

HOOK = Path(phone.__file__).with_name("native-method-redirect.js")
HARNESS = Path(__file__).with_name("shiny_hook_harness.cjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_hook_only_redirects_marked_requests_to_listed_methods():
    out = json.loads(
        subprocess.check_output(["node", str(HARNESS), str(HOOK)], timeout=20)
    )
    assert out["lobbyJoin"] == {"method": 159, "length": 2, "bytes": [9, 9]}
    assert out["invite"] == 1504
    assert out["search"] == 5012
    # Left as an ordinary GET_PLAYER: the replacement method is never written.
    for untouched in ("battleStart", "friendRemoval", "wrongNonce", "notGetPlayer", "plainGetPlayer"):
        assert out[untouched] is None, untouched
    assert out["log"][0] == "OWNED_REDIRECT_READY"
    assert out["log"][-2:] == ["UNPATCHED", "OWNED_REDIRECT_DETACHED requested"]


@pytest.fixture
def shell(mocker):
    """Every adb/docker command the module runs, and what each one prints."""
    calls = []
    answers = {}

    def run(argv, **_):
        calls.append(argv)
        text = " ".join(argv)
        for needle, answer in answers.items():
            if needle in text:
                if isinstance(answer, Exception):
                    raise answer
                return MagicMock(returncode=0, stdout=answer.encode())
        return MagicMock(returncode=0, stdout=b"")

    mocker.patch.object(phone.subprocess, "run", side_effect=run)
    mocker.patch.object(phone.time, "sleep")
    return calls, answers


def docker_calls(calls):
    return [c[1] for c in calls if c[0] == "docker"]


def test_pids_and_version_are_read_from_the_phone(shell):
    _, answers = shell
    answers["pidof com.nianticlabs.pokemongo"] = "16473\n"
    answers["pidof com.pokemod.aegis:mapping"] = ""
    answers["dumpsys package"] = "  versionName=0.431.0\n"
    assert phone.game_pid() == 16473
    assert phone.mapping_pid() is None
    assert phone.game_version() == "0.431.0"


def test_an_unreachable_phone_reads_as_not_running(shell, mocker):
    mocker.patch.object(
        phone.subprocess, "run", return_value=MagicMock(returncode=1, stdout=b"")
    )
    assert phone.game_pid() is None
    assert phone.game_version() is None


def test_quiet_scanner_unpauses_even_when_the_body_raises(shell):
    calls, _ = shell
    with pytest.raises(RuntimeError):
        with phone.quiet_scanner():
            raise RuntimeError("boom")
    assert docker_calls(calls) == ["pause", "unpause"]


def test_attach_pauses_starts_the_injector_and_waits_for_ready(shell):
    calls, answers = shell
    answers["echo $!"] = "4242\n"
    answers["cat /data/data"] = "OWNED_REDIRECT_READY\n"
    hook = phone.Hook()
    hook.attach(16473)
    assert hook.injector_pid == 4242
    assert docker_calls(calls) == ["pause", "unpause"]
    pushed = [c for c in calls if "push" in c]
    assert len(pushed) == 1


def test_attach_reports_a_setup_error_and_still_unpauses(shell):
    calls, answers = shell
    answers["echo $!"] = "4242\n"
    answers["cat /data/data"] = "OWNED_REDIRECT_SETUP_ERROR no module\n"
    with pytest.raises(phone.HookError, match="setup"):
        phone.Hook().attach(16473)
    assert docker_calls(calls) == ["pause", "unpause"]


def test_wrap_marks_the_request_with_nonce_method_and_body():
    hook = phone.Hook()
    envelope = fields(hook.wrap(159, b"body"))
    assert one(envelope, 4095) == hook.nonce.encode()
    assert one(envelope, 4094) == b"body"
    assert one(envelope, 4093) == 159


def test_detach_asks_then_kills_the_injector_only_after_the_hook_left(shell):
    calls, answers = shell
    hook = phone.Hook()
    hook.injector_pid = 4242
    answers["cat /data/data"] = "OWNED_REDIRECT_READY\nOWNED_REDIRECT_DETACHED requested\n"
    sent = []
    # First log read says attached, so detach has to ask.
    logs = iter(["OWNED_REDIRECT_READY\n", "OWNED_REDIRECT_DETACHED requested\n"])
    hook.log = lambda: next(logs)
    assert hook.detach(sent.append) is True
    assert one(fields(sent[0]), 4093) == 0
    assert any("kill 4242" in " ".join(c) for c in calls)
    assert docker_calls(calls) == ["pause", "unpause"]


def test_detach_leaves_the_injector_alone_when_the_hook_did_not_leave(shell):
    calls, _ = shell
    hook = phone.Hook()
    hook.injector_pid = 4242
    hook.log = lambda: "OWNED_REDIRECT_READY\n"
    assert hook.detach(lambda payload: None) is False
    assert not any("kill" in " ".join(c) for c in calls)
    assert docker_calls(calls) == ["pause", "unpause"]


def test_detach_without_an_attach_is_a_no_op(shell):
    calls, _ = shell
    assert phone.Hook().detach(lambda payload: None) is True
    assert calls == []
```

Run: `pytest -q tests/scripts/test_shiny_linker_phone.py 2>&1 | tail -3`
Expected: `ImportError: cannot import name 'phone'`.

- [ ] **Step 4: Write `scripts/shiny_linker/phone.py`**

```python
"""The scanner phone: process checks over adb, and the temporary hook.

The scanner's agent (Aegis) only forwards a fixed list of game methods, and
the ones the linker needs are not on it. The hook rewrites a GET_PLAYER
request that carries this run's random nonce into one of five listed
methods; every other request passes untouched.

Patching the game's send function while workers are calling it crashed the
game twice on 2026-10-10 (SIGSEGV at the function's entry), so Dragonite is
frozen for a few seconds around attach and around detach, and the injector
is only ever killed after the hook has removed itself.
"""

import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .proto import blob, integer

GAME = "com.nianticlabs.pokemongo"
MAPPING = "com.pokemod.aegis:mapping"
INJECTOR = "/data/user/0/com.pokemod.aegis/files/injector/pokemod-injector-arm64"
# Social proxy (search by friend code), lobby join, lobby leave, free-ticket
# claim, raid invitation.
METHODS = [5012, 159, 160, 815, 1504]
SOCIAL_ACTIONS = [10000]
# The hook removes itself after this long whatever happens.
LIFETIME_MS = 600_000
_HOOK_SOURCE = Path(__file__).with_name("native-method-redirect.js")


class PhoneError(RuntimeError):
    pass


class HookError(RuntimeError):
    """The hook itself misbehaved. The linker switches itself off on this."""


def _serial():
    return os.environ.get("ADB_DEVICE") or "192.168.1.222:5555"


def adb(command, timeout=20):
    done = subprocess.run(
        ["adb", "-s", _serial(), "exec-out", command],
        capture_output=True,
        timeout=timeout,
    )
    if done.returncode:
        raise PhoneError("adb command failed")
    return done.stdout.decode("utf8", "replace").strip()


def _pid(process):
    try:
        out = adb(f"pidof {process}")
    except (PhoneError, subprocess.TimeoutExpired):
        return None
    return int(out.split()[0]) if out else None


def game_pid():
    return _pid(GAME)


def mapping_pid():
    return _pid(MAPPING)


def game_version():
    try:
        out = adb(f"dumpsys package {GAME}")
    except (PhoneError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"\bversionName=([0-9.]+)", out)
    return match.group(1) if match else None


class quiet_scanner:
    """Freeze Dragonite so no worker request reaches the game meanwhile."""

    def __enter__(self):
        subprocess.run(["docker", "pause", "dragonite"], capture_output=True, check=True)
        time.sleep(2)

    def __exit__(self, *error):
        subprocess.run(["docker", "unpause", "dragonite"], capture_output=True, check=True)


class Hook:
    def __init__(self):
        self.nonce = uuid.uuid4().hex
        self.injector_pid = None
        self._script = f"/data/local/tmp/shiny-linker-{self.nonce}.js"
        self._log = f"/data/data/{GAME}/cache/shiny-linker-{self.nonce}.log"

    def log(self):
        return adb(f"su -c 'cat {self._log} 2>/dev/null'")

    def wrap(self, method, payload=b""):
        """A GET_PLAYER payload the hook turns into `method` with `payload`."""
        return blob(4095, self.nonce) + blob(4094, payload) + integer(4093, method)

    def attach(self, pid):
        config = {
            "nonce": list(self.nonce.encode()),
            "apiFieldCount": 36,
            "sendIndex": 28,
            "logPath": self._log,
            "methods": METHODS,
            "socialActions": SOCIAL_ACTIONS,
            "lifetimeMs": LIFETIME_MS,
            "detachDelayMs": 1500,
        }
        source = _HOOK_SOURCE.read_text().replace(
            "__OWNED_REDIRECT_CONFIG__", json.dumps(config)
        )
        with tempfile.NamedTemporaryFile("w", suffix=".js") as local:
            local.write(source)
            local.flush()
            subprocess.run(
                ["adb", "-s", _serial(), "push", local.name, self._script],
                capture_output=True,
                check=True,
                timeout=30,
            )
        try:
            with quiet_scanner():
                self.injector_pid = int(
                    adb(
                        f"su -c 'nohup {INJECTOR} -p {pid} -s {self._script} -R v8 "
                        "> /dev/null 2>&1 & echo $!'"
                    )
                )
                for _ in range(20):
                    log = self.log()
                    if "OWNED_REDIRECT_READY" in log:
                        break
                    if "OWNED_REDIRECT_SETUP_ERROR" in log:
                        raise HookError("hook setup failed")
                    time.sleep(0.5)
                else:
                    raise HookError("hook did not become ready")
                time.sleep(1)
        except (PhoneError, subprocess.SubprocessError, ValueError) as error:
            raise HookError(f"hook attach failed: {type(error).__name__}") from error
        finally:
            try:
                adb(f"su -c 'rm -f {self._script}'")
            except (PhoneError, subprocess.SubprocessError):
                pass

    def detach(self, send):
        """Ask the hook to remove itself, then stop the injector.

        `send(payload)` delivers a GET_PLAYER payload through a logged-in
        session. Returns False if the hook is still in the game; the
        injector is then left running (killing it with the hook attached is
        what crashed the game) and the hook removes itself at LIFETIME_MS.
        """
        if self.injector_pid is None:
            return True
        try:
            with quiet_scanner():
                if "OWNED_REDIRECT_DETACHED" not in self.log():
                    try:
                        send(self.wrap(0))
                    except Exception:
                        pass
                    time.sleep(4)
                    if "OWNED_REDIRECT_DETACHED" not in self.log():
                        return False
                adb(f"su -c 'kill {self.injector_pid}'")
                time.sleep(1.5)
            adb(f"su -c 'rm -f {self._log}'")
            return True
        except (PhoneError, subprocess.SubprocessError):
            return False
```

- [ ] **Step 5: Run the phone tests**

Run: `pytest -q tests/scripts/test_shiny_linker_phone.py 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/shiny_linker/native-method-redirect.js scripts/shiny_linker/phone.py tests/scripts/shiny_hook_harness.cjs tests/scripts/test_shiny_linker_phone.py
git commit -m "Shiny linker: phone checks and the hook, attached and removed with Dragonite frozen"
```

---

### Task 10: Linker — the lobby

**Files:**
- Create: `scripts/shiny_linker/lobby.py`
- Test: `tests/scripts/test_shiny_linker_lobby.py`

Field numbers used below (POGOProtos): `GymGetInfoOutProto.result=4`, fort `raid_info=20`, `raid_seed=1`; `GetRaidDetailsOutProto.lobby=1, result=4, ticket_used=6, free_ticket_available=7`; `JoinLobbyOutProto.result=1, lobby=2`; `LobbyProto.lobby_id=1, private=10, invited_player_ids=14`; `SendRaidInvitationOutProto.result=1, failed_invitee_ids=3`; social proxy `status=1, host=2, payload=3`; search reply `result=1, player=2`, player `player_id=1, codename=2`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from scripts.shiny_linker.lobby import CodeError, Lobby, RunError
from scripts.shiny_linker.proto import blob, fields, integer, one, text, uint

GYM = ("gym1", 39.74, -8.80)
ID_A = "0123456789abcdef"
ID_B = "fedcba9876543210"


class FakeHook:
    def wrap(self, method, payload=b""):
        return integer(4093, method) + blob(4094, payload)


class FakeSession:
    """Answers by method; a marked GET_PLAYER answers as its target method."""

    def __init__(self, replies):
        self.host = b""
        self.replies = {k: list(v) for k, v in replies.items()}
        self.calls = []

    def rpc(self, method, payload=b""):
        if method == 2:
            envelope = fields(payload)
            method, payload = one(envelope, 4093), one(envelope, 4094, b"")
        self.calls.append((method, fields(payload)))
        return self.replies[method].pop(0)


def lobby_proto(invited=(), private=1):
    return (
        blob(1, uint(7) + uint(8))
        + integer(10, private)
        + b"".join(blob(14, i) for i in invited)
    )


def search_reply(name="Rita99", seen="E:abc", result=1, status=1):
    player = blob(1, seen) + blob(2, name)
    return integer(1, status) + blob(2, b"host9") + blob(3, integer(1, result) + blob(2, player))


def gym_info(seed=555, gym="gym1"):
    fort = blob(1, gym) + blob(20, integer(1, seed))
    return blob(1, blob(1, fort)) + integer(4, 1)


def details(invited=(), free_ticket=0, result=1):
    return blob(1, lobby_proto(invited)) + integer(4, result) + integer(7, free_ticket)


def opened(extra=None):
    replies = {
        156: [gym_info()],
        163: [details()],
        159: [integer(1, 1) + blob(2, lobby_proto())],
    }
    replies.update(extra or {})
    session = FakeSession(replies)
    lobby = Lobby(session, FakeHook(), GYM, sleep=lambda _s: None)
    lobby.open()
    return lobby, session


class TestSearch:
    def test_returns_the_trainer_name_and_the_id_this_account_sees(self):
        session = FakeSession({5012: [search_reply()]})
        lobby = Lobby(session, FakeHook(), GYM)
        assert lobby.search("123456789012") == ("Rita99", "E:abc")
        method, request = session.calls[0]
        assert method == 5012 and one(request, 1) == 10000
        assert text(fields(one(request, 3)), 1) == "123456789012"
        assert session.host == b"host9"

    def test_an_unknown_code_fails_only_that_code(self):
        lobby = Lobby(FakeSession({5012: [search_reply(result=3)]}), FakeHook(), GYM)
        with pytest.raises(CodeError, match="não encontrado"):
            lobby.search("123456789012")

    def test_a_proxy_error_stops_the_run(self):
        lobby = Lobby(FakeSession({5012: [integer(1, 6)]}), FakeHook(), GYM)
        with pytest.raises(RunError):
            lobby.search("123456789012")


class TestOpen:
    def test_joins_privately_without_an_entry_cost(self):
        lobby, session = opened()
        method, join = [c for c in session.calls if c[0] == 159][0]
        assert one(join, 1) == 555 and one(join, 2) == b"gym1" and one(join, 4) == 1
        assert 13 not in join  # raid_entry_cost made the server answer NO_TICKET
        assert lobby.lobby_id == [7, 8]

    def test_claims_the_free_ticket_when_the_game_offers_it(self):
        _, session = opened({163: [details(free_ticket=1)], 815: [integer(1, 1)]})
        assert 815 in [c[0] for c in session.calls]

    def test_a_refused_ticket_stops_the_run(self):
        with pytest.raises(RunError, match="passe"):
            opened({163: [details(free_ticket=1)], 815: [integer(1, 4)]})

    def test_a_gym_whose_raid_ended_stops_the_run(self):
        with pytest.raises(RunError, match="raid"):
            opened({156: [blob(1, blob(1, blob(1, "gym1"))) + integer(4, 1)]})

    def test_a_refused_join_stops_the_run(self):
        with pytest.raises(RunError, match="9"):
            opened({159: [integer(1, 9)]})

    def test_a_public_lobby_is_refused(self):
        with pytest.raises(RunError):
            opened({159: [integer(1, 1) + blob(2, lobby_proto(private=0))]})


class TestInvite:
    def test_returns_the_one_new_invited_id(self):
        lobby, session = opened(
            {163: [details(), details(), details([ID_A])], 1504: [integer(1, 1)]}
        )
        assert lobby.invite("E:abc") == ID_A
        method, request = [c for c in session.calls if c[0] == 1504][0]
        assert one(request, 1) == b"E:abc" and one(request, 2) == b"gym1"

    def test_a_second_invite_ignores_the_first_id(self):
        lobby, _ = opened(
            {
                163: [details(), details([ID_A]), details([ID_A, ID_B])],
                1504: [integer(1, 1), integer(1, 1)],
            }
        )
        assert lobby.invite("E:a") == ID_A
        assert lobby.invite("E:b") == ID_B

    def test_a_refused_invite_fails_only_that_code(self):
        lobby, _ = opened({1504: [integer(1, 10)]})
        with pytest.raises(CodeError, match="recusado"):
            lobby.invite("E:abc")

    def test_a_listed_failed_invitee_fails_only_that_code(self):
        lobby, _ = opened({1504: [integer(1, 1) + blob(3, "E:abc")]})
        with pytest.raises(CodeError, match="recusado"):
            lobby.invite("E:abc")

    @pytest.mark.parametrize("result", [4, 5, 6, 7])
    def test_a_dead_lobby_stops_the_run(self, result):
        lobby, _ = opened({1504: [integer(1, result)]})
        with pytest.raises(RunError):
            lobby.invite("E:abc")

    def test_no_new_id_after_every_poll_fails_only_that_code(self):
        lobby, _ = opened({163: [details()] * 7, 1504: [integer(1, 1)]})
        with pytest.raises(CodeError, match="não devolveu"):
            lobby.invite("E:abc")

    def test_an_id_in_another_shape_is_not_stored(self):
        lobby, _ = opened({163: [details(), details(["E:viewer"])], 1504: [integer(1, 1)]})
        with pytest.raises(CodeError, match="formato"):
            lobby.invite("E:abc")

    def test_two_new_ids_at_once_stop_the_run(self):
        lobby, _ = opened({163: [details(), details([ID_A, ID_B])], 1504: [integer(1, 1)]})
        with pytest.raises(RunError, match="mais de um"):
            lobby.invite("E:abc")


class TestLeave:
    def test_leaves_the_lobby_it_opened(self):
        lobby, session = opened({160: [integer(1, 1)]})
        lobby.leave()
        method, request = session.calls[-1]
        assert method == 160 and one(request, 1) == 555
        assert lobby.lobby_id is None

    def test_leaving_without_a_lobby_sends_nothing(self):
        session = FakeSession({})
        Lobby(session, FakeHook(), GYM).leave()
        assert session.calls == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest -q tests/scripts/test_shiny_linker_lobby.py 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'scripts.shiny_linker.lobby'`.

- [ ] **Step 3: Write `scripts/shiny_linker/lobby.py`**

```python
"""One private raid lobby: look players up by friend code, invite each, and
read the internal ID the game lists for them.

What the live runs on 2026-10-10 established, and this relies on:
- a friend-code search returns the player's ID *as the searching account
  sees it*; that value is what the invitation takes, and it is not the ID
  the shiny roll uses;
- inviting needs no friendship;
- the lobby's `invited_player_ids` then lists the real ID (16 hex or 21
  digits), one more entry per invitation, so inviting one player at a time
  says whose it is;
- joining with a `raid_entry_cost` is answered NO_TICKET, a plain join works;
- leaving before the battle keeps the raid pass.

Battle start is never sent: the session and the hook both refuse it.
"""

import time

from modules.shiny_roll import is_player_id

from .proto import blob, fields, integer, one, packed_ints, real, text, uint

SEARCH_PLAYER = 10000
# Raid-details reads after an invitation before giving up on its ID.
POLLS = 6
# SendRaidInvitation results that mean the lobby itself is gone or spent:
# lobby not found, past cut-off, no invites remaining, lobby full.
_LOBBY_OVER = (4, 5, 6, 7)


class CodeError(Exception):
    """This one code cannot be linked. The text is shown to the member."""


class RunError(RuntimeError):
    """The run cannot continue. Unresolved codes stay pending."""


class Lobby:
    def __init__(self, session, hook, gym, sleep=time.sleep):
        self.session, self.hook = session, hook
        self.gym_id, self.lat, self.lon = gym
        self.sleep = sleep
        self.raid_seed = None
        self.lobby_id = None
        self.known = []

    def _native(self, method, payload=b""):
        return fields(self.session.rpc(2, self.hook.wrap(method, payload)))

    def search(self, friend_code):
        """(trainer name, the player's ID as this account sees it)."""
        proxy = self._native(
            5012,
            integer(1, SEARCH_PLAYER)
            + blob(2, self.session.host)
            + blob(3, blob(1, friend_code)),
        )
        if one(proxy, 1) not in (1, 2) or not isinstance(one(proxy, 3), bytes):
            raise RunError(f"pesquisa social falhou ({one(proxy, 1, 0)})")
        self.session.host = one(proxy, 2, self.session.host)
        reply = fields(one(proxy, 3))
        player = fields(one(reply, 2, b""))
        if one(reply, 1) != 1 or not text(player, 1) or not text(player, 2):
            raise CodeError("código não encontrado")
        return text(player, 2), text(player, 1)

    def _details(self):
        request = (
            integer(1, self.raid_seed)
            + blob(2, self.gym_id)
            + real(4, self.lat)
            + real(5, self.lon)
            + real(6, self.lat)
            + real(7, self.lon)
        )
        if self.lobby_id:
            request += blob(3, self._lobby_blob())
        return fields(self.session.rpc(163, request))

    def _lobby_blob(self):
        return b"".join(uint(part) for part in self.lobby_id)

    @staticmethod
    def _invited(lobby):
        return [v.decode("utf8") for v in lobby.get(14, []) if isinstance(v, bytes)]

    def open(self):
        here = real(2, self.lat) + real(3, self.lon)
        info = fields(
            self.session.rpc(
                156, blob(1, self.gym_id) + here + real(4, self.lat) + real(5, self.lon)
            )
        )
        if one(info, 4) != 1:
            raise RunError(f"ginásio não respondeu ({one(info, 4, 0)})")
        fort = fields(one(fields(one(info, 1, b"")), 1, b""))
        self.raid_seed = one(fields(one(fort, 20, b"")), 1)
        if (
            text(fort, 1) != self.gym_id
            or not isinstance(self.raid_seed, int)
            or not self.raid_seed
        ):
            raise RunError("o ginásio já não tem raid")

        details = self._details()
        if one(details, 4) != 1:
            raise RunError(f"detalhes da raid falharam ({one(details, 4, 0)})")
        if not one(details, 6, 0) and one(details, 7, 0):
            # Today's free pass is unclaimed. 3 = already awarded, also fine.
            award = self._native(815, blob(1, self.gym_id) + here)
            if one(award, 1) not in (1, 3):
                raise RunError(f"passe diário recusado ({one(award, 1, 0)})")

        reply = self._native(
            159,
            integer(1, self.raid_seed)
            + blob(2, self.gym_id)
            + integer(4, 1)
            + real(5, self.lat)
            + real(6, self.lon)
            + real(7, self.lat)
            + real(8, self.lon),
        )
        lobby = fields(one(reply, 2, b""))
        lobby_id = packed_ints(lobby, 1)
        if one(reply, 1) != 1 or not lobby_id or not one(lobby, 10, 0):
            raise RunError(f"entrada no lobby privado falhou ({one(reply, 1, 0)})")
        self.lobby_id = lobby_id
        self.known = self._invited(lobby)

    def invite(self, seen_id):
        """Invite one player; return the ID the lobby then lists for them."""
        reply = self._native(
            1504,
            blob(1, seen_id)
            + blob(2, self.gym_id)
            + blob(3, self._lobby_blob())
            + real(4, self.lat)
            + real(5, self.lon)
            + integer(6, 1),
        )
        result = one(reply, 1, 0)
        if result in _LOBBY_OVER:
            raise RunError(f"o lobby deixou de aceitar convites ({result})")
        if result != 1 or reply.get(3):
            raise CodeError(f"convite recusado pelo jogo ({result})")
        for _ in range(POLLS):
            lobby = fields(one(self._details(), 1, b""))
            fresh = [value for value in self._invited(lobby) if value not in self.known]
            if len(fresh) > 1:
                raise RunError("mais de um ID novo no lobby")
            if fresh:
                self.known.append(fresh[0])
                if not is_player_id(fresh[0]):
                    raise CodeError("ID com formato inesperado")
                return fresh[0]
            self.sleep(2)
        raise CodeError("o jogo não devolveu o ID")

    def leave(self):
        if not self.lobby_id:
            return
        self._native(
            160,
            integer(1, self.raid_seed) + blob(2, self.gym_id) + blob(3, self._lobby_blob()),
        )
        self.lobby_id = None
```

- [ ] **Step 4: Run the lobby tests**

Run: `pytest -q tests/scripts/test_shiny_linker_lobby.py 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/shiny_linker/lobby.py tests/scripts/test_shiny_linker_lobby.py
git commit -m "Shiny linker: search by code, private lobby, invite, read the ID, leave"
```

---

### Task 11: Linker — store, guards, the run, the CLI

**Files:**
- Create: `scripts/shiny_linker/store.py`, `scripts/shiny_linker/linker.py`, `scripts/shiny_linker/__main__.py`
- Test: `tests/scripts/test_shiny_linker_run.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The linker's decisions, with the phone, Rotom, the lobby and the database
all faked: which guards block a run, what each failure does to the queue,
and when the linker switches itself off."""

from types import SimpleNamespace

import pytest

from scripts.shiny_linker.linker import Linker
from scripts.shiny_linker.lobby import CodeError, RunError
from scripts.shiny_linker.proto import integer

ID_A = "0123456789abcdef"
ID_B = "fedcba9876543210"


class HookError(RuntimeError):
    pass


class FakeStore:
    def __init__(self):
        self.enabled = 1
        self.minutes_since_run = None
        self.alerted_version = None
        self.rows = [{"discord_id": 1, "friend_code": "111111111111", "attempts": 0}]
        self.gym = ("gym1", 39.74, -8.80)
        self.accounts = [("acct1", "token1")]
        self.linked, self.failed, self.bumped = [], [], []
        self.breaker = None
        self.started = 0

    def state(self):
        return {
            "enabled": self.enabled,
            "minutes_since_run": self.minutes_since_run,
            "alerted_version": self.alerted_version,
        }

    def pending(self, limit):
        return self.rows[:limit]

    def raid_gym(self):
        return self.gym

    def account(self, usernames, in_use):
        for name, token in self.accounts:
            if name in usernames and name not in in_use:
                return name, token
        return None

    def start_run(self):
        self.started += 1

    def mark_linked(self, discord_id, trainer_name, player_id):
        self.linked.append((discord_id, trainer_name, player_id))

    def mark_failed(self, discord_id, reason):
        self.failed.append((discord_id, reason))

    def bump_attempts(self, discord_ids, limit, reason):
        self.bumped.append((list(discord_ids), limit))

    def trip_breaker(self, reason):
        self.breaker = reason

    def set_alerted_version(self, version):
        self.alerted_version = version


class World:
    """Everything outside the linker, with knobs for what goes wrong."""

    def __init__(self):
        self.store = FakeStore()
        self.idle = 4
        self.in_use = set()
        self.pid = 100
        self.pid_after = 100
        self.mapping = 200
        self.version = "0.431.0"
        self.attach_error = None
        self.detached = True
        self.login_error = None
        self.search = {"111111111111": ("Rita99", "E:a"), "222222222222": ("Zé", "E:b")}
        self.invite = {"E:a": ID_A, "E:b": ID_B}
        self.open_error = None
        self.notified = []
        self.events = []
        world = self

        class Session:
            def __init__(self, lat, lon):
                world.events.append("session")
                self.host = b""

            def login(self, account, token):
                if world.login_error:
                    raise world.login_error
                world.events.append(f"login {account}")

            def rpc(self, method, payload=b""):
                return integer(1, 1)

            def close(self):
                world.events.append("close")

        class Hook:
            def attach(self, pid):
                world.events.append("attach")
                if world.attach_error:
                    raise world.attach_error

            def detach(self, send):
                world.events.append("detach")
                send(b"detach")
                return world.detached

        class Lobby:
            def __init__(self, session, hook, gym):
                pass

            def search(self, code):
                found = world.search[code]
                if isinstance(found, Exception):
                    raise found
                return found

            def open(self):
                world.events.append("open")
                if world.open_error:
                    raise world.open_error

            def invite(self, seen):
                found = world.invite[seen]
                if isinstance(found, Exception):
                    raise found
                return found

            def leave(self):
                world.events.append("leave")

        calls = {"pid": 0}

        def game_pid():
            calls["pid"] += 1
            return world.pid if calls["pid"] == 1 else world.pid_after

        self.rotom = SimpleNamespace(
            status=lambda: {"in_use": world.in_use},
            idle_workers=lambda state: world.idle,
            accounts_in_use=lambda state: set(state["in_use"]),
            Session=Session,
        )
        self.phone = SimpleNamespace(
            game_pid=game_pid,
            mapping_pid=lambda: world.mapping,
            game_version=lambda: world.version,
            Hook=Hook,
            HookError=HookError,
        )
        self.lobby_cls = Lobby

    def linker(self, **env):
        settings = {
            "SHINY_LINKER_ACCOUNTS": "acct1,acct2",
            "SHINY_LINKER_GAME_VERSIONS": "0.431.0",
            "SHINY_LINKER_SELFTEST_CODE": "999999999999",
            "SHINY_LINKER_SELFTEST_PLAYER_ID": ID_B,
        }
        settings.update(env)
        return Linker(
            self.store,
            settings,
            rotom=self.rotom,
            phone=self.phone,
            lobby_cls=self.lobby_cls,
            notify=self.notified.append,
        )


@pytest.fixture
def world():
    return World()


class TestGuards:
    def test_a_clean_world_links_the_pending_code(self, world):
        outcome = world.linker().run()
        assert world.store.linked == [(1, "Rita99", ID_A)]
        assert world.store.started == 1
        assert "1 ligado" in outcome
        # Logged in before the hook went in, and left before it came out.
        assert world.events == [
            "session", "login acct1", "attach", "open", "leave", "detach", "close",
        ]

    @pytest.mark.parametrize(
        "break_it, guard",
        [
            (lambda w: setattr(w.store, "enabled", 0), "ligador ativo"),
            (lambda w: setattr(w.store, "rows", []), "códigos pendentes"),
            (lambda w: setattr(w.store, "minutes_since_run", 59), "intervalo"),
            (lambda w: setattr(w.store, "gym", None), "raid ativa"),
            (lambda w: setattr(w, "idle", 0), "worker livre"),
            (lambda w: setattr(w, "in_use", {"acct1", "acct2"}), "conta livre"),
            (lambda w: setattr(w, "pid", None), "jogo a correr"),
            (lambda w: setattr(w, "mapping", None), "Aegis a mapear"),
            (lambda w: setattr(w, "version", "0.433.0"), "versão validada"),
        ],
    )
    def test_each_guard_alone_blocks_the_run(self, world, break_it, guard):
        break_it(world)
        outcome = world.linker().run()
        assert guard in outcome
        assert world.events == [] and world.store.started == 0
        assert world.store.linked == [] and world.store.breaker is None

    def test_an_hour_since_the_last_run_is_enough(self, world):
        world.store.minutes_since_run = 60
        world.linker().run()
        assert world.store.linked

    def test_the_second_account_is_used_when_the_first_is_scanning(self, world):
        world.in_use = {"acct1"}
        world.store.accounts = [("acct1", "t1"), ("acct2", "t2")]
        world.linker().run()
        assert "login acct2" in world.events

    def test_an_unvalidated_version_alerts_the_owner_once(self, world):
        world.version = "0.433.0"
        world.linker().run()
        world.linker().run()
        assert len(world.notified) == 1 and "0.433.0" in world.notified[0]

    def test_check_evaluates_every_guard_and_touches_nothing(self, world):
        world.store.rows = []
        world.idle = 0
        checks, _ = world.linker().survey()
        failed = [name for name, ok, _ in checks if not ok]
        assert failed == ["códigos pendentes", "worker livre"]
        assert world.events == []


class TestFailures:
    def test_an_unknown_code_fails_and_the_rest_are_linked(self, world):
        world.store.rows.append({"discord_id": 2, "friend_code": "222222222222", "attempts": 0})
        world.search["111111111111"] = CodeError("código não encontrado")
        world.linker().run()
        assert world.store.failed == [(1, "código não encontrado")]
        assert world.store.linked == [(2, "Zé", ID_B)]

    def test_no_lobby_is_opened_when_no_code_resolved(self, world):
        world.search["111111111111"] = CodeError("código não encontrado")
        world.linker().run()
        assert "open" not in world.events

    def test_a_refused_invite_fails_only_that_code(self, world):
        world.invite["E:a"] = CodeError("convite recusado pelo jogo (10)")
        world.linker().run()
        assert world.store.failed == [(1, "convite recusado pelo jogo (10)")]
        assert world.store.breaker is None

    def test_a_run_error_leaves_codes_pending_with_one_more_attempt(self, world):
        world.open_error = RunError("entrada no lobby privado falhou (9)")
        outcome = world.linker().run()
        assert world.store.bumped == [([1], 5)]
        assert world.store.linked == [] and world.store.failed == []
        assert world.store.breaker is None
        assert "falhou (9)" in outcome
        assert world.events[-3:] == ["leave", "detach", "close"]

    def test_a_failed_login_never_touches_the_game(self, world):
        world.login_error = RuntimeError("login did not reach a token (5)")
        world.linker().run()
        assert "attach" not in world.events and "detach" not in world.events
        assert world.store.bumped == [([1], 5)]
        assert world.store.breaker is None

    def test_an_account_that_started_scanning_is_not_logged_in(self, world):
        states = iter([set(), {"acct1"}])
        world.rotom.status = lambda: {"in_use": next(states)}
        world.linker().run()
        assert "login acct1" not in world.events and "attach" not in world.events
        assert world.store.breaker is None

    def test_a_hook_error_switches_the_linker_off_and_tells_the_owner(self, world):
        world.attach_error = HookError("hook setup failed")
        world.linker().run()
        assert world.store.breaker == "hook setup failed"
        assert len(world.notified) == 1 and "desligado" in world.notified[0]

    def test_a_hook_that_did_not_leave_switches_the_linker_off(self, world):
        world.detached = False
        world.linker().run()
        assert "hook" in world.store.breaker
        assert world.store.linked == [(1, "Rita99", ID_A)]  # the work is kept

    def test_a_game_restart_during_the_run_switches_the_linker_off(self, world):
        world.pid_after = 999
        world.linker().run()
        assert "reiniciou" in world.store.breaker


class TestValidate:
    def test_passes_when_the_known_account_returns_its_known_id(self, world):
        world.search["999999999999"] = ("Scanner", "E:b")
        world.version = "0.433.0"  # the point of validating
        world.store.rows = []
        world.store.minutes_since_run = 1
        outcome = world.linker().run(validate=True)
        assert outcome.startswith("validação OK")
        assert world.store.linked == [] and world.store.started == 0

    def test_fails_when_another_id_comes_back(self, world):
        world.search["999999999999"] = ("Scanner", "E:a")
        outcome = world.linker().run(validate=True)
        assert outcome.startswith("validação FALHOU")
        assert world.store.bumped == [] and world.store.failed == []

    def test_still_needs_the_linker_switched_on(self, world):
        world.store.enabled = 0
        assert "ligador ativo" in world.linker().run(validate=True)
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest -q tests/scripts/test_shiny_linker_run.py 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'scripts.shiny_linker.linker'`.

- [ ] **Step 3: Write `scripts/shiny_linker/store.py`**

```python
"""The linker's database access: the queue it drains, its own switch and
bookkeeping, and the two read-only lookups it needs from the scanner.

Runs on the host, so it reaches MariaDB on 127.0.0.1 (the container name in
Poliswag's DB_HOST only resolves inside Docker). Credentials and database
names come from Poliswag's .env.
"""

import os

import pymysql


class Store:
    def __init__(self):
        env = os.environ
        self.db = pymysql.connect(
            host=env.get("SHINY_LINKER_DB_HOST", "127.0.0.1"),
            port=int(env.get("DB_PORT", "3306")),
            user=env["DB_USER"],
            password=env["DB_PASSWORD"],
            database=env["DB_POLISWAG"],
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=15,
            write_timeout=15,
        )
        self.scanner = env["DB_SCANNER_NAME"]
        self.dragonite = env.get("DB_DRAGONITE", "dragonite")

    def _all(self, sql, params=()):
        with self.db.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())

    def _run(self, sql, params=()):
        with self.db.cursor() as cursor:
            return cursor.execute(sql, params)

    def state(self):
        return self._all(
            "SELECT shiny_linker_enabled AS enabled, "
            "shiny_linker_alerted_version AS alerted_version, "
            "TIMESTAMPDIFF(MINUTE, shiny_linker_last_run_at, UTC_TIMESTAMP()) "
            "AS minutes_since_run FROM poliswag"
        )[0]

    def pending(self, limit):
        return self._all(
            "SELECT discord_id, friend_code, attempts FROM shiny_link "
            "WHERE status = 'pending' AND friend_code IS NOT NULL "
            "ORDER BY requested_at LIMIT %s",
            (limit,),
        )

    def start_run(self):
        self._run("UPDATE poliswag SET shiny_linker_last_run_at = UTC_TIMESTAMP()")

    def mark_linked(self, discord_id, trainer_name, player_id):
        self._run(
            "UPDATE shiny_link SET status = 'linked', trainer_name = %s, player_id = %s, "
            "friend_code = NULL, fail_reason = NULL, resolved_at = UTC_TIMESTAMP() "
            "WHERE discord_id = %s AND status = 'pending'",
            (trainer_name[:32], player_id, discord_id),
        )

    def mark_failed(self, discord_id, reason):
        self._run(
            "UPDATE shiny_link SET status = 'failed', fail_reason = %s, "
            "resolved_at = UTC_TIMESTAMP() WHERE discord_id = %s AND status = 'pending'",
            (reason[:64], discord_id),
        )

    def bump_attempts(self, discord_ids, limit, reason):
        """One more failed run for these codes; past `limit` they fail."""
        if not discord_ids:
            return
        marks = ",".join(["%s"] * len(discord_ids))
        self._run(
            f"UPDATE shiny_link SET attempts = attempts + 1 "
            f"WHERE status = 'pending' AND discord_id IN ({marks})",
            tuple(discord_ids),
        )
        self._run(
            f"UPDATE shiny_link SET status = 'failed', fail_reason = %s, "
            f"resolved_at = UTC_TIMESTAMP() "
            f"WHERE status = 'pending' AND attempts >= %s AND discord_id IN ({marks})",
            (reason[:64], limit, *discord_ids),
        )

    def trip_breaker(self, reason):
        self._run(
            "UPDATE poliswag SET shiny_linker_enabled = 0, shiny_linker_disabled_reason = %s",
            (reason[:128],),
        )

    def set_alerted_version(self, version):
        self._run(
            "UPDATE poliswag SET shiny_linker_alerted_version = %s", (version[:16],)
        )

    def raid_gym(self):
        """(gym id, lat, lon) of an ordinary raid with 15+ minutes left."""
        rows = self._all(
            f"SELECT id, lat, lon FROM {self.scanner}.gym WHERE deleted = 0 "
            "AND raid_level IN (1, 3) AND raid_is_exclusive = 0 AND ex_raid_eligible = 0 "
            "AND raid_pokemon_id > 0 AND raid_battle_timestamp < UNIX_TIMESTAMP() "
            "AND raid_end_timestamp > UNIX_TIMESTAMP() + 900 "
            "ORDER BY raid_end_timestamp DESC LIMIT 1"
        )
        if not rows:
            return None
        return rows[0]["id"], float(rows[0]["lat"]), float(rows[0]["lon"])

    def account(self, usernames, in_use):
        """(username, refresh token) of the first listed healthy account that
        no Rotom controller is using, in the order they were listed."""
        candidates = [name for name in usernames if name not in in_use]
        if not candidates:
            return None
        marks = ",".join(["%s"] * len(candidates))
        rows = self._all(
            f"SELECT username, refresh_token FROM {self.dragonite}.account "
            f"WHERE username IN ({marks}) AND provider = 'ptc' AND refresh_token <> '' "
            "AND invalid = 0 AND auth_banned = 0 "
            "AND COALESCE(banned, 0) = 0 AND COALESCE(suspended, 0) = 0",
            tuple(candidates),
        )
        healthy = {row["username"]: row["refresh_token"] for row in rows}
        for name in candidates:
            if name in healthy:
                return name, healthy[name]
        return None
```

- [ ] **Step 4: Write `scripts/shiny_linker/linker.py`**

```python
"""One linker run: decide whether to run at all, then drive a scanner
account through a private lobby for up to ten pending codes.

The scanner comes first. A run only starts when every guard holds, uses a
worker and an account nobody else is using, logs in before the game is
touched, and switches the whole linker off (and tells the owner) if the hook
or the game misbehaves. It never restarts anything.
"""

import subprocess
import sys
from pathlib import Path

from . import phone as _phone
from . import rotom as _rotom
from .lobby import CodeError, Lobby, RunError
from .proto import fields, one

MAX_CODES = 10
MAX_ATTEMPTS = 5
MIN_MINUTES_BETWEEN_RUNS = 60
VERSION_GUARD = "versão validada"
_NOTIFY = Path(__file__).resolve().parent.parent / "notify_owner.py"


def notify_owner(message):
    subprocess.run(
        [sys.executable, str(_NOTIFY), message, "Shiny linker"], timeout=60, check=False
    )


def _listed(value):
    return [part.strip() for part in (value or "").split(",") if part.strip()]


class Linker:
    def __init__(
        self, store, env, rotom=_rotom, phone=_phone, lobby_cls=Lobby, notify=notify_owner
    ):
        self.store, self.rotom, self.phone = store, rotom, phone
        self.lobby_cls, self.notify = lobby_cls, notify
        self.accounts = _listed(env.get("SHINY_LINKER_ACCOUNTS"))
        self.versions = _listed(env.get("SHINY_LINKER_GAME_VERSIONS"))
        self.selftest_code = env.get("SHINY_LINKER_SELFTEST_CODE", "")
        self.selftest_id = env.get("SHINY_LINKER_SELFTEST_PLAYER_ID", "")
        self.session = None

    # --- guards -----------------------------------------------------------

    def survey(self, validate=False, stop_early=False):
        """([(guard, ok, detail)], context for the run). Changes nothing."""
        checks, ctx = [], {}
        for name, probe in self._guards(validate):
            ok, detail = probe(ctx)
            checks.append((name, bool(ok), detail))
            if stop_early and not ok:
                break
        return checks, ctx

    def _guards(self, validate):
        guards = [("ligador ativo", self._enabled)]
        if validate:
            guards.append(("conta de teste configurada", self._selftest))
        else:
            guards += [
                ("códigos pendentes", self._pending),
                ("intervalo desde a última ligação", self._interval),
            ]
        guards += [
            ("raid ativa", self._raid),
            ("worker livre", self._worker),
            ("conta livre", self._account),
            ("jogo a correr", self._game),
            ("Aegis a mapear", self._mapping),
        ]
        if not validate:
            guards.append((VERSION_GUARD, self._version))
        return guards

    def _enabled(self, ctx):
        ctx["state"] = self.store.state()
        return ctx["state"]["enabled"], ""

    def _selftest(self, ctx):
        ctx["targets"] = [
            {"discord_id": None, "friend_code": self.selftest_code, "attempts": 0}
        ]
        return self.selftest_code and self.selftest_id, ""

    def _pending(self, ctx):
        ctx["targets"] = self.store.pending(MAX_CODES)
        return ctx["targets"], f"{len(ctx['targets'])} em fila"

    def _interval(self, ctx):
        minutes = (ctx.get("state") or self.store.state())["minutes_since_run"]
        ok = minutes is None or minutes >= MIN_MINUTES_BETWEEN_RUNS
        return ok, "nunca correu" if minutes is None else f"há {minutes} min"

    def _raid(self, ctx):
        ctx["gym"] = self.store.raid_gym()
        return ctx["gym"], ""

    def _worker(self, ctx):
        try:
            ctx["rotom"] = self.rotom.status()
        except Exception:
            return False, "rotom não responde"
        idle = self.rotom.idle_workers(ctx["rotom"])
        return idle > 0, f"{idle} livres"

    def _account(self, ctx):
        in_use = self.rotom.accounts_in_use(ctx["rotom"]) if "rotom" in ctx else set()
        ctx["account"] = self.store.account(self.accounts, in_use)
        return ctx["account"], ""

    def _game(self, ctx):
        ctx["pid"] = self.phone.game_pid()
        return ctx["pid"], ""

    def _mapping(self, ctx):
        return self.phone.mapping_pid(), ""

    def _version(self, ctx):
        ctx["version"] = self.phone.game_version()
        return ctx["version"] in self.versions, ctx["version"] or "desconhecida"

    # --- the run ----------------------------------------------------------

    def run(self, validate=False):
        checks, ctx = self.survey(validate, stop_early=True)
        name, ok, detail = checks[-1]
        if not ok:
            if name == VERSION_GUARD and ctx.get("version"):
                self._alert_new_version(ctx)
            return f"sem ligação: {name}" + (f" ({detail})" if detail else "")
        if not validate:
            self.store.start_run()
        return self._link(ctx, validate)

    def _alert_new_version(self, ctx):
        version = ctx["version"]
        if ctx["state"]["alerted_version"] == version:
            return
        self.store.set_alerted_version(version)
        self.notify(
            f"O jogo no telemóvel passou para {version}. As ligações de shiny "
            "ficam paradas até correres `shiny-linker --validate` e juntares a "
            "versão a SHINY_LINKER_GAME_VERSIONS."
        )

    def _login(self, gym, account, token):
        # Re-checked here: minutes may have passed since the guard looked.
        if account in self.rotom.accounts_in_use(self.rotom.status()):
            raise RunError("a conta entrou em uso pelo scanner")
        self.session = self.rotom.Session(gym[1], gym[2])
        self.session.login(account, token)
        if one(fields(self.session.rpc(2)), 1) != 1:
            raise RunError("GET_PLAYER não respondeu com sucesso")

    def _link(self, ctx, validate):
        gym, (account, token), pid = ctx["gym"], ctx["account"], ctx["pid"]
        unsettled = list(ctx["targets"])
        linked = failed = 0
        breaker = error = None
        hook = self.phone.Hook()
        lobby = None
        attach_attempted = False

        def send_detach(payload):
            try:
                self.session.rpc(2, payload)
            except Exception:
                # The session died mid-run; the hook can only be asked to
                # leave through a logged-in one.
                self._close()
                self._login(gym, account, token)
                self.session.rpc(2, payload)

        try:
            self._login(gym, account, token)
            attach_attempted = True
            hook.attach(pid)
            lobby = self.lobby_cls(self.session, hook, gym)
            found = []
            for row in list(unsettled):
                try:
                    found.append((row, *lobby.search(row["friend_code"])))
                except CodeError as reason:
                    unsettled.remove(row)
                    failed += 1
                    if not validate:
                        self.store.mark_failed(row["discord_id"], str(reason))
            if found:
                lobby.open()
            for row, trainer_name, seen_id in found:
                try:
                    player_id = lobby.invite(seen_id)
                except CodeError as reason:
                    unsettled.remove(row)
                    failed += 1
                    if not validate:
                        self.store.mark_failed(row["discord_id"], str(reason))
                    continue
                unsettled.remove(row)
                if validate:
                    if player_id != self.selftest_id:
                        raise RunError("a conta de teste devolveu outro ID")
                else:
                    self.store.mark_linked(row["discord_id"], trainer_name, player_id)
                linked += 1
        except self.phone.HookError as problem:
            breaker = str(problem)
        except Exception as problem:
            error = str(problem) if isinstance(problem, RunError) else type(problem).__name__
        finally:
            if lobby is not None:
                try:
                    lobby.leave()
                except Exception:
                    pass
            detached = True
            if attach_attempted:
                try:
                    detached = hook.detach(send_detach)
                except Exception:
                    detached = False
            self._close()
            if not detached:
                breaker = breaker or "o hook não saiu do jogo"
            if self.phone.game_pid() != pid:
                breaker = "o jogo reiniciou durante a ligação"

        if unsettled and not validate:
            self.store.bump_attempts(
                [row["discord_id"] for row in unsettled], MAX_ATTEMPTS, "sem resposta do jogo"
            )
        if breaker:
            self.store.trip_breaker(breaker)
            self.notify(
                f"Ligador de shiny desligado: {breaker}. Vê o estado do scanner; "
                "para o voltar a ligar: UPDATE poliswag SET shiny_linker_enabled = 1."
            )
        if validate:
            if linked == 1 and not error and not breaker:
                return "validação OK: a conta de teste devolveu o ID conhecido"
            return f"validação FALHOU: {breaker or error or 'a conta de teste não foi resolvida'}"
        summary = f"{linked} ligado(s), {failed} falhado(s), {len(unsettled)} por resolver"
        return summary + (f"; erro: {error}" if error else "") + (
            f"; DESLIGADO: {breaker}" if breaker else ""
        )

    def _close(self):
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
```

- [ ] **Step 5: Run the run tests**

Run: `pytest -q tests/scripts/test_shiny_linker_run.py 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 6: Write `scripts/shiny_linker/__main__.py`**

```python
"""python -m scripts.shiny_linker [--check | --validate]

  (no flag)   resolve pending codes if every guard holds; what the timer runs
  --check     print each guard's state and change nothing
  --validate  after a game update: resolve the known scanner account
              (SHINY_LINKER_SELFTEST_*) and compare the ID
"""

import argparse
import fcntl
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[2]
LOCK = "/run/lock/shiny-linker.lock"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="shiny-linker", description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--validate", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv(REPO / ".env")
    # Imported after the environment is loaded: phone.py and store.py read it.
    from .linker import Linker
    from .store import Store

    with open(LOCK, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("outra ligação está a correr")
            return 0
        linker = Linker(Store(), os.environ)
        if args.check:
            checks, _ = linker.survey(validate=False)
            for name, ok, detail in checks:
                print(f"{'ok ' if ok else 'NÃO'}  {name}" + (f"  ({detail})" if detail else ""))
            return 0
        outcome = linker.run(validate=args.validate)
        print(outcome)
        return 1 if "FALHOU" in outcome or "DESLIGADO" in outcome else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: Check the CLI parses without touching anything**

Run: `python3 -m scripts.shiny_linker --help | head -3`
Expected: the usage line `usage: shiny-linker [-h] [--check | --validate]`.

- [ ] **Step 8: Run the whole suite**

Run: `pytest -q 2>&1 | tail -3`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add scripts/shiny_linker/store.py scripts/shiny_linker/linker.py scripts/shiny_linker/__main__.py tests/scripts/test_shiny_linker_run.py
git commit -m "Shiny linker: guards, one run, per-code and per-run failures, the breaker"
```

---

### Task 12: Units and documentation

**Files:**
- Create: `scripts/shiny-linker.service`, `scripts/shiny-linker.timer`, `docs/context/shiny.md`
- Modify: `docs/context.md`, `docs/context/host-ops.md`

- [ ] **Step 1: Write the units**

`scripts/shiny-linker.service`:

```ini
[Unit]
Description=Resolve pending !shiny friend codes through one scanner account
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=/root/Poliswag
# adb reads its key from $HOME/.android; system services get no HOME.
Environment=HOME=/root
ExecStart=/opt/shiny-linker/venv/bin/python -m scripts.shiny_linker
TimeoutStartSec=6min
```

`scripts/shiny-linker.timer`:

```ini
[Unit]
Description=Look for pending !shiny friend codes every 10 minutes

[Timer]
OnCalendar=*:0/10
AccuracySec=30s

[Install]
WantedBy=timers.target
```

- [ ] **Step 2: Write `docs/context/shiny.md`**

```markdown
# Shiny prediction

Read when touching `!shiny`, the linker, or shiny alert DMs.
Spec: `docs/superpowers/specs/2026-10-10-shiny-prediction-design.md`.
How the formula and the ID route were established, with the phone's crash
history: `/root/unonwhash/CLAUDE-HANDOVER.md`.

## The rule

`modules/shiny_roll.shiny_die(encounter_id, player_id)` is 0 for a spawn that
is shiny for that player at 1/512 (and at 1/64, 1/128, 1/256). `player_id` is
the game's internal ID: 16 hex (PTC) or 21 digits (Google). It is **not** the
ID a friend-code search or GET_PLAYER returns; those differ per viewer.

## Flow

| Step | Where | What |
|---|---|---|
| `!shiny <código>` | `cogs/shiny.py` | Row in `shiny_link`, `pending` |
| Resolve | `scripts/shiny_linker` (host, `shiny-linker.timer`, every 10 min) | Scanner account searches the code, opens a private raid lobby, invites, reads `invited_player_ids` → `linked` or `failed` |
| Announce | `modules/shiny_alerts.py` tick | One DM per finished link |
| Alert | same tick | New `golbat.pokemon` rows rolled for linked users in `SHINY_ALERT_USER_IDS`; roll 0 → DM with Era shiny / Não era |

## Linker rules

- Runs only if: enabled, pending codes, 60+ min since the last run, a level 1
  or 3 raid with 15+ min left, an idle worker, a listed account not scanning,
  game and Aegis running, game version validated. `--check` prints them.
- Logs in first, then attaches the hook with `dragonite` paused (~5 s), and
  pauses again to remove it. Never kills the injector with the hook attached.
- Breaker: a hook error, a hook that did not leave, or a game restart during
  a run sets `poliswag.shiny_linker_enabled = 0` and alerts the owner. Turn it
  back on with `UPDATE poliswag SET shiny_linker_enabled = 1`.
- After a game update: `python -m scripts.shiny_linker --validate`, then add
  the version to `SHINY_LINKER_GAME_VERSIONS` in `.env`.

## Settings (`.env`)

| Variable | Meaning |
|---|---|
| `SHINY_ALERT_USER_IDS` | Discord IDs that get alerts; default `MY_ID` |
| `SHINY_LINKER_ACCOUNTS` | Scanner usernames allowed to open lobbies, in order of preference |
| `SHINY_LINKER_GAME_VERSIONS` | Game versions the hook was validated on |
| `SHINY_LINKER_SELFTEST_CODE`, `SHINY_LINKER_SELFTEST_PLAYER_ID` | An owned scanner account's friend code and known player ID, for `--validate`; must not be one of `SHINY_LINKER_ACCOUNTS` |

## Tables (`poliswag`, migration 017)

`shiny_link` (one row per Discord user; the linker is the only writer of
`linked`/`failed`), `shiny_alert` (dedupe and the player's verdict), and on
`poliswag`: `shiny_linker_enabled`, `shiny_linker_disabled_reason`,
`shiny_linker_last_run_at`, `shiny_linker_alerted_version`,
`shiny_alert_watermark`.

## Known limits

Boosted and event shiny odds are not predicted. Lure, tappable and cell-only
sightings are not alerted. Nothing proves a friend code belongs to its sender.
```

- [ ] **Step 3: Add the rows to `docs/context.md`**

In the module map table, after the `hundo_alerts.py` row:

```markdown
| `shiny_roll.py` / `shiny_store.py` / `shiny_alerts.py` | Shiny prediction: the roll, the `shiny_link`/`shiny_alert` tables, link announcements and roll-0 DMs. See `shiny.md`. |
```

In the cog map table, after the `announcements.py` row:

```markdown
| `shiny.py` | `!shiny <código>` / `!shiny` / `!shiny remover`; listener for the alert verdict buttons | members; works in DMs (own membership check) |
```

In the "Databases" section, in the **poliswag** bullet, add `shiny_link`, `shiny_alert` to the list of tables.

In the "Topic files — read when…" table at the end, add:

```markdown
| `docs/context/shiny.md` | `!shiny`, the host linker and its guards/breaker, shiny alert DMs |
```

- [ ] **Step 4: Add the row to `docs/context/host-ops.md`**

In the table, after the `scanner-db-shutdown` row:

```markdown
| `python -m scripts.shiny_linker` | `shiny-linker.timer` (every 10 min) → `shiny-linker.service`, venv `/opt/shiny-linker/venv`. Resolves pending `!shiny` friend codes through one scanner account; does nothing unless every guard holds; switches itself off and alerts the owner if the hook or the game misbehaves. `--check` prints the guards, `--validate` re-checks the hook after a game update. Units: `scripts/shiny-linker.{service,timer}`. See [shiny.md](shiny.md). |
```

- [ ] **Step 5: Commit**

```bash
git add scripts/shiny-linker.service scripts/shiny-linker.timer docs/context/shiny.md docs/context.md docs/context/host-ops.md
git commit -m "Shiny prediction: timer units and context docs"
```

---

### Task 13: Go live (owner present for every step marked LIVE)

Nothing before this task touched production. Stop and confirm with the owner before each LIVE step.

- [ ] **Step 1: Final check on the branch**

Run: `cd /root/worktrees/poliswag-shiny && pytest -q 2>&1 | tail -3 && git status --short`
Expected: all tests pass; no uncommitted files.

- [ ] **Step 2: Merge into `main`**

```bash
cd /root/Poliswag
rm -f docs/superpowers/specs/2026-10-10-shiny-prediction-design.md docs/superpowers/plans/2026-10-10-shiny-prediction.md
git merge --ff-only shiny-prediction
```

(The two `rm`s remove the untracked originals so the merge can bring in the committed copies.) Expected: `Fast-forward`.

- [ ] **Step 3: Build the linker's venv**

```bash
python3 -m venv /opt/shiny-linker/venv
/opt/shiny-linker/venv/bin/pip install --quiet websocket-client pymysql python-dotenv
/opt/shiny-linker/venv/bin/python -c "import websocket, pymysql, dotenv; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Add the settings to `/root/Poliswag/.env` without printing them**

The three accounts that opened lobbies on 2026-10-10 become the linker accounts; the fourth account (resolved without friendship that day) is the self-test target.

```bash
python3 - <<'EOF'
import json
from pathlib import Path
evidence = Path("/root/unonwhash/shiny-validation")
accounts = [json.loads((evidence / f"native-friend-code-20261010-{tag}.paired.jsonl").read_text().splitlines()[0])["scannerAccount"] for tag in "cdb"]
(target,) = json.loads((evidence / "raid-nonfriend-20261010-a.raid-ids.json").read_text()).values()
env = Path("/root/Poliswag/.env")
text = env.read_text()
assert "SHINY_LINKER_ACCOUNTS" not in text, "already configured"
env.write_text(text.rstrip("\n") + "\n\n# Shiny prediction (docs/context/shiny.md)\n"
    f"SHINY_LINKER_ACCOUNTS={','.join(accounts)}\n"
    "SHINY_LINKER_GAME_VERSIONS=0.431.0\n"
    f"SHINY_LINKER_SELFTEST_CODE={target['friendCode']}\n"
    f"SHINY_LINKER_SELFTEST_PLAYER_ID={target['raidInvitedPlayerId']}\n")
print("added", len(accounts), "accounts and the self-test target")
EOF
```

Expected: `added 3 accounts and the self-test target`.

- [ ] **Step 5 (LIVE): Restart the bot so it applies migration 017 and loads the cog**

```bash
cd /root/Poliswag && docker compose -f docker-compose.prod.yaml up -d --force-recreate --no-deps poliswag
sleep 25 && docker logs --since 1m poliswag 2>&1 | grep -E "Shiny loaded|Migration|CRASH|Traceback" | tail
docker exec db mariadb -upogoleiria -ppogoleiria -N poliswag -e "SHOW TABLES LIKE 'shiny%'; SELECT shiny_linker_enabled, shiny_alert_watermark FROM poliswag"
```

Expected: `Shiny loaded!`, `Migrations up to date`, no `CRASH`; tables `shiny_alert` and `shiny_link`; `1` and a watermark that is NULL or a recent Unix time.

- [ ] **Step 6: Read-only guard check**

Run: `cd /root/Poliswag && /opt/shiny-linker/venv/bin/python -m scripts.shiny_linker --check`
Expected: every line `ok` except `códigos pendentes  (0 em fila)`, and possibly `raid ativa` outside raid hours. Any other `NÃO` is investigated before going on.

- [ ] **Step 7 (LIVE, needs a live raid): Validate against the known account**

This attaches the hook once (two ~5 s Dragonite pauses).

```bash
cd /root/Poliswag && /opt/shiny-linker/venv/bin/python -m scripts.shiny_linker --validate
adb -s 192.168.1.222:5555 shell 'pidof com.nianticlabs.pokemongo; ps -A | grep -c pokemod-injector'
docker ps --format '{{.Names}} {{.Status}}' | grep dragonite
```

Expected: `validação OK: a conta de teste devolveu o ID conhecido`; the same game PID as before the run; injector count `1` (Aegis's own); `dragonite Up …` without `(Paused)`. If it prints `validação FALHOU`, stop here and read the reason; do not enable the timer.

- [ ] **Step 8 (LIVE): The owner links their own code**

The owner sends `!shiny <their friend code>` to Poliswag. Then:

```bash
cd /root/Poliswag && /opt/shiny-linker/venv/bin/python -m scripts.shiny_linker
docker exec db mariadb -upogoleiria -ppogoleiria -N poliswag -e "SELECT status, trainer_name, LENGTH(player_id), fail_reason FROM shiny_link"
```

Expected: `1 ligado(s), 0 falhado(s), 0 por resolver`; the row `linked` with the owner's trainer name and a length of 16 or 21. Within a minute the owner gets the "associado ao treinador" DM.

- [ ] **Step 9 (LIVE): Enable the timer**

```bash
cp /root/Poliswag/scripts/shiny-linker.service /root/Poliswag/scripts/shiny-linker.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now shiny-linker.timer
systemctl list-timers shiny-linker.timer --no-pager | head -3
```

Expected: the timer listed with a next run within 10 minutes.

- [ ] **Step 10: Watch the first alerts**

```bash
docker exec db mariadb -upogoleiria -ppogoleiria -N poliswag -e "SELECT COUNT(*), SUM(verdict='shiny'), SUM(verdict='not_shiny') FROM shiny_alert"
```

Expected within a few hours: a handful of rows. The owner checks the alerted spawns in game and presses the buttons. The bar from the spec for adding a second tester: three `Era shiny` and no `Não era` on species the scanner has seen shiny.

- [ ] **Step 11: Record the outcome**

Append a dated section to `/root/unonwhash/CLAUDE-HANDOVER.md` saying the linker and owner alerts are live, where the code is (`/root/Poliswag`, `docs/context/shiny.md`), and the result of the first verdicts. Remove the worktree: `git -C /root/Poliswag worktree remove /root/worktrees/poliswag-shiny`.
