"""Recompute the pokestop totals per active scan area.

The `poliswag` table keeps one `quest_expected_<area_key>` column per area: the
natural ceiling the quest scan climbs toward, normally written by the bot when a
scan completes. When an area's fence changes, or a new area is activated, those
numbers go stale and the progress footer lies. This recounts them from the
scanner database, using the same fences the bot uses (the active registry rows
and their Dragonite geofences), so a stop belongs to exactly one area.

    docker exec poliswag python scripts/area_totals.py            # report only
    docker exec poliswag python scripts/area_totals.py --apply    # write them

Read-only unless `--apply` is given. A column that does not exist yet is created
by `--apply` before the first write for that area.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pymysql

sys.path.insert(0, "/app")

import modules.scan_areas as scan_areas  # noqa: E402


def connect(database: str):
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "db"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=database,
        cursorclass=pymysql.cursors.DictCursor,
    )


def current_totals() -> dict:
    """The stored expected totals, keyed by area."""
    with connect(os.environ["DB_NAME"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM poliswag")
        row = cur.fetchone() or {}
    return {
        key: value
        for key, value in row.items()
        if key.startswith("quest_expected_") and value is not None
    }


def stops_in_area(rows, key: str) -> int:
    """Stops of `rows` (lat, lon) that fall inside the area's fence."""
    box = scan_areas.area_bbox(key)
    if box is None:
        return -1
    # area_bbox is (min_lat, min_lon, max_lat, max_lon).
    lat_min, lon_min, lat_max, lon_max = box
    return sum(
        1
        for lat, lon in rows
        if lat_min <= lat <= lat_max
        and lon_min <= lon <= lon_max
        and scan_areas.area_for(lat, lon) == key
    )


def apply_totals(totals: dict) -> None:
    """Write the totals, adding a column for an area that has none yet."""
    with connect(os.environ["DB_NAME"]) as conn, conn.cursor() as cur:
        cur.execute("SHOW COLUMNS FROM poliswag")
        existing = {row["Field"] for row in cur.fetchall()}
        for key, value in totals.items():
            column = f"quest_expected_{key}"
            if column not in existing:
                cur.execute(
                    f"ALTER TABLE poliswag ADD COLUMN {column} INT NOT NULL DEFAULT 0"
                )
                print(f"  + coluna {column}")
            cur.execute(f"UPDATE poliswag SET {column} = %s", (value,))
        conn.commit()


async def main(apply: bool) -> int:
    if not await scan_areas.refresh():
        print("não consegui ler o registo de áreas", file=sys.stderr)
        return 1
    areas = scan_areas.active_areas()
    if not areas:
        print("nenhuma área activa", file=sys.stderr)
        return 1

    with connect(os.environ["DB_SCANNER_NAME"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT lat, lon FROM pokestop WHERE deleted = 0")
        rows = [(float(r["lat"]), float(r["lon"])) for r in cur.fetchall()]

    stored = current_totals()
    counts = {}
    print(f"{len(rows)} poképaragens na base do scanner; {len(areas)} áreas activas")
    print(f"{'área':<22}{'guardado':>10}{'contado':>10}")
    for area in areas:
        key = area["area_key"]
        count = stops_in_area(rows, key)
        counts[key] = count
        old = stored.get(f"quest_expected_{key}", "—")
        flag = "" if count < 0 else ("" if old == count else "  ←")
        print(f"{key:<22}{str(old):>10}{count:>10}{flag}")

    if not apply:
        print("só relatório; usa --apply para gravar")
        return 0
    writable = {key: value for key, value in counts.items() if value >= 0}
    apply_totals(writable)
    print("gravado:", ", ".join(f"{k}={v}" for k, v in writable.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main("--apply" in sys.argv)))
