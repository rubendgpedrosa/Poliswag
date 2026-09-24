"""Pokédex 100IV alerts: keep Poracle rules in step with each collector's
missing 100IV tiles, so Poracle DMs them when one spawns.

Every minute: remove managed rules of anyone no longer active, DM a
confirmation for each new settings revision, rebuild each active player's
rules when they differ, and reload Poracle once if anything changed.
Settings revisions identify confirmation attempts; timestamps are audit data
only. Spec: docs/superpowers/specs/2026-09-24-pokedex-100iv-alerts-design.md
"""

import asyncio
import json
import time
from collections import Counter
from contextlib import closing

import discord

from modules.config import Config
from modules.database_connector import connect

TEMPLATE = "pokedex-100iv"

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

# Where the switch lives on the site, named as the site names it.
SETTINGS_WHERE = "pogoleiria.pt/pokedex → Perfil e outras opções"

# Site key, Poracle geofence, and display name, in display order.
_AREAS = (
    ("leiria", "leiria", "Leiria"),
    ("marinha", "marinhagrande", "Marinha Grande"),
)


def _area_keys(hundo_areas):
    """Return known areas in display order; an empty selection means both."""
    chosen = set((hundo_areas or "").split(","))
    keys = [key for key, _geofence, _label in _AREAS if key in chosen]
    return keys or [key for key, _geofence, _label in _AREAS]


def areas_json(hundo_areas):
    """Translate the site's area selection into Poracle's JSON-array string."""
    names = {key: geofence for key, geofence, _label in _AREAS}
    return json.dumps([names[key] for key in _area_keys(hundo_areas)])


def confirmation_text(on, hundo_areas):
    """Describe the full settings, without promising immediate rule removal."""
    if not on:
        return (
            "100IV por DM: **desligado**. Vamos remover os teus alertas de 100IV. "
            f"Para voltar a ligar: {SETTINGS_WHERE}."
        )
    labels = {key: label for key, _geofence, label in _AREAS}
    keys = _area_keys(hundo_areas)
    where = (
        " e ".join(labels[key] for key in keys)
        if len(keys) > 1
        else f"só {labels[keys[0]]}"
    )
    return (
        f"100IV por DM: **ligado** · {where}. "
        "Vais receber aqui os 100IV que te faltam na Pokédex. "
        f"Para mudar a zona ou desligar: {SETTINGS_WHERE}."
    )


def confirmation_due(row):
    """A new settings revision or explicit retry needs a confirmation attempt."""
    return row["hundo_settings_revision"] > max(
        row["hundo_confirmed_revision"], row["hundo_dm_refused_revision"]
    )


def collects_hundo(row):
    """A current member collecting 100IV: the only players alerts are for."""
    return row["left_at"] is None and "hundo" in (row["collecting"] or "").split(",")


def may_confirm(row):
    """Who gets a confirmation DM: an "off" goes to any current member (the
    site allows switching off after un-collecting), an "on" only to a
    collector (the site refuses it otherwise)."""
    return row["left_at"] is None and (row["hundo_dms"] == 0 or collects_hundo(row))


def is_active(row):
    """An eligible collector has a delivered confirmation of current settings."""
    revision = row["hundo_settings_revision"]
    return (
        row["hundo_dms"] == 1
        and collects_hundo(row)
        and revision > 0
        and row["hundo_confirmed_revision"] == revision
        and row["hundo_dm_refused_revision"] < revision
    )


def default_forms(pokemon):
    """Map species to ordinary forms; None means no safe exact rule exists.

    Poracle treats form 0 as a wildcard, so retain it only for species
    explicitly present in the masterfile without alternative forms.
    """
    result = {}
    for pokemon_id, details in pokemon.items():
        form = int(details.get("defaultFormId") or 0)
        result[int(pokemon_id)] = (
            form if form else (None if details.get("forms") else 0)
        )
    return result


def wanted_rules(missing_tiles, forms):
    """Translate missing tiles into unique species/form pairs for Poracle.

    Named forms keep their IDs. An ordinary tile with no safe form is
    skipped, never widened to Poracle's form-0 wildcard: such a species
    (Ogerpon, defaultFormId 0) only spawns in named forms, which have tiles
    and rules of their own. Rejecting the whole rebuild instead left anyone
    missing Ogerpon with no rules at all.
    """
    result = set()
    for pokemon_id, form_id in missing_tiles:
        if form_id == 0:
            form_id = forms.get(pokemon_id)
            if form_id is None:
                continue
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


# hundo_ticks/hundo_ticked_at are a cheap change marker for the player's
# 100IV ticks (a tick adds a row, an untick removes one), so an unchanged
# player's ~1500-row rebuild check can be skipped (HundoAlerts._unchanged).
_PLAYERS_SQL = """
SELECT p.discord_id, COALESCE(NULLIF(p.trainer_name, ''), p.display_name) AS display_name,
       p.collecting, p.show_costumes, p.left_at, p.hundo_dms, p.hundo_areas,
       p.hundo_settings_revision, p.hundo_confirmed_revision, p.hundo_dm_refused_revision,
       COALESCE(t.ticks, 0) AS hundo_ticks, t.ticked_at AS hundo_ticked_at
FROM trade_player p
LEFT JOIN (
    SELECT discord_id, COUNT(*) AS ticks, MAX(created_at) AS ticked_at
    FROM collection_entry WHERE category = 'hundo' GROUP BY discord_id
) t ON t.discord_id = p.discord_id
WHERE p.hundo_dms = 1 OR p.hundo_settings_revision > 0
"""

# A player whose marker hasn't moved is still rebuilt this often: catches
# what the marker can't see (a rule edited by hand, a pokemon_name change).
FULL_RESYNC_SECONDS = 3600

# The clock _synced is stamped with (a hook for tests: asyncio shares time).
_monotonic = time.monotonic

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
# The CAST takes the connection's collation (general_ci from pymysql) while
# monsters.id is unicode_ci: without the COLLATE, MariaDB refuses the join.
_CLEANUP_SQL = """
DELETE m FROM monsters m
LEFT JOIN pogoleiria.trade_player p
  ON m.id = CAST(p.discord_id AS CHAR) COLLATE utf8mb4_unicode_ci
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


def _signature(row, profile_no, masterfile_date):
    """Everything a player's wanted rules are built from, cheaply: settings
    revision (switch/areas), costumes, the 100IV tick marker, the Poracle
    profile they follow, and the masterfile load (default forms, names)."""
    return (
        row["hundo_settings_revision"],
        row["hundo_areas"],
        row["show_costumes"],
        row.get("hundo_ticks"),
        str(row.get("hundo_ticked_at")),
        profile_no,
        masterfile_date,
    )


class HundoAlerts:
    """The scheduled tick; the sole writer of this template's rules."""

    def __init__(self, poliswag):
        self.poliswag = poliswag
        # Survives transient reload failures within the process only; Poracle
        # also reloads its state periodically, which covers a restart.
        self._reload_pending = False
        self._stopped_logged = set()
        # discord_id -> (signature, monotonic time) of the last sync that
        # left the player's rules matching; in memory only (a restart syncs).
        self._synced = {}

    def _log(self, message):
        self.poliswag.utility.log_to_file(f"[HUNDO] {message}")

    async def _cleanup_rules(self):
        try:
            changed = await asyncio.to_thread(self._cleanup)
            self._reload_pending |= changed
            if changed:
                # Someone's rules were deleted; don't trust any "unchanged".
                self._synced.clear()
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
                if not may_confirm(row) or not confirmation_due(row):
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
            masterfile_date = masterfile.get("date")
            for row in players:
                if is_active(row):
                    await self._sync_active(row, forms, masterfile_date)
                else:
                    # Its rules go in cleanup; a later return must rebuild.
                    self._synced.pop(row["discord_id"], None)
        finally:
            await self._cleanup_rules()
            await self._reload_if_pending()

    async def _sync_active(self, row, forms, masterfile_date=None):
        """One player's rules; a failure here leaves other players alone."""
        discord_id = row["discord_id"]
        try:
            human = await asyncio.to_thread(self._human, discord_id)
            if human is None:
                await self.poliswag.poracle.create_user(
                    discord_id,
                    row["display_name"],
                    area=areas_json(row["hundo_areas"]),
                )
                human = await asyncio.to_thread(self._human, discord_id)
                if human is None:
                    raise RuntimeError("Created human not visible yet")
            if human["type"] != "discord:user":
                raise ValueError("Existing Poracle human is not a Discord user")
            if not human["enabled"] or human["admin_disable"]:
                # Stopped by the player (or an admin): never restart it.
                if discord_id not in self._stopped_logged:
                    self._log(f"Poracle user {discord_id} is stopped")
                    self._stopped_logged.add(discord_id)
                self._synced.pop(discord_id, None)
                return
            self._stopped_logged.discard(discord_id)
            signature = _signature(row, human["current_profile_no"], masterfile_date)
            if self._unchanged(discord_id, signature):
                return
            self._synced.pop(discord_id, None)
            changed = await asyncio.to_thread(
                self._sync_player, row, forms, human["current_profile_no"]
            )
            self._reload_pending |= changed
            self._synced[discord_id] = (signature, _monotonic())
        except Exception as exc:
            self._log(f"rule sync for {discord_id} failed: {exc}")

    def _unchanged(self, discord_id, signature):
        synced = self._synced.get(discord_id)
        return (
            synced is not None
            and synced[0] == signature
            and _monotonic() - synced[1] < FULL_RESYNC_SECONDS
        )

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
        """Read current settings, including switched-off players needing a DM."""
        with closing(connect(Config.DB_POGOLEIRIA, dict_rows=True)) as db:
            with db.cursor() as cursor:
                cursor.execute(_PLAYERS_SQL)
                return list(cursor.fetchall())

    def _record(self, discord_id, revision, *, delivered):
        """Record an outcome only for the current, not-yet-acknowledged revision.

        False means settings changed during delivery or an outcome was already
        recorded. Callers must re-read settings before deciding eligibility.
        """
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
        """Replace the player's rules in one transaction when any differ.

        A player who opts out meanwhile loses them in the tick's final cleanup.
        """
        discord_id = str(row["discord_id"])
        with closing(connect(Config.DB_POGOLEIRIA, dict_rows=True)) as pogo:
            with pogo.cursor() as read:
                read.execute(_MISSING_SQL, (row["discord_id"], row["show_costumes"]))
                tiles = [(r["pokemon_id"], r["form_id"]) for r in read.fetchall()]
        areas = areas_json(row["hundo_areas"])
        wanted = [
            rule_row(discord_id, pokemon_id, form, areas, profile_no)
            for pokemon_id, form in sorted(wanted_rules(tiles, forms))
        ]
        columns = ", ".join(f"`{column}`" for column in RULE_COLUMNS)
        with closing(connect("poracle", dict_rows=True, autocommit=False)) as db:
            try:
                with db.cursor() as cursor:
                    cursor.execute(
                        f"SELECT {columns} FROM monsters WHERE id = %s AND template = %s",
                        (discord_id, TEMPLATE),
                    )
                    changed = not rules_equal(cursor.fetchall(), wanted)
                    if changed:
                        cursor.execute(
                            "DELETE FROM monsters WHERE id = %s AND template = %s",
                            (discord_id, TEMPLATE),
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
