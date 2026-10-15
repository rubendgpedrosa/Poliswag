"""Refresh the checked sprite map shared by Poliswag and Poracle templates."""

import json
from pathlib import Path
from urllib.request import urlopen

BASE = "https://raw.githubusercontent.com/nileplumb/PkmnHomeIcons/master/UICONS_OS_128/pokemon"
INDEX = BASE.rsplit("/", 1)[0] + "/index.json"


def build_map(files):
    files = set(files)
    result = {}
    for name in sorted(files):
        species = name.split("_", 1)[0].split(".", 1)[0]
        shiny = (
            name if name.endswith("_s.png") else name.removesuffix(".png") + "_s.png"
        )
        if shiny not in files:
            shiny = f"{species}_s.png"
        if shiny not in files:
            shiny = name
        result[f"{BASE}/{name}"] = f"{BASE}/{shiny}"
    return result


if __name__ == "__main__":
    with urlopen(INDEX, timeout=30) as response:
        sprites = build_map(json.load(response)["pokemon"])
    destination = (
        Path(__file__).resolve().parents[1] / "templates/poracle/shiny-sprites.json"
    )
    destination.write_text(json.dumps(sprites, indent=2) + "\n")
    print(f"Updated {len(sprites)} checked sprites: {destination}")
