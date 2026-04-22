"""Tests for modules.quest_search.QuestSearch pure-logic methods.

QuestSearch.__init__ loads masterfile JSON files from disk. We bypass __init__
with QuestSearch.__new__(QuestSearch) and set only the attributes each test
needs, then exercise the pure-logic methods directly.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
import requests

from modules.quest_search import QuestSearch


@pytest.fixture
def qs():
    """A QuestSearch instance with its disk-heavy __init__ bypassed."""
    q = QuestSearch.__new__(QuestSearch)
    q.poliswag = MagicMock()
    q.db = MagicMock()
    q.pokemon_name_map = {}
    q.item_name_map = {}
    q.quest_data = None
    q.alternative_quest_data = None
    q.translationfile_data = {
        "data": {
            "quest_catch_pokemon": "Catch {0} Pokémon",
            "quest_throw_great": "Make {0} Great Throws",
        }
    }
    q.masterfile_data = {
        "questRewardTypes": {
            "5": "avatar clothing",
            "6": "quest",
        },
        "items": {
            "1": {"name": "Poké Ball"},
            "2": "Great Ball",
        },
        "pokemon": {
            "25": {"name": "Pikachu"},
            "150": "Mewtwo",
        },
    }
    q.POKEMON_NAME_FILE = "/nonexistent/pokemon.json"
    q.ITEM_NAME_FILE = "/nonexistent/item.json"
    q.UI_ICONS_URL = "https://icons.example/"
    return q


class TestGetPokemonIdByPokemonNameMap:
    def test_returns_ids_containing_substring(self, qs):
        qs.pokemon_name_map = {"1": "bulbasaur", "25": "pikachu", "26": "raichu"}
        assert sorted(qs.get_pokemon_id_by_pokemon_name_map("chu")) == ["25", "26"]

    def test_case_insensitive(self, qs):
        qs.pokemon_name_map = {"25": "pikachu"}
        assert qs.get_pokemon_id_by_pokemon_name_map("PIKA") == ["25"]

    def test_no_matches_returns_empty_list(self, qs):
        qs.pokemon_name_map = {"1": "bulbasaur"}
        assert qs.get_pokemon_id_by_pokemon_name_map("mew") == []

    def test_empty_map_returns_empty_list(self, qs, mocker):
        # Force load-from-file path; patch open to return "{}" so the map stays empty.
        qs.pokemon_name_map = {}
        mocker.patch(
            "builtins.open",
            mocker.mock_open(read_data="{}"),
        )
        assert qs.get_pokemon_id_by_pokemon_name_map("pika") == []


class TestGetItemIdByItemNameMap:
    def test_returns_ids_containing_substring(self, qs):
        qs.item_name_map = {"1": "poké ball", "2": "great ball", "701": "razz berry"}
        assert sorted(qs.get_item_id_by_item_name_map("ball")) == ["1", "2"]

    def test_case_insensitive(self, qs):
        qs.item_name_map = {"701": "razz berry"}
        assert qs.get_item_id_by_item_name_map("RAZZ") == ["701"]

    def test_no_matches_returns_empty_list(self, qs):
        qs.item_name_map = {"1": "poké ball"}
        assert qs.get_item_id_by_item_name_map("mega") == []


class TestIsLocationRelevant:
    def test_leiria_excludes_marinha_lon(self, qs):
        quest = {"lon": "-8.9123"}
        assert qs.is_location_relevant(quest, is_leiria=True) is False

    def test_leiria_includes_non_marinha_lon(self, qs):
        quest = {"lon": "-8.8"}
        assert qs.is_location_relevant(quest, is_leiria=True) is True

    def test_marinha_includes_marinha_lon(self, qs):
        quest = {"lon": "-8.9456"}
        assert qs.is_location_relevant(quest, is_leiria=False) is True

    def test_marinha_excludes_non_marinha_lon(self, qs):
        quest = {"lon": "-8.8"}
        assert qs.is_location_relevant(quest, is_leiria=False) is False

    def test_float_lon_is_stringified(self, qs):
        quest = {"lon": -8.9123}
        assert qs.is_location_relevant(quest, is_leiria=False) is True


class TestGenerateQuestSlugForImage:
    def test_experience_reward(self, qs):
        assert (
            qs.generate_quest_slug_for_image({"quest_reward_type": 1})
            == "reward/experience/0.png"
        )

    def test_item_reward(self, qs):
        quest = {"quest_reward_type": 2, "quest_item_id": 1}
        assert qs.generate_quest_slug_for_image(quest) == "reward/item/1.png"

    def test_stardust_reward(self, qs):
        assert (
            qs.generate_quest_slug_for_image({"quest_reward_type": 3})
            == "reward/stardust/0.png"
        )

    def test_candy_reward(self, qs):
        quest = {"quest_reward_type": 4, "quest_pokemon_id": 25}
        assert qs.generate_quest_slug_for_image(quest) == "reward/candy/25.png"

    def test_pokemon_encounter(self, qs):
        quest = {"quest_reward_type": 7, "quest_pokemon_id": 150}
        assert qs.generate_quest_slug_for_image(quest) == "pokemon/150.png"

    def test_mega_energy(self, qs):
        quest = {"quest_reward_type": 12, "quest_pokemon_id": 150}
        assert qs.generate_quest_slug_for_image(quest) == "reward/mega_energy/150.png"

    def test_fallback_uses_masterfile_reward_type_names(self, qs):
        quest = {"quest_reward_type": 5, "quest_reward_amount": 3}
        # "avatar clothing" → "avatar_clothing" and amount 3.
        assert qs.generate_quest_slug_for_image(quest) == "reward/avatar_clothing/3.png"

    def test_unknown_reward_type_returns_default(self, qs):
        quest = {"quest_reward_type": 999}
        assert qs.generate_quest_slug_for_image(quest) == "reward/unknown/0.png"

    def test_no_masterfile_returns_default(self, qs):
        qs.masterfile_data = None
        assert (
            qs.generate_quest_slug_for_image({"quest_reward_type": 1})
            == "reward/unknown/0.png"
        )

    def test_alternative_fields_are_preferred(self, qs):
        quest = {
            "alternative_quest_reward_type": 7,
            "alternative_quest_pokemon_id": 25,
            # These should be ignored.
            "quest_reward_type": 1,
            "quest_pokemon_id": 150,
        }
        assert qs.generate_quest_slug_for_image(quest) == "pokemon/25.png"


class TestIsQuestRelevant:
    def _leiria_quest(self, **overrides):
        base = {
            "lon": "-8.8",
            "quest_title": "quest_catch_pokemon",
            "quest_pokemon_id": 25,
            "quest_item_id": 1,
            "quest_target": "5",
            "quest_reward_type": 7,
        }
        base.update(overrides)
        return base

    def test_matches_by_translated_title_keyword(self, qs):
        quest = self._leiria_quest()
        assert qs.is_quest_relevant(quest, "catch", [], [], is_leiria=True) is True

    def test_matches_by_pokemon_id(self, qs):
        quest = self._leiria_quest()
        assert qs.is_quest_relevant(quest, "xxx", ["25"], [], is_leiria=True) is True

    def test_matches_by_item_id(self, qs):
        quest = self._leiria_quest()
        assert qs.is_quest_relevant(quest, "xxx", [], ["1"], is_leiria=True) is True

    def test_mega_energy_special_case(self, qs):
        quest = self._leiria_quest(quest_reward_type=12)
        assert (
            qs.is_quest_relevant(quest, "mega energy", [], [], is_leiria=True) is True
        )

    def test_experience_special_case(self, qs):
        quest = self._leiria_quest(quest_reward_type=1)
        assert qs.is_quest_relevant(quest, "experience", [], [], is_leiria=True) is True

    def test_location_irrelevant_returns_false(self, qs):
        quest = self._leiria_quest(lon="-8.9")  # marinha
        assert qs.is_quest_relevant(quest, "catch", [], [], is_leiria=True) is False

    def test_no_match_returns_false(self, qs):
        quest = self._leiria_quest()
        assert qs.is_quest_relevant(quest, "zzz", [], [], is_leiria=True) is False

    def test_missing_translation_data_falls_back_to_raw_title(self, qs):
        qs.translationfile_data = None
        quest = self._leiria_quest(quest_title="quest_catch_pokemon")
        # Raw title contains "catch".
        assert qs.is_quest_relevant(quest, "catch", [], [], is_leiria=True) is True


class TestAddQuestToFoundQuests:
    def test_appends_new_quest_group(self, qs):
        found = []
        quest = {"quest_title": "quest_catch_pokemon", "quest_target": "5"}
        qs.add_quest_to_found_quests(found, quest)
        assert len(found) == 1
        assert found[0]["quest_title"] == "Catch 5 Pokémon"
        assert found[0]["quests"] == [quest]

    def test_merges_into_existing_group_by_title(self, qs):
        found = []
        q1 = {"quest_title": "quest_catch_pokemon", "quest_target": "5"}
        q2 = {"quest_title": "quest_catch_pokemon", "quest_target": "5"}
        qs.add_quest_to_found_quests(found, q1)
        qs.add_quest_to_found_quests(found, q2)
        assert len(found) == 1
        assert found[0]["quests"] == [q1, q2]

    def test_different_targets_produce_different_groups(self, qs):
        found = []
        q1 = {"quest_title": "quest_catch_pokemon", "quest_target": "5"}
        q2 = {"quest_title": "quest_catch_pokemon", "quest_target": "10"}
        qs.add_quest_to_found_quests(found, q1)
        qs.add_quest_to_found_quests(found, q2)
        assert len(found) == 2
        assert {f["quest_title"] for f in found} == {
            "Catch 5 Pokémon",
            "Catch 10 Pokémon",
        }

    def test_none_target_is_stringified_empty(self, qs):
        found = []
        quest = {"quest_title": "quest_catch_pokemon", "quest_target": None}
        qs.add_quest_to_found_quests(found, quest)
        # "{0}" becomes "" → "Catch  Pokémon"
        assert found[0]["quest_title"] == "Catch  Pokémon"

    def test_missing_translation_data_uses_raw_title(self, qs):
        qs.translationfile_data = None
        found = []
        quest = {"quest_title": "quest_catch_pokemon", "quest_target": "5"}
        qs.add_quest_to_found_quests(found, quest)
        assert found[0]["quest_title"] == "quest_catch_pokemon"


class TestGroupPokestopsGeographically:
    def test_small_group_returns_single_cluster(self, qs):
        pokestops = [
            {"lat": "39.7", "lon": "-8.8", "name": "A"},
            {"lat": "39.8", "lon": "-8.8", "name": "B"},
        ]
        result = qs.group_pokestops_geographically(pokestops, max_per_group=10)
        assert result == [pokestops]

    def test_exact_threshold_returns_single_cluster(self, qs):
        pokestops = [{"lat": "39.7", "lon": "-8.8", "name": f"S{i}"} for i in range(10)]
        result = qs.group_pokestops_geographically(pokestops, max_per_group=10)
        assert len(result) == 1
        assert len(result[0]) == 10

    def test_splits_when_exceeding_threshold(self, qs):
        # 20 points spread across two distant clusters.
        pokestops = []
        for i in range(10):
            pokestops.append({"lat": "39.7", "lon": "-8.8", "name": f"N{i}"})
        for i in range(10):
            pokestops.append({"lat": "40.5", "lon": "-7.5", "name": f"S{i}"})
        result = qs.group_pokestops_geographically(pokestops, max_per_group=10)
        assert len(result) >= 2
        # Every stop must land in exactly one group.
        total = sum(len(g) for g in result)
        assert total == 20


class TestGroupPokestopsByReward:
    def test_item_reward_sets_reward_text(self, qs):
        found = [
            {
                "quest_title": "Catch Balls",
                "quests": [
                    {
                        "quest_slug": "reward/item/1.png",
                        "quest_reward_type": 2,
                        "quest_reward_amount": 3,
                        "quest_item_id": 1,
                    }
                ],
            }
        ]
        groups = qs.group_pokestops_by_reward(found)
        assert "reward/item/1.png" in groups
        assert groups["reward/item/1.png"]["reward_text"] == "3x Poké Ball"

    def test_stardust_reward(self, qs):
        found = [
            {
                "quest_title": "Stardust",
                "quests": [
                    {
                        "quest_slug": "reward/stardust/0.png",
                        "quest_reward_type": 3,
                        "quest_reward_amount": 500,
                    }
                ],
            }
        ]
        groups = qs.group_pokestops_by_reward(found)
        assert groups["reward/stardust/0.png"]["reward_text"] == "500 Stardust"

    def test_candy_reward_with_dict_pokemon(self, qs):
        found = [
            {
                "quest_title": "Candy",
                "quests": [
                    {
                        "quest_slug": "reward/candy/25.png",
                        "quest_reward_type": 4,
                        "quest_reward_amount": 3,
                        "quest_pokemon_id": 25,
                    }
                ],
            }
        ]
        groups = qs.group_pokestops_by_reward(found)
        assert groups["reward/candy/25.png"]["reward_text"] == "3 Pikachu Candy"

    def test_pokemon_encounter_reward(self, qs):
        found = [
            {
                "quest_title": "Pika",
                "quests": [
                    {
                        "quest_slug": "pokemon/25.png",
                        "quest_reward_type": 7,
                        "quest_pokemon_id": 25,
                    }
                ],
            }
        ]
        groups = qs.group_pokestops_by_reward(found)
        assert groups["pokemon/25.png"]["reward_text"] == "Pikachu"

    def test_pokemon_encounter_with_string_entry(self, qs):
        found = [
            {
                "quest_title": "Mewtwo",
                "quests": [
                    {
                        "quest_slug": "pokemon/150.png",
                        "quest_reward_type": 7,
                        "quest_pokemon_id": 150,
                    }
                ],
            }
        ]
        groups = qs.group_pokestops_by_reward(found)
        assert groups["pokemon/150.png"]["reward_text"] == "Mewtwo"

    def test_mega_energy_reward(self, qs):
        found = [
            {
                "quest_title": "Mega",
                "quests": [
                    {
                        "quest_slug": "reward/mega_energy/25.png",
                        "quest_reward_type": 12,
                        "quest_reward_amount": 50,
                        "quest_pokemon_id": 25,
                    }
                ],
            }
        ]
        groups = qs.group_pokestops_by_reward(found)
        assert (
            groups["reward/mega_energy/25.png"]["reward_text"]
            == "50 Pikachu Mega Energy"
        )

    def test_experience_reward(self, qs):
        found = [
            {
                "quest_title": "XP",
                "quests": [
                    {
                        "quest_slug": "reward/experience/0.png",
                        "quest_reward_type": 1,
                        "quest_reward_amount": 1000,
                    }
                ],
            }
        ]
        groups = qs.group_pokestops_by_reward(found)
        assert groups["reward/experience/0.png"]["reward_text"] == "1000 XP"

    def test_quest_without_slug_is_skipped(self, qs):
        found = [
            {
                "quest_title": "No slug",
                "quests": [{"quest_reward_type": 1, "quest_reward_amount": 100}],
            }
        ]
        assert qs.group_pokestops_by_reward(found) == {}

    def test_multiple_quests_sharing_slug_are_grouped(self, qs):
        found = [
            {
                "quest_title": "Candy",
                "quests": [
                    {
                        "quest_slug": "reward/candy/25.png",
                        "quest_reward_type": 4,
                        "quest_reward_amount": 3,
                        "quest_pokemon_id": 25,
                        "name": "Stop A",
                    },
                    {
                        "quest_slug": "reward/candy/25.png",
                        "quest_reward_type": 4,
                        "quest_reward_amount": 3,
                        "quest_pokemon_id": 25,
                        "name": "Stop B",
                    },
                ],
            }
        ]
        groups = qs.group_pokestops_by_reward(found)
        assert len(groups["reward/candy/25.png"]["pokestops"]) == 2


# --- load_translation_data / load_masterfile_data / name_map helpers ----------


class TestLoadTranslationData:
    def test_returns_cached_when_fresh(self, qs):
        qs.translationfile_data = {
            "data": {"x": "y"},
            "date": datetime.now().isoformat(),
        }
        result = qs.load_translation_data()
        assert result == qs.translationfile_data

    def test_refreshes_when_stale(self, qs, mocker):
        qs.translationfile_data = {
            "data": {"old": "value"},
            "date": (datetime.now() - timedelta(hours=48)).isoformat(),
        }
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": ['"key1"', '"value1"', "key2", "value2"]
        }
        mocker.patch("modules.quest_search.requests.get", return_value=mock_response)
        result = qs.load_translation_data()
        assert result["data"]["key1"] == "value1"
        assert result["data"]["key2"] == "value2"

    def test_request_exception_logs_and_returns_none(self, qs, mocker):
        qs.translationfile_data = None
        mocker.patch(
            "modules.quest_search.requests.get",
            side_effect=requests.exceptions.ConnectionError("down"),
        )
        assert qs.load_translation_data() is None


class TestLoadMasterfileData:
    def test_returns_false_when_fresh(self, qs):
        qs.masterfile_data = {
            "pokemon": {},
            "items": {},
            "date": datetime.now().isoformat(),
        }
        assert qs.load_masterfile_data() is False

    def test_refreshes_when_stale_and_filters_keys(self, qs, mocker):
        qs.masterfile_data = {
            "pokemon": {},
            "date": (datetime.now() - timedelta(days=2)).isoformat(),
        }
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "pokemon": {"25": {"name": "Pikachu"}},
            "items": {"1": {"name": "Ball"}},
            "questRewardTypes": {"1": "xp"},
            "junk_key": "discard",
        }
        mocker.patch("modules.quest_search.requests.get", return_value=mock_response)
        assert qs.load_masterfile_data() is True
        assert "junk_key" not in qs.masterfile_data
        assert "pokemon" in qs.masterfile_data
        assert "date" in qs.masterfile_data

    def test_request_exception_returns_false(self, qs, mocker):
        qs.masterfile_data = None
        mocker.patch(
            "modules.quest_search.requests.get",
            side_effect=requests.exceptions.ConnectionError("down"),
        )
        assert qs.load_masterfile_data() is False


class TestGeneratePokemonItemNameMap:
    def test_returns_early_when_masterfile_missing(self, qs):
        qs.masterfile_data = None
        qs.generate_pokemon_item_name_map()
        assert qs.pokemon_name_map == {}

    def test_returns_early_when_pokemon_key_missing(self, qs):
        qs.masterfile_data = {"items": {}}
        qs.generate_pokemon_item_name_map()
        assert qs.pokemon_name_map == {}

    def test_populates_name_maps_from_masterfile(self, qs, tmp_path):
        qs.masterfile_data = {
            "pokemon": {
                "25": {"name": "Pikachu"},
                "150": "invalid",  # triggers warning branch
            },
            "items": {
                "1": {"name": "Poké Ball"},
                "2": "Great Ball",  # string form
                "3": 999,  # unexpected — warning branch
            },
        }
        qs.POKEMON_NAME_FILE = str(tmp_path / "pokemon.json")
        qs.ITEM_NAME_FILE = str(tmp_path / "item.json")
        qs.generate_pokemon_item_name_map()
        assert qs.pokemon_name_map == {"25": "pikachu"}
        assert qs.item_name_map == {"1": "poké ball", "2": "great ball"}
        # Files should be written.
        import json as _json

        assert _json.loads((tmp_path / "pokemon.json").read_text()) == {"25": "pikachu"}

    def test_file_write_error_is_logged(self, qs, mocker):
        qs.masterfile_data = {
            "pokemon": {"25": {"name": "Pikachu"}},
            "items": {"1": {"name": "Ball"}},
        }
        qs.POKEMON_NAME_FILE = "/nonexistent/dir/pokemon.json"
        qs.ITEM_NAME_FILE = "/nonexistent/dir/item.json"
        # Should not raise — exception is caught and logged.
        qs.generate_pokemon_item_name_map()
        assert qs.pokemon_name_map == {"25": "pikachu"}


# --- get_quest_data / get_alternative_quest_data (cached DB queries) ----------


class TestGetQuestData:
    def test_returns_cached_when_fresh(self, qs):
        qs.quest_data = {
            "data": [{"name": "stop"}],
            "date": datetime.now().isoformat(),
        }
        result = qs.get_quest_data()
        assert result == qs.quest_data
        qs.db.get_data_from_database.assert_not_called()

    def test_queries_when_stale(self, qs):
        qs.quest_data = {
            "data": [],
            "date": (datetime.now() - timedelta(hours=2)).isoformat(),
        }
        qs.db.get_data_from_database.return_value = [{"name": "fresh"}]
        result = qs.get_quest_data()
        assert result["data"] == [{"name": "fresh"}]
        qs.db.get_data_from_database.assert_called_once()

    def test_queries_when_no_cache(self, qs):
        qs.quest_data = None
        qs.db.get_data_from_database.return_value = [{"name": "first"}]
        result = qs.get_quest_data()
        assert result["data"] == [{"name": "first"}]


class TestGetAlternativeQuestData:
    def test_returns_cached_when_fresh(self, qs):
        qs.alternative_quest_data = {
            "data": [{"name": "ar"}],
            "date": datetime.now().isoformat(),
        }
        result = qs.get_alternative_quest_data()
        assert result == qs.alternative_quest_data
        qs.db.get_data_from_database.assert_not_called()

    def test_queries_when_stale(self, qs):
        qs.alternative_quest_data = None
        qs.db.get_data_from_database.return_value = [{"name": "ar"}]
        result = qs.get_alternative_quest_data()
        assert result["data"] == [{"name": "ar"}]


# --- find_quest_by_search_keyword / find_and_process_quest_by_search_keyword --


class TestFindQuestBySearchKeyword:
    def test_merges_standard_and_alternative_results(self, qs, mocker):
        qs.poliswag = MagicMock()
        qs.poliswag.quest_search = qs
        # Pre-seed name maps so find_and_process_... doesn't try to read files.
        qs.pokemon_name_map = {"25": "pikachu"}
        qs.item_name_map = {"1": "poké ball"}
        qs.quest_data = {
            "data": [
                {
                    "lon": "-8.8",
                    "quest_title": "quest_catch_pokemon",
                    "quest_pokemon_id": 25,
                    "quest_item_id": 1,
                    "quest_target": "5",
                    "quest_reward_type": 7,
                    "name": "Leiria Stop",
                    "lat": 39.7,
                    "url": "",
                }
            ],
            "date": datetime.now().isoformat(),
        }
        qs.alternative_quest_data = {"data": [], "date": datetime.now().isoformat()}
        mocker.patch.object(
            qs, "generate_quest_slug_for_image", return_value="slug.png"
        )
        result = qs.find_quest_by_search_keyword("catch", is_leiria=True)
        assert result is not None
        assert result[0]["quest_title"] == "Catch 5 Pokémon"

    def test_returns_none_when_nothing_found(self, qs):
        qs.pokemon_name_map = {"25": "pikachu"}
        qs.item_name_map = {"1": "poké ball"}
        qs.quest_data = {"data": [], "date": datetime.now().isoformat()}
        qs.alternative_quest_data = {"data": [], "date": datetime.now().isoformat()}
        assert qs.find_quest_by_search_keyword("nothing", is_leiria=True) is None


# --- create_quest_embed --------------------------------------------------------


class TestCreateQuestEmbed:
    def test_leiria_embed_is_blue_with_thumbnail(self, qs):
        stops = [
            {
                "lat": 39.7,
                "lon": -8.8,
                "name": "Stop A",
                "quest_slug": "reward/item/1.png",
            }
        ]
        embed = qs.create_quest_embed("Catch 5 Pokémon", stops, is_leiria=True)
        assert embed.title == "Catch 5 Pokémon"
        assert "Leiria" in embed.description
        assert embed.color == discord.Color.blue()
        assert embed.thumbnail.url == "https://icons.example/reward/item/1.png"
        assert len(embed.fields) == 1
        assert "Stop A" in embed.fields[0].name

    def test_marinha_embed_is_green(self, qs):
        embed = qs.create_quest_embed(
            "T", [{"lat": 39.7, "lon": -8.9, "name": "S"}], is_leiria=False
        )
        assert "Marinha" in embed.description
        assert embed.color == discord.Color.green()

    def test_pagination_footer(self, qs):
        embed = qs.create_quest_embed("T", [], is_leiria=True, page=2, total_pages=5)
        assert "Página 2/5" in embed.footer.text

    def test_no_thumbnail_when_stops_lack_slug(self, qs):
        embed = qs.create_quest_embed(
            "T", [{"lat": 0, "lon": 0, "name": "X"}], is_leiria=True
        )
        assert embed.thumbnail.url is None


# --- check_tracked -------------------------------------------------------------


class TestCheckTracked:
    async def test_empty_tracked_list_logs_and_exits(self, qs):
        qs.poliswag = MagicMock()
        qs.poliswag.db.get_data_from_database.return_value = []
        channel = MagicMock()
        channel.send = AsyncMock()
        await qs.check_tracked(channel)
        channel.send.assert_not_called()
        qs.poliswag.utility.log_to_file.assert_called_once()

    async def test_no_matches_logs_and_exits_without_embeds(self, qs, mocker):
        qs.poliswag = MagicMock()
        qs.poliswag.db.get_data_from_database.return_value = [{"target": "ditto"}]
        qs.poliswag.quest_search = qs
        mocker.patch.object(qs, "find_quest_by_search_keyword", return_value=None)
        channel = MagicMock()
        channel.send = AsyncMock()
        await qs.check_tracked(channel)
        # Header was sent, but no summary embeds.
        assert channel.send.await_count == 1
        log_msgs = [c.args[0] for c in qs.poliswag.utility.log_to_file.call_args_list]
        assert any("Nenhuma quest" in m for m in log_msgs)

    async def test_sends_summary_embeds_when_matches_found(self, qs, mocker):
        qs.poliswag = MagicMock()
        qs.poliswag.db.get_data_from_database.return_value = [{"target": "pikachu"}]
        qs.poliswag.quest_search = qs
        mocker.patch.object(
            qs,
            "find_quest_by_search_keyword",
            side_effect=[
                # leiria hit
                [
                    {
                        "quest_title": "Pikachu",
                        "quests": [
                            {
                                "quest_slug": "pokemon/25.png",
                                "quest_reward_type": 7,
                                "quest_pokemon_id": 25,
                                "name": "A",
                            }
                        ],
                    }
                ],
                # marinha hit
                [
                    {
                        "quest_title": "Pikachu",
                        "quests": [
                            {
                                "quest_slug": "pokemon/25.png",
                                "quest_reward_type": 7,
                                "quest_pokemon_id": 25,
                                "name": "B",
                            }
                        ],
                    }
                ],
            ],
        )
        build_summary = mocker.patch(
            "modules.embeds.build_tracked_summary_embeds", new=AsyncMock()
        )
        channel = MagicMock()
        channel.send = AsyncMock()
        await qs.check_tracked(channel)
        # Header send plus two summary invocations (leiria + marinha).
        assert build_summary.await_count == 2
