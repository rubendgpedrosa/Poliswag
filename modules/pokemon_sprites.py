"""Checked Pokémon artwork shared by alert thumbnails and trade summaries."""

import json
from functools import lru_cache
from itertools import product
from pathlib import Path

from modules.art_map import reviewed_art

# Where the filenames are checked: the upstream set the snapshot was built from.
CDN_SPRITE_BASE = "https://raw.githubusercontent.com/nileplumb/PkmnHomeIcons/master/UICONS_OS_128/pokemon"
# The 512px set has no reviewed twin; trade digests keep using it for the big image.
LARGE_SPRITE_BASE = CDN_SPRITE_BASE.replace("/UICONS_OS_128/", "/UICONS_OS/")


@lru_cache(maxsize=1)
def sprite_map():
    return json.loads(
        (
            Path(__file__).resolve().parents[1] / "templates/poracle/shiny-sprites.json"
        ).read_text()
    )


def pokemon_sprite_url(
    pokemon_id, *, form=0, costume=0, gender=0, shiny=False, base=None
):
    """Artwork for an embed.

    ``base`` defaults to the upstream set: an embed image is fetched by Discord,
    not by us, and pointing that traffic at our own origin traded a CDN that
    always answers for one that can be challenged — which is how the shiny
    alerts lost their pictures. Our own set is used only for the reviewed art
    the CDN does not have, through ``reviewed_art`` above.
    """
    # Reviewed art the icon set does not carry (most event costumes) comes from
    # the site's published table first, so a Discord embed shows the same
    # artwork as the Pokédex. Its files are absolute and single-sized, so the
    # `base` argument does not apply to them.
    reviewed = reviewed_art(pokemon_id, form=form, costume=costume, shiny=shiny)
    if reviewed:
        return reviewed
    sprites = sprite_map()
    url = f"{CDN_SPRITE_BASE}/0.png"
    # A costume is filed under its canonical form (0) far more often than under
    # the form the scanner reports, which is Golbat's own id for it: Noibat's
    # Halloween costume arrives as form 3070 and is drawn as 714_c85.png.
    forms = [form, 0] if costume and form else [form]
    for current in forms:
        suffixes = [
            (f"_{tag}{int(value)}", "") if value else ("",)
            for tag, value in (("f", current), ("c", costume), ("g", gender))
        ]
        for combination in product(*suffixes):
            candidate = f"{CDN_SPRITE_BASE}/{int(pokemon_id)}{''.join(combination)}.png"
            if candidate in sprites:
                url = candidate
                break
        if url != f"{CDN_SPRITE_BASE}/0.png":
            break
    if url == f"{CDN_SPRITE_BASE}/0.png":
        # No costume file, but the species is there: show the ordinary Pokémon
        # rather than the question-mark placeholder. The reviewed art for a
        # costume nobody has drawn lives in the site's own catalogue.
        species = f"{CDN_SPRITE_BASE}/{int(pokemon_id)}.png"
        if species in sprites:
            url = species
    if shiny:
        url = sprites.get(url, url)
    if base is None:
        return url
    return url.replace(CDN_SPRITE_BASE, base, 1)
