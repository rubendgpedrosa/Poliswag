"""Tests for modules.page_view_stats.

The period helpers are pure and get a fixed clock injected. The collector
talks to a mocked DatabaseConnector, so nothing here needs a live schema.
"""

from datetime import datetime, timezone

import pytest

from modules.page_view_stats import resolve_period, since_for

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


from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from modules.page_view_stats import (  # noqa: E402
    _OPTIONAL_COLUMNS,
    _OPTIONAL_STATEMENTS,
    _STATEMENTS,
    PageViewStats,
)

SINCE = datetime(2026, 9, 13)


@pytest.fixture
def stats():
    poliswag = MagicMock()
    return PageViewStats(poliswag)


def fake_connector(available=()):
    db = AsyncMock()

    async def answer(query, params=None, **kwargs):
        if "information_schema" in query:
            return [{"COLUMN_NAME": name} for name in available]
        return [{"n": 1}]

    db.get_data_from_database.side_effect = answer
    return db


class TestStatementSafety:
    @pytest.mark.parametrize("name,sql", sorted(_STATEMENTS.items()))
    def test_every_placeholder_is_a_bare_param(self, name, sql):
        # pymysql runs `query % args` whenever params are passed, so a stray
        # % (from DATE_FORMAT, say) raises "unsupported format character".
        assert sql.count("%") == sql.count("%s")

    @pytest.mark.parametrize("name,sql", sorted(_STATEMENTS.items()))
    def test_every_statement_takes_exactly_the_since_param(self, name, sql):
        assert sql.count("%s") == 1

    @pytest.mark.parametrize("name", sorted(_OPTIONAL_STATEMENTS))
    def test_optional_statements_are_equally_safe(self, name):
        _column, sql = _OPTIONAL_STATEMENTS[name]
        assert sql.count("%") == sql.count("%s") == 1

    def test_optional_columns_are_the_seven_the_sibling_spec_defines(self):
        assert _OPTIONAL_COLUMNS == (
            "os_version",
            "browser_version",
            "model",
            "arch",
            "bitness",
            "cpu_cores",
            "device_memory",
        )

    def test_every_optional_statement_guards_its_own_column(self):
        for _name, (column, sql) in _OPTIONAL_STATEMENTS.items():
            assert f"{column} IS NOT NULL" in sql


class TestLazyConnection:
    def test_building_the_service_opens_no_connection(self, stats):
        with patch("modules.page_view_stats.DatabaseConnector") as connector:
            assert connector.call_count == 0
        assert stats._db is None

    async def test_the_first_collect_connects_and_the_second_reuses(self, stats):
        with patch(
            "modules.page_view_stats.DatabaseConnector", return_value=fake_connector()
        ) as connector:
            await stats.collect(SINCE)
            await stats.collect(SINCE)
        assert connector.call_count == 1

    async def test_a_dead_connection_is_the_cogs_problem(self, stats):
        with patch(
            "modules.page_view_stats.DatabaseConnector",
            side_effect=RuntimeError("down"),
        ):
            with pytest.raises(RuntimeError):
                await stats.collect(SINCE)


class TestCollect:
    async def test_runs_every_always_on_statement_with_the_period(self, stats):
        db = fake_connector()
        with patch("modules.page_view_stats.DatabaseConnector", return_value=db):
            out = await stats.collect(SINCE)
        assert set(_STATEMENTS) <= set(out)
        for call in db.get_data_from_database.await_args_list:
            if "information_schema" in call.args[0]:
                continue
            assert call.kwargs["params"] == (SINCE,)

    async def test_skips_every_optional_statement_when_the_columns_are_absent(
        self, stats
    ):
        db = fake_connector(available=())
        with patch("modules.page_view_stats.DatabaseConnector", return_value=db):
            out = await stats.collect(SINCE)
        assert not set(_OPTIONAL_STATEMENTS) & set(out)

    async def test_runs_only_the_optional_statements_whose_column_exists(self, stats):
        db = fake_connector(available=("model",))
        with patch("modules.page_view_stats.DatabaseConnector", return_value=db):
            out = await stats.collect(SINCE)
        assert "models" in out
        assert "os_versions" not in out

    async def test_runs_them_all_once_the_alter_has_run(self, stats):
        db = fake_connector(available=_OPTIONAL_COLUMNS)
        with patch("modules.page_view_stats.DatabaseConnector", return_value=db):
            out = await stats.collect(SINCE)
        assert set(_OPTIONAL_STATEMENTS) <= set(out)

    async def test_asks_for_the_optional_columns_as_one_tuple_param(self, stats):
        db = fake_connector()
        with patch("modules.page_view_stats.DatabaseConnector", return_value=db):
            await stats.collect(SINCE)
        detect = [
            c
            for c in db.get_data_from_database.await_args_list
            if "information_schema" in c.args[0]
        ][0]
        assert detect.kwargs["params"][1] == _OPTIONAL_COLUMNS
