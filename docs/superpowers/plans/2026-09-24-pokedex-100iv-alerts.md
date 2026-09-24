# Pokédex 100IV alerts (Poliswag side) implementation plan

**Status:** live pilot running for the owner only (Leiria, at their
request), since 2026-09-24 21:53. Tasks 1–6 done; Task 7 done except a real
spawn observation and a live opt-out (see "Pilot record"). The site side
(settings UI, retry, notices) is not started; see Task 8's handoff.

**Goal:** DM a Trades collector (only the owner during the pilot) when a 100IV
spawns of a Pokémon missing from their 100IV Pokédex.

**Architecture:** `modules/hundo_alerts.py` runs every minute. Database-only
cleanup runs before other work and again in `finally`. Confirmations acknowledge
an explicit settings revision, and each player's failures are isolated. Active
players' missing tiles become Poracle rules in their current profile. Compare
all managed fields and duplicate counts; replace each player's rows in a
transaction. Retry a pending reload once per tick. Poracle sends spawn alerts.

**Spec:** `docs/superpowers/specs/2026-09-24-pokedex-100iv-alerts-design.md`

**Tech:** Python 3.11, discord.py, pymysql, aiohttp, pytest with asyncio auto
mode, MariaDB (`pogoleiria`, `poliswag`, `poracle`), PoracleNG.

**Conventions:** run tests from `/root/Poliswag`. Format with Black and lint
with Ruff. Put test imports at the top of the file, including imports needed
by later tasks. If committing, stage explicit paths; this workspace contains
unrelated edits. Use accurate commit authorship. A document review/edit does
not execute the deployment or message-sending steps in this plan.

## Files

| File | Work |
|---|---|
| `migrations/014_add_hundo_dms.sql` | Add eight columns, off by default |
| `tests/modules/test_migrations.py` | Accept commas inside SET declarations while still rejecting non-re-runnable ALTER clauses |
| `modules/hundo_alerts.py` | Revision rules, confirmations, cleanup, per-player sync, reload retry |
| `tests/modules/test_hundo_alerts.py` | Pure, orchestration, and actual DB-helper tests with fake connections |
| `tests/integration/test_hundo_alerts_sql.py` | Actual SQL against disposable MariaDB schemas |
| `modules/poracle_client.py` / `tests/modules/test_poracle_client.py` | Create a Discord user with its initial area |
| `cogs/scheduled.py` / `tests/cogs/test_scheduled.py` | Schedule the tick and verify successful execution |
| `/root/poracleng/config/dts.json` | Pilot template, backed up before deployment |
| `docs/context.md` | Document the module and revision contract |

## Task 1: Migration and writer contract

- [x] Create `migrations/014_add_hundo_dms.sql`:

```sql
-- Site/pilot writes settings and increments their revision atomically.
-- Poliswag records the attempted revision; timestamps are audit fields only.
ALTER TABLE pogoleiria.trade_player
  ADD COLUMN IF NOT EXISTS hundo_dms TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS hundo_areas SET('leiria','marinha') NOT NULL DEFAULT 'leiria,marinha',
  ADD COLUMN IF NOT EXISTS hundo_settings_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS hundo_settings_at DATETIME(6) NULL,
  ADD COLUMN IF NOT EXISTS hundo_confirmed_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS hundo_confirmed_at DATETIME(6) NULL,
  ADD COLUMN IF NOT EXISTS hundo_dm_refused_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS hundo_dm_refused_at DATETIME(6) NULL;
```

- [x] Update the ALTER expression in `_RERUNNABLE` in
  `tests/modules/test_migrations.py`. The existing `[^,]*` rejects the commas
  inside `SET('leiria','marinha')` and its quoted default. Use a tokenizer that
  splits clauses only at commas outside quoted strings and parentheses, then
  require every clause to begin `ADD COLUMN IF NOT EXISTS`. Preserve the
  existing CREATE TABLE check. Do not accept arbitrary ALTER statements.
  Cover SET/ENUM values, a quoted comma in a default, multiple columns,
  escaped quotes, and rejection of `DROP COLUMN` or `ADD COLUMN` without
  `IF NOT EXISTS` mixed into an otherwise valid statement.
  Replace the `_RERUNNABLE` list with this helper (the file already imports
  `re`), and change the per-statement assertion to `assert is_rerunnable(statement)`.

```python
def is_rerunnable(statement):
    if re.match(r"^CREATE TABLE IF NOT EXISTS\b", statement, re.I):
        return True
    match = re.fullmatch(r"ALTER TABLE \S+\s+(.+)", statement, re.I | re.S)
    if not match:
        return False
    text = match.group(1)
    clauses = []
    start = depth = index = 0
    quote = None
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
        elif char == "," and depth == 0:
            clauses.append(text[start:index].strip())
            start = index + 1
        index += 1
    if quote or depth:
        return False
    clauses.append(text[start:].strip())
    return all(
        re.match(r"ADD COLUMN IF NOT EXISTS\b", clause, re.I) for clause in clauses
    )
```

- [x] Run migration unit tests: `python3 -m pytest tests/modules/test_migrations.py -q --no-cov`
  — 34 passed; Black and Ruff passed.
- [x] In the disposable SQL test in Task 4, apply migration 014 twice and
  verify exactly eight `hundo%` columns with the defaults above. Do not apply
  it live yet.

The later site's settings writer must use an atomic server-side increment,
with canonical area ordering (`leiria,marinha`). Record this interface now:

```sql
UPDATE trade_player
SET hundo_dms = %s, hundo_areas = %s,
    hundo_settings_revision = hundo_settings_revision + 1,
    hundo_settings_at = NOW(6)
WHERE discord_id = %s
  AND NOT (hundo_dms <=> %s AND hundo_areas <=> %s);
```

Bind `(on, areas, discord_id, on, areas)`. A no-op save affects zero rows.
An explicit **Tentar novamente** action instead runs:

```sql
UPDATE trade_player
SET hundo_settings_revision = hundo_settings_revision + 1,
    hundo_settings_at = NOW(6)
WHERE discord_id = %s AND left_at IS NULL;
```

No unrelated setting updates this revision. `NOW(6)` is audit information;
same-time writes and clock changes cannot determine due/active state. The
site's refusal notice compares revision numbers, as specified in the design.

## Task 2: Pure rules

- [x] Implement `confirmation_due` and `is_active` with focused revision,
  eligibility, retry, timestamp-tie, and stale-delivery tests (22 tests pass).
  Extend the current module and test file rather than replacing these
  completed tests.
- [x] Implement area selection and Portuguese confirmation text for Leiria,
  Marinha Grande, both, and empty/default selection (11 additional tests pass).
- [x] Implement safe ordinary-form mapping and missing-tile rule pairs,
  preserving named forms and rejecting unresolved wildcards (12 additional
  tests pass).
- [x] Implement complete Poracle rule construction and comparison, including
  current profile, all filters, duplicate counts, canonical areas, and malformed
  stored rows (49 additional tests pass). The pure helpers are complete.

- [x] Start `tests/modules/test_hundo_alerts.py` with the following tests.
  The helpers are also used by Task 4. Import the implementation as
  `from modules import hundo_alerts as h`.

```python
"""Regression tests for the implementation reference in the reviewed plan."""

import json
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules import hundo_alerts as h


def player(**patch):
    return {
        "discord_id": 1,
        "display_name": "Rui",
        "collecting": "hundo,shiny",
        "show_costumes": 1,
        "left_at": None,
        "hundo_dms": 1,
        "hundo_areas": "leiria,marinha",
        "hundo_settings_revision": 1,
        "hundo_confirmed_revision": 1,
        "hundo_dm_refused_revision": 0,
        **patch,
    }


def human(**patch):
    return {
        "id": "1",
        "type": "discord:user",
        "enabled": 1,
        "admin_disable": 0,
        "current_profile_no": 2,
        **patch,
    }


def sender(players):
    bot = MagicMock()
    bot.quest_search.masterfile_data = {"pokemon": {"19": {"defaultFormId": 45}}}
    bot.poracle.reload = AsyncMock()
    bot.poracle.create_user = AsyncMock()
    bot.get_user.return_value.send = AsyncMock()
    bot.fetch_user = AsyncMock(return_value=bot.get_user.return_value)
    alerts = h.HundoAlerts(bot)
    alerts._read_players = MagicMock(return_value=players)
    alerts._cleanup = MagicMock(return_value=False)
    alerts._human = MagicMock(return_value=human())
    alerts._sync_player = MagicMock(return_value=False)
    alerts._record = MagicMock(return_value=True)
    return alerts, bot


@pytest.mark.parametrize(
    "patch,active,due",
    [
        ({}, True, False),
        ({"hundo_dms": 0}, False, False),
        ({"collecting": "shiny"}, False, False),
        ({"left_at": "left"}, False, False),
        ({"hundo_settings_revision": 0, "hundo_confirmed_revision": 0}, False, False),
        ({"hundo_settings_revision": 2}, False, True),
        ({"hundo_dm_refused_revision": 1}, False, False),
        (
            {
                "hundo_settings_revision": 2,
                "hundo_confirmed_revision": 2,
                "hundo_dm_refused_revision": 1,
            },
            True,
            False,
        ),
    ],
)
def test_revisions_determine_state(patch, active, due):
    row = player(**patch)
    assert h.is_active(row) is active
    assert h.confirmation_due(row) is due


def test_same_timestamp_does_not_hide_new_revision():
    row = player(
        hundo_settings_revision=2,
        hundo_settings_at="same second",
        hundo_confirmed_at="same second",
        hundo_dm_refused_at="same second",
    )
    assert h.confirmation_due(row)
    row["hundo_confirmed_revision"] = 2
    assert h.is_active(row)


def test_forms():
    forms = h.default_forms({"19": {"defaultFormId": 45}, "132": {}})
    assert h.wanted_rules([(19, 0), (19, 46), (132, 0)], forms) == {
        (19, 45),
        (19, 46),
        (132, 0),
    }
    with pytest.raises(ValueError):
        h.wanted_rules([(25, 0)], forms)
    with pytest.raises(ValueError):
        h.wanted_rules([(19, 0)], h.default_forms({"19": {"forms": {"46": {}}}}))


@pytest.mark.parametrize(
    "setting,areas,label",
    [
        ("", ["leiria", "marinhagrande"], "Leiria e Marinha Grande"),
        ("leiria", ["leiria"], "só Leiria"),
        ("marinha", ["marinhagrande"], "só Marinha Grande"),
    ],
)
def test_area_and_confirmation_text(setting, areas, label):
    assert json.loads(h.areas_json(setting)) == areas
    assert label in h.confirmation_text(True, setting)
    assert "Vamos remover" in h.confirmation_text(False, setting)


@pytest.mark.parametrize(
    "column,value",
    [
        ("min_iv", 0),
        ("max_iv", 99),
        ("profile_no", 3),
        ("distance", 500),
        ("costume", 0),
        ("min_cp", 200),
        ("override_areas", '["marinhagrande"]'),
        ("override_areas", "not json"),
    ],
)
def test_any_managed_field_change_requires_rebuild(column, value):
    wanted = h.rule_row(1, 19, 45, '["leiria"]', 2)
    assert not h.rules_equal([{**wanted, column: value}], [wanted])


def test_equality_ignores_uid_and_json_format_but_counts_duplicates():
    wanted = h.rule_row(1, 19, 45, '["leiria", "marinhagrande"]', 2)
    current = {**wanted, "uid": 123, "override_areas": '["marinhagrande","leiria"]'}
    assert h.rules_equal([current], [wanted])
    assert not h.rules_equal([current, current], [wanted])
```

- [x] Create `modules/hundo_alerts.py` with this pure half. The imports also
  cover the orchestration half appended in Task 4.

```python
import asyncio
import json
from collections import Counter
from contextlib import closing

import discord

from modules.config import Config
from modules.database_connector import connect

TEMPLATE = "pokedex-100iv"

# The site's area keys, in display order, with Poracle's geofence names.
_AREAS = (
    ("leiria", "leiria", "Leiria"),
    ("marinha", "marinhagrande", "Marinha Grande"),
)

# Everything a rule needs besides who, which Pokémon and where. Copied from
# the channel rules' own row (poracle.monsters), with IV pinned at 100.
_RULE_DEFAULTS = {
    "ping": "",
    "clean": 0,
    "distance": 0,
    "min_iv": 100,
    "max_iv": 100,
    "min_cp": 0,
    "max_cp": 9000,
    "min_level": 0,
    "max_level": 55,
    "atk": 0,
    "def": 0,
    "sta": 0,
    "max_atk": 15,
    "max_def": 15,
    "max_sta": 15,
    "min_weight": 0,
    "max_weight": 9000000,
    "gender": 0,
    "min_time": 0,
    "rarity": -1,
    "max_rarity": 6,
    "pvp_ranking_worst": 4096,
    "pvp_ranking_best": 1,
    "pvp_ranking_min_cp": 0,
    "pvp_ranking_league": 0,
    "pvp_ranking_cap": 0,
    "size": -1,
    "max_size": 5,
    "override_location_label": None,
    "pvp_ranking_evolution": 0,
    "costume": 9000,
}


def _area_keys(hundo_areas):
    """The chosen areas in display order; none chosen means both."""
    chosen = {a for a in (hundo_areas or "").split(",") if a}
    keys = [key for key, _poracle, _label in _AREAS if key in chosen]
    return keys or [key for key, _poracle, _label in _AREAS]


def areas_json(hundo_areas):
    """Poracle's override_areas value for the player's choice."""
    names = {key: poracle for key, poracle, _label in _AREAS}
    return json.dumps([names[key] for key in _area_keys(hundo_areas)])


def confirmation_text(on, hundo_areas):
    """The settings as they are now, never a diff: a minute's several
    changes arrive together."""
    if not on:
        return "100IV por DM: **desligado**. Vamos remover os teus alertas de 100IV."
    labels = {key: label for key, _poracle, label in _AREAS}
    keys = _area_keys(hundo_areas)
    where = (
        " e ".join(labels[key] for key in keys)
        if len(keys) > 1
        else f"só {labels[keys[0]]}"
    )
    return (
        f"100IV por DM: **ligado** · {where}. "
        "Vais receber aqui os 100IV que te faltam na Pokédex."
    )


def confirmation_due(row):
    return row["hundo_settings_revision"] > max(
        row["hundo_confirmed_revision"], row["hundo_dm_refused_revision"]
    )


def is_active(row):
    revision = row["hundo_settings_revision"]
    return (
        row["hundo_dms"] == 1
        and "hundo" in (row["collecting"] or "").split(",")
        and row["left_at"] is None
        and revision > 0
        and row["hundo_confirmed_revision"] == revision
        and row["hundo_dm_refused_revision"] < revision
    )


def default_forms(pokemon):
    """None marks an ordinary tile that cannot safely become an exact rule."""
    result = {}
    for pokemon_id, details in pokemon.items():
        form = int(details.get("defaultFormId") or 0)
        result[int(pokemon_id)] = (
            form if form else (None if details.get("forms") else 0)
        )
    return result


def wanted_rules(missing_tiles, forms):
    result = set()
    for pokemon_id, form_id in missing_tiles:
        if form_id == 0:
            form_id = forms.get(pokemon_id)
            if form_id is None:
                raise ValueError(f"No safe ordinary form for species {pokemon_id}")
        result.add((pokemon_id, form_id))
    return result


def rule_row(discord_id, pokemon_id, form, override_areas, profile_no):
    """One poracle.monsters row."""
    return {
        **_RULE_DEFAULTS,
        "id": str(discord_id),
        "profile_no": profile_no,
        "template": TEMPLATE,
        "pokemon_id": pokemon_id,
        "form": form,
        "override_areas": override_areas,
    }


RULE_COLUMNS = tuple(rule_row(0, 0, 0, "[]", 1))


def rules_equal(current, wanted):
    """Compare every managed field, retaining duplicate counts; ignore uid."""

    def signature(row):
        values = []
        for column in RULE_COLUMNS:
            value = row[column]
            if column == "override_areas":
                areas = json.loads(value)
                if not isinstance(areas, list) or not all(
                    isinstance(area, str) for area in areas
                ):
                    raise ValueError("Malformed area override")
                value = tuple(sorted(set(areas)))
            values.append(value)
        return tuple(values)

    try:
        return Counter(map(signature, current)) == Counter(map(signature, wanted))
    except (KeyError, TypeError, ValueError):
        return False
```

- [x] Run `python3 -m pytest tests/modules/test_hundo_alerts.py tests/modules/test_migrations.py -q --no-cov` (128 passed).
  Keep the tests for every managed field, duplicate rows, JSON canonicalization,
  timestamp ties, revision transitions, area wording, and safe form translation.

## Task 3: Create a Poracle user with its initial area

- [x] Add this method beside `create_channel` in `modules/poracle_client.py`:

```python
    async def create_user(self, user_id: str | int, name: str, *, area: str) -> dict:
        """Create an enabled Discord user and default profile with this area."""
        return await self._request(
            "POST",
            "/api/humans",
            json={"id": str(user_id), "name": name, "type": "discord:user", "area": area},
        )
```

Poracle expects `area` to be a JSON-array **string**. Its creation endpoint
creates the enabled human and default profile. Do not add a separate `start`
call or direct post-creation area UPDATE. An existing stopped user stays stopped.

- [x] Add creation-payload tests to `TestHumans` in `tests/modules/test_poracle_client.py`,
  plus conflict/server-error and timeout propagation checks (five new cases):

```python
    async def test_create_user_sends_expected_payload(self, client):
        session = _install_session(client, _response(json_data={"id": "123"}))
        await client.create_user(123, "Rui", area='["leiria"]')
        args, kwargs = session.request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/api/humans")
        assert kwargs["json"] == {
            "id": "123", "name": "Rui", "type": "discord:user", "area": '["leiria"]'
        }
```

- [x] Verify client, hundo-helper, and migration tests together:
  `python3 -m pytest tests/modules/test_poracle_client.py tests/modules/test_hundo_alerts.py tests/modules/test_migrations.py -q --no-cov`
  — 156 passed; Black and Ruff passed.
  Existing-user conflicts/timeouts must surface as failures; the tick retries
  the lookup later and never resets an existing user's state.

## Task 4: Tick, transactions, and regression coverage

- [x] Append the following tests to `tests/modules/test_hundo_alerts.py`.
  Imports are already at the top from Task 2; do not append new imports below
  test definitions. These tests intentionally call the real `_sync_player`,
  `_record`, and `_cleanup` methods with fake connections in addition to
  orchestration tests.

```python
async def test_transient_confirmation_does_not_block_other_users_or_cleanup():
    first = player(hundo_settings_revision=2)
    alerts, bot = sender([first, player(discord_id=2)])
    bot.get_user.return_value.send.side_effect = discord.HTTPException(
        MagicMock(status=503), "unavailable"
    )
    await alerts.tick()
    alerts._record.assert_not_called()
    assert alerts._cleanup.call_count == 2
    assert alerts._sync_player.call_args.args[0]["discord_id"] == 2


async def test_confirmation_rereads_newer_settings_before_sync():
    old = player(hundo_settings_revision=2)
    new = player(hundo_settings_revision=3)
    alerts, _ = sender([old])
    alerts._read_players.side_effect = [[old], [new]]
    alerts._record.return_value = False  # conditional UPDATE rejected old revision
    await alerts.tick()
    alerts._record.assert_called_once_with(1, 2, delivered=True)
    alerts._sync_player.assert_not_called()


async def test_confirmed_current_revision_can_sync_in_same_tick():
    old = player(hundo_settings_revision=2)
    alerts, _ = sender([old])
    alerts._read_players.side_effect = [
        [old],
        [player(hundo_settings_revision=2, hundo_confirmed_revision=2)],
    ]
    await alerts.tick()
    alerts._sync_player.assert_called_once()
    assert alerts._sync_player.call_args.args[2] == 2  # existing active profile


@pytest.mark.parametrize("error", [discord.Forbidden, discord.NotFound])
async def test_refusal_records_exact_revision(error):
    alerts, bot = sender([player(hundo_settings_revision=2)])
    bot.get_user.return_value.send.side_effect = error(MagicMock(status=403), "closed")
    await alerts.tick()
    alerts._record.assert_called_once_with(1, 2, delivered=False)
    alerts._sync_player.assert_not_called()
    assert alerts._cleanup.call_count == 2


async def test_missing_masterfile_still_cleans_and_reloads():
    alerts, bot = sender([player(hundo_dms=0)])
    bot.quest_search.masterfile_data = {}
    alerts._cleanup.side_effect = [True, False]
    await alerts.tick()
    assert alerts._cleanup.call_count == 2
    alerts._sync_player.assert_not_called()
    bot.poracle.reload.assert_awaited_once()


async def test_failed_creation_does_not_block_other_users():
    alerts, bot = sender([player(), player(discord_id=2)])
    alerts._human.side_effect = [None, human(id="2")]
    bot.poracle.create_user.side_effect = RuntimeError("API unavailable")
    await alerts.tick()
    assert alerts._sync_player.call_args.args[0]["discord_id"] == 2
    assert alerts._cleanup.call_count == 2


async def test_creation_supplies_area_and_does_not_start_or_patch_user():
    alerts, bot = sender([player()])
    alerts._human.side_effect = [None, human()]
    await alerts.tick()
    bot.poracle.create_user.assert_awaited_once_with(
        1, "Rui", area=h.areas_json("leiria,marinha")
    )
    bot.poracle.start.assert_not_called()
    alerts._sync_player.assert_called_once()


@pytest.mark.parametrize("patch", [{"enabled": 0}, {"admin_disable": 1}])
async def test_stopped_user_is_never_reenabled(patch):
    alerts, bot = sender([player()])
    alerts._human.return_value = human(**patch)
    await alerts.tick()
    bot.poracle.create_user.assert_not_awaited()
    bot.poracle.start.assert_not_called()
    alerts._sync_player.assert_not_called()


async def test_failed_reload_is_retried_without_new_changes():
    alerts, bot = sender([])
    alerts._cleanup.side_effect = [True, False, False, False]
    bot.poracle.reload.side_effect = [RuntimeError("unavailable"), None]
    await alerts.tick()
    assert alerts._reload_pending
    await alerts.tick()
    assert not alerts._reload_pending
    assert bot.poracle.reload.await_count == 2


async def test_player_failure_does_not_prevent_committed_cleanup_reload():
    alerts, bot = sender([player(), player(discord_id=2)])
    alerts._cleanup.side_effect = [True, False]
    alerts._sync_player.side_effect = [RuntimeError("insert failed"), True]
    await alerts.tick()
    assert alerts._sync_player.call_count == 2
    bot.poracle.reload.assert_awaited_once()


async def test_read_failure_still_runs_final_cleanup_and_pending_reload():
    alerts, bot = sender([])
    alerts._cleanup.side_effect = [True, RuntimeError("cleanup failed")]
    alerts._read_players.side_effect = RuntimeError("read failed")
    with pytest.raises(RuntimeError, match="read failed"):
        await alerts.tick()
    bot.poracle.reload.assert_awaited_once()


def connection(rows=(), rowcount=0):
    db = MagicMock()
    cursor = db.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = rows
    cursor.rowcount = rowcount
    return db, cursor


def test_record_is_conditional_on_captured_revision(monkeypatch):
    db, cursor = connection(rowcount=0)
    monkeypatch.setattr(h, "connect", MagicMock(return_value=db))
    alerts = h.HundoAlerts(MagicMock())
    assert not alerts._record(1, 7, delivered=True)
    sql, params = cursor.execute.call_args.args
    assert "hundo_settings_revision = %s" in sql
    assert "GREATEST(hundo_confirmed_revision" in sql
    assert params == (7, 1, 7, 7)
    db.close.assert_called_once()


@pytest.mark.parametrize(
    "current_kind",
    ["same", "empty", "wrong_iv", "duplicate", "wrong_profile", "wrong_area"],
)
def test_real_sync_helper_compares_and_writes_full_rows(monkeypatch, current_kind):
    wanted = h.rule_row(1, 19, 45, h.areas_json("leiria,marinha"), 2)
    current = {
        "same": [wanted],
        "empty": [],
        "wrong_iv": [{**wanted, "min_iv": 0}],
        "duplicate": [wanted, wanted],
        "wrong_profile": [{**wanted, "profile_no": 1}],
        "wrong_area": [{**wanted, "override_areas": '["leiria"]'}],
    }[current_kind]
    pogo, read = connection([{"pokemon_id": 19, "form_id": 0}])
    poracle, write = connection(current)
    monkeypatch.setattr(h, "connect", MagicMock(side_effect=[pogo, poracle]))
    alerts = h.HundoAlerts(MagicMock())
    assert alerts._sync_player(player(), {19: 45}, 2) is (current_kind != "same")
    assert read.execute.call_args.args[1] == (1, 1)
    select_sql = write.execute.call_args_list[0].args[0]
    assert all(f"`{column}`" in select_sql for column in h.RULE_COLUMNS)
    if current_kind == "same":
        write.executemany.assert_not_called()
    else:
        assert write.execute.call_args_list[1].args == (
            "DELETE FROM monsters WHERE id = %s AND template = %s",
            ("1", h.TEMPLATE),
        )
        assert write.executemany.call_args.args[1] == [
            tuple(wanted[c] for c in h.RULE_COLUMNS)
        ]
    poracle.commit.assert_called_once()
    poracle.rollback.assert_not_called()
    pogo.close.assert_called_once()
    poracle.close.assert_called_once()


def test_empty_wanted_removes_owned_tile_without_inserting(monkeypatch):
    pogo, _ = connection([])
    poracle, write = connection([h.rule_row(1, 19, 45, '["leiria"]', 2)])
    monkeypatch.setattr(h, "connect", MagicMock(side_effect=[pogo, poracle]))
    assert h.HundoAlerts(MagicMock())._sync_player(player(), {19: 45}, 2)
    write.executemany.assert_not_called()
    assert "DELETE" in write.execute.call_args.args[0]


def test_failed_insert_rolls_back_player_transaction(monkeypatch):
    pogo, _ = connection([{"pokemon_id": 19, "form_id": 0}])
    poracle, write = connection([])
    write.executemany.side_effect = RuntimeError("insert failed")
    monkeypatch.setattr(h, "connect", MagicMock(side_effect=[pogo, poracle]))
    with pytest.raises(RuntimeError, match="insert failed"):
        h.HundoAlerts(MagicMock())._sync_player(player(), {19: 45}, 2)
    poracle.rollback.assert_called_once()
    poracle.commit.assert_not_called()
    poracle.close.assert_called_once()


def test_cleanup_uses_its_own_transaction(monkeypatch):
    db, cursor = connection(rowcount=3)
    monkeypatch.setattr(h, "connect", MagicMock(return_value=db))
    assert h.HundoAlerts(MagicMock())._cleanup()
    cursor.execute.assert_called_once_with(h._CLEANUP_SQL, (h.TEMPLATE,))
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


async def test_unchanged_tick_does_not_reload():
    alerts, bot = sender([player()])
    await alerts.tick()
    bot.poracle.reload.assert_not_awaited()


async def test_off_confirmation_and_cleanup():
    alerts, bot = sender([player(hundo_dms=0, hundo_settings_revision=2)])
    await alerts.tick()
    assert "**desligado**" in bot.get_user.return_value.send.call_args.args[0]
    alerts._record.assert_called_once_with(1, 2, delivered=True)
    alerts._sync_player.assert_not_called()
    assert alerts._cleanup.call_count == 2


async def test_departed_player_does_not_receive_confirmation():
    alerts, bot = sender([player(left_at="left", hundo_settings_revision=2)])
    await alerts.tick()
    bot.get_user.return_value.send.assert_not_awaited()
    alerts._record.assert_not_called()
    assert alerts._cleanup.call_count == 2


async def test_fetch_user_when_not_cached():
    alerts, bot = sender([player(hundo_settings_revision=2)])
    bot.get_user.return_value = None
    await alerts.tick()
    bot.fetch_user.assert_awaited_once_with(1)
    bot.fetch_user.return_value.send.assert_awaited_once()
    alerts._record.assert_called_once_with(1, 2, delivered=True)


async def test_stopped_user_logging_resets_after_restart():
    alerts, bot = sender([player()])
    alerts._human.side_effect = [
        human(enabled=0),
        human(enabled=0),
        human(),
        human(enabled=0),
    ]
    for _ in range(4):
        await alerts.tick()
    stops = [
        c.args[0]
        for c in bot.utility.log_to_file.call_args_list
        if "is stopped" in c.args[0]
    ]
    assert len(stops) == 2
    bot.poracle.start.assert_not_called()


def test_cleanup_failure_rolls_back(monkeypatch):
    db, cursor = connection()
    cursor.execute.side_effect = RuntimeError("DB failed")
    monkeypatch.setattr(h, "connect", MagicMock(return_value=db))
    with pytest.raises(RuntimeError, match="DB failed"):
        h.HundoAlerts(MagicMock())._cleanup()
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
    db.close.assert_called_once()
```

- [x] Run them first to see the missing implementation fail.
- [x] Append the following database queries and class to `modules/hundo_alerts.py`:

```python
_PLAYERS_SQL = """
SELECT discord_id, COALESCE(NULLIF(trainer_name, ''), display_name) AS display_name,
       collecting, show_costumes, left_at, hundo_dms, hundo_areas,
       hundo_settings_revision, hundo_confirmed_revision, hundo_dm_refused_revision
FROM trade_player
WHERE hundo_dms = 1 OR hundo_settings_revision > 0
"""

_MISSING_SQL = """
SELECT pn.pokemon_id, pn.form_id
FROM poliswag.pokemon_name pn
LEFT JOIN pogoleiria.collection_entry ce ON ce.discord_id = %s
  AND ce.category = 'hundo' AND ce.pokemon_id = pn.pokemon_id
  AND ce.form_id = pn.form_id
WHERE pn.pokemon_id > 0 AND ce.discord_id IS NULL
  AND (pn.is_costume = 0 OR %s = 1)
"""

# Cleanup consults current database state, not a snapshot from before a DM.
# Pending revisions lose their old rules until their new confirmation succeeds.
_CLEANUP_SQL = """
DELETE m FROM monsters m
LEFT JOIN pogoleiria.trade_player p ON m.id = CAST(p.discord_id AS CHAR)
LEFT JOIN humans h ON h.id = m.id
WHERE m.template = %s AND (
    p.discord_id IS NULL OR p.hundo_dms <> 1
    OR COALESCE(FIND_IN_SET('hundo', p.collecting), 0) = 0
    OR p.left_at IS NOT NULL OR p.hundo_settings_revision = 0
    OR p.hundo_confirmed_revision <> p.hundo_settings_revision
    OR p.hundo_dm_refused_revision >= p.hundo_settings_revision
    OR h.id IS NULL OR h.enabled = 0 OR h.admin_disable = 1
    OR h.type <> 'discord:user'
)
"""


class HundoAlerts:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self._reload_pending = False
        self._stopped_logged = set()

    def _log(self, message):
        self.poliswag.utility.log_to_file(f"[HUNDO] {message}")

    async def _cleanup_rules(self):
        try:
            changed = await asyncio.to_thread(self._cleanup)
            self._reload_pending |= changed
        except Exception as exc:
            self._log(f"cleanup failed: {exc}")

    async def _reload_if_pending(self):
        if not self._reload_pending:
            return
        try:
            await self.poliswag.poracle.reload()
        except Exception as exc:
            self._log(f"reload pending after failure: {exc}")
        else:
            self._reload_pending = False

    async def tick(self):
        try:
            await self._cleanup_rules()
            players = await asyncio.to_thread(self._read_players)
            for row in players:
                eligible_for_confirmation = row["left_at"] is None and (
                    row["hundo_dms"] == 0
                    or "hundo" in (row["collecting"] or "").split(",")
                )
                if not eligible_for_confirmation or not confirmation_due(row):
                    continue
                try:
                    await self._confirm(row)
                except Exception as exc:
                    # No outcome recorded for transient delivery/DB failures.
                    self._log(f"confirmation for {row['discord_id']} failed: {exc}")

            # Do not turn a stale in-memory snapshot into an active player.
            players = await asyncio.to_thread(self._read_players)
            masterfile = getattr(self.poliswag.quest_search, "masterfile_data", None)
            pokemon = (masterfile or {}).get("pokemon")
            if not pokemon:
                self._log("rule creation skipped: masterfile unavailable")
                return
            forms = default_forms(pokemon)
            for row in players:
                if not is_active(row):
                    continue
                try:
                    human = await asyncio.to_thread(self._human, row["discord_id"])
                    if human is None:
                        await self.poliswag.poracle.create_user(
                            row["discord_id"],
                            row["display_name"],
                            area=areas_json(row["hundo_areas"]),
                        )
                        human = await asyncio.to_thread(self._human, row["discord_id"])
                        if human is None:
                            raise RuntimeError("Created human not visible yet")
                    if human["type"] != "discord:user":
                        raise ValueError("Existing Poracle human is not a Discord user")
                    if not human["enabled"] or human["admin_disable"]:
                        if row["discord_id"] not in self._stopped_logged:
                            self._log(f"Poracle user {row['discord_id']} is stopped")
                            self._stopped_logged.add(row["discord_id"])
                        continue
                    self._stopped_logged.discard(row["discord_id"])
                    changed = await asyncio.to_thread(
                        self._sync_player, row, forms, human["current_profile_no"]
                    )
                    self._reload_pending |= changed
                except Exception as exc:
                    # Another player's cleanup/update can still succeed.
                    self._log(f"rule sync for {row['discord_id']} failed: {exc}")
        finally:
            await self._cleanup_rules()
            await self._reload_if_pending()

    async def _confirm(self, row):
        try:
            user = self.poliswag.get_user(int(row["discord_id"]))
            if user is None:
                user = await self.poliswag.fetch_user(int(row["discord_id"]))
            await user.send(
                confirmation_text(row["hundo_dms"] == 1, row["hundo_areas"]),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            delivered = True
        except (discord.Forbidden, discord.NotFound):
            delivered = False
        await asyncio.to_thread(
            self._record,
            row["discord_id"],
            row["hundo_settings_revision"],
            delivered=delivered,
        )

    def _read_players(self):
        with closing(connect(Config.DB_POGOLEIRIA, dict_rows=True)) as db:
            with db.cursor() as cursor:
                cursor.execute(_PLAYERS_SQL)
                return list(cursor.fetchall())

    def _record(self, discord_id, revision, *, delivered):
        prefix = "hundo_confirmed" if delivered else "hundo_dm_refused"
        with closing(connect(Config.DB_POGOLEIRIA, autocommit=True)) as db:
            with db.cursor() as cursor:
                cursor.execute(
                    f"UPDATE trade_player SET {prefix}_revision = %s,"
                    f" {prefix}_at = NOW(6) WHERE discord_id = %s"
                    " AND hundo_settings_revision = %s"
                    " AND GREATEST(hundo_confirmed_revision,"
                    " hundo_dm_refused_revision) < %s",
                    (revision, discord_id, revision, revision),
                )
                return cursor.rowcount == 1

    def _human(self, discord_id):
        with closing(connect("poracle", dict_rows=True)) as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "SELECT id, type, enabled, admin_disable, current_profile_no"
                    " FROM humans WHERE id = %s",
                    (str(discord_id),),
                )
                return cursor.fetchone()

    def _cleanup(self):
        with closing(connect("poracle", autocommit=False)) as db:
            try:
                with db.cursor() as cursor:
                    cursor.execute(_CLEANUP_SQL, (TEMPLATE,))
                    changed = cursor.rowcount > 0
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise

    def _sync_player(self, row, forms, profile_no):
        # A fresh read in tick gives bounded eventual consistency; finally's
        # cleanup removes rules if a player opts out during this operation.
        with closing(connect(Config.DB_POGOLEIRIA, dict_rows=True)) as pogo:
            with pogo.cursor() as read:
                read.execute(_MISSING_SQL, (row["discord_id"], row["show_costumes"]))
                tiles = [(r["pokemon_id"], r["form_id"]) for r in read.fetchall()]
        wanted = [
            rule_row(
                str(row["discord_id"]), p, f, areas_json(row["hundo_areas"]), profile_no
            )
            for p, f in sorted(wanted_rules(tiles, forms))
        ]
        columns = ", ".join(f"`{column}`" for column in RULE_COLUMNS)
        with closing(connect("poracle", dict_rows=True, autocommit=False)) as db:
            try:
                with db.cursor() as cursor:
                    cursor.execute(
                        f"SELECT {columns} FROM monsters WHERE id = %s AND template = %s",
                        (str(row["discord_id"]), TEMPLATE),
                    )
                    changed = not rules_equal(cursor.fetchall(), wanted)
                    if changed:
                        cursor.execute(
                            "DELETE FROM monsters WHERE id = %s AND template = %s",
                            (str(row["discord_id"]), TEMPLATE),
                        )
                        if wanted:
                            marks = ", ".join(["%s"] * len(RULE_COLUMNS))
                            cursor.executemany(
                                f"INSERT INTO monsters ({columns}) VALUES ({marks})",
                                [tuple(r[c] for c in RULE_COLUMNS) for r in wanted],
                            )
                db.commit()
                return changed
            except Exception:
                db.rollback()
                raise
```

The single scheduled loop is the sole writer for this template. Changes made
during a tick are eventually reconciled; final cleanup consults current rows
and removes opt-outs/refusals even after a failure. Already queued alerts may
still arrive. A read outage prevents new rule creation but still attempts
cleanup and reload. A cleanup DB outage is logged and retried on the next tick;
there is no claim that remote delivery can stop while its database is unavailable.

The reload flag survives transient HTTP failures within the process. The
reviewed Poracle build also reloads state every 60 seconds; this covers a
Poliswag restart after committing a change. Do not claim the flag is durable.

- [x] Add `tests/integration/test_hundo_alerts_sql.py`, gated by an explicit
  disposable-test-database setting. It must reject production connection
  settings. Use an isolated MariaDB container with fixture schemas named
  `pogoleiria`, `poliswag`, and `poracle` so the SQL runs **unchanged**. Run the
  real helper methods, not rewritten equivalents. Required fixtures/checks:

| Scenario | Required assertion |
|---|---|
| Migration replay | Both executions succeed; eight columns, correct SET/defaults |
| Changed settings, unchanged save, explicit retry | Revisions increment only for change/retry; outcome revisions are untouched |
| Change during DM delivery | `_record` for revision R updates zero rows after R+1; R+1 stays due |
| Refusal then successful retry in the same second | Current confirmed revision activates the player despite timestamp ties |
| Missing/owned tiles and costumes | Exact form/tick joins; `show_costumes=0` excludes costume tiles; `1` includes them |
| Inactive, missing, refused, departed, pending, or stopped player | `_cleanup` removes only their managed rows; other templates and humans/profiles remain byte-for-byte unchanged |
| Active player | Cleanup preserves their rows; zero eligible users removes all managed rows |
| Area/profile/filter change or duplicate | Rebuild corrects all managed values and duplicates, then next sync is a no-op |
| Failed insertion | Transaction rollback restores the previous complete rule set |
| Independent users | One failed replacement does not undo another user's committed change |

Use the actual Poracle rule-table DDL for the insert/rollback checks. Python
mocks cannot establish that MariaDB accepted the SQL or enforced its types.

Done: live DDL dumped to `tests/integration/hundo_schema.sql`; the module's
real helpers run against it (run instructions in the test's docstring). The
cleanup's `CAST(discord_id AS CHAR)` took pymysql's `utf8mb4_general_ci`
against `monsters.id`'s `utf8mb4_unicode_ci` and MariaDB refused the join
(error 1267): every live cleanup would have failed. Fixed with
`COLLATE utf8mb4_unicode_ci`.

- [x] Verify the appended off/departed/fetched-user, unchanged-reload, final-cleanup
  failure, and stopped-user log/reset tests pass. Exercise the actual scheduler
  in Task 5.
- [x] Run the unit tests and disposable SQL tests. Format/lint both changed
  modules and tests before proceeding. Do not substitute a live owner test for
  these regression checks.

## Task 5: Scheduler integration

- [x] Import `HundoAlerts` beside `TradeDM` in `cogs/scheduled.py`.
- [x] Instantiate `self._hundo_alerts = HundoAlerts(poliswag)` in `__init__`.
- [x] Add `self._hundo_alerts.tick` after `self._trade_dm.tick` in the step tuple.
- [x] Extend `tests/cogs/test_scheduled.py`: mock every unrelated scheduled
  step, keep the real new tick, fake its DB/Discord/Poracle dependencies, and
  run `scheduled_tasks.coro(cog)`. Assert a due confirmation's revision is
  recorded, a rule change is processed, exactly one reload occurs, later
  steps run, and no `CRASH` or unexpected `[HUNDO] ... failed` log was emitted.
  Also verify a transient confirmation failure still allows cleanup and
  subsequent scheduler steps. Swallowed exceptions are not successful tests.
- [x] Run the relevant tests, then the whole suite with its exit status intact:

```bash
python3 -m pytest tests/modules/test_hundo_alerts.py tests/modules/test_poracle_client.py tests/cogs/test_scheduled.py tests/modules/test_migrations.py -q
python3 -m pytest -q
```

Do not pipe pytest into `tail`. If capturing output is needed, use Bash
`set -o pipefail` with `tee` and retain the complete failure report.

## Task 6: Template and deterministic matching verification

- [x] Prepare this DTS entry and validate its JSON before deployment:

```json
{
  "id": "pokedex-100iv",
  "language": "en",
  "type": "monster",
  "default": false,
  "platform": "discord",
  "template": {
    "embed": {
      "color": "{{color}}",
      "title": "💯 100IV que te falta: {{fullName}}",
      "url": "{{{reactMapUrl}}}?o=dm-100iv",
      "description": "CP {{cp}} · Lvl {{level}}\n📍 {{areas}}\n⏰ até {{time}} · faltam {{tthm}} min\n[Ver no mapa]({{{reactMapUrl}}}?o=dm-100iv)",
      "thumbnail": {"url": "{{{imgUrl}}}"},
      "footer": {"text": "Pokédex · teste 100IV"}
    }
  }
}
```

The pilot footer does not advertise unavailable settings. When the site switch,
areas, and retry action have shipped, replace it with:
`Pokédex · desliga ou muda a área em pogoleiria.pt/trocas`.

- [x] Run an isolated Poracle instance of the deployed version against the
  disposable DB, with a fake delivery sink and local fixture geofences. Use
  the actual generated rules and pass synthetic webhooks through its normal
  matching path. Assert the following delivery counts for the test user:

| Input/state | Deliveries |
|---|---:|
| Missing Rattata, form 45, 100IV, selected area | 1 |
| Same species/form after ticking it and syncing | 0 |
| Missing form, 100IV, outside the selected area | 0 |
| Missing form, selected area, 15/15/14 IVs | 0 |
| Owned Alolan form 46 while ordinary form 45 is missing | 0 |
| Active Poracle profile 2 after sync | 1 |
| After switching off and running cleanup/reload | 0 |

Use distinct encounter IDs and future despawn times so caching/expiry cannot
produce false negatives. Make the positive control pass before trusting the
negative cases. Keep synthetic webhooks away from the production webhook
endpoint; it also delivers to unrelated channels/users.

Done 2026-09-24 against PoracleNG 5.2.1-main (commit c8901ad1, the live
image): internal docker network (no Discord, so no delivery), disposable
MariaDB with the live poracle DDL and migration rows, live geofences and
resources, `level = "debug"`. The **real** `HundoAlerts.tick` drove it
(real `PoracleClient` against the isolated API): confirmation DM, user
created via `POST /api/humans` with `area: ["leiria"]`, two rules
(Rattata 19/45, Bulbasaur 1/163), reload 200. Matches read from the
`… and N humans cared` log line:

| Input/state | Expected | Got |
|---|---:|---:|
| Missing Rattata 45, 100IV, Leiria (positive control) | 1 | 1 |
| Same after ticking 19/0 and syncing | 0 | 0 |
| Marinha Grande spawn, only Leiria selected | 0 | 0 |
| Both areas selected, Marinha Grande spawn (area positive control) | 1 | 1 |
| 15/15/14 in Leiria | 0 | 0 |
| Alolan 46 (owned) while 45 missing | 0 | 0 |
| Profile 2 after sync (rules rebuilt at profile 2) | 1 | 1 |
| Switched off, cleanup + reload | 0 | 0 |

Lowercase `override_areas` (`marinhagrande`) match the `MarinhaGrande`
geofence. Gotcha: the MarinhaGrande polygon's vertex centroid lies outside
it; use (39.7475, -8.9322). `/api/test` returns `{"status":"ok"}` and does
not log the rendered embed, so the look is checked by the Task 7 preview DM.

`POST /api/test` supports `target.template` in the reviewed version, but skips
normal rule matching. Use it for rendering only. If it produces the standard
look, investigate version/template loading instead of declaring the preview
successful or assuming the API ignores the template.

## Task 7: Authorized deployment and owner pilot

Start this task only when implementing/deploying the feature is the authorized
work. The plan's edit/review is not that deployment. Complete Tasks 1–6 first.

- [x] Verify the deployed Poracle version/schema, default/current reload
  interval, API area payload, template preview support, `{{areas}}`, and
  current-profile semantics against the references in the spec. Record the
  reload fallback interval; do not silently rely on a different build.
- [x] Capture the owner's original settings, collection ticks, and Poracle
  profile so test changes can be restored. Never decrease a settings revision
  when restoring: restore values with a fresh revision.
- [x] Apply migration 014, using the normal startup migration mechanism or
  `modules.migrations.split_statements` and `connect` with autocommit. Verify
  the eight columns/defaults. No player's switch should change during DDL.
- [x] Back up `/root/poracleng/config/dts.json` to a unique timestamped filename.
  Add exactly one entry with the Task 6 ID/language/type/platform. Validate
  the complete JSON, then atomically replace the file. Never overwrite an
  earlier backup on a retry. If an identical entry exists, leave it; if it
  differs, review/update that entry instead of appending a duplicate.
- [x] Record a deployment timestamp, restart Poracle, and inspect startup logs
  **since that timestamp**. Verify this template loads without errors and the
  process is healthy. A hardcoded total template count is not an assertion
  about this entry.
- [x] Restart Poliswag directly with Docker Compose using the appropriate
  production/dev compose file. Avoid `timeout make reload`: that target also
  follows logs, and a timeout cannot distinguish successful restart from failure.
  Inspect timestamped container logs and new `[HUNDO]` entries over at least one
  tick. Existing `logs/error.log` content is retained; do not expect it to be empty.
- [x] Ensure the owner collects hundo, then enable **only** Discord ID
  `98846248865398784` with a fresh revision. Example SQL (via the configured
  connection, without printing credentials):

```sql
UPDATE pogoleiria.trade_player
SET hundo_dms = 1, hundo_areas = 'leiria,marinha',
    hundo_settings_revision = hundo_settings_revision + 1,
    hundo_settings_at = NOW(6)
WHERE discord_id = 98846248865398784;
```

- [x] Within two normal ticks, verify the confirmation arrived and
  `hundo_confirmed_revision = hundo_settings_revision > hundo_dm_refused_revision`.
  Check `humans.type`, `enabled`, `admin_disable`, and `current_profile_no`.
  Check managed rules' full values/profile against the missing tiles (including
  default-form mapping); use an exact expected count, not “at least 600”.
  If this human already existed, preserve its area/profile and all other settings.
- [x] Preview on the owner using `test_pokemon` and a target containing
  `id`, `name`, `type: discord:user`, `language: en`, and
  `template: pokedex-100iv`. Include a future despawn time and form 45 in the
  Rattata payload. Verify title, area, map link, and pilot footer. This sends a
  real DM and is part of this authorized pilot, not automated unit testing.
- [ ] Observe a real eligible spawn. Do not promise one within an hour: daily
  average volume does not guarantee an arrival time. Tick/untick a chosen tile
  and verify its exact rule disappears/reappears after sync. Change the area
  with a new settings revision and verify it after confirmation. Test profile
  switching on the isolated instance; leave unrelated live profiles unchanged.
- [ ] Test opt-out with a fresh revision:

```sql
UPDATE pogoleiria.trade_player
SET hundo_dms = 0,
    hundo_settings_revision = hundo_settings_revision + 1,
    hundo_settings_at = NOW(6)
WHERE discord_id = 98846248865398784;
```

Verify no owner rows with this template remain after cleanup, unrelated rules
remain unchanged, and reload succeeds (or the verified periodic fallback runs).
A queued DM can still arrive; use the isolated matching test for deterministic
non-delivery. Restore any test tick/area changes. Leave the pilot off unless
the owner explicitly requested ongoing alerts.

### Pilot record (2026-09-24)

- Poracle 5.2.1-main (c8901ad1), `reload_interval_secs` default 60 (not
  overridden). Isolated check ran this same image (Task 6).
- Owner before: collecting `hundo,shiny`, `show_costumes=1`, **0** hundo
  ticks (1500 missing tiles, 133 costumes); Poracle human already existed
  (`discord:user`, area `["marinhagrande"]`, profile 1, no profiles rows,
  no monster rules). Snapshot kept outside the repo.
- Read-only dry run first: Ogerpon (1017, defaultFormId 0) made
  `wanted_rules` reject the owner's whole rebuild. Changed to skip that tile
  (commit 3f9f66f); expected count 1499.
- Migration 014 applied live twice; 8 columns/defaults verified; all 8
  players off at revision 0.
- DTS: backup `dts.json.bak-100iv-20260924-215053`, one entry appended,
  JSON validated, atomic replace. Restart 21:50:54: "DTS loaded: 77
  templates", no errors, bot connected.
- Poliswag restarted with `docker compose -f docker-compose.prod.yaml
  restart poliswag`; "Migrations up to date", no `[HUNDO]`/CRASH lines.
- Owner enabled with `hundo_areas='leiria'` (their choice, not both):
  revision 1 confirmed at 21:53:28 (confirmation DM delivered), refused 0.
  Human unchanged (area/profile preserved). Exactly 1499 rules, all
  100/100, profile 1, `["leiria"]`, no form 0; 20 channel rules untouched;
  reload 200; Poracle state 1519 monsters.
- Preview via `/api/test` with `template: pokedex-100iv`: DM delivered
  (DM channel created). Owner to confirm the look.
- Tick/untick Rattata 19/0: 1499 → 1498 (19/45 gone) → 1499; collection
  restored to 0 hundo ticks.
- Not done live: a real spawn (none awaited), a live area change and profile
  switch (covered by the isolated matcher), and the opt-out test: the owner
  asked for ongoing alerts, and cleanup/opt-out is covered by the SQL tests
  and the isolated matcher. The live cleanup statement has run every minute
  since 21:51 without errors (proves MariaDB accepts it with live collations).

Rollback: disable affected players with fresh revisions while the cleanup
worker is available, verify managed rows are removed and Poracle has reloaded,
then disable/revert the worker. If the worker cannot run, delete only
`template = 'pokedex-100iv'` rows and reload Poracle using the operational
connection/API. Restore the DTS backup if needed. Leave additive columns in
place; do not drop data or modify other users' rules/profiles.

## Task 8: Documentation and release handoff

- [x] Add a module-map row in `docs/context.md`: revision-specific confirmation,
  independent cleanup, complete managed-rule comparison, current-profile
  following, reload retry/fallback, and off-by-default setting. Link the spec.
- [ ] Hand the site implementation the eight-column contract, atomic writer
  SQL, explicit retry behavior, pending/refused notice rules, and tester gating.
  Ordinary save is not a retry. Do not publish a footer linking to settings
  until those controls work.
- [x] Record completed unit/SQL/matcher checks and actual pilot observations;
  leave unperformed checks unchecked. Run Black/Ruff on changed Python files
  and `git diff --check`. Commit only explicit feature paths if committing.
