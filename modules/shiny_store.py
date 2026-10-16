"""Poliswag's side of the shiny tables (migration 017).

shiny_link is shared with the host script scripts/shiny_linker, which is the
only writer of `linked`/`failed`. This module queues codes, announces
results, and owns shiny_alert and the alert watermark.
"""

from modules.config import Config

_REQUEST_SQL = """
INSERT INTO shiny_link (discord_id, discord_name, friend_code, status, requested_at)
VALUES (%s, %s, %s, 'pending', UTC_TIMESTAMP())
ON DUPLICATE KEY UPDATE
  discord_name = VALUES(discord_name), friend_code = VALUES(friend_code),
  status = 'pending', trainer_name = NULL, player_id = NULL, fail_reason = NULL,
  attempts = 0, requested_at = UTC_TIMESTAMP(), resolved_at = NULL, link_processing_at = NULL,
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

    async def find_linked(self, name):
        """The linked player with this Discord or trainer name, any case."""
        rows = await self.db.get_data_from_database(
            "SELECT l.discord_id, l.trainer_name, l.channel_id, p.shiny_access "
            "FROM shiny_link l LEFT JOIN "
            + Config.DB_POGOLEIRIA
            + ".trade_player p ON p.discord_id = l.discord_id "
            "WHERE l.status = 'linked' "
            "AND (LOWER(l.discord_name) = LOWER(%s) OR LOWER(l.trainer_name) = LOWER(%s)) "
            "LIMIT 1",
            params=(name, name),
        )
        return rows[0] if rows else None

    async def set_channel(self, discord_id, channel_id):
        """The player's own private channel, where their alerts go."""
        await self.db.execute_query_to_database(
            "UPDATE shiny_link SET channel_id = %s, channel_archived = 1, "
            "channel_intro_message_id = NULL, channel_intro_state = NULL WHERE discord_id = %s",
            params=(channel_id, discord_id),
        )

    async def activate_from_channel(self, discord_id):
        """The owner's manual channel command activates an approved profile too."""
        await self.db.execute_query_to_database(
            f"UPDATE {Config.DB_POGOLEIRIA}.trade_player SET shiny_map = 1, "
            "collecting = CASE WHEN FIND_IN_SET('shiny', collecting) = 0 "
            "THEN CONCAT_WS(',', NULLIF(collecting, ''), 'shiny') ELSE collecting END, "
            "shiny_access_requested_at = NULL WHERE discord_id = %s "
            "AND shiny_access = 1 AND left_at IS NULL",
            params=(discord_id,),
        )

    async def channel_players(self, discord_id=None):
        """Desired website state plus existing channels that may need archiving."""
        sql = (
            "SELECT l.discord_id, l.trainer_name, l.channel_id, l.channel_archived, "
            "l.channel_intro_message_id, l.channel_intro_state, l.status, l.player_id, "
            "COALESCE(l.link_processing_at > UTC_TIMESTAMP() - INTERVAL 15 MINUTE, 0) AS link_processing, "
            "p.shiny_access, p.shiny_map, p.shiny_notify_discord, p.left_at "
            f"FROM shiny_link l JOIN {Config.DB_POGOLEIRIA}.trade_player p "
            "ON p.discord_id = l.discord_id "
        )
        if discord_id is not None:
            sql += "WHERE l.discord_id = %s"
        else:
            sql += "WHERE l.channel_id IS NOT NULL OR (p.shiny_access = 1 AND p.shiny_map = 1 AND p.left_at IS NULL)"
        return await self.db.get_data_from_database(
            sql, params=() if discord_id is None else (discord_id,)
        )

    async def set_channel_intro(self, discord_id, message_id):
        await self.db.execute_query_to_database(
            "UPDATE shiny_link SET channel_intro_message_id = %s WHERE discord_id = %s",
            params=(message_id, discord_id),
        )

    async def set_channel_intro_state(self, discord_id, state):
        await self.db.execute_query_to_database(
            "UPDATE shiny_link SET channel_intro_state = %s WHERE discord_id = %s",
            params=(state, discord_id),
        )

    async def set_channel_archived(self, discord_id, archived):
        await self.db.execute_query_to_database(
            "UPDATE shiny_link SET channel_archived = %s WHERE discord_id = %s",
            params=(int(archived), discord_id),
        )

    async def unannounced(self):
        """Links the linker finished that their owner has not been told about."""
        return await self.db.get_data_from_database(
            "SELECT l.discord_id, l.status, l.trainer_name, l.fail_reason FROM shiny_link l "
            f"JOIN {Config.DB_POGOLEIRIA}.trade_player p ON p.discord_id = l.discord_id "
            "WHERE l.status IN ('linked', 'failed') AND l.announced_at IS NULL "
            "AND p.shiny_access = 1 AND p.shiny_map = 1 AND p.shiny_notify_discord = 1 AND p.left_at IS NULL"
        )

    async def mark_announced(self, discord_id):
        await self.db.execute_query_to_database(
            "UPDATE shiny_link SET announced_at = UTC_TIMESTAMP() WHERE discord_id = %s",
            params=(discord_id,),
        )

    async def recipients(self, user_ids):
        """[{discord_id, discord_name, player_id, alert_leiria, alert_marinha,
        channel_id, shiny_notify_discord, shiny_notify_all, shiny_notify_hundo}] for the linked
        players who get alerts: anyone with a channel of their own
        (`!shiny canal`), and the listed users, by DM."""
        who = "(l.channel_id IS NOT NULL AND l.channel_archived = 0 AND l.channel_intro_message_id IS NOT NULL)"
        if user_ids:
            placeholders = ",".join(["%s"] * len(user_ids))
            who = f"({who} OR (l.channel_id IS NULL AND l.discord_id IN ({placeholders})))"
        return await self.db.get_data_from_database(
            "SELECT l.discord_id, l.discord_name, l.player_id, l.alert_leiria, l.alert_marinha, "
            "l.channel_id, p.shiny_notify_discord, p.shiny_notify_all, p.shiny_notify_hundo, p.shiny_notify_background FROM shiny_link l "
            f"JOIN {Config.DB_POGOLEIRIA}.trade_player p ON p.discord_id = l.discord_id "
            f"WHERE l.status = 'linked' AND p.left_at IS NULL AND p.shiny_access = 1 "
            f"AND p.shiny_map = 1 AND p.shiny_notify_discord = 1 AND {who}",
            params=tuple(user_ids),
        )

    async def notifications_enabled(self, discord_id):
        """Recheck before delivery, so a switch-off during a tick stops its alerts."""
        rows = await self.db.get_data_from_database(
            f"SELECT 1 FROM {Config.DB_POGOLEIRIA}.trade_player "
            "WHERE discord_id = %s AND shiny_notify_discord = 1 "
            "AND shiny_access = 1 AND shiny_map = 1 AND left_at IS NULL",
            params=(discord_id,),
        )
        return bool(rows)

    async def notification_changes(self):
        """Preferences not yet acknowledged, independently of settings revisions."""
        return await self.db.get_data_from_database(
            "SELECT p.discord_id, p.shiny_map, p.shiny_notify_discord, "
            "l.channel_id, l.channel_archived "
            f"FROM {Config.DB_POGOLEIRIA}.trade_player p "
            "LEFT JOIN shiny_link l ON l.discord_id = p.discord_id "
            "WHERE p.shiny_notify_discord <> p.shiny_notify_discord_applied "
            "AND p.left_at IS NULL"
        )

    async def claim_notification_change(self, discord_id, enabled):
        """Claim once before sending; never acknowledge a newer toggle value."""
        changed = await self.db.execute_query_to_database(
            f"UPDATE {Config.DB_POGOLEIRIA}.trade_player "
            "SET shiny_notify_discord_applied = %s WHERE discord_id = %s "
            "AND shiny_notify_discord = %s AND shiny_notify_discord_applied <> %s",
            params=(int(enabled), discord_id, int(enabled), int(enabled)),
        )
        return changed == 1

    async def pending_settings(self):
        """[{discord_id, shiny_settings_revision}] for the players whose last
        save in the Pokédex the bot has not confirmed yet. The site shows
        "A aguardar pelo Poliswag…" to each of them until confirm_settings."""
        return await self.db.get_data_from_database(
            "SELECT discord_id, shiny_settings_revision "
            f"FROM {Config.DB_POGOLEIRIA}.trade_player "
            "WHERE shiny_applied_revision < shiny_settings_revision"
        )

    async def confirm_settings(self, discord_id, revision):
        """Stamp a revision read before this tick's work as applied. Not while
        the channel still disagrees with the switch: on and linked without a
        ready channel, or off with a channel not archived yet. Those wait for
        a later tick, and the site keeps saying so."""
        wants = "(p.shiny_access = 1 AND p.shiny_map = 1 AND p.left_at IS NULL)"
        active = "(l.channel_id IS NOT NULL AND l.channel_archived = 0)"
        ready = f"({active} AND l.channel_intro_message_id IS NOT NULL)"
        await self.db.execute_query_to_database(
            f"UPDATE {Config.DB_POGOLEIRIA}.trade_player p "
            "LEFT JOIN shiny_link l ON l.discord_id = p.discord_id "
            "SET p.shiny_applied_revision = %s "
            "WHERE p.discord_id = %s AND p.shiny_applied_revision < %s "
            f"AND NOT ({wants} AND p.shiny_notify_discord = 1 AND COALESCE(l.status, '') = 'linked' AND NOT {ready}) "
            f"AND NOT (NOT {wants} AND {active})",
            params=(revision, discord_id, revision),
        )

    async def shiny_eligible(self):
        """Exact eligible species/form/costume tuples, shared with the website."""
        rows = await self.db.get_data_from_database(
            "SELECT pokemon_id, form_id, costume_id FROM shiny_eligible_variant"
        )
        return {(row["pokemon_id"], row["form_id"], row["costume_id"]) for row in rows}

    async def background_rules(self, now):
        return await self.db.get_data_from_database(
            "SELECT * FROM background_prediction_rule "
            "WHERE enabled = 1 AND starts_at <= %s AND ends_at > %s ORDER BY rule_id",
            params=(now, now),
        )

    async def background_notifications_enabled(self, discord_id):
        rows = await self.db.get_data_from_database(
            f"SELECT 1 FROM {Config.DB_POGOLEIRIA}.trade_player "
            "WHERE discord_id = %s AND shiny_notify_background = 1 "
            "AND shiny_notify_discord = 1 AND shiny_access = 1 AND shiny_map = 1 AND left_at IS NULL",
            params=(discord_id,),
        )
        return bool(rows)

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

    async def owns_shiny(self, discord_id, pokemon_id, form_id):
        """An owned Shiny suppresses an alert, with scanner forms filed as in
        the Pokédex: an ordinary form unknown to pokemon_name falls back to 0.
        Read at delivery time so a newly marked catch stops the next alert."""
        rows = await self.db.get_data_from_database(
            f"SELECT 1 AS owned FROM {Config.DB_POGOLEIRIA}.collection_entry c "
            "WHERE c.discord_id = %s AND c.category = 'shiny' AND c.pokemon_id = %s "
            "AND c.form_id = COALESCE((SELECT n.form_id FROM pokemon_name n "
            "WHERE n.pokemon_id = %s AND n.form_id IN (%s, 0) "
            "ORDER BY n.form_id DESC LIMIT 1), 0) LIMIT 1",
            params=(discord_id, pokemon_id, pokemon_id, form_id),
        )
        return bool(rows)

    async def set_verdict(self, discord_id, encounter_id, verdict):
        """True when the answer was stored; False if there already was one."""
        updated = await self.db.execute_query_to_database(
            "UPDATE shiny_alert SET verdict = %s, verdict_at = UTC_TIMESTAMP() "
            "WHERE discord_id = %s AND encounter_id = %s AND verdict IS NULL",
            params=(verdict, discord_id, encounter_id),
        )
        return updated == 1

    async def event_preferences(self, discord_id):
        rows = await self.db.get_data_from_database(
            "SELECT event_alerts_enabled, event_pokemon_ids FROM shiny_link "
            "WHERE discord_id = %s AND status = 'linked'",
            params=(discord_id,),
        )
        return rows[0] if rows else None

    async def set_event_preferences(self, discord_id, enabled, pokemon_ids):
        import json

        changed = await self.db.execute_query_to_database(
            "UPDATE shiny_link SET event_alerts_enabled = %s, event_pokemon_ids = %s "
            "WHERE discord_id = %s AND status = 'linked'",
            params=(
                int(enabled),
                json.dumps(list(pokemon_ids)) if pokemon_ids else None,
                discord_id,
            ),
        )
        if changed == 1:
            return True
        # MySQL also reports zero when the saved setting was unchanged.
        current = await self.event_preferences(discord_id)
        if current is None or bool(current["event_alerts_enabled"]) != bool(enabled):
            return False
        try:
            saved = (
                json.loads(current["event_pokemon_ids"])
                if current["event_pokemon_ids"]
                else []
            )
        except (ValueError, TypeError):
            return False
        return saved == list(pokemon_ids)

    async def event_recipients(self, user_ids):
        if not user_ids:
            return []
        placeholders = ",".join(["%s"] * len(user_ids))
        return await self.db.get_data_from_database(
            "SELECT discord_id, player_id, alert_leiria, alert_marinha, event_pokemon_ids "
            "FROM shiny_link WHERE status = 'linked' AND event_alerts_enabled = 1 "
            f"AND discord_id IN ({placeholders}) "
            "AND (event_digest_at IS NULL OR event_digest_at <= UTC_TIMESTAMP() - INTERVAL 5 MINUTE)",
            params=tuple(user_ids),
        )

    async def claim_event_digest(self, discord_id):
        updated = await self.db.execute_query_to_database(
            "UPDATE shiny_link SET event_digest_at = UTC_TIMESTAMP() "
            "WHERE discord_id = %s AND status = 'linked' AND event_alerts_enabled = 1 "
            "AND (event_digest_at IS NULL OR event_digest_at <= UTC_TIMESTAMP() - INTERVAL 5 MINUTE)",
            params=(discord_id,),
        )
        return updated == 1

    async def record_event_alert(self, discord_id, spawn, odds, draw):
        inserted = await self.db.execute_query_to_database(
            "INSERT IGNORE INTO shiny_alert (discord_id, encounter_id, pokemon_id, form_id, "
            "prediction_odds, prediction_draw, delivery_status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'pending')",
            params=(
                discord_id,
                spawn["id"],
                spawn["pokemon_id"],
                spawn["form_id"],
                odds,
                draw,
            ),
        )
        return inserted == 1

    async def mark_event_delivery(self, discord_id, encounter_ids, delivered):
        if not encounter_ids:
            return
        placeholders = ",".join(["%s"] * len(encounter_ids))
        await self.db.execute_query_to_database(
            "UPDATE shiny_alert SET delivery_status = %s, "
            "delivered_at = CASE WHEN %s = 'sent' THEN UTC_TIMESTAMP() ELSE NULL END "
            f"WHERE discord_id = %s AND encounter_id IN ({placeholders}) AND delivery_status = 'pending'",
            params=(
                "sent" if delivered else "failed",
                "sent" if delivered else "failed",
                discord_id,
                *encounter_ids,
            ),
        )
