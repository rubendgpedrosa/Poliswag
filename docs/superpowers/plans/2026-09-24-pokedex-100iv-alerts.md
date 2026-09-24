# Pokédex 100IV alerts (Poliswag side) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DM a Trades collector (only the owner at first) when a 100IV spawns of a Pokémon missing from their 100IV Pokédex, by keeping Poracle rules in step with the Pokédex.

**Architecture:** A new scheduled step, `modules/hundo_alerts.py`, runs every minute. Phase 1 sends a confirmation DM when a player's 100IV settings changed and records delivered/refused on `trade_player`. Phase 2 computes each active player's missing tiles, turns them into Poracle `monsters` rows (template `pokedex-100iv`, 100IV only, area on the rule), rebuilds them in one transaction when they differ, deletes managed rows of anyone inactive, and reloads Poracle once if anything changed. Poracle then sends the alert DMs with a new DTS template.

**Tech Stack:** Python 3.11, discord.py, pymysql (`modules.database_connector.connect`), aiohttp (`modules.poracle_client`), pytest (asyncio auto mode), MariaDB (`pogoleiria`, `poliswag`, `poracle` schemas), Poracle-NG.

**Spec:** `docs/superpowers/specs/2026-09-24-pokedex-100iv-alerts-design.md`

**Conventions:** run tests from `/root/Poliswag` with `python3 -m pytest …`. Format with `black`, lint with `ruff`. Commit with explicit paths (other sessions leave unrelated edits in this tree: never `git add -A`). End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File structure

| File | Responsibility |
|---|---|
| `migrations/014_add_hundo_dms.sql` (create) | Five columns on `pogoleiria.trade_player` |
| `modules/hundo_alerts.py` (create) | Pure rules (who is active, what to confirm, which Poracle rows) + `HundoAlerts` tick (DB reads/writes, DMs, reload) |
| `modules/poracle_client.py` (modify) | `create_user()` for a `discord:user` human |
| `cogs/scheduled.py` (modify) | Run `HundoAlerts.tick` every minute |
| `tests/modules/test_hundo_alerts.py` (create) | Pure rules and tick orchestration, DB and Discord faked |
| `tests/modules/test_poracle_client.py` (modify) | `create_user` payload |
| `/root/poracleng/config/dts.json` (modify, not in git) | The `pokedex-100iv` DM template |
| `docs/context.md` (modify) | One module-map row |

---

### Task 1: Migration 014

**Files:**
- Create: `migrations/014_add_hundo_dms.sql`

Migrations are replayed at every start (`modules/migrations.py`), so the statement must be re-runnable (`ADD COLUMN IF NOT EXISTS`). `tests/modules/test_migrations.py` already checks every file in `migrations/` splits into statements.

- [ ] **Step 1: Write the migration**

```sql
-- Pokédex 100IV alerts (modules/hundo_alerts.py). The site writes the
-- switch, the areas and the settings stamp (Perfil e outras opções); this
-- module writes the other two. Off for everyone by default. Also a site
-- db/ file later, both re-runnable, so whichever lands first adds them.
--
-- hundo_settings_at moves only when hundo_dms or hundo_areas change: the
-- row's updated_at moves on any edit, and on this module's own writes.
ALTER TABLE pogoleiria.trade_player
  ADD COLUMN IF NOT EXISTS hundo_dms TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS hundo_areas SET('leiria','marinha') NOT NULL DEFAULT 'leiria,marinha',
  ADD COLUMN IF NOT EXISTS hundo_settings_at DATETIME NULL,
  ADD COLUMN IF NOT EXISTS hundo_confirmed_at DATETIME NULL,
  ADD COLUMN IF NOT EXISTS hundo_dm_refused_at DATETIME NULL;
```

- [ ] **Step 2: Run the migration tests**

Run: `python3 -m pytest tests/modules/test_migrations.py -q`
Expected: all pass.

- [ ] **Step 3: Apply it to the live DB (re-runnable, additive)**

```bash
cd /root/Poliswag && set -a && . ./.env && set +a
docker exec -i db mariadb -u$DB_USER -p$DB_PASSWORD < migrations/014_add_hundo_dms.sql
docker exec -i db mariadb -u$DB_USER -p$DB_PASSWORD pogoleiria -e "SHOW COLUMNS FROM trade_player LIKE 'hundo%'"
```
Expected: five rows, `hundo_dms` default `0`, `hundo_areas` default `leiria,marinha`.

- [ ] **Step 4: Commit**

```bash
git add migrations/014_add_hundo_dms.sql
git commit -m "migration 014: trade_player columns for Pokédex 100IV alerts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Pure rules in `hundo_alerts.py`

**Files:**
- Create: `modules/hundo_alerts.py`
- Test: `tests/modules/test_hundo_alerts.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Pokédex 100IV alerts: who is active, when to confirm, which Poracle rules.
The tick's database and Discord are faked (see TestTick)."""

import json
from datetime import datetime

from modules.hundo_alerts import (
    TEMPLATE,
    areas_json,
    confirmation_due,
    confirmation_text,
    default_forms,
    is_active,
    rule_row,
    wanted_rules,
)

T0 = datetime(2026, 9, 24, 12, 0)
T1 = datetime(2026, 9, 24, 12, 5)
T2 = datetime(2026, 9, 24, 12, 10)


def player(**patch):
    row = {
        "discord_id": 1,
        "display_name": "Rui",
        "collecting": "hundo,shiny",
        "show_costumes": 1,
        "left_at": None,
        "hundo_dms": 1,
        "hundo_areas": "leiria,marinha",
        "hundo_settings_at": T0,
        "hundo_confirmed_at": T1,
        "hundo_dm_refused_at": None,
    }
    row.update(patch)
    return row


class TestConfirmationText:
    def test_on_both_areas(self):
        text = confirmation_text(True, "leiria,marinha")
        assert text.startswith("100IV por DM: **ligado** · Leiria e Marinha Grande.")
        assert "os 100IV que te faltam na Pokédex" in text

    def test_on_one_area(self):
        assert "· só Marinha Grande." in confirmation_text(True, "marinha")
        assert "· só Leiria." in confirmation_text(True, "leiria")

    def test_empty_areas_mean_both(self):
        assert "Leiria e Marinha Grande" in confirmation_text(True, "")

    def test_off(self):
        assert confirmation_text(False, "leiria") == (
            "100IV por DM: **desligado**. Já não te enviamos 100IV."
        )


class TestConfirmationDue:
    def test_due_when_settings_are_newer_than_both(self):
        assert confirmation_due(player(hundo_settings_at=T2))

    def test_not_due_once_confirmed(self):
        assert not confirmation_due(player(hundo_settings_at=T0, hundo_confirmed_at=T1))

    def test_not_retried_after_a_refusal_until_settings_change(self):
        row = player(hundo_settings_at=T1, hundo_confirmed_at=None, hundo_dm_refused_at=T2)
        assert not confirmation_due(row)
        assert confirmation_due({**row, "hundo_settings_at": datetime(2026, 9, 24, 13)})

    def test_never_touched_is_not_due(self):
        assert not confirmation_due(player(hundo_settings_at=None, hundo_confirmed_at=None))


class TestIsActive:
    def test_on_confirmed_collecting(self):
        assert is_active(player())

    def test_switched_off(self):
        assert not is_active(player(hundo_dms=0))

    def test_not_collecting_hundo(self):
        assert not is_active(player(collecting="shiny"))

    def test_left_the_server(self):
        assert not is_active(player(left_at=T2))

    def test_not_confirmed_yet(self):
        assert not is_active(player(hundo_confirmed_at=None))

    def test_refused_after_the_confirmation(self):
        assert not is_active(player(hundo_confirmed_at=T1, hundo_dm_refused_at=T2))

    def test_confirmed_again_after_an_old_refusal(self):
        assert is_active(player(hundo_dm_refused_at=T0, hundo_confirmed_at=T1))


class TestForms:
    masterfile = {
        "19": {"name": "Rattata", "defaultFormId": 45},
        "25": {"name": "Pikachu", "defaultFormId": 598},
        "132": {"name": "Ditto"},
    }

    def test_default_forms_skip_species_without_forms(self):
        assert default_forms(self.masterfile) == {19: 45, 25: 598}

    def test_ordinary_tile_becomes_the_default_form_and_named_forms_stay(self):
        missing = [(19, 0), (19, 46), (25, 2332), (132, 0)]
        assert wanted_rules(missing, default_forms(self.masterfile)) == {
            (19, 45),
            (19, 46),
            (25, 2332),
            (132, 0),
        }


class TestRuleRow:
    def test_shape(self):
        row = rule_row(1, 19, 45, areas_json("marinha"))
        assert row["id"] == "1"
        assert row["template"] == TEMPLATE
        assert (row["pokemon_id"], row["form"]) == (19, 45)
        assert (row["min_iv"], row["max_iv"]) == (100, 100)
        assert row["distance"] == 0
        assert json.loads(row["override_areas"]) == ["marinhagrande"]

    def test_areas_json_both_and_default(self):
        both = ["leiria", "marinhagrande"]
        assert json.loads(areas_json("leiria,marinha")) == both
        assert json.loads(areas_json("")) == both
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m pytest tests/modules/test_hundo_alerts.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'modules.hundo_alerts'`.

- [ ] **Step 3: Write the pure half of the module**

```python
"""DM a collector when a 100IV they haven't ticked in the Pokédex spawns.

Poracle sends the alerts; this module keeps each collector's Poracle rules
in step with their Trades Pokédex, as cogs/notifications.py (!notify) does
by hand for channels. Spec: docs/superpowers/specs/
2026-09-24-pokedex-100iv-alerts-design.md.

Each minute:
1. Confirmations. A player whose 100IV settings changed since the last
   confirmation or refusal gets a DM stating the settings now. Delivered or
   refused is recorded on trade_player: Poracle-NG doesn't record a refused
   DM, and Discord only reports one when a message is actually sent, so this
   DM is the delivery check.
2. Rules. Every Pokédex tile an active player is missing at 100IV becomes a
   Poracle rule marked with TEMPLATE; their rows are rebuilt when they differ
   from that, and everyone else's TEMPLATE rows are deleted. Poracle reloads
   once if anything changed. Rows with another template, and existing
   Poracle users, are never touched.

Reads trade_player's hundo_dms, hundo_areas and hundo_settings_at (written
by the site); writes hundo_confirmed_at and hundo_dm_refused_at.
"""

import json

TEMPLATE = "pokedex-100iv"

# The site's area keys, in display order, with Poracle's geofence names.
_AREAS = (("leiria", "leiria", "Leiria"), ("marinha", "marinhagrande", "Marinha Grande"))

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
    "profile_no": 1,
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
        return "100IV por DM: **desligado**. Já não te enviamos 100IV."
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


def _later(a, b):
    """The later of two optional times; None is earlier than anything."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def confirmation_due(row):
    """The settings changed since the last confirmation or refusal."""
    settings = row.get("hundo_settings_at")
    if settings is None:
        return False
    last = _later(row.get("hundo_confirmed_at"), row.get("hundo_dm_refused_at"))
    return last is None or settings > last


def is_active(row):
    """Switched on, still collecting 100IV and in the server, and the last
    confirmation arrived with no refusal since."""
    collecting = (row.get("collecting") or "").split(",")
    confirmed = row.get("hundo_confirmed_at")
    refused = row.get("hundo_dm_refused_at")
    return (
        row.get("hundo_dms") == 1
        and "hundo" in collecting
        and row.get("left_at") is None
        and confirmed is not None
        and (refused is None or refused < confirmed)
    )


def default_forms(masterfile_pokemon):
    """{species: the form id Golbat reports for its ordinary form}."""
    out = {}
    for pokemon_id, details in (masterfile_pokemon or {}).items():
        form = (details or {}).get("defaultFormId")
        if form:
            out[int(pokemon_id)] = int(form)
    return out


def wanted_rules(missing_tiles, forms):
    """(pokemon_id, form) per missing Pokédex tile.

    In Poracle form 0 means any form, so the Pokédex's ordinary tile (form 0)
    becomes the species' own ordinary form id, and an owned Alolan never
    alerts because the ordinary one is missing. A species with no forms stays
    at 0; a named form keeps its id.
    """
    return {
        (pokemon_id, forms.get(pokemon_id, 0) if form_id == 0 else form_id)
        for pokemon_id, form_id in missing_tiles
    }


def rule_row(discord_id, pokemon_id, form, override_areas):
    """One poracle.monsters row."""
    return {
        **_RULE_DEFAULTS,
        "id": str(discord_id),
        "template": TEMPLATE,
        "pokemon_id": pokemon_id,
        "form": form,
        "override_areas": override_areas,
    }
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/modules/test_hundo_alerts.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
black -q modules/hundo_alerts.py tests/modules/test_hundo_alerts.py && ruff check modules/hundo_alerts.py tests/modules/test_hundo_alerts.py
git add modules/hundo_alerts.py tests/modules/test_hundo_alerts.py
git commit -m "hundo_alerts: who is active, when to confirm, which Poracle rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `PoracleClient.create_user`

**Files:**
- Modify: `modules/poracle_client.py` (after `create_channel`)
- Test: `tests/modules/test_poracle_client.py` (after `test_create_channel_sends_expected_payload`)

- [ ] **Step 1: Write the failing test**

```python
    async def test_create_user_sends_expected_payload(self, client):
        session = _install_session(client, _response(json_data={"id": "123"}))
        await client.create_user(123, "Rui")
        _, kwargs = session.request.call_args
        assert kwargs["json"] == {
            "id": "123",
            "name": "Rui",
            "type": "discord:user",
        }
```

- [ ] **Step 2: Run it to see it fail**

Run: `python3 -m pytest tests/modules/test_poracle_client.py -q -k create_user`
Expected: FAIL, `AttributeError: 'PoracleClient' object has no attribute 'create_user'`.

- [ ] **Step 3: Add the method**

```python
    async def create_user(self, user_id: str | int, name: str) -> dict:
        """A Discord user as a Poracle human, for DMs (hundo_alerts.py)."""
        return await self._request(
            "POST",
            "/api/humans",
            json={"id": str(user_id), "name": name, "type": "discord:user"},
        )
```

- [ ] **Step 4: Run the client tests**

Run: `python3 -m pytest tests/modules/test_poracle_client.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add modules/poracle_client.py tests/modules/test_poracle_client.py
git commit -m "poracle_client: create a Discord user as a Poracle human

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The tick

**Files:**
- Modify: `modules/hundo_alerts.py` (append)
- Test: `tests/modules/test_hundo_alerts.py` (append)

The database halves are methods (`_read_players`, `_record`, `_humans`, `_set_new_human_area`, `_sync`) so the tests can replace them, as `tests/modules/test_trade_dm.py` does for `TradeDM`. The SQL itself is checked live in Task 7.

- [ ] **Step 1: Write the failing tests**

Append to `tests/modules/test_hundo_alerts.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord

from modules.hundo_alerts import HundoAlerts


def alerts(players, humans=None, sync_changed=False, masterfile=None):
    bot = MagicMock()
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user.return_value = user
    bot.poracle.create_user = AsyncMock()
    bot.poracle.start = AsyncMock()
    bot.poracle.reload = AsyncMock()
    bot.quest_search.masterfile_data = {
        "pokemon": masterfile if masterfile is not None else {"19": {"defaultFormId": 45}}
    }
    sender = HundoAlerts(bot)
    sender._read_players = MagicMock(return_value=players)
    sender._record = MagicMock()
    sender._humans = MagicMock(return_value=humans or {})
    sender._set_new_human_area = MagicMock()
    sender._sync = MagicMock(return_value=sync_changed)
    return sender, bot, user


class TestTick:
    def test_confirms_a_change_then_syncs_the_now_active_player(self):
        row = player(hundo_settings_at=T2, hundo_confirmed_at=None)
        sender, bot, user = alerts([row], humans={"1": 1})
        asyncio.run(sender.tick())
        user.send.assert_awaited_once()
        assert "**ligado**" in user.send.await_args.args[0]
        sender._record.assert_called_once_with(1, delivered=True)
        active = sender._sync.call_args.args[0]
        assert [p["discord_id"] for p in active] == [1]

    def test_a_refused_confirmation_is_recorded_and_not_synced(self):
        row = player(hundo_settings_at=T2, hundo_confirmed_at=None)
        sender, bot, user = alerts([row])
        user.send.side_effect = discord.Forbidden(MagicMock(status=403), "closed")
        asyncio.run(sender.tick())
        sender._record.assert_called_once_with(1, delivered=False)
        assert sender._sync.call_args.args[0] == []

    def test_switching_off_is_confirmed_too(self):
        row = player(hundo_dms=0, hundo_settings_at=T2)
        sender, bot, user = alerts([row])
        asyncio.run(sender.tick())
        assert "**desligado**" in user.send.await_args.args[0]
        assert sender._sync.call_args.args[0] == []

    def test_a_missing_poracle_user_is_created_started_and_given_the_area(self):
        sender, bot, user = alerts([player()], humans={})
        asyncio.run(sender.tick())
        bot.poracle.create_user.assert_awaited_once_with(1, "Rui")
        bot.poracle.start.assert_awaited_once_with(1)
        sender._set_new_human_area.assert_called_once()

    def test_a_stopped_poracle_user_is_left_stopped_and_not_synced(self):
        sender, bot, user = alerts([player()], humans={"1": 0})
        asyncio.run(sender.tick())
        bot.poracle.start.assert_not_awaited()
        assert sender._sync.call_args.args[0] == []

    def test_reloads_only_when_the_sync_changed_something(self):
        sender, bot, _ = alerts([player()], humans={"1": 1}, sync_changed=False)
        asyncio.run(sender.tick())
        bot.poracle.reload.assert_not_awaited()
        sender, bot, _ = alerts([player()], humans={"1": 1}, sync_changed=True)
        asyncio.run(sender.tick())
        bot.poracle.reload.assert_awaited_once()

    def test_no_sync_without_the_masterfile(self):
        # Without default forms, ordinary tiles would become "any form" rules.
        sender, bot, _ = alerts([player()], humans={"1": 1}, masterfile={})
        asyncio.run(sender.tick())
        sender._sync.assert_not_called()
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m pytest tests/modules/test_hundo_alerts.py -q -k TestTick`
Expected: FAIL, `ImportError: cannot import name 'HundoAlerts'`.

- [ ] **Step 3: Append the tick to `modules/hundo_alerts.py`**

Add these imports at the top of the module (below the docstring, above `import json`):

```python
import asyncio

import discord

from modules.config import Config
from modules.database_connector import connect
```

Then append:

```python
_PLAYERS_SQL = """
SELECT discord_id, COALESCE(trainer_name, display_name) AS display_name,
       collecting, show_costumes, left_at, hundo_dms, hundo_areas,
       hundo_settings_at, hundo_confirmed_at, hundo_dm_refused_at
FROM trade_player
WHERE hundo_dms = 1 OR hundo_settings_at IS NOT NULL
"""

# The Pokédex's tiles a player hasn't ticked at 100IV: the same "missing" as
# trade_dm.py, costumes only for players who show them.
_MISSING_SQL = """
SELECT pn.pokemon_id, pn.form_id
FROM poliswag.pokemon_name pn
LEFT JOIN pogoleiria.collection_entry ce ON ce.discord_id = %s
  AND ce.category = 'hundo' AND ce.pokemon_id = pn.pokemon_id
  AND ce.form_id = pn.form_id
WHERE pn.pokemon_id > 0 AND ce.discord_id IS NULL
  AND (pn.is_costume = 0 OR %s = 1)
"""


class HundoAlerts:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        # Stopped Poracle users already logged, so the log says it once.
        self._stopped_logged = set()

    async def tick(self):
        players = await asyncio.to_thread(self._read_players)

        for row in players:
            if confirmation_due(row):
                await self._confirm(row)

        masterfile = getattr(self.poliswag.quest_search, "masterfile_data", None)
        forms = default_forms((masterfile or {}).get("pokemon"))
        if not forms:
            # Without the default forms, an ordinary tile would become a rule
            # for any form. Wait for the masterfile rather than send wrong DMs.
            return

        active = [row for row in players if is_active(row)]
        humans = await asyncio.to_thread(
            self._humans, [str(row["discord_id"]) for row in active]
        )
        ready = []
        for row in active:
            human_id = str(row["discord_id"])
            enabled = humans.get(human_id)
            if enabled is None:
                await self.poliswag.poracle.create_user(
                    row["discord_id"], row["display_name"]
                )
                await self.poliswag.poracle.start(row["discord_id"])
                await asyncio.to_thread(
                    self._set_new_human_area, human_id, areas_json(row["hundo_areas"])
                )
            elif not enabled:
                # Stopped by Poracle (its alert limit) or by hand: left as is.
                if human_id not in self._stopped_logged:
                    self.poliswag.utility.log_to_file(
                        f"[HUNDO] Poracle user {human_id} is stopped; not syncing"
                    )
                    self._stopped_logged.add(human_id)
                continue
            ready.append(row)

        changed = await asyncio.to_thread(self._sync, ready, forms)
        if changed:
            await self.poliswag.poracle.reload()

    async def _confirm(self, row):
        text = confirmation_text(row["hundo_dms"] == 1, row["hundo_areas"])
        try:
            user = self.poliswag.get_user(
                int(row["discord_id"])
            ) or await self.poliswag.fetch_user(int(row["discord_id"]))
            await user.send(text, allowed_mentions=discord.AllowedMentions.none())
            delivered = True
        except (discord.Forbidden, discord.NotFound) as e:
            self.poliswag.utility.log_to_file(
                f"[HUNDO] confirmation to {row['discord_id']} not delivered: {e}"
            )
            delivered = False
        await asyncio.to_thread(self._record, row["discord_id"], delivered=delivered)
        # The same tick decides who is active from these.
        stamp = row["hundo_settings_at"]
        if delivered:
            row["hundo_confirmed_at"] = stamp
        else:
            row["hundo_dm_refused_at"] = stamp

    # ---- database ------------------------------------------------------

    def _read_players(self):
        db = connect(Config.DB_POGOLEIRIA, dict_rows=True)
        try:
            with db.cursor() as cursor:
                cursor.execute(_PLAYERS_SQL)
                return list(cursor.fetchall())
        finally:
            db.close()

    def _record(self, discord_id, *, delivered):
        column = "hundo_confirmed_at" if delivered else "hundo_dm_refused_at"
        db = connect(Config.DB_POGOLEIRIA, autocommit=True)
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    f"UPDATE trade_player SET {column} = NOW() WHERE discord_id = %s",
                    (discord_id,),
                )
        finally:
            db.close()

    def _humans(self, ids):
        """{discord id: enabled} for the Poracle users that exist."""
        if not ids:
            return {}
        db = connect("poracle", dict_rows=True)
        try:
            with db.cursor() as cursor:
                marks = ", ".join(["%s"] * len(ids))
                cursor.execute(
                    f"SELECT id, enabled FROM humans WHERE id IN ({marks})", ids
                )
                return {row["id"]: row["enabled"] for row in cursor.fetchall()}
        finally:
            db.close()

    def _set_new_human_area(self, human_id, override_areas):
        """Only for a user this module just created; an existing one keeps
        whatever area the player gave their own alerts."""
        db = connect("poracle", autocommit=True)
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    "UPDATE humans SET area = %s WHERE id = %s",
                    (override_areas, human_id),
                )
        finally:
            db.close()

    def _sync(self, ready, forms):
        """Rebuild each ready player's TEMPLATE rows where they differ from
        the Pokédex, and delete everyone else's. True if anything changed."""
        changed = False
        pogo = connect(Config.DB_POGOLEIRIA, dict_rows=True)
        poracle = connect("poracle", dict_rows=True, autocommit=False)
        try:
            with pogo.cursor() as read, poracle.cursor() as write:
                keep = []
                for row in ready:
                    human_id = str(row["discord_id"])
                    keep.append(human_id)
                    read.execute(
                        _MISSING_SQL, (row["discord_id"], row["show_costumes"])
                    )
                    missing = [(r["pokemon_id"], r["form_id"]) for r in read.fetchall()]
                    wanted = wanted_rules(missing, forms)
                    areas = areas_json(row["hundo_areas"])
                    write.execute(
                        "SELECT pokemon_id, form, override_areas FROM monsters"
                        " WHERE id = %s AND template = %s",
                        (human_id, TEMPLATE),
                    )
                    current = write.fetchall()
                    if {(r["pokemon_id"], r["form"]) for r in current} == wanted and all(
                        r["override_areas"] == areas for r in current
                    ):
                        continue
                    write.execute(
                        "DELETE FROM monsters WHERE id = %s AND template = %s",
                        (human_id, TEMPLATE),
                    )
                    rows = [rule_row(human_id, p, f, areas) for p, f in sorted(wanted)]
                    if rows:
                        columns = list(rows[0])
                        write.executemany(
                            f"INSERT INTO monsters ({', '.join(f'`{c}`' for c in columns)})"
                            f" VALUES ({', '.join(['%s'] * len(columns))})",
                            [tuple(r[c] for c in columns) for r in rows],
                        )
                    changed = True

                # Everyone not ready loses this module's rows, and only those.
                # With nobody ready, all of them: `id NOT IN (NULL)` would
                # match nothing and leave the last player's rules behind.
                if keep:
                    marks = ", ".join(["%s"] * len(keep))
                    write.execute(
                        f"DELETE FROM monsters WHERE template = %s AND id NOT IN ({marks})",
                        (TEMPLATE, *keep),
                    )
                else:
                    write.execute(
                        "DELETE FROM monsters WHERE template = %s", (TEMPLATE,)
                    )
                changed = changed or write.rowcount > 0
            poracle.commit()
            return changed
        except Exception:
            poracle.rollback()
            raise
        finally:
            pogo.close()
            poracle.close()
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/modules/test_hundo_alerts.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
black -q modules/hundo_alerts.py tests/modules/test_hundo_alerts.py && ruff check modules/hundo_alerts.py tests/modules/test_hundo_alerts.py
git add modules/hundo_alerts.py tests/modules/test_hundo_alerts.py
git commit -m "hundo_alerts: confirm settings by DM, keep Poracle rules in step

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Run it every minute

**Files:**
- Modify: `cogs/scheduled.py` (imports ~line 15, `__init__` ~line 45, the step tuple ~line 229)

- [ ] **Step 1: Wire it in**

Import beside `TradeDM`:

```python
from modules.hundo_alerts import HundoAlerts
```

In `__init__`, beside `self._trade_dm = TradeDM(poliswag)`:

```python
        self._hundo_alerts = HundoAlerts(poliswag)
```

In the tuple of steps in `scheduled_tasks`, right after `self._trade_dm.tick,`:

```python
            self._hundo_alerts.tick,
```

- [ ] **Step 2: Run the whole suite**

Run: `python3 -m pytest -q 2>&1 | tail -2`
Expected: all pass (the scheduler's tests fake `poliswag`, and each step runs through `_run_tick_step`, which logs and swallows a failure).

- [ ] **Step 3: Commit**

```bash
black -q cogs/scheduled.py && ruff check cogs/scheduled.py
git add cogs/scheduled.py
git commit -m "scheduler: run the Pokédex 100IV alerts every minute

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The Poracle DM template

**Files:**
- Modify: `/root/poracleng/config/dts.json` (not in git)

- [ ] **Step 1: Back it up**

```bash
cp -p /root/poracleng/config/dts.json /root/poracleng/config/dts.json.bak-pokedex-100iv
```

- [ ] **Step 2: Add the entry**

```bash
python3 - <<'EOF'
import json
p = "/root/poracleng/config/dts.json"
data = json.load(open(p))
assert not any(t.get("id") == "pokedex-100iv" for t in data)
data.append({
    "id": "pokedex-100iv",
    "language": "en",
    "type": "monster",
    "default": False,
    "platform": "discord",
    "template": {"embed": {
        "color": "{{color}}",
        "title": "💯 100IV que te falta: {{fullName}}",
        "url": "{{{reactMapUrl}}}?o=dm-100iv",
        "description": "CP {{cp}} · Lvl {{level}}\n⏰ até {{time}} · faltam {{tthm}} min\n[Ver no mapa]({{{reactMapUrl}}}?o=dm-100iv)",
        "thumbnail": {"url": "{{{imgUrl}}}"},
        "footer": {"text": "Pokédex · desliga ou muda a área em pogoleiria.pt/trocas"},
    }},
})
open(p, "w").write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
EOF
```

- [ ] **Step 3: Restart Poracle and check it loaded**

```bash
docker restart poracle >/dev/null && sleep 5 && docker logs --since 1m poracle 2>&1 | grep -i "DTS loaded"
```
Expected: `DTS loaded: 77 templates …` (one more than before, 76).

---

### Task 7: Deploy and test on the owner

- [ ] **Step 1: Reload Poliswag and watch one tick**

```bash
cd /root/Poliswag && timeout 40 make reload >/dev/null 2>&1; sleep 75; wc -l logs/error.log; grep -i "HUNDO" logs/actions.log | tail -3
```
Expected: `0 logs/error.log`, no HUNDO lines (nobody is on).

- [ ] **Step 2: Owner turns on collecting 100IV on the site** (Pokédex, 100IV category). Check:

```bash
set -a && . ./.env && set +a
docker exec -i db mariadb -u$DB_USER -p$DB_PASSWORD pogoleiria -e "SELECT collecting FROM trade_player WHERE discord_id = 98846248865398784"
```
Expected: `collecting` contains `hundo`.

- [ ] **Step 3: Switch the owner on**

```bash
docker exec -i db mariadb -u$DB_USER -p$DB_PASSWORD pogoleiria -e "UPDATE trade_player SET hundo_dms = 1, hundo_areas = 'leiria,marinha', hundo_settings_at = NOW() WHERE discord_id = 98846248865398784"
```

- [ ] **Step 4: Within two minutes, check each stage**

```bash
sleep 120
docker exec -i db mariadb -u$DB_USER -p$DB_PASSWORD -e "
SELECT hundo_confirmed_at, hundo_dm_refused_at FROM pogoleiria.trade_player WHERE discord_id = 98846248865398784;
SELECT id, type, enabled, area FROM poracle.humans WHERE id = '98846248865398784';
SELECT COUNT(*) rules, MIN(override_areas) areas FROM poracle.monsters WHERE id = '98846248865398784' AND template = 'pokedex-100iv';
SELECT form, COUNT(*) FROM poracle.monsters WHERE id = '98846248865398784' AND template = 'pokedex-100iv' AND pokemon_id = 19 GROUP BY form;"
```
Expected: `hundo_confirmed_at` set and no refusal; the owner got "100IV por DM: **ligado** · Leiria e Marinha Grande…"; a `discord:user` human, enabled, area `["leiria","marinhagrande"]`; rules ≈ the Pokédex's tile count minus 100IV ticks (≥ 600); Rattata as forms 45 and 46, never 0.

- [ ] **Step 5: Preview the template on the owner**

```bash
docker exec -i poliswag python - <<'EOF'
import asyncio, sys
sys.path.insert(0, "/app")
from types import SimpleNamespace
from modules.poracle_client import PoracleClient
async def main():
    c = PoracleClient(SimpleNamespace(utility=SimpleNamespace(log_to_file=print)))
    hook = {"pokemon_id": 19, "form": 45, "latitude": 39.744, "longitude": -8.807,
            "individual_attack": 15, "individual_defense": 15, "individual_stamina": 15,
            "cp": 300, "pokemon_level": 20}
    print(await c.test_pokemon(hook, {"id": "98846248865398784", "name": "owner",
          "type": "discord:user", "language": "en", "template": "pokedex-100iv"}))
    await c.close()
asyncio.run(main())
EOF
```
Expected: a DM titled "💯 100IV que te falta: Rattata" with the map link. If it arrives in the standard alert look instead, `/api/test` ignores `template`; wait for a real spawn (Step 6) to see the new one.

- [ ] **Step 6: Wait for a real one.** At ~35 a day with nothing ticked, the first real 100IV DM should arrive within the hour. Tick one of the Pokémon on the site and check its rule disappears on the next minute:

```bash
docker exec -i db mariadb -u$DB_USER -p$DB_PASSWORD -e "SELECT COUNT(*) FROM poracle.monsters WHERE id = '98846248865398784' AND template = 'pokedex-100iv'"
```
Expected: one fewer than in Step 4.

---

### Task 8: Context doc

**Files:**
- Modify: `docs/context.md` (module map, after the `trade_digest.py` row)

- [ ] **Step 1: Add the row**

```markdown
| `hundo_alerts.py` | Pokédex 100IV alerts: DMs a settings confirmation (the delivery check, recorded on `trade_player.hundo_confirmed_at` / `hundo_dm_refused_at`), then keeps each active collector's Poracle `monsters` rows (template `pokedex-100iv`, area in `override_areas`) equal to their missing 100IV tiles; reloads Poracle once on change. Off by default (`hundo_dms`). Spec: `docs/superpowers/specs/2026-09-24-pokedex-100iv-alerts-design.md`. |
```

- [ ] **Step 2: Commit**

```bash
git add docs/context.md
git commit -m "context: hundo_alerts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
