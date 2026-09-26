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
from modules.hundo_confirmation import send_once

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


# One number for a whole setting, as hundo_confirmed_setting stores it: the
# zones' bits (the hundo_areas SET's own order), 0 when switched off.
_AREA_BITS = {"leiria": 1, "marinha": 2}


def setting_code(hundo_dms, hundo_areas):
    """0 off, 1 Leiria, 2 Marinha Grande, 3 both."""
    if int(hundo_dms) != 1:
        return 0
    return sum(_AREA_BITS[key] for key in _area_keys(hundo_areas))


def _code_keys(code):
    return [key for key, _geofence, _label in _AREAS if code & _AREA_BITS[key]]


def _thousands(n):
    """1499 -> "1 499", as pt-PT writes it."""
    return f"{n:,}".replace(",", "\u00a0")


def _zones_line(keys):
    names = [f"**{label}**" for key, _geofence, label in _AREAS if key in keys]
    return (
        f"Zonas ativas: {' e '.join(names)}."
        if len(names) > 1
        else f"Zona ativa: {names[0]}."
    )


def _joined(keys):
    # Plain: it goes inside an already bold headline (nested ** breaks).
    return " e ".join(label for key, _geofence, label in _AREAS if key in keys)


def confirmation_message(row, missing_count=None):
    """The DM confirming the player's current settings, said as a change from
    the last confirmed ones (hundo_confirmed_setting) when that is known:
    a headline for what happened, then one line per fact."""
    footer = f"-# Para mudar as zonas ou desligar: {SETTINGS_WHERE}."
    if row["hundo_dms"] != 1:
        return "\n".join(
            [
                "🔕 **Alertas de 100IV desligados.**",
                "Já não vais receber aqui os 100IV que te faltam.",
                f"-# Para voltar a ligar: {SETTINGS_WHERE}.",
            ]
        )
    after = _area_keys(row["hundo_areas"])
    before = _code_keys(row.get("hundo_confirmed_setting") or 0)
    was_on = bool(before)
    added = [key for key in after if key not in before]
    removed = [key for key in before if key not in after]
    if was_on and (added or removed):
        if added and removed:
            headline = f"📍 **Trocaste {_joined(removed)} por {_joined(added)}.**"
        elif added:
            headline = f"📍 **Começaste a seguir também {_joined(added)}.**"
        else:
            headline = f"📍 **Deixaste de seguir {_joined(removed)}.**"
        return "\n".join([headline, _zones_line(after), footer])
    lines = [
        "💯 **Alertas de 100IV ligados.**",
        _zones_line(after),
        "Quando aparecer um 100IV que te falta na Pokédex, avisamos-te aqui.",
    ]
    if missing_count:
        lines.append(f"Faltam-te {_thousands(missing_count)} na Pokédex de 100IV.")
    return "\n".join([*lines, footer])


def confirmation_embed(row, missing_count=None):
    """One compact column for mobile, with the settings hint in the footer."""
    lines = confirmation_message(row, missing_count).split("\n")
    embed = discord.Embed(
        title=lines[0].replace("**", ""),
        description="\n".join(lines[1:-1]),
        color=Config.EMBED_COLOR if row["hundo_dms"] == 1 else 0x747F8D,
    )
    embed.set_footer(text=lines[-1].removeprefix("-# "))
    return embed


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


def settled_back(row):
    """The settings are exactly the last confirmed ones again (a change undone
    before its DM went out), and no DM was refused since that confirmation:
    nothing to confirm, and the alerts never stop."""
    confirmed = row["hundo_confirmed_revision"]
    return (
        confirmed > 0
        and row.get("hundo_confirmed_setting") is not None
        and int(row["hundo_confirmed_setting"])
        == setting_code(row["hundo_dms"], row["hundo_areas"])
        and row["hundo_dm_refused_revision"] < confirmed
    )


def is_active(row):
    """An eligible collector whose current settings were confirmed by DM, or
    are the confirmed ones again (settled_back). Mirrors _CLEANUP_SQL."""
    revision = row["hundo_settings_revision"]
    return (
        row["hundo_dms"] == 1
        and collects_hundo(row)
        and revision > 0
        and row["hundo_dm_refused_revision"] < revision
        and (row["hundo_confirmed_revision"] == revision or settled_back(row))
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


# Count/time alone miss a same-second swap of two collection tiles. Include
# an order-independent 64-bit fingerprint of tile identities. The hourly full
# resync remains a backstop for hash collisions and out-of-band rule edits.
_PLAYERS_SQL = """
SELECT p.discord_id, COALESCE(NULLIF(p.trainer_name, ''), p.display_name) AS display_name,
       p.collecting, p.show_costumes, p.left_at, p.hundo_dms, p.hundo_areas,
       p.hundo_settings_revision, p.hundo_confirmed_revision, p.hundo_dm_refused_revision,
       p.hundo_confirmed_setting,
       -- Quiet period: the DM waits until the last change is this old, so a
       -- burst of taps gets one DM (or none, if it ends where it started).
       -- Same clock as the site's NOW(6) stamp; decides when, never whether.
       COALESCE(p.hundo_settings_at > NOW(6) - INTERVAL 30 SECOND, 0) AS hundo_settling,
       COALESCE(t.ticks, 0) AS hundo_ticks, t.ticked_at AS hundo_ticked_at,
       COALESCE(t.fingerprint, 0) AS hundo_tiles_fingerprint
FROM trade_player p
LEFT JOIN (
    SELECT discord_id, COUNT(*) AS ticks, MAX(created_at) AS ticked_at,
           BIT_XOR(CAST(CONV(SUBSTRING(SHA2(CONCAT(pokemon_id, ':', form_id), 256),
                                     1, 16), 16, 10) AS UNSIGNED)) AS fingerprint
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
    OR NOT (
        p.hundo_confirmed_revision = p.hundo_settings_revision
        -- settled_back: the confirmed settings again, nothing refused since
        OR (p.hundo_confirmed_revision > 0
            AND p.hundo_confirmed_setting <=> IF(p.hundo_dms = 1,
                IF(p.hundo_areas + 0 = 0, 3, p.hundo_areas + 0), 0)
            AND p.hundo_dm_refused_revision < p.hundo_confirmed_revision)
    )
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
        row.get("hundo_tiles_fingerprint"),
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
        # Keep Discord outcomes until their DB acknowledgement succeeds. A DB
        # outage must not cause the same confirmation to be sent every minute.
        # Process-local: a crash between delivery and recording can still repeat.
        self._pending_records = {}
        self._health = {}

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
        players = []
        self._health = {}
        try:
            await self._cleanup_rules()
            # Publish removals before potentially slow Discord sends/rebuilds.
            await self._reload_if_pending()
            players = await asyncio.to_thread(self._read_players)
            pending_revisions = {
                r["discord_id"]: r["hundo_settings_revision"]
                for r in players
                if confirmation_due(r)
            }
            self._pending_records = {
                user_id: outcome
                for user_id, outcome in self._pending_records.items()
                if pending_revisions.get(user_id) == outcome[0]
            }
            for row in players:
                if not may_confirm(row) or not confirmation_due(row):
                    continue
                try:
                    if not settled_back(row) and row.get("hundo_settling"):
                        continue  # still inside the quiet period
                    if settled_back(row):
                        # Undone before its DM: acknowledge without one.
                        await asyncio.to_thread(
                            self._record,
                            row["discord_id"],
                            row["hundo_settings_revision"],
                            delivered=True,
                            settings=(row["hundo_dms"], row["hundo_areas"]),
                        )
                        continue
                    await self._confirm(row)
                except Exception as exc:
                    # No outcome recorded for transient delivery/DB failures.
                    self._log(f"confirmation for {row['discord_id']} failed: {exc}")

            # Do not turn a stale in-memory snapshot into an active player.
            players = await asyncio.to_thread(self._read_players)
            masterfile = getattr(self.poliswag.quest_search, "masterfile_data", None)
            pokemon = (masterfile or {}).get("pokemon")
            if not pokemon:
                self._health.update(
                    {r["discord_id"]: "error" for r in players if is_active(r)}
                )
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
            if players:
                try:
                    await self.poliswag.poracle.health()
                    reachable = not self._reload_pending
                except Exception as exc:
                    reachable = False
                    self._log(f"Poracle health check failed: {exc}")
                for row in players:
                    state = self._health.get(row["discord_id"], "pending")
                    if state == "ready" and not reachable:
                        state = "unavailable"
                    try:
                        await asyncio.to_thread(self._record_health, row, state)
                    except Exception as exc:
                        self._log(f"health recording failed: {exc}")

    def _record_health(self, row, state):
        with closing(connect(Config.DB_POGOLEIRIA, autocommit=True)) as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "UPDATE trade_player SET hundo_health_revision = %s, "
                    "hundo_health_state = %s, hundo_health_at = UTC_TIMESTAMP(6) "
                    "WHERE discord_id = %s AND hundo_settings_revision = %s",
                    (
                        row["hundo_settings_revision"],
                        state,
                        row["discord_id"],
                        row["hundo_settings_revision"],
                    ),
                )

    async def _sync_active(self, row, forms, masterfile_date=None):
        """One player's rules; a failure here leaves other players alone."""
        discord_id = row["discord_id"]
        self._health[discord_id] = "error"
        try:
            human = await asyncio.to_thread(self._human, discord_id)
            if human is None:
                self._synced.pop(discord_id, None)
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
                self._health[discord_id] = "stopped"
                # Stopped by the player (or an admin): never restart it.
                if discord_id not in self._stopped_logged:
                    self._log(f"Poracle user {discord_id} is stopped")
                    self._stopped_logged.add(discord_id)
                self._synced.pop(discord_id, None)
                return
            self._stopped_logged.discard(discord_id)
            signature = _signature(row, human["current_profile_no"], masterfile_date)
            if self._unchanged(discord_id, signature):
                self._health[discord_id] = "ready"
                return
            self._synced.pop(discord_id, None)
            changed = await asyncio.to_thread(
                self._sync_player, row, forms, human["current_profile_no"]
            )
            self._reload_pending |= changed
            self._synced[discord_id] = (signature, _monotonic())
            self._health[discord_id] = "ready"
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
        discord_id = row["discord_id"]
        revision = row["hundo_settings_revision"]
        outcome = self._pending_records.get(discord_id)
        if outcome is None or outcome[0] != revision:
            missing = None
            if row["hundo_dms"] == 1:
                try:
                    missing = await asyncio.to_thread(self._missing_count, row)
                except Exception as exc:
                    # The count is a nicety; the confirmation goes without it.
                    self._log(f"missing count for {discord_id} failed: {exc}")
            try:
                delivered = await self._deliver_confirmation(
                    row, confirmation_embed(row, missing)
                )
                if delivered is None:
                    return
            except (discord.Forbidden, discord.NotFound):
                delivered = False
            outcome = (revision, delivered, (row["hundo_dms"], row["hundo_areas"]))
            self._pending_records[discord_id] = outcome
        await asyncio.to_thread(
            self._record,
            discord_id,
            revision,
            delivered=outcome[1],
            settings=outcome[2],
        )
        # False also means this outcome is obsolete/already recorded. Only an
        # exception retains it for a DB-only retry on the next tick.
        self._pending_records.pop(discord_id, None)

    async def _deliver_confirmation(self, row, embed):
        return await send_once(self.poliswag, row, embed)

    def _missing_count(self, row):
        """Missing 100IV tiles, as the Pokédex counts them (costumes per setting)."""
        with closing(connect(Config.DB_POGOLEIRIA, dict_rows=True)) as db:
            with db.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) AS n FROM ({_MISSING_SQL}) missing",
                    (row["discord_id"], row["show_costumes"]),
                )
                return int(cursor.fetchone()["n"])

    def _read_players(self):
        """Read current settings, including switched-off players needing a DM."""
        with closing(connect(Config.DB_POGOLEIRIA, dict_rows=True)) as db:
            with db.cursor() as cursor:
                cursor.execute(_PLAYERS_SQL)
                return list(cursor.fetchall())

    def _record(self, discord_id, revision, *, delivered, settings=None):
        """Record an outcome only for the current, not-yet-acknowledged revision.

        A delivery also stores the setting it confirmed (`settings` =
        (hundo_dms, hundo_areas) as read with `revision`) as one number,
        hundo_confirmed_setting: the next DM is worded against it, and
        settled_back compares with it.
        False means settings changed during delivery or an outcome was already
        recorded. Callers must re-read settings before deciding eligibility.
        """
        prefix = "hundo_confirmed" if delivered else "hundo_dm_refused"
        snapshot, params = "", ()
        if delivered and settings is not None:
            snapshot = ", hundo_confirmed_setting = %s"
            params = (setting_code(*settings),)
        with closing(connect(Config.DB_POGOLEIRIA, autocommit=True)) as db:
            with db.cursor() as cursor:
                cursor.execute(
                    f"UPDATE trade_player SET {prefix}_revision = %s,"
                    f" {prefix}_at = NOW(6){snapshot} WHERE discord_id = %s"
                    " AND hundo_settings_revision = %s"
                    " AND GREATEST(hundo_confirmed_revision,"
                    " hundo_dm_refused_revision) < %s",
                    (revision, *params, discord_id, revision, revision),
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
