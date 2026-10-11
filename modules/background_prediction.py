"""Explicit wild-background rules shared with the map, never shiny-rate inference.

Times are UTC epoch seconds, starts inclusive and ends exclusive. Variant IDs
are raw scanner IDs. A geographical rectangle is mandatory; split rules when
a region crosses the date line. Conflicting overlapping cutoffs fail closed.
"""

import math


def background_rule_for(spawn, rules, now):
    # 'encounter' is a scanner observation state, not an encounter source.
    # Require wild spawn provenance as well as the allowed observation state.
    if (
        spawn.get("seen_type") not in ("wild", "encounter")
        or spawn.get("spawn_id") in (None, "", 0, "0")
        or spawn.get("pokestop_id") not in (None, "", "None")
    ):
        return None
    hits = []
    for rule in rules:
        try:
            probability = float(rule["probability"])
            start, end = int(rule["starts_at"]), int(rule["ends_at"])
            south, north = float(rule["min_lat"]), float(rule["max_lat"])
            west, east = float(rule["min_lon"]), float(rule["max_lon"])
            lat, lon = float(spawn["lat"]), float(spawn["lon"])
            numbers = (probability, south, north, west, east, lat, lon)
            if not all(math.isfinite(n) for n in numbers):
                continue
            if not (
                0 < probability <= 1
                and -90 <= south <= north <= 90
                and -180 <= west <= east <= 180
            ):
                continue
            if (
                int(rule.get("enabled", 0)) != 1
                or rule.get("encounter_source") != "wild"
            ):
                continue
            if (
                int(rule["pokemon_id"]),
                int(rule["form_id"]),
                int(rule["costume_id"]),
            ) != (
                int(spawn["pokemon_id"]),
                int(spawn["form_id"]),
                int(spawn.get("costume") or 0),
            ):
                continue
            if not (
                start <= now < end and south <= lat <= north and west <= lon <= east
            ):
                continue
            hits.append({**rule, "probability": probability})
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    if not hits or len({r["probability"] for r in hits}) != 1:
        return None
    return sorted(hits, key=lambda r: str(r.get("rule_id", "")))[0]
