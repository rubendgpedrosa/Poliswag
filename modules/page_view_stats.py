"""Reads pogoleiria.page_view — the landing app's own analytics table.

Owned by apps/landing in the PoGoLeiria repo (db/001_page_view.sql,
db/002_page_view_device_detail.sql, db/003_analytics_events.sql); this module only ever reads it.

Two rules run through everything here:

* **No `%` in any SQL but `%s`.** Passing params to
  `cursor.execute` makes pymysql apply `query % args`, so a
  `DATE_FORMAT(created_at, '%Y-%m-%d')` would raise "unsupported format
  character". Dates are grouped with `DATE()`/`HOUR()` and formatted in Python.
* **UTC everywhere.** `created_at` is written by a server running UTC while
  this container is on Europe/Lisbon. A local `now()` would push the period
  boundary an hour into the future and drop the oldest day's first hour.
"""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import time

import pymysql

from modules.config import Config
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


# Period reach is documents (COUNT(DISTINCT load_id)) or tool openings, never
# COUNT(DISTINCT visitor): the visitor hash rotates at UTC midnight, so it only
# counts uniques *within* one day. The daily statement is the one place a
# visitor count is honest, and it is grouped by DATE(created_at).
# Internal legacy aliases (sessions/views) stay in the raw contract. The report
# deliberately labels them documents/tool openings, never activity sessions.
_WINDOW = "created_at >= %s AND created_at < %s"
_STATEMENTS = {
    "totals": f"""
        SELECT COUNT(*) views, SUM(is_load) loads,
          COUNT(DISTINCT load_id) sessions,
          COUNT(DISTINCT CASE WHEN is_load = 1 THEN load_id END) entries,
          COUNT(DISTINCT CASE WHEN standalone = 1 THEN load_id END) pwa_sessions,
          COUNT(DISTINCT DATE(created_at)) days,
          SUM(visitor IS NULL) unknown_visitor_events
        FROM page_view WHERE {_WINDOW}
    """,
    "daily": f"""
        SELECT DATE(created_at) d, SUM(is_load) loads, COUNT(*) views,
          COUNT(DISTINCT load_id) sessions, COUNT(DISTINCT visitor) visitors
        FROM page_view WHERE {_WINDOW} GROUP BY d ORDER BY d
    """,
    "views": f"""
        SELECT `view`, SUM(is_load) loads, SUM(1-is_load) switches,
          COUNT(*) views, COUNT(DISTINCT load_id) sessions
        FROM page_view WHERE {_WINDOW} GROUP BY `view` ORDER BY sessions DESC, `view`
    """,
    "hourly": f"""
        SELECT HOUR(created_at) h, COUNT(*) views
        FROM page_view WHERE {_WINDOW} GROUP BY h ORDER BY h
    """,
    "referrers": f"""
        SELECT COALESCE(referrer, '(direto/desconhecido)') referrer, COUNT(*) sessions
        FROM (SELECT load_id, MAX(CASE WHEN is_load = 1 THEN referrer END) referrer
          FROM page_view WHERE {_WINDOW}
          GROUP BY load_id HAVING MAX(is_load) = 1) entries
        GROUP BY referrer ORDER BY sessions DESC, referrer
    """,
}
# Every dimension is a partition of DOCUMENTS, not of events. MAX supplies a
# deterministic representative if legacy/spoofed events disagree within a load.
_DIMENSIONS = {
    "devices": ("device", "os", "browser"),
    "screens": ("screen_w",),
    "langs": ("lang",),
    "countries": ("country",),
    "os_versions": ("os", "os_version"),
    "browser_versions": ("browser", "browser_version"),
    "models": ("model",),
    "arch": ("arch",),
    "bitness": ("bitness",),
    "cpu_cores": ("cpu_cores",),
    "device_memory": ("device_memory",),
}


def dimension_sql(columns):
    names = ", ".join(f"`{c}`" for c in columns)
    representatives = ", ".join(f"MAX(`{c}`) `{c}`" for c in columns)
    return f"""SELECT {names}, COUNT(*) sessions FROM
      (SELECT load_id, {representatives} FROM page_view
       WHERE {_WINDOW} GROUP BY load_id) documents
      GROUP BY {names} ORDER BY sessions DESC, {names}"""


class PageViewStats(LoggingMixin):
    """One read-only snapshot off the bot loop; bounded cache and DB timeouts."""

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self._lock = asyncio.Lock()
        self._cache = {}

    async def collect(self, since, until=None, detail="summary"):
        until = until or datetime.now(timezone.utc).replace(tzinfo=None)
        key = (since, detail)
        async with self._lock:
            cached = self._cache.get(key)
            if (
                cached
                and time.monotonic() - cached[0] < 60
                and 0 <= (until - cached[1]["as_of"]).total_seconds() < 60
            ):
                return deepcopy(cached[1])
            # Connection creation, queries, transaction and close all occur on
            # one worker thread. No shared PyMySQL connection crosses callers.
            result = await asyncio.to_thread(self._collect_sync, since, until, detail)
            if len(self._cache) >= 8:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (time.monotonic(), deepcopy(result))
            return result

    def _collect_sync(self, since, until, detail):
        db = pymysql.connect(
            host=Config.DB_HOST,
            port=Config.DB_PORT,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            database=Config.DB_POGOLEIRIA,
            connect_timeout=5,
            read_timeout=10,
            write_timeout=5,
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with db.cursor() as cursor:
                # Per-statement server budget also bounds CPU use after clients
                # disconnect. This reader targets the existing MariaDB server.
                cursor.execute("SET SESSION max_statement_time = 8")
                cursor.execute("SET SESSION time_zone = '+00:00'")
                cursor.execute(
                    "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                )
                cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                return self._read(cursor, since, until, detail)
        finally:
            try:
                db.rollback()
            finally:
                db.close()

    def _read(self, cursor, since, until, detail):
        deadline = time.monotonic() + 20

        def query(sql, params=()):
            if time.monotonic() >= deadline:
                raise TimeoutError("Analytics report exceeded its query budget")
            cursor.execute(sql, params)
            return list(cursor.fetchall())

        columns = {
            r["COLUMN_NAME"]
            for r in query(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'page_view'",
                (Config.DB_POGOLEIRIA,),
            )
        }
        event_filter = (
            " AND event_name = 'tool_view'" if "event_name" in columns else ""
        )

        def traffic(sql, start=since, end=until):
            return query(sql.replace(_WINDOW, _WINDOW + event_filter), (start, end))

        out = {
            "as_of": until,
            "since": since,
            "schema_v2": {"event_id", "event_name", "schema_version", "occurred_at"}
            <= columns,
        }
        out["health"] = query(
            "SELECT MIN(created_at) first_seen, MAX(created_at) last_seen FROM page_view WHERE created_at < %s",
            (until,),
        )
        names = ["totals", "daily", "views"]
        if detail in {"sources", "export"}:
            names += ["referrers"]
        if detail in {"tools", "export"}:
            names += ["hourly"]
        for name in names:
            out[name] = traffic(_STATEMENTS[name])
        # Same number of calendar days, ending at the same elapsed time of day.
        # Only the export needs it now; the live report computes its own
        # comparison, and the Discord snapshot shows no deltas.
        if detail == "export" and since.year != 1970:
            span = timedelta(days=(until.date() - since.date()).days + 1)
            previous_start = since - span
            previous_end = until - span
            out["previous"] = traffic(
                _STATEMENTS["totals"], previous_start, previous_end
            )
            out["previous_end"] = previous_end
            out["previous_start"] = previous_start
        # The summary feeds a three-line Discord embed. It used to pull all
        # eleven hardware dimensions to render none of them.
        if detail in {"devices", "export"}:
            for name, cols in _DIMENSIONS.items():
                if set(cols) <= columns:
                    out[name] = traffic(dimension_sql(cols))
        if "event_name" in columns:
            out["actions"] = query(
                f"""
                SELECT `view`, event_name, COUNT(*) events, COUNT(DISTINCT load_id) documents
                FROM page_view WHERE {_WINDOW} AND event_name != 'tool_view'
                GROUP BY `view`, event_name ORDER BY events DESC, `view`, event_name
            """,
                (since, until),
            )
        if "schema_version" in columns:
            out["versions"] = query(
                f"""
                SELECT schema_version, COUNT(*) events FROM page_view
                WHERE {_WINDOW} GROUP BY schema_version ORDER BY schema_version
            """,
                (since, until),
            )
        return out
