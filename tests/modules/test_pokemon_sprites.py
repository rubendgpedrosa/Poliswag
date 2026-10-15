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
