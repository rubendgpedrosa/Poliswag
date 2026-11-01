import json

import pytest

from modules import art_map
from modules.pokemon_sprites import pokemon_sprite_url


@pytest.fixture
def published(tmp_path, monkeypatch):
    path = tmp_path / "art-map.json"
    path.write_text(
        json.dumps(
            {
                "714:0:85": {
                    "normal": "https://pogoleiria.pt/pokedex-data/sprites/costume/714-0-85-normal.webp",
                    "shiny": "https://pogoleiria.pt/pokedex-data/sprites/costume/714-0-85-shiny.webp",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(art_map, "MAP_PATH", path)
    monkeypatch.setattr(art_map, "_cache", {"mtime": None, "map": {}})
    return path


def test_reviewed_art_wins_for_a_costume_the_icon_set_lacks(published):
    assert published.exists()
    # Noibat's Halloween costume arrives with Golbat's form id (3070).
    assert pokemon_sprite_url(714, form=3070, costume=85).endswith(
        "714-0-85-normal.webp"
    )


def test_shiny_uses_the_reviewed_shiny_twin(published):
    assert published.exists()
    assert pokemon_sprite_url(714, form=3070, costume=85, shiny=True).endswith(
        "714-0-85-shiny.webp"
    )


def test_a_variant_the_table_does_not_list_keeps_the_icon_set(published):
    assert published.exists()
    # No costume, so the reviewed table has nothing to say: the icon set answers.
    assert pokemon_sprite_url(25).endswith("/25.png")


def test_an_unreadable_table_is_not_fatal(tmp_path, monkeypatch):
    broken = tmp_path / "art-map.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(art_map, "MAP_PATH", broken)
    monkeypatch.setattr(art_map, "_cache", {"mtime": None, "map": {}})
    assert art_map.reviewed_art(714, form=3070, costume=85) is None
    assert pokemon_sprite_url(714, form=3070, costume=85).endswith("/714.png")
