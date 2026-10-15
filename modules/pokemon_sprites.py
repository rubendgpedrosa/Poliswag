"""Checked Pokémon artwork shared by alert thumbnails and trade summaries."""

import json
from functools import lru_cache
from itertools import product
from pathlib import Path

SPRITE_BASE = "https://raw.githubusercontent.com/nileplumb/PkmnHomeIcons/master/UICONS_OS_128/pokemon"
LARGE_SPRITE_BASE = SPRITE_BASE.replace("/UICONS_OS_128/", "/UICONS_OS/")


@lru_cache(maxsize=1)
def sprite_map():
    return json.loads(
        (
            Path(__file__).resolve().parents[1] / "templates/poracle/shiny-sprites.json"
        ).read_text()
    )


def pokemon_sprite_url(
    pokemon_id, *, form=0, costume=0, gender=0, shiny=False, base=SPRITE_BASE
):
    sprites = sprite_map()
    suffixes = [
        (f"_{tag}{int(value)}", "") if value else ("",)
        for tag, value in (("f", form), ("c", costume), ("g", gender))
    ]
    url = f"{SPRITE_BASE}/0.png"
    for combination in product(*suffixes):
        candidate = f"{SPRITE_BASE}/{int(pokemon_id)}{''.join(combination)}.png"
        if candidate in sprites:
            url = candidate
            break
    if shiny:
        url = sprites.get(url, url)
    return url.replace(SPRITE_BASE, base, 1)
