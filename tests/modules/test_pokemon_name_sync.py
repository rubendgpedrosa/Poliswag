"""Tests for modules.pokemon_name_sync."""

from unittest.mock import AsyncMock, MagicMock

from modules.pokemon_name_sync import build_pokemon_name_rows, sync_pokemon_names

MASTERFILE = {
    "pokemon": {
        "888": {
            "name": "Zacian",
            "defaultFormId": 2577,
            "forms": {"2576": {"name": "Crowned Sword"}, "2577": {"name": "Hero"}},
        },
        "25": {
            "name": "Pikachu",
            "defaultFormId": 598,
            "forms": {
                "598": {"name": "Normal"},
                "2335": {"name": "Normal"},
                "950": {"name": "Libre"},
            },
        },
        "1": {"name": "Bulbasaur", "forms": {"163": {}, "897": {}}},
    }
}


class TestBuildPokemonNameRows:
    def test_one_species_row_per_pokemon(self):
        rows = build_pokemon_name_rows(MASTERFILE)
        species = sorted(r for r in rows if r[1] == 0)
        assert species == [
            (1, 0, "Bulbasaur", None),
            (25, 0, "Pikachu", None),
            (888, 0, "Zacian", None),
        ]

    def test_keeps_named_non_default_forms(self):
        rows = build_pokemon_name_rows(MASTERFILE)
        assert (888, 2576, "Zacian", "Crowned Sword") in rows
        assert (25, 950, "Pikachu", "Libre") in rows

    def test_skips_default_normal_and_unnamed_forms(self):
        form_ids = {r[1] for r in build_pokemon_name_rows(MASTERFILE) if r[1] != 0}
        assert form_ids == {2576, 950}

    def test_skips_entries_without_a_name(self):
        rows = build_pokemon_name_rows({"pokemon": {"999": {"forms": {}}, "4": "junk"}})
        assert rows == []

    def test_missing_masterfile_gives_no_rows(self):
        assert build_pokemon_name_rows(None) == []
        assert build_pokemon_name_rows({}) == []


class TestSyncPokemonNames:
    async def test_upserts_all_rows_in_one_statement(self):
        db = MagicMock()
        db.execute_query_to_database = AsyncMock(return_value=5)

        written = await sync_pokemon_names(db, MASTERFILE)

        assert written == 5
        db.execute_query_to_database.assert_awaited_once()
        query = db.execute_query_to_database.await_args.args[0]
        params = db.execute_query_to_database.await_args.kwargs["params"]
        assert query.startswith(
            "INSERT INTO pokemon_name (pokemon_id, form_id, name, form_name) VALUES "
        )
        assert query.count("(%s, %s, %s, %s)") == 5
        assert "ON DUPLICATE KEY UPDATE" in query
        assert len(params) == 20

    async def test_empty_masterfile_writes_nothing(self):
        db = MagicMock()
        db.execute_query_to_database = AsyncMock()

        assert await sync_pokemon_names(db, {}) == 0
        db.execute_query_to_database.assert_not_awaited()
