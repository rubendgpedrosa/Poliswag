"""Tests for modules.pokemon_name_sync."""

from unittest.mock import AsyncMock, MagicMock

from modules.pokemon_name_sync import build_pokemon_name_rows, sync_pokemon_names

MASTERFILE = {
    "pokemon": {
        "888": {
            "name": "Zacian",
            "defaultFormId": 2577,
            "family": 888,
            "forms": {"2576": {"name": "Crowned Sword"}, "2577": {"name": "Hero"}},
        },
        "25": {
            "name": "Pikachu",
            "defaultFormId": 598,
            "family": 25,
            "forms": {
                "598": {"name": "Normal"},
                "2335": {"name": "Normal"},
                "950": {"name": "Libre", "isCostume": True},
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
            (1, 0, "Bulbasaur", None, None, 0),
            (25, 0, "Pikachu", None, 25, 0),
            (888, 0, "Zacian", None, 888, 0),
        ]

    def test_keeps_named_non_default_forms(self):
        rows = build_pokemon_name_rows(MASTERFILE)
        assert (888, 2576, "Zacian", "Crowned Sword", None, 0) in rows

    def test_flags_costumes(self):
        rows = build_pokemon_name_rows(MASTERFILE)
        assert (25, 950, "Pikachu", "Libre", None, 1) in rows

    def test_treats_dated_and_numbered_event_forms_as_costumes(self):
        rows = build_pokemon_name_rows(
            {
                "pokemon": {
                    "4": {
                        "name": "Charmander",
                        "forms": {
                            "3354": {"name": "Goggles 2026"},
                            "10": {"name": "Tshirt 04"},
                            "11": {"name": "Flying 05"},
                        },
                    },
                    "493": {
                        "name": "Arceus",
                        "forms": {"12": {"name": "Flying"}, "13": {"name": "Galarian"}},
                    },
                }
            }
        )
        flags = {r[3]: r[5] for r in rows if r[1] != 0}
        assert flags == {
            "Goggles 2026": 1,
            "Tshirt 04": 1,
            "Flying 05": 1,
            "Flying": 0,
            "Galarian": 0,
        }

    def test_skips_default_normal_and_unnamed_forms(self):
        form_ids = {r[1] for r in build_pokemon_name_rows(MASTERFILE) if r[1] != 0}
        assert form_ids == {2576, 950}

    def test_ignores_a_non_numeric_family(self):
        rows = build_pokemon_name_rows(
            {"pokemon": {"7": {"name": "Squirtle", "family": "junk", "forms": {}}}}
        )
        assert rows == [(7, 0, "Squirtle", None, None, 0)]

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
            "INSERT INTO pokemon_name "
            "(pokemon_id, form_id, name, form_name, family_id, is_costume) VALUES "
        )
        assert query.count("(%s, %s, %s, %s, %s, %s)") == 5
        assert "ON DUPLICATE KEY UPDATE" in query
        assert len(params) == 30

    async def test_empty_masterfile_writes_nothing(self):
        db = MagicMock()
        db.execute_query_to_database = AsyncMock()

        assert await sync_pokemon_names(db, {}) == 0
        db.execute_query_to_database.assert_not_awaited()
