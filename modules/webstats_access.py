"""The permanent link to the private statistics report.

The token is a password, the same shape as a trades login code: it works on
any device, it does not expire, and issuing a new one ends every link handed
out before it. Only the SHA-256 is stored, in pogoleiria.webstats_access, so a
leaked database row cannot be opened.

It replaces a store that inserted a rendered HTML snapshot per command and
expired it after a day. The page queries the database on load now, so there is
nothing left to publish and one URL is worth keeping.
"""

import hashlib
import secrets

from modules.config import Config


class WebStatsAccess:
    def __init__(self, site_db, base_url=None):
        # The bot's handle for the site's schema (poliswag.site_db).
        self.db = site_db
        self.base_url = base_url or Config.WEBSTATS_URL

    async def current_or_issue(self):
        """The existing link, or a fresh one if none has been issued.

        A token cannot be recovered from its hash, so an existing row means
        the link exists somewhere in the owner's DMs and this returns None
        rather than inventing a second one.
        """
        # Tuple rows, as before: the count is read by position.
        existing = await self.db.unit(self._count, dict_rows=False)
        if existing:
            return None
        return await self.rotate()

    async def rotate(self):
        """A new link, and the end of every previous one."""
        token = secrets.token_hex(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        await self.db.unit(self._replace, digest, dict_rows=False)
        return f"{self.base_url}/{token}"

    def _count(self, db):
        with db.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM webstats_access")
            return int((cursor.fetchone() or [0])[0])

    def _replace(self, db, digest):
        # One statement pair in one unit, so one transaction: a crash between
        # them would otherwise leave no working link at all.
        with db.cursor() as cursor:
            cursor.execute("DELETE FROM webstats_access")
            cursor.execute(
                "INSERT INTO webstats_access (token_hash) VALUES (%s)", (digest,)
            )
