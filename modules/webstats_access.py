"""The permanent link to the private statistics report.

The token is a password, the same shape as a trades login code: it works on
any device, it does not expire, and issuing a new one ends every link handed
out before it. Only the SHA-256 is stored, in pogoleiria.webstats_access, so a
leaked database row cannot be opened.

It replaces a store that inserted a rendered HTML snapshot per command and
expired it after a day. The page queries the database on load now, so there is
nothing left to publish and one URL is worth keeping.
"""

import asyncio
import hashlib
import secrets

from modules.config import Config
from modules.database_connector import connect


class WebStatsAccess:
    def __init__(self, base_url=None):
        self.base_url = base_url or Config.WEBSTATS_URL

    async def current_or_issue(self):
        """The existing link, or a fresh one if none has been issued.

        A token cannot be recovered from its hash, so an existing row means
        the link exists somewhere in the owner's DMs and this returns None
        rather than inventing a second one.
        """
        existing = await asyncio.to_thread(self._count)
        if existing:
            return None
        return await self.rotate()

    async def rotate(self):
        """A new link, and the end of every previous one."""
        token = secrets.token_hex(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        await asyncio.to_thread(self._replace, digest)
        return f"{self.base_url}/{token}"

    def _connect(self):
        return connect(Config.DB_POGOLEIRIA)

    def _count(self):
        db = self._connect()
        try:
            with db.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM webstats_access")
                return int((cursor.fetchone() or [0])[0])
        finally:
            db.close()

    def _replace(self, digest):
        # One statement pair in one transaction: a crash between them would
        # otherwise leave no working link at all.
        db = self._connect()
        try:
            with db.cursor() as cursor:
                cursor.execute("DELETE FROM webstats_access")
                cursor.execute(
                    "INSERT INTO webstats_access (token_hash) VALUES (%s)", (digest,)
                )
            db.commit()
        finally:
            db.close()
