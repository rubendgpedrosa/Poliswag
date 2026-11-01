from modules.pokemon_sprites import LARGE_SPRITE_BASE, pokemon_sprite_url, sprite_map


def test_shiny_and_normal_art_are_distinct():
    assert pokemon_sprite_url(25, shiny=True).endswith("/25_s.png")
    assert pokemon_sprite_url(25).endswith("/25.png")


def test_available_form_is_preserved():
    assert pokemon_sprite_url(570, form=2796, shiny=True).endswith("/570_f2796_s.png")


def test_unknown_form_falls_back_to_available_shiny_species():
    assert pokemon_sprite_url(60, form=2332, shiny=True).endswith("/60_s.png")


def test_unknown_species_uses_placeholder():
    assert pokemon_sprite_url(99999, shiny=True).endswith("/0.png")


def test_large_art_uses_the_same_variant():
    assert pokemon_sprite_url(25, shiny=True, base=LARGE_SPRITE_BASE).endswith(
        "/UICONS_OS/pokemon/25_s.png"
    )


def test_map_never_points_at_an_unlisted_image():
    sprites = sprite_map()
    assert all(url in sprites for url in sprites.values())


def test_costume_filed_under_the_canonical_form_still_draws():
    # Noibat's Halloween costume arrives with Golbat's form id (3070) and the
    # set files it as 714_c85.png; before this fallback the alert showed the
    # placeholder while the site showed the right art.
    # Reviewed art wins over the icon set, and we serve that art ourselves.
    assert pokemon_sprite_url(714, form=3070, costume=85).endswith(
        "714-0-85-normal.webp"
    )


def test_an_exact_form_and_costume_still_wins():
    # Reviewed art for this costume exists, so it takes precedence over the set.
    assert pokemon_sprite_url(263, form=946, costume=47).endswith(
        "263-946-47-normal.webp"
    )
