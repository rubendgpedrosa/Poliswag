import hashlib

import pytest

from modules.quest_exporter import ITEM_EMOJI, QuestExporter


class TestTranslateTitle:
    def test_known_key_substitutes_target(self, sample_translations):
        result = QuestExporter._translate_title(
            "quest_catch_pokemon", 10, sample_translations
        )
        assert result == "Catch 10 Pokémon"

    def test_known_key_is_case_insensitive(self, sample_translations):
        result = QuestExporter._translate_title(
            "QUEST_CATCH_POKEMON", 3, sample_translations
        )
        assert result == "Catch 3 Pokémon"

    def test_none_target_becomes_empty_string(self, sample_translations):
        result = QuestExporter._translate_title(
            "quest_catch_pokemon", None, sample_translations
        )
        assert result == "Catch  Pokémon"

    def test_missing_key_falls_back_to_prettified_key(self, sample_translations):
        result = QuestExporter._translate_title(
            "quest_walk_with_buddy", 5, sample_translations
        )
        # "quest " stripped, "_" -> " ", Title Case, then {0} substitution
        # Source has no {0} placeholder in the fallback, so target is unused here.
        assert result == "Walk With Buddy"

    def test_missing_key_with_empty_translations(self):
        result = QuestExporter._translate_title("quest_do_thing", 2, {})
        assert result == "Do Thing"


class TestGetZone:
    def test_marinha_zone_when_longitude_matches_prefix(self):
        assert QuestExporter._get_zone("-8.9123") == "marinha"

    def test_leiria_zone_for_other_longitudes(self):
        assert QuestExporter._get_zone("-8.8456") == "leiria"

    def test_float_input_is_stringified(self):
        assert QuestExporter._get_zone(-8.9321) == "marinha"
        assert QuestExporter._get_zone(-8.8) == "leiria"


class TestCategorize:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            # throwing
            ("Make 3 Great Throws", "throwing"),
            ("Land a Curve Ball", "throwing"),
            ("THROW Excellent", "throwing"),
            # battling
            ("Win a Raid", "battling"),
            ("Battle in GO Battle League", "battling"),
            ("Defeat a Team GO Rocket Grunt", "battling"),
            ("Fight in a Gym", "battling"),
            ("Win 5 battles", "battling"),
            # buddy / social
            ("Walk 5 km with your buddy", "buddy"),
            ("Earn 3 hearts with your buddy", "buddy"),
            ("Give your buddy a treat", "buddy"),
            ("Send a gift to a friend", "buddy"),
            ("Trade a Pokémon", "buddy"),
            ("Make a new friend", "buddy"),
            # catching
            ("Catch 10 Pokémon", "catching"),
            ("Hatch an egg", "catching"),
            # default
            ("Take a snapshot", "others"),
            ("Power up a Pokémon", "others"),
        ],
    )
    def test_categorize_maps_title_to_expected_bucket(self, title, expected):
        assert QuestExporter._categorize(title) == expected

    def test_categorize_is_case_insensitive(self):
        assert QuestExporter._categorize("CATCH 5 POKEMON") == "catching"

    def test_throwing_takes_precedence_over_battling(self):
        # A title containing both keywords should match the first rule checked.
        assert QuestExporter._categorize("Curve ball in a gym battle") == "throwing"


class TestMakeId:
    def test_is_deterministic_for_same_inputs(self):
        a = QuestExporter._make_id("Catch 10 Pokémon", "xp")
        b = QuestExporter._make_id("Catch 10 Pokémon", "xp")
        assert a == b

    def test_different_inputs_produce_different_ids(self):
        a = QuestExporter._make_id("Catch 10 Pokémon", "xp")
        b = QuestExporter._make_id("Catch 10 Pokémon", "stardust")
        c = QuestExporter._make_id("Make 3 Great Throws", "xp")
        assert len({a, b, c}) == 3

    def test_id_is_always_twelve_characters(self):
        assert len(QuestExporter._make_id("title", "type")) == 12
        assert len(QuestExporter._make_id("", "")) == 12
        assert len(QuestExporter._make_id("a" * 500, "z")) == 12

    def test_id_is_prefix_of_md5_hex(self):
        title, reward_type = "Catch 10 Pokémon", "xp"
        expected = hashlib.md5(f"{title}|{reward_type}".encode()).hexdigest()[:12]
        assert QuestExporter._make_id(title, reward_type) == expected


class TestMapReward:
    def test_xp_with_amount(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(
            1, 500, None, None, pokemon_names, item_names
        )
        assert result == {"type": "xp", "label": "XP x500", "emoji": "⭐"}

    def test_xp_without_amount(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(
            1, None, None, None, pokemon_names, item_names
        )
        assert result == {"type": "xp", "label": "XP", "emoji": "⭐"}

    def test_item_known_with_suffix(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(2, 5, 1, None, pokemon_names, item_names)
        assert result == {
            "type": "item_1",
            "label": "Poké Ball x5",
            "emoji": ITEM_EMOJI[1],
        }

    def test_item_with_amount_one_has_no_suffix(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(2, 1, 2, None, pokemon_names, item_names)
        assert result["label"] == "Great Ball"
        assert result["emoji"] == ITEM_EMOJI[2]

    def test_item_with_berry_emoji(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(2, 3, 701, None, pokemon_names, item_names)
        assert result["emoji"] == ITEM_EMOJI[701]
        assert result["label"] == "Razz Berry x3"

    def test_item_unknown_falls_back_to_generic_label_and_emoji(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(2, 2, 999, None, pokemon_names, item_names)
        assert result == {
            "type": "item_999",
            "label": "Item #999 x2",
            "emoji": "🎒",
        }

    @pytest.mark.parametrize("reward_type", [3, 4])
    def test_stardust_reward_types(self, reward_type, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(
            reward_type, 1000, None, None, pokemon_names, item_names
        )
        assert result == {
            "type": "stardust",
            "label": "Stardust x1000",
            "emoji": "✨",
        }

    def test_stardust_without_amount(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(
            3, None, None, None, pokemon_names, item_names
        )
        assert result["label"] == "Stardust"

    def test_encounter_with_known_pokemon(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(7, None, None, 25, pokemon_names, item_names)
        assert result == {
            "type": "encounter_25",
            "label": "Pikachu encounter",
            "emoji": "🎯",
        }

    def test_encounter_with_unknown_pokemon_falls_back_to_hash_id(
        self, sample_name_maps
    ):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(
            7, None, None, 9999, pokemon_names, item_names
        )
        assert result["label"] == "#9999 encounter"
        assert result["type"] == "encounter_9999"

    def test_mega_energy_with_amount(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(12, 50, None, 25, pokemon_names, item_names)
        assert result == {
            "type": "mega_energy",
            "label": "Mega Energy x50",
            "emoji": "⚡",
        }

    def test_xl_candy_with_amount(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(
            13, 20, None, None, pokemon_names, item_names
        )
        assert result == {
            "type": "xl_candy",
            "label": "XL Candy x20",
            "emoji": "🍬",
        }

    def test_unknown_reward_type_returns_generic_reward(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(99, 0, None, None, pokemon_names, item_names)
        assert result == {"type": "reward_99", "label": "Reward", "emoji": "🎁"}


class TestItemEmojiConstant:
    def test_known_item_ids_have_emoji(self):
        # Guard: if someone removes these mappings, item reward rendering breaks.
        assert ITEM_EMOJI[1] == "⚪"
        assert ITEM_EMOJI[2] == "🔵"
        assert ITEM_EMOJI[701] == "🍓"


class TestExport:
    """End-to-end test for QuestExporter.export().

    export() reads two DB queries, merges rows by (title|reward-type),
    categorises/zone-tags each pokestop, and writes the result as JSON.
    """

    async def test_writes_merged_quests_to_json(self, tmp_path, mocker):
        from unittest.mock import MagicMock
        from modules.quest_exporter import QuestExporter

        poliswag = MagicMock()
        qs = poliswag.quest_search
        qs.translationfile_data = {"data": {"quest_catch_pokemon": "Catch {0} Pokémon"}}
        qs.masterfile_data = {
            "pokemon": {"25": {"name": "Pikachu"}, "150": "Mewtwo"},
            "items": {"1": {"name": "Poké Ball"}, "2": "Great Ball"},
        }
        standard_rows = [
            {
                "name": "Fonte Luminosa",
                "lat": 39.75,
                "lon": -8.80,  # Leiria (no -8.9 substring)
                "url": "https://img/stop1.png",
                "quest_title": "quest_catch_pokemon",
                "quest_target": 5,
                "quest_reward_type": 7,  # pokemon
                "quest_item_id": None,
                "quest_pokemon_id": 25,
                "quest_reward_amount": 1,
            },
            {
                "name": "Praça",
                "lat": 39.76,
                "lon": -8.9123,  # Marinha
                "url": None,
                "quest_title": "quest_catch_pokemon",
                "quest_target": 5,
                "quest_reward_type": 7,
                "quest_item_id": None,
                "quest_pokemon_id": 25,
                "quest_reward_amount": 1,
            },
        ]
        ar_rows = [
            {
                "name": "Monumento",
                "lat": 39.77,
                "lon": -8.80,
                "url": "https://img/stop3.png",
                "quest_title": "quest_catch_pokemon",
                "quest_target": 3,
                "quest_reward_type": 2,  # item
                "quest_item_id": 1,
                "quest_pokemon_id": None,
                "quest_reward_amount": 3,
            }
        ]

        def fake_get_data(query):
            return (
                ar_rows if "alternative_quest_reward_type" in query else standard_rows
            )

        qs.db.get_data_from_database.side_effect = fake_get_data

        output = tmp_path / "quests.json"
        exporter = QuestExporter(poliswag=poliswag)
        exporter.output_path = str(output)

        await exporter.export()

        import json

        payload = json.loads(output.read_text())
        assert "generatedAt" in payload
        assert payload["generatedAt"].endswith("Z")
        quests = payload["quests"]
        # Two unique (title|reward-type) keys → two merged quest entries:
        #  1. standard "Catch 5 Pokémon" with both pokestops
        #  2. AR "[AR] Catch 3 Pokémon" with one pokestop
        assert len(quests) == 2
        ar_quest = next(q for q in quests if q["title"].startswith("[AR]"))
        standard_quest = next(q for q in quests if not q["title"].startswith("[AR]"))

        assert standard_quest["stopsCount"] == 2
        assert ar_quest["stopsCount"] == 1

        # Zone tagging applied per pokestop.
        zones = {s["zone"] for s in standard_quest["pokestops"]}
        assert zones == {"leiria", "marinha"}

        # Image URLs are passed through when present, absent otherwise.
        stop_with_image = next(
            s for s in standard_quest["pokestops"] if s["name"] == "Fonte Luminosa"
        )
        assert stop_with_image["imageUrl"] == "https://img/stop1.png"
        stop_without_image = next(
            s for s in standard_quest["pokestops"] if s["name"] == "Praça"
        )
        assert "imageUrl" not in stop_without_image

        # Each pokestop is marked as not-done and has location floats.
        assert all(s["done"] is False for s in standard_quest["pokestops"])
        assert isinstance(standard_quest["pokestops"][0]["location"]["lat"], float)

    async def test_handles_missing_translations_and_masterfile(self, tmp_path):
        from unittest.mock import MagicMock
        from modules.quest_exporter import QuestExporter

        poliswag = MagicMock()
        qs = poliswag.quest_search
        qs.translationfile_data = None
        qs.masterfile_data = None
        qs.db.get_data_from_database.return_value = []

        output = tmp_path / "quests.json"
        exporter = QuestExporter(poliswag=poliswag)
        exporter.output_path = str(output)
        await exporter.export()

        import json

        payload = json.loads(output.read_text())
        assert payload["quests"] == []
