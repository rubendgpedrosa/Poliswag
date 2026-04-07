import pytest


@pytest.fixture
def sample_translations() -> dict:
    """Translations dict shape consumed by QuestExporter._translate_title.

    This is the inner ``data`` mapping extracted from the masterfile, keyed by
    lowercase quest_title with ``{0}`` placeholders for the target count.
    """
    return {
        "quest_catch_pokemon": "Catch {0} Pokémon",
        "quest_throw_great": "Make {0} Great Throws",
    }


@pytest.fixture
def sample_name_maps() -> tuple[dict, dict]:
    """(pokemon_names, item_names) keyed by stringified IDs, matching masterfile shape."""
    pokemon_names = {"1": "Bulbasaur", "25": "Pikachu", "150": "Mewtwo"}
    item_names = {"1": "Poké Ball", "2": "Great Ball", "701": "Razz Berry"}
    return pokemon_names, item_names
