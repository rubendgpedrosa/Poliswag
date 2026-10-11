"""Real MariaDB checks, only on an explicitly marked disposable server.

Run inside the bot with SPAWN_HISTORY_SQL_TEST_HOST pointing to the throwaway
container, whose root password is `disposable`. Never runs on production.
"""

import gzip
import json
import os
import time
from pathlib import Path

import pymysql
import pytest

from modules import spawn_history
from modules.config import Config
from modules.database_connector import connect as configured_connect
from modules.migrations import split_statements

_REAL_CONNECT = pymysql.connect
_HOST = os.environ.get("SPAWN_HISTORY_SQL_TEST_HOST")
pytestmark = pytest.mark.skipif(not _HOST, reason="No disposable spawn-history DB")
MIGRATION = Path(__file__).parents[2] / "migrations/031_add_spawn_history.sql"


def root(database=None):
    return _REAL_CONNECT(
        host=_HOST,
        user="root",
        password="disposable",
        database=database,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def sql(statement, params=None, database="poliswag"):
    connection = root(database)
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return list(cursor.fetchall())
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def disposable(monkeypatch):
    connection = root()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW DATABASES")
            names = {row["Database"] for row in cursor.fetchall()}
            if "spawn_history_disposable" not in names or names & {
                "golbat",
                "dragonite",
                "poracle",
                "koji",
            }:
                pytest.fail("Refusing a database without the disposable marker")
            for schema in ("poliswag", "pogoleiria", "spawn_scanner_disposable"):
                cursor.execute(f"DROP DATABASE IF EXISTS {schema}")
                cursor.execute(f"CREATE DATABASE {schema}")
    finally:
        connection.close()
    for _ in range(2):  # Verify the startup migration can be replayed.
        for statement in split_statements(MIGRATION.read_text()):
            sql(statement)
    sql(
        "CREATE TABLE shiny_eligible_variant (pokemon_id INT,form_id INT,costume_id INT,PRIMARY KEY(pokemon_id,form_id,costume_id))"
    )
    sql("INSERT INTO shiny_eligible_variant VALUES (25,0,0),(4,0,0)")
    sql(
        "CREATE TABLE shiny_rate (pokemon_id INT PRIMARY KEY,rate INT,source VARCHAR(16),ends_at DATETIME)"
    )
    sql(
        """CREATE TABLE pokemon (
        id VARCHAR(25) PRIMARY KEY, first_seen_timestamp BIGINT,
        expire_timestamp BIGINT, expire_timestamp_verified TINYINT DEFAULT 1,
        pokemon_id INT, form INT, costume INT, gender INT,
        lat DOUBLE DEFAULT 39.7,lon DOUBLE DEFAULT -8.8,
        seen_type VARCHAR(32) DEFAULT 'wild',display_pokemon_id INT,is_ditto TINYINT DEFAULT 0,
        atk_iv TINYINT,def_iv TINYINT,sta_iv TINYINT,cp INT,level INT,weather INT)""",
        database="spawn_scanner_disposable",
    )
    sql(
        "CREATE TABLE shiny_link (discord_id BIGINT,trainer_name VARCHAR(64),player_id VARCHAR(32),status VARCHAR(16))"
    )
    sql(
        """CREATE TABLE trade_player (discord_id BIGINT,shiny_access TINYINT DEFAULT 1,
        shiny_map TINYINT DEFAULT 1,left_at DATETIME,
        shiny_notify_discord TINYINT DEFAULT 0,shiny_notify_all TINYINT DEFAULT 0)""",
        database="pogoleiria",
    )
    for key, value in {
        "DB_HOST": _HOST,
        "DB_PORT": 3306,
        "DB_USER": "root",
        "DB_PASSWORD": "disposable",
        "DB_POLISWAG": "poliswag",
        "DB_SCANNER_NAME": "spawn_scanner_disposable",
        "DB_POGOLEIRIA": "pogoleiria",
    }.items():
        monkeypatch.setattr(Config, key, value)
    monkeypatch.setattr(pymysql, "connect", _REAL_CONNECT)
    clock = {"now": int(time.time())}
    monkeypatch.setattr(spawn_history.time, "time", lambda: clock["now"])

    def timed_connect(*args, **kwargs):
        connection = configured_connect(*args, **kwargs)
        with connection.cursor() as cursor:
            cursor.execute("SET timestamp=%s", (clock["now"],))
        return connection

    monkeypatch.setattr(spawn_history, "connect", timed_connect)
    return clock


def add_spawn(clock, encounter="18446744073709551614", pokemon_id=25, **changes):
    values = {
        "id": encounter,
        "first_seen_timestamp": clock["now"] - 120,
        "expire_timestamp": clock["now"] + 600,
        "pokemon_id": pokemon_id,
        "form": 0,
        "costume": 0,
        **changes,
    }
    sql(
        f"INSERT INTO pokemon ({','.join(values)}) VALUES ({','.join(['%s']*len(values))})",
        tuple(values.values()),
        database="spawn_scanner_disposable",
    )


def test_captures_all_spawns_without_players_and_deduplicates(disposable):
    add_spawn(disposable)
    add_spawn(disposable, "123", 999, seen_type="nearby_stop")
    spawn_history.capture()
    disposable["now"] += 30
    spawn_history.capture()
    rows = sql("SELECT * FROM spawn_history ORDER BY pokemon_id")
    assert (
        len(rows) == 2
        and rows[0]["shiny_eligible"] == 1
        and rows[1]["shiny_eligible"] == 0
    )
    assert rows[0]["encounter_id"] == "18446744073709551614"
    assert rows[0]["first_captured_at"] == disposable["now"] - 30
    assert rows[0]["last_captured_at"] == disposable["now"]


def test_late_ivs_and_metadata_preserve_first_captured_odds(disposable):
    add_spawn(disposable)
    spawn_history.capture()
    disposable["now"] += 30
    sql("INSERT INTO shiny_rate VALUES (25,25,'auto','2099-01-01')")
    sql(
        "UPDATE pokemon SET atk_iv=15,def_iv=15,sta_iv=15,cp=400,seen_type='encounter'",
        database="spawn_scanner_disposable",
    )
    spawn_history.capture()
    row = sql("SELECT * FROM spawn_history")[0]
    assert (row["atk_iv"], row["def_iv"], row["sta_iv"]) == (15, 15, 15)
    assert row["cp"] == 400 and row["seen_type"] == "encounter"
    assert row["shiny_odds"] == 512 and row["odds_source"] == "base"
    sql("UPDATE pokemon SET atk_iv=NULL,cp=NULL", database="spawn_scanner_disposable")
    spawn_history.capture()
    row = sql("SELECT * FROM spawn_history")[0]
    assert row["atk_iv"] == 15 and row["cp"] == 400


def test_corrected_species_refreshes_prediction_metadata(disposable):
    add_spawn(disposable, pokemon_id=999, atk_iv=15, def_iv=15, sta_iv=15, cp=400)
    spawn_history.capture()
    disposable["now"] += 30
    sql("INSERT INTO shiny_rate VALUES (25,25,'manual','2099-01-01')")
    sql(
        "UPDATE pokemon SET pokemon_id=25,atk_iv=NULL,def_iv=NULL,sta_iv=NULL,cp=NULL",
        database="spawn_scanner_disposable",
    )
    spawn_history.capture()
    row = sql("SELECT * FROM spawn_history")[0]
    assert row["shiny_eligible"] == 1 and row["shiny_odds"] == 25
    assert (row["atk_iv"], row["def_iv"], row["sta_iv"], row["cp"]) == (
        None,
        None,
        None,
        None,
    )
    assert (
        row["odds_source"] == "manual"
        and row["prediction_config_at"] == disposable["now"]
    )


def test_archived_spawn_survives_source_purge_and_expires_after_48h(disposable):
    add_spawn(disposable)
    spawn_history.capture()
    started = disposable["now"]
    sql("DELETE FROM pokemon", database="spawn_scanner_disposable")
    disposable["now"] += 30
    spawn_history.capture()
    assert len(sql("SELECT * FROM spawn_history")) == 1
    disposable["now"] += 48 * 3600
    spawn_history.capture()
    assert sql("SELECT * FROM spawn_history") == []
    assert sql("SELECT started_at FROM spawn_history_state")[0]["started_at"] == started


def test_gaps_are_recorded_once_and_block_hourly_averages(disposable):
    add_spawn(disposable)
    spawn_history.capture()
    first = disposable["now"]
    disposable["now"] += 120
    spawn_history.capture()
    spawn_history.capture()
    assert sql("SELECT * FROM spawn_history_gap") == [
        {"started_at": first, "ended_at": disposable["now"]}
    ]
    data = spawn_history.status(1)
    assert not data["complete_window"] and len(data["gaps"]) == 1


def test_failed_capture_rolls_back_checkpoint_and_recovers(disposable, monkeypatch):
    add_spawn(disposable)
    spawn_history.capture()
    previous = disposable["now"]
    disposable["now"] += 120
    original = spawn_history.capture_sql
    monkeypatch.setattr(
        spawn_history, "capture_sql", lambda: "SELECT %s,%s,%s,%s,%s FROM missing"
    )
    with pytest.raises(pymysql.MySQLError):
        spawn_history.capture()
    assert (
        sql("SELECT last_capture_at FROM spawn_history_state")[0]["last_capture_at"]
        == previous
    )
    assert sql("SELECT * FROM spawn_history_gap") == []
    monkeypatch.setattr(spawn_history, "capture_sql", original)
    spawn_history.capture()
    assert len(sql("SELECT * FROM spawn_history_gap")) == 1


def test_json_export_is_valid_exact_and_marks_partial_history(disposable):
    add_spawn(disposable)
    spawn_history.capture()
    with spawn_history.export_json(24) as output:
        data = json.loads(gzip.decompress(output.read()))
    assert data["schema_version"] == 1 and not data["metadata"]["complete_window"]
    assert data["spawns"][0]["encounter_id"] == "18446744073709551614"
    assert isinstance(data["spawns"][0]["lat"], float)
    assert data["spawns"][0]["atk_iv"] is None
    assert "player_id" not in data["spawns"][0]


def test_report_ignores_preferences_and_exposes_only_aggregates(
    disposable, monkeypatch
):
    add_spawn(disposable, atk_iv=15, def_iv=15, sta_iv=15)
    add_spawn(disposable, "123", lon=-9.0)
    add_spawn(disposable, "456", 999)
    spawn_history.capture()
    sql("INSERT INTO shiny_link VALUES (1,'Trainer','0123456789abcdef','linked')")
    sql("INSERT INTO pogoleiria.trade_player (discord_id) VALUES (1)")
    monkeypatch.setattr(spawn_history, "shiny_draw", lambda *args: 0.0)
    report = spawn_history.leaderboard(24)
    row = report["trainers"][0]
    assert row["predicted_shinies"] == 2 and report["eligible_spawns"] == 2
    assert row["leiria"] == 1 and row["marinha"] == 1 and row["hundo_shinies"] == 1
    assert row["shinies_per_hour"] is None and "player_id" not in row
    sql("UPDATE spawn_history_state SET started_at=last_capture_at-86400")
    report = spawn_history.leaderboard(24)
    assert report["metadata"]["complete_window"]
    assert report["trainers"][0]["shinies_per_hour"] == round(2 / 24, 3)


def test_expired_rate_falls_back_and_live_rate_is_retained(disposable):
    add_spawn(disposable)
    add_spawn(disposable, "123", 4)
    sql(
        "INSERT INTO shiny_rate VALUES (25,25,'auto','2099-01-01'),(4,64,'auto','2000-01-01')"
    )
    spawn_history.capture()
    rows = {r["pokemon_id"]: r for r in sql("SELECT * FROM spawn_history")}
    assert rows[25]["shiny_odds"] == 25 and rows[25]["odds_source"] == "auto"
    assert rows[4]["shiny_odds"] == 512 and rows[4]["odds_source"] == "base"
