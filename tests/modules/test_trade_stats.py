"""TradeStats.collect: the !stats aggregates over the shared trades tables."""

from unittest.mock import MagicMock, patch

import pytest

from modules.trade_stats import TradeStats


def collect_db(fetchone=None, fetchall=None):
    db = MagicMock()
    cursor = db.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = fetchone
    cursor.fetchall.return_value = fetchall
    return db, cursor


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
        with patch("modules.trade_stats.connect", return_value=db):
            assert await TradeStats().collect("s", "u") == {
                "entries": 10,
                "users": 4,
                "have_entries": 6,
                "want_entries": 4,
                "have_users": 3,
                "want_users": 2,
            }
        assert cursor.execute.call_args_list[1].args[1] == ("s", "u")
        db.close.assert_called_once()

    async def test_nulls_read_as_zero(self):
        db, _ = collect_db({})
        with patch("modules.trade_stats.connect", return_value=db):
            assert await TradeStats().collect("s", "u") == {
                "entries": 0,
                "users": 0,
                "have_entries": 0,
                "want_entries": 0,
                "have_users": 0,
                "want_users": 0,
            }

    async def test_a_read_failure_closes_the_connection(self):
        db, cursor = collect_db()
        cursor.execute.side_effect = RuntimeError("down")
        with patch("modules.trade_stats.connect", return_value=db):
            with pytest.raises(RuntimeError):
                await TradeStats().collect("s", "u")
        db.close.assert_called_once()


class TestCollectHundo:
    async def test_an_empty_room_has_no_alerts(self):
        db, cursor = collect_db(fetchall=[])
        with patch("modules.trade_stats.connect", return_value=db):
            assert await TradeStats().collect_hundo() == {
                "active": 0,
                "leiria": 0,
                "marinha": 0,
                "waiting": 0,
                "dms_closed": 0,
                "unhealthy": 0,
            }
        db.close.assert_called_once()


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
        with patch("modules.trade_stats.connect", return_value=db):
            assert await TradeStats().collect_hundo() == {
                "active": 1,
                "leiria": 1,
                "marinha": 0,
                "waiting": 0,
                "dms_closed": 0,
                "unhealthy": 0,
            }
