#!/usr/bin/env python3
"""Build Poracle's ``geofence.json`` from the Poliswag scan-area registry.

The active rows of ``scan_area`` say which areas are scanned; their polygons
are the matching Dragonite fences. Poracle names each entry with the row's
``poracle_area`` and keeps the registry's ``sort_order`` as its id, so
the two original areas keep ids 1 and 2.

By default nothing is written: it prints a per-area summary (id, points,
path or multipath) and, when there is an existing file to compare with, what
would change. Pass ``--write PATH`` to write the file.

Run in the bot container (it needs the database and Dragonite):
    python scripts/sync_poracle_geofences.py
    python scripts/sync_poracle_geofences.py --write /etc/poracle/geofences/geofence.json
"""

# ruff: noqa: E402 -- Add the repository path before importing its modules.

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules import scan_areas
from modules.config import Config
from modules.database_connector import DatabaseConnector


def point_count(entry) -> int:
    paths = entry.get("multipath") or [entry.get("path") or []]
    return sum(len(path) for path in paths)


def _polygons(value):
    """Accept the module's normalized fences or a raw GeoJSON payload."""
    if isinstance(value, (dict, str)):
        return scan_areas.normalize_geometry(value)
    return tuple(value or ())


def build_entries(areas, fences):
    """Poracle geofence entries for the active areas, in registry order.

    A single outline becomes ``path``; a MultiPolygon (or a Polygon with a
    hole) becomes ``multipath``. Poracle's format has no hole concept, so
    holes are dropped from the outline with a warning: leaving them out is
    safer than emitting them as extra polygons (which would turn a hole into
    a second scanned area).
    """
    entries = []
    for row in areas:
        polygons = _polygons(fences.get(row["area_key"]))
        if not polygons:
            continue
        entry = {
            "id": row["sort_order"],
            "name": row["poracle_area"],
            "userSelectable": True,
            "displayInMatches": True,
            "group": "",
        }
        holes = sum(len(polygon) - 1 for polygon in polygons)
        if holes:
            print(
                f"aviso: {row['area_key']} tem {holes} buraco(s) no geofence; "
                "o formato do Poracle não os representa e ficam fora do ficheiro",
                file=sys.stderr,
            )
        if len(polygons) == 1:
            # One outline (a hole, if any, was warned about above and is not
            # representable): Poracle's single-path entry.
            entry["path"] = [[lat, lon] for lat, lon in polygons[0][0]]
        else:
            entry["multipath"] = [
                [[lat, lon] for lat, lon in polygon[0]] for polygon in polygons
            ]
        entries.append(entry)
    return entries


def _old_by_name(existing):
    if not isinstance(existing, list):
        return {}
    return {
        entry.get("name"): entry
        for entry in existing
        if isinstance(entry, dict) and entry.get("name")
    }


def diff_lines(entries, existing):
    """Human diff against an existing geofence file, by Poracle name."""
    old = _old_by_name(existing)
    lines = []
    for entry in entries:
        name = entry["name"]
        points = point_count(entry)
        if name not in old:
            lines.append(f"  + {name}: {points} pontos")
            continue
        before = old[name]
        old_points = point_count(before)
        geometry_changed = before.get("path") != entry.get("path") or before.get(
            "multipath"
        ) != entry.get("multipath")
        if not geometry_changed and int(before.get("id") or 0) == entry["id"]:
            lines.append(f"  = {name}: {points} pontos")
        else:
            lines.append(f"  ~ {name}: {old_points} → {points} pontos")
    new_names = {entry["name"] for entry in entries}
    for name in sorted(old):
        if name not in new_names:
            lines.append(f"  - {name}: já não está ativo")
    return lines


def summary_lines(entries):
    lines = [f"{len(entries)} área(s) ativa(s):"]
    for entry in entries:
        shape = "multipath" if "multipath" in entry else "path"
        lines.append(
            f"  {entry['id']:>2} {entry['name']}: {point_count(entry)} pontos ({shape})"
        )
    return lines


def load_existing(path):
    if path is None:
        return None
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise SystemExit(f"Não consegui ler {path}: {error}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        help="write the geofence file here (default: only print the summary)",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        default=None,
        help="existing geofence file to diff against (default: --write, if it exists)",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="continue when an active area has no fence (otherwise exit 1)",
    )
    args = parser.parse_args(argv)

    # The registry is read through a database handle, as in the bot. A
    # handle that cannot connect fails like a failed registry read.
    try:
        scan_areas.use(DatabaseConnector(Config.DB_POLISWAG))
    except Exception:
        raise SystemExit("Não consegui ler o registo scan_area nem os geofences.")
    if not asyncio.run(scan_areas.refresh()):
        raise SystemExit("Não consegui ler o registo scan_area nem os geofences.")
    areas = scan_areas.active_areas()
    fences = scan_areas.fences()
    missing = [row["area_key"] for row in areas if not fences.get(row["area_key"])]
    if missing and not args.allow_missing:
        raise SystemExit(
            "Sem geofence para: " + ", ".join(missing) + " (usa --allow-missing)"
        )

    entries = build_entries(areas, fences)
    print("\n".join(summary_lines(entries)))
    existing = load_existing(args.compare or args.write)
    if existing is not None:
        print("Alterações face ao ficheiro existente:")
        print("\n".join(diff_lines(entries, existing)))

    if args.write is None:
        print("Nada escrito (usa --write PATH para gravar).")
        return 0
    target = Path(args.write)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n")
    print(f"Escrito {target} ({len(entries)} área(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
