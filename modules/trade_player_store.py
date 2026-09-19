"""Writes pogoleiria.trade_player — the trades tool's player identities.

The table is shared with apps/trades in the PoGoLeiria repo
(apps/trades/db/001_trades.sql). Poliswag owns identity, the code hash and
membership; the web app owns trainer_name, friend_code, note and every
trade_entry row. There is no API between them: this table is the contract.

Same two rules as page_view_stats: no `%` in any SQL but `%s`, and UTC only
(UTC_TIMESTAMP(), because this container runs Europe/Lisbon while the DB
rows are written in UTC by the web app).
"""

from modules.config import Config
from modules.database_connector import DatabaseConnector


class TradePlayerStore:
    def __init__(self):
        self.db = DatabaseConnector(Config.DB_POGOLEIRIA)

    async def upsert(self, discord_id, username, display_name, avatar_url, code_hash):
        """Issue (or re-issue) a code. Also refreshes identity and un-leaves."""
        await self.db.execute_query_to_database(
            """
            INSERT INTO trade_player
                (discord_id, username, display_name, avatar_url, code_hash,
                 code_issued_at, left_at)
            VALUES (%s, %s, %s, %s, %s, UTC_TIMESTAMP(), NULL)
            ON DUPLICATE KEY UPDATE
                username = VALUES(username),
                display_name = VALUES(display_name),
                avatar_url = VALUES(avatar_url),
                code_hash = VALUES(code_hash),
                code_issued_at = UTC_TIMESTAMP(),
                left_at = NULL
            """,
            params=(discord_id, username, display_name, avatar_url, code_hash),
        )

    async def refresh_identity(self, discord_id, username, display_name, avatar_url):
        """Name/avatar changed on Discord. Never creates a row."""
        await self.db.execute_query_to_database(
            """
            UPDATE trade_player SET username = %s, display_name = %s, avatar_url = %s
            WHERE discord_id = %s
            """,
            params=(username, display_name, avatar_url, discord_id),
        )

    async def set_left(self, discord_id):
        await self.db.execute_query_to_database(
            "UPDATE trade_player SET left_at = UTC_TIMESTAMP() "
            "WHERE discord_id = %s AND left_at IS NULL",
            params=(discord_id,),
        )

    async def clear_left(self, discord_id):
        await self.db.execute_query_to_database(
            "UPDATE trade_player SET left_at = NULL WHERE discord_id = %s",
            params=(discord_id,),
        )

    async def all_ids(self):
        rows = await self.db.get_data_from_database(
            "SELECT discord_id FROM trade_player"
        )
        return [int(row["discord_id"]) for row in rows]

    async def reconcile(self, member_ids):
        """Align left_at with who is actually in the guild.

        Returns (newly_left, newly_returned) so the caller can log what the
        bot missed while it was down.
        """
        rows = await self.db.get_data_from_database(
            "SELECT discord_id, left_at FROM trade_player"
        )
        left, returned = [], []
        for row in rows:
            discord_id = int(row["discord_id"])
            is_member = discord_id in member_ids
            if not is_member and row["left_at"] is None:
                await self.set_left(discord_id)
                left.append(discord_id)
            elif is_member and row["left_at"] is not None:
                await self.clear_left(discord_id)
                returned.append(discord_id)
        return left, returned
