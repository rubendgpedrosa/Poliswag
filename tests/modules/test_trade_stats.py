"""TradeStats.collect: the !stats aggregates over the shared trades tables."""

import pytest

from modules.trade_stats import TradeStats
from tests.fakes import FakeHandle


def collect_db(fetchone=None, fetchall=None):
    handle = FakeHandle()
    cursor = handle.cursor
    cursor.fetchone.return_value = fetchone
    cursor.fetchall.return_value = fetchall
    return handle, cursor


class TestCollect:
    async def test_counts_entries_users_and_sides(self):
        db, cursor = collect_db(
            {
                "entries": 10,
                "users": 4,
                "have_entries": 6,
                "want_entries": 4,
                "have_users": 3,
                "want_users": 2,
            }
        )
        assert await TradeStats(db).collect("s", "u") == {
            "entries": 10,
            "users": 4,
            "have_entries": 6,
            "want_entries": 4,
            "have_users": 3,
            "want_users": 2,
        }
        assert cursor.execute.call_args_list[0].args[0] == (
            "SET SESSION max_statement_time = 5"
        )
        assert cursor.execute.call_args_list[1].args[1] == ("s", "u")
        # One unit, with the old connect(dict_rows=True) options: the defaults.
        ((_work, args, options),) = db.units
        assert args == ("s", "u")
        assert options == {}

    async def test_nulls_read_as_zero(self):
        db, _ = collect_db({})
        assert await TradeStats(db).collect("s", "u") == {
            "entries": 0,
            "users": 0,
            "have_entries": 0,
            "want_entries": 0,
            "have_users": 0,
            "want_users": 0,
        }

    async def test_a_read_failure_reaches_the_caller(self):
        # The unit closes the connection; the error still reaches !stats.
        db, cursor = collect_db()
        cursor.execute.side_effect = RuntimeError("down")
        with pytest.raises(RuntimeError):
            await TradeStats(db).collect("s", "u")


class TestCollectHundo:
    async def test_an_empty_room_has_no_alerts(self):
        db, cursor = collect_db(fetchall=[])
        assert await TradeStats(db).collect_hundo() == {
            "active": 0,
            "areas": {"leiria": 0, "marinha": 0},
            "waiting": 0,
            "dms_closed": 0,
            "unhealthy": 0,
        }
        ((_work, args, options),) = db.units
        assert options == {}


class TestCollectHundoActiveRow:
    async def test_an_active_row_counts_areas(self):
        db, cursor = collect_db(
            fetchall=[
                {
                    "collecting": "hundo",
                    "left_at": None,
                    "hundo_dms": 1,
                    "hundo_areas": "leiria",
                    "hundo_settings_revision": 2,
                    "hundo_confirmed_revision": 2,
                    "hundo_dm_refused_revision": 0,
                    "hundo_confirmed_setting": 1,
                    "hundo_health_revision": 0,
                    "hundo_health_state": "ready",
                }
            ]
        )
        assert await TradeStats(db).collect_hundo() == {
            "active": 1,
            "areas": {"leiria": 1, "marinha": 0},
            "waiting": 0,
            "dms_closed": 0,
            "unhealthy": 0,
        }
