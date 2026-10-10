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

    def accounts_free(self, usernames, in_use):
        """[(username, refresh token)] of the listed healthy accounts that no
        Rotom controller is using, in the order they were listed."""
        candidates = [name for name in usernames if name not in in_use]
        if not candidates:
            return []
        marks = ",".join(["%s"] * len(candidates))
        rows = self._all(
            f"SELECT username, refresh_token FROM {self.dragonite}.account "
            f"WHERE username IN ({marks}) AND provider = 'ptc' AND refresh_token <> '' "
            "AND invalid = 0 AND auth_banned = 0 "
            "AND COALESCE(banned, 0) = 0 AND COALESCE(suspended, 0) = 0",
            tuple(candidates),
        )
        healthy = {row["username"]: row["refresh_token"] for row in rows}
        return [(name, healthy[name]) for name in candidates if name in healthy]
