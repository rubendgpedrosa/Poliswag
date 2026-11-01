"""Refresh the local copy of the site's reviewed artwork table.

The site publishes /pokedex-data/art-map.json whenever its reviewed costumes
change. Run daily (hermes-art-map.timer on the container host); Poliswag picks
the new file up on the next lookup, with no restart.

    python3 scripts/update_art_map.py [--dry-run]
"""

import json
import sys
import urllib.request
from pathlib import Path

URL = "https://pogoleiria.pt/pokedex-data/art-map.json"
TARGET = Path(__file__).resolve().parents[1] / "templates/art-map.json"
UA = {"User-Agent": "poliswag-art-map"}


def main() -> int:
    try:
        with urllib.request.urlopen(
            urllib.request.Request(URL, headers=UA), timeout=30
        ) as response:
            payload = response.read()
    except Exception as error:  # noqa: BLE001 - keep the previous copy on any failure
        print(f"art-map: fetch failed ({error}); keeping the existing copy")
        return 1

    try:
        entries = json.loads(payload)
    except ValueError as error:
        print(f"art-map: invalid JSON ({error}); keeping the existing copy")
        return 1

    if not entries or not all(
        isinstance(v, dict) and v.get("normal") for v in entries.values()
    ):
        print("art-map: unexpected shape; keeping the existing copy")
        return 1

    if "--dry-run" in sys.argv:
        print(f"art-map: {len(entries)} entries would be written to {TARGET}")
        return 0

    staging = TARGET.with_suffix(".tmp")
    staging.write_text(json.dumps(entries, ensure_ascii=False) + "\n", encoding="utf-8")
    staging.replace(TARGET)
    print(f"art-map: {len(entries)} entries written to {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
