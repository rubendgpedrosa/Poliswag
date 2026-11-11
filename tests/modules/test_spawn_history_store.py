import gzip
import json
import time
from unittest.mock import MagicMock

import pymysql
import pytest

from modules import database_connector, spawn_history
from modules.config import Config
from modules.database_connector import DatabaseConnector
from modules.shiny_roll import is_shiny_at, shiny_draw


async def run_unit(monkeypatch, connection, work, *args, **options):
    """Run work through the real DatabaseConnector.unit on a mock connection.

    Returns (result, opened): opened is the patched connect(), so a test can
    check the options the unit asked for; commit/rollback/close are unit()'s.
    """
    db = DatabaseConnector.__new__(DatabaseConnector)
    db.database = Config.DB_POLISWAG
    opened = MagicMock(return_value=connection)
    monkeypatch.setattr(database_connector, "connect", opened)
    return await db.unit(work, *args, **options), opened


@pytest.mark.parametrize("hours", [0, 49, -1, True, "24", 1.5, None])
def test_invalid_period_never_touches_the_connection(hours):
    # The callers also check hours_value before unit(), so no connection is
    # opened at all (tests/cogs/test_spawn_history.py).
    connection = MagicMock()
    for function in (
        spawn_history.status,
        spawn_history.export_json,
        spawn_history.read_leaderboard,
    ):
        with pytest.raises(ValueError):
            function(connection, hours)
    connection.cursor.assert_not_called()


async def test_failed_collector_rolls_back_and_closes(monkeypatch):
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.execute.side_effect = (
        RuntimeError("failure")
    )
    with pytest.raises(RuntimeError):
        await run_unit(monkeypatch, connection, spawn_history.capture, read_timeout=15)
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()
    connection.commit.assert_not_called()


def test_first_poll_is_not_historical_coverage():
    cursor = MagicMock()
    cursor.fetchall.side_effect = [[{"started_at": 1000, "last_capture_at": 1000}], []]
    assert not spawn_history.window_metadata(cursor, 24)["complete_window"]


def test_no_archive_is_unavailable():
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    assert spawn_history.window_metadata(cursor, 24) == {
        "available": False,
        "complete_window": False,
        "hours": 24,
    }


NOW = 1_750_000_000


def make_connection(fetchone=None, fetchall=None, rows=None):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = fetchone
    cursor.fetchall.side_effect = fetchall
    if rows is not None:
        cursor.__iter__.return_value = iter(rows)
    return connection, cursor


def test_valid_period_is_returned_unchanged():
    assert spawn_history.hours_value(1) == 1
    assert spawn_history.hours_value(48) == 48


def test_capture_sql_upserts_scanner_rows_by_encounter():
    sql = spawn_history.capture_sql()
    assert "INSERT INTO spawn_history" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "first_seen_at = LEAST(" in sql
    assert "last_captured_at = VALUES(last_captured_at)" in sql


class TestCapture:
    async def run_capture(self, monkeypatch, previous, now=NOW):
        connection, cursor = make_connection(
            fetchone=[{"now_utc": now}, {"last_capture_at": previous}]
        )
        result, connect = await run_unit(
            monkeypatch, connection, spawn_history.capture, read_timeout=15
        )
        assert result == now
        return connection, cursor, connect

    async def test_happy_path_writes_state_and_prunes(self, monkeypatch):
        now = NOW
        previous = now - 30
        cutoff = now - 48 * 3600
        connection, cursor, connect = await self.run_capture(monkeypatch, previous, now)
        # The options the old connect(DB_POLISWAG, dict_rows=True,
        # read_timeout=15) had, through the unit.
        connect.assert_called_once_with(
            Config.DB_POLISWAG, read_timeout=15, dict_rows=True, autocommit=False
        )
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        assert statements[:3] == [
            "SET SESSION time_zone = '+00:00'",
            "SET SESSION max_statement_time=10",
            "SET SESSION innodb_lock_wait_timeout=5",
        ]
        assert "INSERT IGNORE INTO spawn_history_gap" not in " ".join(statements)
        upsert = next(
            call
            for call in cursor.execute.call_args_list
            if "INSERT INTO spawn_history" in call.args[0]
        )
        assert upsert.args[1] == (now, now, now, cutoff, now)
        deletes = [
            call
            for call in cursor.execute.call_args_list
            if call.args[0].startswith("DELETE FROM")
        ]
        assert [(c.args[0], c.args[1]) for c in deletes] == [
            ("DELETE FROM spawn_history WHERE first_seen_at < %s", (cutoff,)),
            ("DELETE FROM spawn_history_gap WHERE ended_at < %s", (cutoff,)),
        ]
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()
        connection.close.assert_called_once()

    async def test_a_poll_gap_is_recorded(self, monkeypatch):
        now, previous = NOW, NOW - 91
        _, cursor, _ = await self.run_capture(monkeypatch, previous, now)
        gap = next(
            call
            for call in cursor.execute.call_args_list
            if "INSERT IGNORE INTO spawn_history_gap" in call.args[0]
        )
        assert gap.args[1] == (previous, now)

    async def test_a_backwards_clock_rolls_back(self, monkeypatch):
        connection, _ = make_connection(
            fetchone=[{"now_utc": NOW}, {"last_capture_at": NOW + 1}]
        )
        with pytest.raises(ValueError, match="backwards"):
            await run_unit(
                monkeypatch, connection, spawn_history.capture, read_timeout=15
            )
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        connection.close.assert_called_once()


class TestWindowMetadata:
    def test_a_complete_window(self, monkeypatch):
        end = int(time.time())
        cursor = MagicMock()
        cursor.fetchall.side_effect = [
            [{"started_at": 1, "last_capture_at": end}],
            [],
        ]
        metadata = spawn_history.window_metadata(cursor, 24)
        assert metadata["available"] is True
        assert metadata["complete_window"] is True
        assert metadata["collector_stale"] is False
        assert metadata["gaps"] == []
        assert "sem filtros" in metadata["note"]

    def test_gaps_make_the_window_partial(self, monkeypatch):
        end = int(time.time()) - 10_000
        gaps = [{"started_at": end - 100, "ended_at": end - 50}]
        cursor = MagicMock()
        cursor.fetchall.side_effect = [
            [{"started_at": 1, "last_capture_at": end}],
            gaps,
        ]
        metadata = spawn_history.window_metadata(cursor, 24)
        assert metadata["complete_window"] is False
        assert metadata["collector_stale"] is True
        assert metadata["gaps"] == gaps
        assert "parcial" in metadata["note"]


class TestReader:
    def test_reader_holds_a_consistent_snapshot(self):
        connection = MagicMock()
        cursor = spawn_history.reader(connection)
        assert cursor is connection.cursor.return_value
        connection.cursor.assert_called_once_with(pymysql.cursors.DictCursor)
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        assert "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY" in statements

    def test_streaming_reader_uses_a_server_side_cursor(self):
        connection = MagicMock()
        spawn_history.reader(connection, streaming=True)
        connection.cursor.assert_called_once_with(pymysql.cursors.SSDictCursor)

    async def test_a_reader_failure_closes_the_connection(self, monkeypatch):
        connection = MagicMock()
        connection.cursor.side_effect = RuntimeError("down")
        with pytest.raises(RuntimeError):
            await run_unit(
                monkeypatch, connection, spawn_history.status, 24, read_timeout=15
            )
        connection.close.assert_called_once()


class TestStatus:
    async def test_status_reports_stored_spawns(self, monkeypatch):
        end = int(time.time())
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.side_effect = [
            [{"started_at": 1, "last_capture_at": end}],
            [],
        ]
        cursor.fetchone.return_value = {"stored_spawns": 7}
        metadata, connect = await run_unit(
            monkeypatch, connection, spawn_history.status, 24, read_timeout=15
        )
        connect.assert_called_once_with(
            Config.DB_POLISWAG, read_timeout=15, dict_rows=True, autocommit=False
        )
        assert metadata["stored_spawns"] == 7
        assert metadata["available"] is True
        connection.close.assert_called_once()


def state_fetches(end, gaps=None, started=1):
    return [
        [{"started_at": started, "last_capture_at": end}],
        gaps if gaps is not None else [],
    ]


class TestExport:
    async def test_without_an_archive_the_export_refuses(self, monkeypatch):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = []
        with pytest.raises(ValueError, match="recolha"):
            await run_unit(
                monkeypatch, connection, spawn_history.export_json, 24, read_timeout=15
            )
        connection.close.assert_called_once()

    async def test_export_is_a_gzipped_schema_envelope(self, monkeypatch):
        end = int(time.time())
        rows = [
            {"encounter_id": "1", "pokemon_id": 25},
            {"encounter_id": "2", "pokemon_id": 129},
        ]
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.side_effect = state_fetches(end)
        cursor.__iter__.return_value = iter(rows)
        output, _ = await run_unit(
            monkeypatch, connection, spawn_history.export_json, 1, read_timeout=15
        )
        try:
            with gzip.open(output, mode="rt", encoding="utf-8") as zipped:
                payload = json.load(zipped)
        finally:
            output.close()
            connection.close.assert_called_once()
        assert payload["schema_version"] == 1
        assert payload["metadata"]["hours"] == 1
        assert payload["spawns"] == rows

    async def test_an_oversized_export_refuses(self, monkeypatch):
        end = int(time.time())
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.side_effect = state_fetches(end)
        cursor.__iter__.return_value = iter([{"encounter_id": "1"}])
        monkeypatch.setattr(spawn_history, "MAX_EXPORT_BYTES", 10)
        with pytest.raises(ValueError, match="8 MiB"):
            await run_unit(
                monkeypatch, connection, spawn_history.export_json, 1, read_timeout=15
            )


PLAYER_A = "0123456789abcdef"
PLAYER_B = "ffffffffffffffff"


def trainer_rows(names):
    return [{"trainer_name": name, "player_id": player} for name, player in names]


class TestLeaderboard:
    def run_leaderboard(self, monkeypatch, trainers, spawns, end=None, started=1):
        end = end if end is not None else int(time.time())
        if started == "end":
            started = end
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.side_effect = state_fetches(end, started=started) + [
            trainers,
            spawns,
        ]
        return spawn_history.build_leaderboard(
            spawn_history.read_leaderboard(connection, 1), 1
        )

    def test_without_an_archive_there_are_no_trainers(self, monkeypatch):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = []
        assert spawn_history.build_leaderboard(
            spawn_history.read_leaderboard(connection, 24), 24
        ) == {
            "metadata": {
                "available": False,
                "complete_window": False,
                "hours": 24,
            },
            "trainers": [],
        }

    def test_shundo_counts_and_sorts(self, monkeypatch):
        spawns = [
            {
                "encounter_id": 101,
                "pokemon_id": 25,
                "lat": 39.74,
                "lon": -8.8,
                "atk_iv": 15,
                "def_iv": 15,
                "sta_iv": 15,
                "shiny_odds": 1,
            },
            {
                "encounter_id": 102,
                "pokemon_id": 129,
                "lat": 39.74,
                "lon": -8.93,
                "atk_iv": 10,
                "def_iv": 12,
                "sta_iv": 14,
                "shiny_odds": 1,
            },
        ]
        result = self.run_leaderboard(
            monkeypatch,
            trainer_rows([("b trainer", PLAYER_A), ("a trainer", PLAYER_B)]),
            spawns,
        )
        assert result["eligible_spawns"] == 2
        assert result["expected_per_trainer"] == 2.0
        assert result["total_predicted_shinies"] == 4
        first, second = result["trainers"]
        assert (first["predicted_shinies"], second["predicted_shinies"]) == (2, 2)
        assert (first["trainer"], second["trainer"]) == ("a trainer", "b trainer")
        assert first["hundo_shinies"] == 1
        assert first["unique_species"] == 2
        assert first["areas"] == {"leiria": 1, "marinha": 1}
        assert first["shinies_per_hour"] == round(2 / 1, 3)
        base = sum(
            is_shiny_at(shiny_draw(spawn["encounter_id"], PLAYER_B), 512)
            for spawn in spawns
        )
        assert first["base_512_shinies"] == base

    def test_a_partial_window_has_no_hourly_rate(self, monkeypatch):
        end = int(time.time())
        result = self.run_leaderboard(
            monkeypatch,
            trainer_rows([("solo", PLAYER_A)]),
            [
                {
                    "encounter_id": 103,
                    "pokemon_id": 25,
                    "lat": 39.74,
                    "lon": -8.8,
                    "atk_iv": 15,
                    "def_iv": 15,
                    "sta_iv": 15,
                    "shiny_odds": 1,
                }
            ],
            end=end,
            started="end",
        )
        assert result["metadata"]["complete_window"] is False
        assert result["trainers"][0]["shinies_per_hour"] is None

    def test_more_than_100_trainers_is_refused(self, monkeypatch):
        trainers = trainer_rows([(f"t{i:03d}", PLAYER_A) for i in range(101)])
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.side_effect = state_fetches(int(time.time())) + [trainers]
        with pytest.raises(ValueError, match="100"):
            spawn_history.build_leaderboard(
                spawn_history.read_leaderboard(connection, 1), 1
            )

    def test_a_trainer_without_a_player_id_is_refused(self, monkeypatch):
        result_trainers = trainer_rows([("ghost", "not-an-id")])
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.side_effect = state_fetches(int(time.time())) + [
            result_trainers,
            [],
        ]
        with pytest.raises(ValueError, match="ID"):
            spawn_history.build_leaderboard(
                spawn_history.read_leaderboard(connection, 1), 1
            )


async def test_ranking_runs_after_the_snapshot_connection_closes(monkeypatch):
    import threading

    connection = MagicMock()
    snapshot = ({"available": True}, [], [])
    monkeypatch.setattr(spawn_history, "read_leaderboard", lambda db, hours: snapshot)
    thread = threading.get_ident()

    def rank(rows, hours):
        connection.commit.assert_called_once()
        connection.close.assert_called_once()
        assert threading.get_ident() != thread
        assert rows is snapshot and hours == 24
        return {"metadata": rows[0], "trainers": []}

    monkeypatch.setattr(spawn_history, "build_leaderboard", rank)
    db = DatabaseConnector.__new__(DatabaseConnector)
    db.database = Config.DB_POLISWAG
    monkeypatch.setattr(
        database_connector, "connect", lambda *args, **kwargs: connection
    )
    assert await spawn_history.leaderboard(db, 24) == {
        "metadata": {"available": True},
        "trainers": [],
    }
