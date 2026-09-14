"""Mirror the masterfile's Pokémon names into poliswag.pokemon_name.

Poliswag already downloads WatWowMap's masterfile for quest and mega names.
The PoGoLeiria hub needs the same names but reads everything over SQL, so
rather than a second download (or a JSON file shared through a bind mount)
the names are written to a table it can LEFT JOIN against.

Only named, non-default forms get their own row: Zacian's "Crowned Sword" is
worth printing, its default "Hero" and every "Normal" are not.
"""

import logging

_UPSERT_PREFIX = (
    "INSERT INTO pokemon_name (pokemon_id, form_id, name, form_name) VALUES "
)
_UPSERT_SUFFIX = (
    " ON DUPLICATE KEY UPDATE name = VALUES(name), form_name = VALUES(form_name)"
)


def build_pokemon_name_rows(masterfile) -> list[tuple[int, int, str, str | None]]:
    rows: list[tuple[int, int, str, str | None]] = []
    for pokemon_id, details in ((masterfile or {}).get("pokemon") or {}).items():
        if not isinstance(details, dict) or not details.get("name"):
            continue
        pid = int(pokemon_id)
        name = details["name"]
        rows.append((pid, 0, name, None))

        default_form = details.get("defaultFormId")
        for form_id, form in (details.get("forms") or {}).items():
            form_name = (form or {}).get("name")
            if not form_name or form_name == "Normal" or int(form_id) == default_form:
                continue
            rows.append((pid, int(form_id), name, form_name))
    return rows


async def sync_pokemon_names(db, masterfile) -> int:
    """Upsert every row in one statement. Upsert rather than delete + insert:
    DatabaseConnector commits per statement, so a delete would leave the hub
    with no names until the insert landed. A row the masterfile has since
    dropped is harmless — nothing will join to it."""
    rows = build_pokemon_name_rows(masterfile)
    if not rows:
        logging.warning("pokemon_name sync: masterfile has no pokemon, skipping")
        return 0

    placeholders = ", ".join(["(%s, %s, %s, %s)"] * len(rows))
    params = tuple(value for row in rows for value in row)
    await db.execute_query_to_database(
        _UPSERT_PREFIX + placeholders + _UPSERT_SUFFIX, params=params
    )
    return len(rows)
