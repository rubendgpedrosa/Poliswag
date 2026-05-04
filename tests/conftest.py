import os
from dotenv import load_dotenv

# Load test-safe env vars before Config is imported by any test module.
# .env.test takes priority only when DISCORD_API_KEY is not already set,
# so a real .env in the working directory is not overridden.
if not os.environ.get("DISCORD_API_KEY"):
    load_dotenv(".env.test", override=False)

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _prevent_real_db_connections():
    """Block all pymysql.connect calls so tests never need a live database."""
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    with patch("pymysql.connect", return_value=mock_conn):
        yield


@pytest.fixture
def sample_translations() -> dict:
    return {
        "quest_catch_pokemon": "Catch {0} Pokémon",
        "quest_throw_great": "Make {0} Great Throws",
    }


@pytest.fixture
def sample_name_maps() -> tuple[dict, dict]:
    pokemon_names = {"1": "Bulbasaur", "25": "Pikachu", "150": "Mewtwo"}
    item_names = {"1": "Poké Ball", "2": "Great Ball", "701": "Razz Berry"}
    return pokemon_names, item_names
