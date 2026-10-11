"""Bounded raw history and reproducible unfiltered shiny statistics.

Collector and readers use separate, short-lived database connections. Recording
needs no linked players and never reads notification, collection or area filters.
The historical odds are estimates captured from shiny_rate, not game guarantees.
"""

import gzip
import json
import tempfile
import time
from datetime import datetime, timezone

import pymysql

from modules.config import Config
from modules.database_connector import connect
from modules.shiny_roll import is_player_id, is_shiny_at, shiny_draw

RETENTION_HOURS = 48
POLL_SECONDS = 30
MAX_POLL_GAP = 90
ALGORITHM = "java-sha1-nextfloat-v1"
MAX_EXPORT_BYTES = 8 * 1024 * 1024


def hours_value(hours):
    if type(hours) is not int or not 1 <= hours <= RETENTION_HOURS:
        raise ValueError("Usa um período entre 1 e 48 horas.")
    return hours


def capture_sql():
    variant_changed = (
        "spawn_history.pokemon_id <> VALUES(pokemon_id) OR spawn_history.form_id <> VALUES(form_id) "
        "OR spawn_history.costume_id <> VALUES(costume_id)"
    )
    return f"""
INSERT INTO spawn_history (
  encounter_id, first_seen_at, first_captured_at, last_captured_at,
  expire_at, expire_verified, pokemon_id, form_id, costume_id, gender,
  lat, lon, seen_type, display_pokemon_id, is_ditto,
  atk_iv, def_iv, sta_iv, cp, level, weather,
  shiny_eligible, shiny_odds, odds_source, prediction_config_at
)
SELECT p.id, p.first_seen_timestamp, %s, %s,
  p.expire_timestamp, p.expire_timestamp_verified, p.pokemon_id,
  COALESCE(p.form, 0), COALESCE(p.costume, 0), p.gender,
  p.lat, p.lon, p.seen_type, p.display_pokemon_id, COALESCE(p.is_ditto, 0),
  p.atk_iv, p.def_iv, p.sta_iv, p.cp, p.level, p.weather,
  (e.pokemon_id IS NOT NULL), COALESCE(NULLIF(r.rate, 0), 512),
  COALESCE(r.source, 'base'), %s
FROM {Config.DB_SCANNER_NAME}.pokemon p
LEFT JOIN shiny_eligible_variant e ON e.pokemon_id = p.pokemon_id
  AND e.form_id = COALESCE(p.form, 0) AND e.costume_id = COALESCE(p.costume, 0)
LEFT JOIN shiny_rate r ON r.pokemon_id = p.pokemon_id AND r.ends_at > UTC_TIMESTAMP()
WHERE p.first_seen_timestamp >= %s AND p.first_seen_timestamp <= %s
ON DUPLICATE KEY UPDATE
  shiny_eligible = IF({variant_changed}, VALUES(shiny_eligible), spawn_history.shiny_eligible),
  shiny_odds = IF({variant_changed}, VALUES(shiny_odds), spawn_history.shiny_odds),
  odds_source = IF({variant_changed}, VALUES(odds_source), spawn_history.odds_source),
  prediction_config_at = IF({variant_changed}, VALUES(prediction_config_at), spawn_history.prediction_config_at),
  first_seen_at = LEAST(spawn_history.first_seen_at, VALUES(first_seen_at)),
  last_captured_at = VALUES(last_captured_at),
  expire_at = VALUES(expire_at), expire_verified = VALUES(expire_verified),
  gender = VALUES(gender),
  lat = VALUES(lat), lon = VALUES(lon), seen_type = VALUES(seen_type),
  display_pokemon_id = VALUES(display_pokemon_id), is_ditto = VALUES(is_ditto),
  atk_iv = IF({variant_changed}, VALUES(atk_iv), COALESCE(VALUES(atk_iv), spawn_history.atk_iv)),
  def_iv = IF({variant_changed}, VALUES(def_iv), COALESCE(VALUES(def_iv), spawn_history.def_iv)),
  sta_iv = IF({variant_changed}, VALUES(sta_iv), COALESCE(VALUES(sta_iv), spawn_history.sta_iv)),
  cp = IF({variant_changed}, VALUES(cp), COALESCE(VALUES(cp), spawn_history.cp)),
  level = IF({variant_changed}, VALUES(level), COALESCE(VALUES(level), spawn_history.level)),
  weather = COALESCE(VALUES(weather), spawn_history.weather),
  pokemon_id = VALUES(pokemon_id), form_id = VALUES(form_id), costume_id = VALUES(costume_id)
"""


def capture():
    connection = connect(Config.DB_POLISWAG, dict_rows=True, read_timeout=15)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION time_zone = '+00:00'")
            cursor.execute("SET SESSION max_statement_time=10")
            cursor.execute("SET SESSION innodb_lock_wait_timeout=5")
            connection.begin()
            cursor.execute("SELECT UNIX_TIMESTAMP() AS now_utc")
            now = int(cursor.fetchone()["now_utc"])
            cutoff = now - RETENTION_HOURS * 3600
            cursor.execute(
                "INSERT IGNORE INTO spawn_history_state VALUES (1, %s, %s)",
                (now, now),
            )
            cursor.execute(
                "SELECT last_capture_at FROM spawn_history_state WHERE id=1 FOR UPDATE"
            )
            previous = int(cursor.fetchone()["last_capture_at"])
            if now < previous:
                raise ValueError("Database clock moved backwards")
            if now - previous > MAX_POLL_GAP:
                cursor.execute(
                    "INSERT IGNORE INTO spawn_history_gap VALUES (%s, %s)",
                    (previous, now),
                )
            cursor.execute(capture_sql(), (now, now, now, cutoff, now))
            cursor.execute(
                "DELETE FROM spawn_history WHERE first_seen_at < %s", (cutoff,)
            )
            cursor.execute(
                "DELETE FROM spawn_history_gap WHERE ended_at < %s", (cutoff,)
            )
            cursor.execute(
                "UPDATE spawn_history_state SET last_capture_at=%s WHERE id=1", (now,)
            )
        connection.commit()
        return now
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def window_metadata(cursor, hours):
    cursor.execute(
        "SELECT started_at, last_capture_at FROM spawn_history_state WHERE id=1"
    )
    states = list(cursor.fetchall())
    state = states[0] if states else None
    if not state:
        return {"available": False, "complete_window": False, "hours": hours}
    end = int(state["last_capture_at"])
    start = end - hours * 3600
    cursor.execute(
        "SELECT started_at, ended_at FROM spawn_history_gap "
        "WHERE started_at < %s AND ended_at > %s ORDER BY started_at",
        (end, start),
    )
    gaps = list(cursor.fetchall())
    complete = int(state["started_at"]) <= start and not gaps
    return {
        "available": True,
        "hours": hours,
        "window_start_utc": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "window_end_utc": datetime.fromtimestamp(end, timezone.utc).isoformat(),
        "start_epoch": start,
        "end_epoch": end,
        "collection_started_utc": datetime.fromtimestamp(
            int(state["started_at"]), timezone.utc
        ).isoformat(),
        "complete_window": complete,
        "collector_stale": time.time() - end > MAX_POLL_GAP,
        "gaps": gaps,
        "retention_hours": RETENTION_HOURS,
        "prediction_algorithm": ALGORITHM,
        "note": (
            "Spawns observados pelo scanner; sem filtros por treinador."
            if complete
            else "Histórico parcial: recolha recente ou falha de recolha. Média por hora indisponível."
        ),
    }


def reader(*, streaming=False):
    connection = connect(
        Config.DB_POLISWAG,
        read_timeout=15,
        cursorclass=(
            pymysql.cursors.SSDictCursor if streaming else pymysql.cursors.DictCursor
        ),
    )
    try:
        cursor = connection.cursor()
        cursor.execute("SET SESSION time_zone = '+00:00'")
        cursor.execute("SET SESSION max_statement_time=10")
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        return connection, cursor
    except BaseException:
        connection.close()
        raise


def status(hours=24):
    hours_value(hours)
    connection, cursor = reader()
    try:
        metadata = window_metadata(cursor, hours)
        cursor.execute("SELECT COUNT(*) AS stored_spawns FROM spawn_history")
        metadata.update(cursor.fetchone())
        return metadata
    finally:
        connection.close()


def export_json(hours=24):
    hours_value(hours)
    connection, cursor = reader(streaming=True)
    output = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    try:
        metadata = window_metadata(cursor, hours)
        if not metadata["available"]:
            raise ValueError("A recolha ainda não começou.")
        cursor.execute(
            "SELECT * FROM spawn_history WHERE first_seen_at >= %s AND first_seen_at < %s ORDER BY first_seen_at, encounter_id",
            (metadata["start_epoch"], metadata["end_epoch"]),
        )
        with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as zipped:
            zipped.write(
                (
                    json.dumps(
                        {"schema_version": 1, "metadata": metadata}, ensure_ascii=False
                    )[:-1]
                    + ',"spawns":['
                ).encode()
            )
            first = True
            for row in cursor:
                zipped.write(
                    (
                        ("" if first else ",") + json.dumps(row, ensure_ascii=False)
                    ).encode()
                )
                first = False
            zipped.write(b"]}")
        if output.tell() > MAX_EXPORT_BYTES:
            raise ValueError("A exportação excede 8 MiB; pede menos horas.")
        output.seek(0)
        return output
    except BaseException:
        output.close()
        raise
    finally:
        connection.close()


def leaderboard(hours=24):
    hours_value(hours)
    connection, cursor = reader()
    try:
        metadata = window_metadata(cursor, hours)
        if not metadata["available"]:
            return {"metadata": metadata, "trainers": []}
        cursor.execute(
            f"SELECT l.trainer_name, l.player_id FROM shiny_link l "
            f"JOIN {Config.DB_POGOLEIRIA}.trade_player p ON p.discord_id=l.discord_id "
            "WHERE l.status='linked' AND p.shiny_access=1 AND p.shiny_map=1 "
            "AND p.left_at IS NULL ORDER BY l.trainer_name LIMIT 101"
        )
        trainers = list(cursor.fetchall())
        if len(trainers) > 100:
            raise ValueError("A leaderboard suporta até 100 treinadores.")
        cursor.execute(
            "SELECT encounter_id, pokemon_id, lon, atk_iv, def_iv, sta_iv, shiny_odds "
            "FROM spawn_history WHERE first_seen_at >= %s AND first_seen_at < %s "
            "AND shiny_eligible=1 AND seen_type IN ('wild','encounter') "
            "AND pokemon_id<>132 AND is_ditto=0 AND costume_id<>255 "
            "AND (display_pokemon_id IS NULL OR display_pokemon_id IN (0,pokemon_id))",
            (metadata["start_epoch"], metadata["end_epoch"]),
        )
        spawns = list(cursor.fetchall())
    finally:
        connection.close()
    rows = []
    for trainer in trainers:
        if not is_player_id(trainer["player_id"]):
            raise ValueError("Treinador associado sem ID de previsão válido.")
        predicted = base = hundos = leiria = 0
        species = set()
        for spawn in spawns:
            draw = shiny_draw(spawn["encounter_id"], trainer["player_id"])
            base += is_shiny_at(draw, 512)
            if is_shiny_at(draw, int(spawn["shiny_odds"])):
                predicted += 1
                species.add(spawn["pokemon_id"])
                hundos += (spawn["atk_iv"], spawn["def_iv"], spawn["sta_iv"]) == (
                    15,
                    15,
                    15,
                )
                leiria += float(spawn["lon"]) > Config.MARINHA_LON_MAX
        rows.append(
            {
                "trainer": trainer["trainer_name"],
                "predicted_shinies": predicted,
                "base_512_shinies": base,
                "hundo_shinies": hundos,
                "unique_species": len(species),
                "leiria": leiria,
                "marinha": predicted - leiria,
                "shinies_per_hour": (
                    round(predicted / hours, 3) if metadata["complete_window"] else None
                ),
            }
        )
    rows.sort(key=lambda row: (-row["predicted_shinies"], row["trainer"].casefold()))
    return {
        "metadata": metadata,
        "eligible_spawns": len(spawns),
        "odds_mode": "Odds guardadas na primeira recolha; boosts estimados pelo scanner, com fallback 1/512.",
        "expected_per_trainer": round(sum(1 / int(s["shiny_odds"]) for s in spawns), 3),
        "total_predicted_shinies": sum(row["predicted_shinies"] for row in rows),
        "trainers": rows,
    }
