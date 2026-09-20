"""Tests for modules.page_view_stats.

The period helpers are pure and get a fixed clock injected. The collector
talks to a mocked DatabaseConnector, so nothing here needs a live schema.
"""

from datetime import datetime, timezone

import pytest

from modules.page_view_stats import resolve_period, since_for

from unittest.mock import MagicMock, patch
import sqlite3
import threading

from modules.page_view_stats import (
    PageViewStats,
    _DIMENSIONS,
    _STATEMENTS,
    dimension_sql,
)

NOW = datetime(2026, 9, 19, 14, 30, 0, tzinfo=timezone.utc)


class TestResolvePeriod:
    def test_defaults_to_a_week(self):
        assert resolve_period(None) == 7

    @pytest.mark.parametrize("arg", ["1d", "1", "hoje", "today", "HOJE"])
    def test_reads_a_single_day(self, arg):
        assert resolve_period(arg) == 1

    @pytest.mark.parametrize(
        "arg,days", [("7d", 7), ("7", 7), ("30d", 30), ("365d", 365)]
    )
    def test_reads_a_span(self, arg, days):
        assert resolve_period(arg) == days

    @pytest.mark.parametrize("arg", ["all", "tudo", "sempre", "ALL"])
    def test_reads_no_bound(self, arg):
        assert resolve_period(arg) is None

    @pytest.mark.parametrize("arg", ["0d", "400d", "abc", "", "-3", "7 d"])
    def test_refuses_what_it_cannot_read(self, arg):
        with pytest.raises(ValueError):
            resolve_period(arg)


class TestSinceFor:
    def test_no_bound_reaches_the_epoch(self):
        assert since_for(None, now=NOW) == datetime(1970, 1, 1)

    def test_one_day_starts_at_todays_utc_midnight(self):
        assert since_for(1, now=NOW) == datetime(2026, 9, 19, 0, 0, 0)

    def test_seven_days_counts_today_as_one_of_them(self):
        assert since_for(7, now=NOW) == datetime(2026, 9, 13, 0, 0, 0)

    def test_is_naive_so_pymysql_sends_a_bare_datetime(self):
        # The column is DATETIME and the server runs UTC; an aware value would
        # be serialized with an offset the column cannot hold.
        assert since_for(7, now=NOW).tzinfo is None

    def test_ignores_the_containers_local_timezone(self, monkeypatch):
        # The container runs TZ=Europe/Lisbon (WEST, UTC+1) while the DB is UTC.
        monkeypatch.setenv("TZ", "Europe/Lisbon")
        late = datetime(2026, 9, 19, 23, 30, tzinfo=timezone.utc)
        assert since_for(1, now=late) == datetime(2026, 9, 19, 0, 0, 0)


class SQLFixture:
    """Run the real aggregation SQL over synthetic events, never production DBs."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.create_function("HOUR", 1, lambda value: int(value[11:13]))
        self.columns = [
            "created_at",
            "load_id",
            "visitor",
            "view",
            "is_load",
            "standalone",
            "referrer",
            "event_name",
            "schema_version",
            "event_id",
            "device",
            "os",
            "browser",
            "screen_w",
            "lang",
            "country",
            "os_version",
            "browser_version",
            "model",
            "arch",
            "bitness",
            "cpu_cores",
            "device_memory",
        ]
        self.db.execute("CREATE TABLE page_view (" + ",".join(self.columns) + ")")
        self.sql = []

    def add(self, **patch):
        row = dict(
            created_at="2026-09-19 12:00:00",
            load_id="a",
            visitor="visitor-a",
            view="home",
            is_load=1,
            standalone=0,
            referrer="discord.com",
            event_name="tool_view",
            schema_version=2,
            device="mobile",
            os="Android",
            browser="Chrome",
            model="Pixel",
            arch="arm",
            bitness="64",
            cpu_cores=8,
            device_memory=4,
        )
        row.update(patch)
        self.db.execute(
            "INSERT INTO page_view ("
            + ",".join(row)
            + ") VALUES ("
            + ",".join("?" for _ in row)
            + ")",
            list(row.values()),
        )

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        if "information_schema" in sql:
            self.rows = [{"COLUMN_NAME": c} for c in self.columns]
        else:
            self.rows = [
                dict(r) for r in self.db.execute(sql.replace("%s", "?"), params)
            ]
            for row in self.rows:
                for key in ("first_seen", "last_seen"):
                    if row.get(key):
                        row[key] = datetime.fromisoformat(row[key])

    def fetchall(self):
        return self.rows


SINCE = datetime(2026, 9, 19)
UNTIL = datetime(2026, 9, 20)


@pytest.fixture
def data():
    fixture = SQLFixture()
    yield fixture
    fixture.db.close()


def collect(data, detail="export"):
    return PageViewStats(None)._read(data, SINCE, UNTIL, detail)


def test_referral_partition_counts_entries_once_not_switches(data):
    data.add()
    data.add(view="quests", is_load=0, referrer=None)
    out = collect(data)
    assert out["totals"][0]["sessions"] == 1
    assert out["totals"][0]["entries"] == 1
    assert out["referrers"] == [{"referrer": "discord.com", "sessions": 1}]


def test_continuation_without_entry_is_not_invented_direct_traffic(data):
    data.add(is_load=0, referrer=None)
    out = collect(data)
    assert out["totals"][0]["sessions"] == 1
    assert out["totals"][0]["entries"] == 0
    assert out["referrers"] == []


def test_actions_do_not_inflate_traffic_or_loads(data):
    data.add()
    data.add(event_name="friend_code_copied", is_load=0, view="trades")
    out = collect(data)
    assert out["totals"][0]["views"] == 1
    assert out["actions"][0]["events"] == 1


def test_end_is_exclusive_in_every_breakdown(data):
    data.add()
    data.add(load_id="future", created_at="2026-09-20 00:00:00")
    out = collect(data)
    assert out["totals"][0]["sessions"] == 1
    assert out["models"] == [{"model": "Pixel", "sessions": 1}]
    assert out["health"][0]["last_seen"] == datetime(2026, 9, 19, 12)


def test_dimensions_partition_documents_and_keep_all_values_for_coverage(data):
    for i in range(12):
        data.add(load_id=str(i), model=f"Model-{i}")
    data.add(load_id="0", model="Model-Z", is_load=0)
    out = collect(data)
    assert len(out["models"]) == 12
    for dimension in _DIMENSIONS:
        assert sum(row["sessions"] for row in out[dimension]) == 12


def test_daily_hash_counts_only_its_day_and_documents_can_span_midnight(data):
    data.add(created_at="2026-09-19 23:59:00")
    data.add(created_at="2026-09-20 00:01:00", visitor="next-day", is_load=0)
    out = PageViewStats(None)._read(data, SINCE, datetime(2026, 9, 21), "summary")
    assert out["totals"][0]["sessions"] == 1
    assert [r["visitors"] for r in out["daily"]] == [1, 1]


def test_previous_window_matches_elapsed_time_of_day(data):
    # Only the export carries a baseline now: the live report computes its own
    # comparison, and the Discord snapshot shows no deltas.
    out = PageViewStats(None)._read(data, SINCE, datetime(2026, 9, 19, 8), "export")
    assert out["previous_start"] == datetime(2026, 9, 18)
    assert out["previous_end"] == datetime(2026, 9, 18, 8)


def test_the_snapshot_does_not_pay_for_a_baseline_or_hardware_it_never_shows(data):
    data.add()
    out = PageViewStats(None)._read(data, SINCE, datetime(2026, 9, 19, 8), "summary")
    assert "previous" not in out
    assert "models" not in out
    assert "browser_versions" not in out
    # What the embed actually reads.
    assert {"totals", "daily", "views"} <= set(out)


def test_legacy_schema_has_no_optional_column_queries(data):
    for column in ("event_id", "event_name", "schema_version", "model"):
        data.columns.remove(column)
    data.add()
    out = collect(data)
    assert not out["schema_v2"]
    assert "actions" not in out
    assert "models" not in out


def test_all_queries_use_bound_placeholders():
    for sql in [
        *_STATEMENTS.values(),
        *[dimension_sql(c) for c in _DIMENSIONS.values()],
    ]:
        assert sql.count("%") == sql.count("%s") == 2


async def test_connection_work_is_off_loop_and_cached_results_are_isolated():
    service = PageViewStats(None)
    main_thread = threading.get_ident()
    threads = []

    def read(since, until, detail):
        threads.append(threading.get_ident())
        return {"as_of": until, "totals": [{"views": 1}]}

    with patch.object(service, "_collect_sync", side_effect=read) as query:
        a = await service.collect(SINCE, UNTIL)
        a["totals"][0]["views"] = 100
        b = await service.collect(SINCE, UNTIL)
    assert query.call_count == 1
    assert b["totals"][0]["views"] == 1
    assert threads[0] != main_thread


def test_snapshot_is_read_only_and_connection_closes_on_failure():
    db = MagicMock()
    with patch(
        "modules.page_view_stats.pymysql.connect", return_value=db
    ), patch.object(PageViewStats, "_read", side_effect=RuntimeError("test")):
        with pytest.raises(RuntimeError):
            PageViewStats(None)._collect_sync(SINCE, UNTIL, "summary")
    sql = [
        call.args[0]
        for call in db.cursor.return_value.__enter__.return_value.execute.call_args_list
    ]
    assert "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY" in sql
    db.rollback.assert_called_once()
    db.close.assert_called_once()
