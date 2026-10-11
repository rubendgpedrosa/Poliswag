#!/usr/bin/env python3
"""Build a reviewed event rule offline; never connects to a database."""

import argparse
from datetime import datetime
import json
import math
import re

# A bare file name from the game's location cards (PokeMiners/pogo_assets,
# Images/LocationCards): the map draws it behind the Pokémon.
IMAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,150}\.png")


def epoch(value):
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset or Z")
    seconds = date.timestamp()
    if seconds < 0 or seconds != int(seconds):
        raise ValueError("timestamps must be positive whole seconds")
    return int(seconds)


def make_rule(args):
    rule = dict(
        rule_id=args.rule_id,
        label=args.label,
        pokemon_id=args.pokemon_id,
        form_id=args.form_id,
        costume_id=args.costume_id,
        encounter_source="wild",
        probability=args.probability,
        starts_at=epoch(args.starts_at),
        ends_at=epoch(args.ends_at),
        min_lat=args.bounds[0],
        max_lat=args.bounds[1],
        min_lon=args.bounds[2],
        max_lon=args.bounds[3],
        enabled=int(args.enabled),
    )
    image = getattr(args, "image", None)
    if image is not None:
        if not IMAGE_NAME.fullmatch(image):
            raise ValueError(
                "the image is a file name from Images/LocationCards, such as "
                "sb_GoFest2024_radiance.png, not a path or a URL"
            )
        rule["image"] = image
    if (
        not rule["rule_id"]
        or len(rule["rule_id"]) > 96
        or not rule["label"]
        or len(rule["label"]) > 160
    ):
        raise ValueError("a rule ID (1–96 characters) and label (1–160) are required")
    if (
        any(not 0 <= rule[k] <= 65535 for k in ("pokemon_id", "form_id", "costume_id"))
        or rule["pokemon_id"] == 0
    ):
        raise ValueError(
            "species/form/costume must be raw unsigned scanner IDs; species must be positive"
        )
    if not all(
        math.isfinite(rule[k])
        for k in ("probability", "min_lat", "max_lat", "min_lon", "max_lon")
    ):
        raise ValueError("probability and bounds must be finite")
    if not 0 < rule["probability"] <= 1:
        raise ValueError("probability must be explicit and greater than 0, at most 1")
    if rule["starts_at"] >= rule["ends_at"]:
        raise ValueError("the end must follow the start")
    if not (
        -90 <= rule["min_lat"] <= rule["max_lat"] <= 90
        and -180 <= rule["min_lon"] <= rule["max_lon"] <= 180
    ):
        raise ValueError(
            "bounds must be SOUTH NORTH WEST EAST; split regions crossing the date line"
        )
    return rule


def insert_sql(rule):
    # Hex literals keep arbitrary ID/label characters out of SQL syntax.
    def literal(value):
        return (
            f"CONVERT(X'{value.encode('utf-8').hex()}' USING utf8mb4)"
            if isinstance(value, str)
            else repr(value)
        )

    columns = ", ".join(f"`{key}`" for key in rule)
    values = ", ".join(literal(value) for value in rule.values())
    return f"INSERT INTO poliswag.background_prediction_rule ({columns}) VALUES ({values});"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rule-id", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--pokemon-id", required=True, type=int)
    parser.add_argument("--form-id", required=True, type=int)
    parser.add_argument("--costume-id", default=0, type=int)
    parser.add_argument(
        "--image",
        help="the event's background, a file name from PokeMiners/pogo_assets "
        "Images/LocationCards (optional; the map draws a plain landscape without it)",
    )
    parser.add_argument("--probability", required=True, type=float)
    parser.add_argument(
        "--starts-at", required=True, help="ISO timestamp including UTC offset or Z"
    )
    parser.add_argument(
        "--ends-at",
        required=True,
        help="exclusive ISO timestamp including UTC offset or Z",
    )
    parser.add_argument(
        "--bounds",
        required=True,
        nargs=4,
        type=float,
        metavar=("SOUTH", "NORTH", "WEST", "EAST"),
    )
    parser.add_argument(
        "--enabled",
        action="store_true",
        help="include enabled=1 in the output (default: disabled)",
    )
    parser.add_argument(
        "--sql",
        action="store_true",
        help="print an INSERT for manual review instead of JSON",
    )
    args = parser.parse_args()
    try:
        rule = make_rule(args)
    except ValueError as error:
        parser.error(str(error))
    print(
        insert_sql(rule) if args.sql else json.dumps(rule, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
