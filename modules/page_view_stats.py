"""Reads pogoleiria.page_view — the landing app's own analytics table.

Owned by apps/landing in the PoGoLeiria repo (db/001_page_view.sql,
db/002_page_view_device_detail.sql); this module only ever reads it.

Two rules run through everything here:

* **No `%` in any SQL but `%s`.** DatabaseConnector passes params straight to
  `cursor.execute`, which makes pymysql apply `query % args`, so a
  `DATE_FORMAT(created_at, '%Y-%m-%d')` would raise "unsupported format
  character". Dates are grouped with `DATE()`/`HOUR()` and formatted in Python.
* **UTC everywhere.** `created_at` is written by a server running UTC while
  this container is on Europe/Lisbon. A local `now()` would push the period
  boundary an hour into the future and drop the oldest day's first hour.
"""

from datetime import datetime, timedelta, timezone

from modules.config import Config
from modules.database_connector import DatabaseConnector
from modules.logging_mixin import LoggingMixin

_DEFAULT_DAYS = 7
_MAX_DAYS = 365
_TODAY_WORDS = {"hoje", "today"}
_ALL_WORDS = {"all", "tudo", "sempre"}


def resolve_period(arg):
    """`None`/`"7d"`/`"hoje"`/`"all"` → a day count, or None for no bound.

    Raises ValueError on anything else so the cog can answer with a usage
    hint before touching the database.
    """
    if arg is None:
        return _DEFAULT_DAYS

    text = arg.strip().lower()
    if text in _TODAY_WORDS:
        return 1
    if text in _ALL_WORDS:
        return None

    digits = text[:-1] if text.endswith("d") else text
    if not digits.isdigit():
        raise ValueError(f"período inválido: {arg}")

    days = int(digits)
    if not 1 <= days <= _MAX_DAYS:
        raise ValueError(f"período fora do intervalo 1-{_MAX_DAYS}: {arg}")
    return days


def since_for(days, now=None):
    """The inclusive lower bound of a period, as whole UTC calendar days.

    Naive on purpose: the column is DATETIME and the server keeps UTC, so an
    aware value would be serialized with an offset the column cannot hold.
    """
    if days is None:
        return datetime(1970, 1, 1)

    moment = now or datetime.now(timezone.utc)
    midnight = moment.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    return midnight - timedelta(days=days - 1)


# Period reach is always sessions (COUNT(DISTINCT load_id)) or views, never
# COUNT(DISTINCT visitor): the visitor hash rotates at UTC midnight, so it only
# counts uniques *within* one day. The daily statement is the one place a
# visitor count is honest, and it is grouped by DATE(created_at).
_STATEMENTS = {
    "totals": """
        SELECT COUNT(*) views, SUM(is_load) loads, COUNT(DISTINCT load_id) sessions,
               COUNT(DISTINCT CASE WHEN standalone = 1 THEN load_id END) pwa_sessions,
               COUNT(DISTINCT DATE(created_at)) days
        FROM page_view WHERE created_at >= %s
    """,
    "daily": """
        SELECT DATE(created_at) d, SUM(is_load) loads, COUNT(*) views,
               COUNT(DISTINCT load_id) sessions, COUNT(DISTINCT visitor) visitors
        FROM page_view WHERE created_at >= %s
        GROUP BY d ORDER BY d
    """,
    "hourly": """
        SELECT HOUR(created_at) h, COUNT(*) views
        FROM page_view WHERE created_at >= %s GROUP BY h ORDER BY h
    """,
    "views": """
        SELECT `view`, SUM(is_load) loads, SUM(1 - is_load) switches, COUNT(*) views
        FROM page_view WHERE created_at >= %s
        GROUP BY `view` ORDER BY views DESC
    """,
    "devices": """
        SELECT device, COALESCE(os, 'n/d') os, COALESCE(browser, 'n/d') browser,
               COUNT(DISTINCT load_id) sessions, COUNT(*) views
        FROM page_view WHERE created_at >= %s
        GROUP BY device, os, browser ORDER BY sessions DESC, device LIMIT 10
    """,
    "screens": """
        SELECT CASE WHEN screen_w IS NULL THEN 'n/d'
                    WHEN screen_w < 380  THEN '<380'
                    WHEN screen_w < 430  THEN '380-429'
                    WHEN screen_w < 768  THEN '430-767'
                    WHEN screen_w < 1280 THEN '768-1279'
                    ELSE '>=1280' END bucket,
               MIN(screen_w) mn, COUNT(DISTINCT load_id) sessions
        FROM page_view WHERE created_at >= %s
        GROUP BY bucket ORDER BY mn LIMIT 6
    """,
    "langs": """
        SELECT COALESCE(lang, 'n/d') lang, COUNT(DISTINCT load_id) sessions
        FROM page_view WHERE created_at >= %s GROUP BY lang ORDER BY sessions DESC LIMIT 6
    """,
    "countries": """
        SELECT COALESCE(country, 'n/d') country, COUNT(DISTINCT load_id) sessions
        FROM page_view WHERE created_at >= %s GROUP BY country ORDER BY sessions DESC LIMIT 5
    """,
    "referrers": """
        SELECT COALESCE(referrer, '(direto)') referrer, COUNT(DISTINCT load_id) sessions
        FROM page_view WHERE created_at >= %s GROUP BY referrer ORDER BY sessions DESC LIMIT 6
    """,
    "paths": """
        SELECT path, COUNT(*) views, COUNT(DISTINCT load_id) sessions
        FROM page_view WHERE created_at >= %s GROUP BY path ORDER BY views DESC LIMIT 6
    """,
}

# Added by the landing app's 002 migration. Absent until it runs, so every one
# of these is gated behind information_schema detection rather than assumed.
_OPTIONAL_COLUMNS = (
    "os_version",
    "browser_version",
    "model",
    "arch",
    "bitness",
    "cpu_cores",
    "device_memory",
)

# `ORDER BY col + 0` because the version columns are VARCHAR, where "9" would
# otherwise sort above "10".
_OPTIONAL_STATEMENTS = {
    "os_versions": (
        "os_version",
        """
        SELECT COALESCE(os, 'n/d') os, os_version, COUNT(DISTINCT load_id) sessions
        FROM page_view WHERE created_at >= %s AND os_version IS NOT NULL
        GROUP BY os, os_version ORDER BY os, os_version + 0 DESC LIMIT 10
        """,
    ),
    "browser_versions": (
        "browser_version",
        """
        SELECT COALESCE(browser, 'n/d') browser, browser_version,
               COUNT(DISTINCT load_id) sessions
        FROM page_view WHERE created_at >= %s AND browser_version IS NOT NULL
        GROUP BY browser, browser_version ORDER BY browser, browser_version + 0 DESC LIMIT 10
        """,
    ),
    "models": (
        "model",
        """
        SELECT model val, COUNT(DISTINCT load_id) sessions
        FROM page_view WHERE created_at >= %s AND model IS NOT NULL
        GROUP BY val ORDER BY sessions DESC LIMIT 8
        """,
    ),
    "arch": (
        "arch",
        """
        SELECT arch val, COUNT(DISTINCT load_id) sessions
        FROM page_view WHERE created_at >= %s AND arch IS NOT NULL
        GROUP BY val ORDER BY sessions DESC LIMIT 6
        """,
    ),
    "bitness": (
        "bitness",
        """
        SELECT bitness val, COUNT(DISTINCT load_id) sessions
        FROM page_view WHERE created_at >= %s AND bitness IS NOT NULL
        GROUP BY val ORDER BY sessions DESC LIMIT 6
        """,
    ),
    "cpu_cores": (
        "cpu_cores",
        """
        SELECT cpu_cores val, COUNT(DISTINCT load_id) sessions
        FROM page_view WHERE created_at >= %s AND cpu_cores IS NOT NULL
        GROUP BY val ORDER BY val LIMIT 8
        """,
    ),
    "device_memory": (
        "device_memory",
        """
        SELECT device_memory val, COUNT(DISTINCT load_id) sessions
        FROM page_view WHERE created_at >= %s AND device_memory IS NOT NULL
        GROUP BY val ORDER BY val LIMIT 6
        """,
    ),
}

_DETECT_COLUMNS = """
    SELECT COLUMN_NAME FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'page_view' AND COLUMN_NAME IN %s
"""


class PageViewStats(LoggingMixin):
    """Collects the landing app's page views. Read-only, one query per section."""

    def __init__(self, poliswag):
        self.poliswag = poliswag
        # Connected on first use, not at boot: DatabaseConnector.__init__
        # connects eagerly and re-raises, and the dev mock database has no
        # pogoleiria schema — an eager connector would take the bot down with
        # it instead of failing one command.
        self._db = None

    def _connector(self):
        if self._db is None:
            self._db = DatabaseConnector(Config.DB_POGOLEIRIA)
        return self._db

    async def collect(self, since):
        """Raw rows per section. No formatting, no Discord types."""
        db = self._connector()
        available = await self._available_columns(db)

        stats = {}
        for name, sql in _STATEMENTS.items():
            stats[name] = await db.get_data_from_database(sql, params=(since,))
        for name, (column, sql) in _OPTIONAL_STATEMENTS.items():
            if column in available:
                stats[name] = await db.get_data_from_database(sql, params=(since,))
        return stats

    async def _available_columns(self, db):
        """Which of the 002 columns exist yet. pymysql expands the tuple for IN."""
        rows = await db.get_data_from_database(
            _DETECT_COLUMNS, params=(Config.DB_POGOLEIRIA, _OPTIONAL_COLUMNS)
        )
        return {row["COLUMN_NAME"] for row in rows}
