"""Rebuild the checked sprite map that Poracle's templates consult.

Poracle builds an image URL from `img_url` and asks this map for the shiny
version of it; a key it cannot find means the ordinary sprite (or nothing) on a
shiny alert. The keys therefore have to be the URLs Poracle builds, and the
values the shiny file for that same variant.

Images stay on the CDN wherever it has the file — Discord fetches embed images
itself, and the CDN is the origin that always answers. Our own set only appears
as a value for the art the CDN does not have (reviewed costumes, the game's
own form icons).
"""

import json
from pathlib import Path

OURS = "https://pogoleiria.pt/uicons/pokemon"
CDN = "https://raw.githubusercontent.com/nileplumb/PkmnHomeIcons/master/UICONS_OS_128/pokemon"
MIRROR = Path("/root/uicons-mirror")
TARGETS = (
    Path("/root/Poliswag/templates/poracle/shiny-sprites.json"),
    Path("/root/unonwhash/poracleng/config/customMaps/shiny-sprites.json"),
)


def build() -> dict:
    ours = json.loads((MIRROR / "index.json").read_text(encoding="utf-8"))["pokemon"]
    cdn = set(
        json.loads((MIRROR / "upstream-index.json").read_text(encoding="utf-8"))[
            "pokemon"
        ]
    )
    ours_set = set(ours)

    def shiny_name(name: str) -> str:
        candidate = name.removesuffix(".png") + "_s.png"
        if candidate in ours_set:
            return candidate
        species = name.split("_", 1)[0].removesuffix(".png") + "_s.png"
        if species in ours_set:
            return species
        return name

    table = {}
    for name in ours:
        target = shiny_name(name)
        origin = CDN if target in cdn else OURS
        table[f"{OURS}/{name}"] = f"{origin}/{target}"
    return table


if __name__ == "__main__":
    table = build()
    for path in TARGETS:
        path.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
        print(f"{path}: {len(table)} entradas")
