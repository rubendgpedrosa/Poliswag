"""Verified costume catalogue identities, never synthetic form IDs."""

import importlib.util

MASTER = {
    "pokemon": {
        "25": {"name": "Pikachu", "family": 25, "forms": {"2676": {"name": "Pop Star"}}}
    }
}
CATALOGUE = {
    "costume_enums": {"1": {"proto": "HOLIDAY_2016"}},
    "variants": [
        {
            "pokemon_id": 25,
            "form_id": 0,
            "costume_id": 1,
            "form_name": "Santa Hat",
            "sources": ["https://9db.jp/pokego/data/51"],
        }
    ],
}


def test_legacy_costume_is_a_real_separate_identity():
    spec = importlib.util.find_spec("modules.pokemon_costume_sync")
    build = (
        __import__(
            "modules.pokemon_costume_sync", fromlist=["build_costume_rows"]
        ).build_costume_rows
        if spec
        else lambda *args: []
    )
    assert build(MASTER, CATALOGUE) == [(25, 0, 1, "Pikachu", "Santa Hat", None, 1)]


def test_unknown_costume_enum_is_rejected():
    import copy
    import pytest
    from modules.pokemon_costume_sync import build_costume_rows

    invalid = copy.deepcopy(CATALOGUE)
    invalid["variants"][0]["costume_id"] = 999
    with pytest.raises(ValueError, match="costume enum"):
        build_costume_rows(MASTER, invalid)


def test_form_must_belong_to_the_same_species():
    import copy
    import pytest
    from modules.pokemon_costume_sync import build_costume_rows

    invalid = copy.deepcopy(CATALOGUE)
    invalid["variants"][0].update(form_id=9999, costume_id=0)
    with pytest.raises(ValueError, match="form"):
        build_costume_rows(MASTER, invalid)


def test_source_is_required_for_every_imported_variant():
    import copy
    import pytest
    from modules.pokemon_costume_sync import build_costume_rows

    invalid = copy.deepcopy(CATALOGUE)
    invalid["variants"][0]["sources"] = []
    with pytest.raises(ValueError, match="source"):
        build_costume_rows(MASTER, invalid)


def test_duplicate_game_identity_cannot_merge_two_costumes():
    import copy
    import pytest
    from modules.pokemon_costume_sync import build_costume_rows

    invalid = copy.deepcopy(CATALOGUE)
    invalid["variants"].append(copy.deepcopy(invalid["variants"][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        build_costume_rows(MASTER, invalid)


async def test_sync_only_upserts_catalogue_never_user_selections():
    from unittest.mock import AsyncMock, MagicMock
    import modules.pokemon_costume_sync as sync

    db = MagicMock()
    db.execute_query_to_database = AsyncMock()
    fn = getattr(sync, "sync_pokemon_costumes", None)
    assert fn is not None, "A verified upsert-only sync is required"
    assert await fn(db, MASTER, CATALOGUE) == 1
    db.execute_query_to_database.assert_awaited_once()
    call = db.execute_query_to_database.await_args
    assert call.args[0].startswith("INSERT INTO pokemon_costume ")
    assert "ON DUPLICATE KEY UPDATE" in call.args[0]
    assert "collection_entry" not in call.args[0]
    assert "trade_entry" not in call.args[0]
    assert "DELETE" not in call.args[0]
    assert call.kwargs["params"] == (25, 0, 1, "Pikachu", "Santa Hat", None, 1)


def test_versioned_catalogue_loads_all_reviewed_source_entries():
    import modules.pokemon_costume_sync as sync

    load = getattr(sync, "load_costume_catalogue", None)
    assert load is not None, "A versioned source-backed catalogue loader is required"
    catalogue = load()
    assert len(catalogue["variants"]) == 323
    assert (
        catalogue["source_entry_count"] == catalogue["mapped_source_entry_count"] == 323
    )
    assert catalogue["pending"] == []


def test_non_integer_or_plain_species_identity_is_rejected():
    import copy
    import pytest
    from modules.pokemon_costume_sync import build_costume_rows

    for changes in [
        {"costume_id": "1"},
        {"pokemon_id": "25"},
        {"form_id": False},
        {"costume_id": 0, "form_id": 0},
        {"costume_id": -1},
    ]:
        invalid = copy.deepcopy(CATALOGUE)
        invalid["variants"][0].update(changes)
        with pytest.raises(ValueError):
            build_costume_rows(MASTER, invalid)
