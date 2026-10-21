"""Curated, source-backed costume identities alongside masterfile forms."""

import json
from pathlib import Path


def load_costume_catalogue():
    """Read the reviewed repository data, not mutable scanner or web labels."""
    path = Path(__file__).resolve().parents[1] / "resources" / "pokemon_costumes.json"
    return json.loads(path.read_text(encoding="utf-8"))


def build_costume_rows(masterfile, catalogue):
    rows = []
    seen = set()
    for entry in catalogue["variants"]:
        if not entry.get("sources"):
            raise ValueError("A source is required for every costume")
        pid = entry["pokemon_id"]
        cid = entry["costume_id"]
        fid = entry["form_id"]
        if any(type(value) is not int or value < 0 for value in (pid, fid, cid)):
            raise ValueError("Costume identity must contain non-negative integers")
        if not fid and not cid:
            raise ValueError("A costume cannot replace the ordinary species row")
        if cid and str(cid) not in catalogue.get("costume_enums", {}):
            raise ValueError("Unknown costume enum")
        fid = entry["form_id"]
        species = masterfile.get("pokemon", {}).get(str(pid))
        if not species or (fid and str(fid) not in species.get("forms", {})):
            raise ValueError("Unknown species or form")
        identity = (pid, fid, cid)
        if identity in seen:
            raise ValueError("Duplicate costume identity")
        seen.add(identity)
        rows.append(
            (
                pid,
                entry["form_id"],
                entry["costume_id"],
                masterfile["pokemon"][str(pid)]["name"],
                entry["form_name"],
                None,
                1,
            )
        )
    return rows


async def sync_pokemon_costumes(db, masterfile, catalogue=None):
    """Upsert validated metadata only; collection and trade rows are never touched."""
    rows = build_costume_rows(
        masterfile, load_costume_catalogue() if catalogue is None else catalogue
    )
    if not rows:
        return 0
    slots = ", ".join(["(%s, %s, %s, %s, %s, %s, %s)"] * len(rows))
    sql = (
        "INSERT INTO pokemon_costume (pokemon_id, form_id, costume_id, name, form_name, family_id, is_costume) VALUES "
        + slots
        + " ON DUPLICATE KEY UPDATE name=VALUES(name), form_name=VALUES(form_name), family_id=VALUES(family_id), is_costume=VALUES(is_costume)"
    )
    await db.execute_query_to_database(
        sql, params=tuple(value for row in rows for value in row)
    )
    return len(rows)
