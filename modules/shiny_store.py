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

    async def find_linked(self, name):
        """The linked player with this Discord or trainer name, any case."""
        rows = await self.db.get_data_from_database(
            "SELECT discord_id, trainer_name, channel_id FROM shiny_link "
            "WHERE status = 'linked' "
            "AND (LOWER(discord_name) = LOWER(%s) OR LOWER(trainer_name) = LOWER(%s)) "
            "LIMIT 1",
            params=(name, name),
        )
        return rows[0] if rows else None

    async def set_map_token(self, discord_id, token_hash):
        await self.db.execute_query_to_database(
            "UPDATE shiny_link SET map_token_hash = %s WHERE discord_id = %s",
            params=(token_hash, discord_id),
        )

    async def set_channel(self, discord_id, channel_id):
        """The player's own private channel, where their alerts go."""
        await self.db.execute_query_to_database(
            "UPDATE shiny_link SET channel_id = %s WHERE discord_id = %s",
            params=(channel_id, discord_id),
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
        """[{discord_id, discord_name, player_id, alert_leiria, alert_marinha,
        channel_id}] for the linked players who get alerts: anyone with a
        channel of their own (`!shiny canal`), and the listed users, by DM."""
        who = "channel_id IS NOT NULL"
        if user_ids:
            placeholders = ",".join(["%s"] * len(user_ids))
            who = f"({who} OR discord_id IN ({placeholders}))"
        return await self.db.get_data_from_database(
            "SELECT discord_id, discord_name, player_id, alert_leiria, alert_marinha, "
            f"channel_id FROM shiny_link WHERE status = 'linked' AND {who}",
            params=tuple(user_ids),
        )

    async def never_shiny(self):
        """{(pokemon_id, form_id)} the scanner has met thousands of times
        without one shiny (modules/shiny_odds.py keeps the list)."""
        rows = await self.db.get_data_from_database(
            "SELECT pokemon_id, form_id FROM shiny_species WHERE never_shiny = 1"
        )
        return {(row["pokemon_id"], row["form_id"]) for row in rows}

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
