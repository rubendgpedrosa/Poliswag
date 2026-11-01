"""Reviewed artwork published by the site, for the embeds the icon set cannot draw.

The icon set has no file for most event costumes (Noibat's Halloween costume is
filed as `714_c85.png`, which does not exist), while the site's Pokédex draws
them from art the owner reviewed and keeps on pogoleiria.pt. The site publishes
that table at /pokedex-data/art-map.json; this reads a local copy, refreshed by
scripts/update_art_map.py, and answers before the icon-set fallback runs.

Keys are `species:form:costume`; a costume is usually filed under the canonical
form 0 even when the scan reports a form of its own, so both are tried.
"""

import json
import logging
from pathlib import Path

MAP_PATH = Path(__file__).resolve().parents[1] / "templates/art-map.json"

_cache = {"mtime": None, "map": {}}


def art_map() -> dict:
    """The published table, reloaded when the refresher rewrites the file."""
    try:
        mtime = MAP_PATH.stat().st_mtime
    except FileNotFoundError:
        return _cache["map"] if _cache["mtime"] is None else {}
    if _cache["mtime"] != mtime:
        try:
            _cache["map"] = json.loads(MAP_PATH.read_text(encoding="utf-8"))
            _cache["mtime"] = mtime
            logging.info(f"ArtMap: {len(_cache['map'])} reviewed entries loaded")
        except (OSError, ValueError) as error:
            logging.warning(f"ArtMap: could not read {MAP_PATH}: {error}")
    return _cache["map"]


def reviewed_art(pokemon_id, *, form=0, costume=0, shiny=False):
    """The reviewed URL for this variant, or None when the table has no entry."""
    if not costume:
        return None
    entries = art_map()
    for current in (form, 0) if form else (0,):
        entry = entries.get(f"{int(pokemon_id)}:{int(current)}:{int(costume)}")
        if entry:
            return entry.get("shiny" if shiny else "normal") or entry.get("normal")
    return None
