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
