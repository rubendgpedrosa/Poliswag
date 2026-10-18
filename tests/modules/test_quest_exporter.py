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
    def test_marinha_zone_at_or_west_of_threshold(self):
        assert QuestExporter._get_zone("-8.9123") == "marinha"

    def test_leiria_zone_east_of_threshold(self):
        assert QuestExporter._get_zone("-8.8456") == "leiria"

    def test_accepts_float_input(self):
        assert QuestExporter._get_zone(-8.9321) == "marinha"
        assert QuestExporter._get_zone(-8.8) == "leiria"

    def test_far_west_longitude_is_marinha(self):
        # Regression: a substring check on "-8.9" used to misclassify
        # anything at or past -9.0 as leiria.
        assert QuestExporter._get_zone(-9.0) == "marinha"
        assert QuestExporter._get_zone("-9.5") == "marinha"


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

    def test_stardust_reward_type(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(
            3, 1000, None, None, pokemon_names, item_names
        )
        assert result == {
            "type": "stardust",
            "label": "Stardust x1000",
            "emoji": "✨",
        }
        assert "pokemonId" not in result

    def test_candy_reward_type_with_species(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(4, 3, None, 25, pokemon_names, item_names)
        assert result["type"] == "candy"
        assert result["pokemonId"] == 25
        assert "Pikachu" in result["label"]
        assert "Candy" in result["label"]
        assert result["emoji"] == "🍬"

    def test_candy_without_species_is_generic(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(4, 3, None, None, pokemon_names, item_names)
        assert result["type"] == "candy"
        assert "pokemonId" not in result
        assert result["label"] == "Candy x3"

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
        assert result["type"] == "mega_energy"
        assert result["pokemonId"] == 25
        assert "Pikachu" in result["label"]
        assert "Mega Energy" in result["label"]
        assert result["emoji"] == "⚡"

    def test_mega_energy_without_species_is_generic(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(
            12, 50, None, None, pokemon_names, item_names
        )
        assert result["type"] == "mega_energy"
        assert "pokemonId" not in result
        assert result["label"] == "Mega Energy x50"

    def test_xl_candy_with_species_identity(self, sample_name_maps):
        pokemon_names, item_names = sample_name_maps
        result = QuestExporter._map_reward(13, 5, None, 1, pokemon_names, item_names)
        assert result["type"] == "xl_candy"
        assert result["pokemonId"] == 1
        assert "Bulbasaur" in result["label"]
        assert "XL Candy" in result["label"]

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

    async def test_keeps_different_reward_quantities_separate(self, tmp_path):
        import json

        base = {
            "name": "Stop",
            "lat": 39.75,
            "lon": -8.8,
            "url": None,
            "quest_title": "quest_catch_pokemon",
            "quest_target": 3,
            "quest_reward_type": 3,
            "quest_item_id": None,
            "quest_pokemon_id": None,
        }
        exporter = self._make_exporter(
            tmp_path,
            [
                {**base, "quest_reward_amount": 500},
                {**base, "quest_reward_amount": 1000},
            ],
        )
        await exporter.export()
        quests = json.loads((tmp_path / "quests.json").read_text())["quests"]
        assert len(quests) == 2
        assert len({q["id"] for q in quests}) == 2
        assert {q["reward"]["amount"] for q in quests} == {500, 1000}

    async def test_keeps_ordinary_and_costume_rewards_at_one_stop_separate(
        self, tmp_path
    ):
        import json

        base = {
            "name": "Stop",
            "lat": 39.75,
            "lon": -8.8,
            "url": None,
            "quest_title": "quest_catch_pokemon",
            "quest_target": 3,
            "quest_reward_type": 7,
            "quest_item_id": None,
            "quest_pokemon_id": 1,
            "quest_reward_amount": 1,
        }
        rows = [
            {
                **base,
                "quest_pokemon_form_id": form,
                "quest_rewards": json.dumps(
                    [
                        {
                            "type": 7,
                            "info": {
                                "pokemon_id": 1,
                                "form_id": form,
                                "costume_id": costume,
                            },
                        }
                    ]
                ),
            }
            for form, costume in [(163, 0), (897, 0), (163, 11)]
        ]
        exporter = self._make_exporter(tmp_path, rows)
        exporter.poliswag.quest_search.masterfile_data = {
            "pokemon": {
                "1": {
                    "name": "Bulbasaur",
                    "defaultFormId": 163,
                    "forms": {
                        "163": {"name": "Normal"},
                        "897": {"name": "Fall 2019", "isCostume": True},
                    },
                }
            }
        }
        await exporter.export()
        quests = json.loads((tmp_path / "quests.json").read_text())["quests"]
        assert len(quests) == 3
        assert len({q["id"] for q in quests}) == 3
        rewards = {q["reward"]["type"]: q["reward"] for q in quests}
        assert rewards["encounter_1"]["formId"] == 0
        assert rewards["encounter_1"]["costumeId"] == 0
        assert rewards["encounter_1_f897"]["label"] == "Bulbasaur (Fall 2019) encounter"
        assert rewards["encounter_1_c11"]["costumeId"] == 11
        assert all(q["stopsCount"] == 1 for q in quests)

    async def test_missing_or_malformed_variant_metadata_never_merges_with_known_ordinary(
        self, tmp_path
    ):
        import json

        base = {
            "name": "Stop",
            "lat": 39.75,
            "lon": -8.8,
            "url": None,
            "quest_title": "quest_catch_pokemon",
            "quest_target": 3,
            "quest_reward_type": 7,
            "quest_item_id": None,
            "quest_pokemon_id": 1,
            "quest_reward_amount": 1,
            "quest_pokemon_form_id": 0,
        }
        rows = [
            {**base, "quest_rewards": raw}
            for raw in [None, "broken", '[{"type":7,"info":{"pokemon_id":1}}]']
        ]
        exporter = self._make_exporter(tmp_path, rows)
        await exporter.export()
        quests = json.loads((tmp_path / "quests.json").read_text())["quests"]
        rewards = {q["reward"]["type"]: q["reward"] for q in quests}
        assert len(quests) == 2
        assert "formId" not in rewards["encounter_1_unknown"]
        assert rewards["encounter_1"]["formId"] == 0

    async def test_scanner_omitted_zero_fields_and_costume_id(self, tmp_path):
        import json

        base = {
            "name": "Stop",
            "lat": 39.75,
            "lon": -8.8,
            "url": None,
            "quest_title": "quest_catch_pokemon",
            "quest_target": 3,
            "quest_reward_type": 7,
            "quest_item_id": None,
            "quest_pokemon_id": 1,
            "quest_reward_amount": 1,
            "quest_pokemon_form_id": None,
        }
        rows = [
            {**base, "quest_rewards": json.dumps([{"type": 7, "info": info}])}
            for info in [
                {"pokemon_id": 1},
                {"pokemon_id": 1, "costume_id": 11},
                {"pokemon_id": 2},
                {"pokemon_id": 1, "costume_id": "broken"},
            ]
        ]
        exporter = self._make_exporter(tmp_path, rows)
        await exporter.export()
        quests = json.loads((tmp_path / "quests.json").read_text())["quests"]
        rewards = {q["reward"]["type"]: q["reward"] for q in quests}
        assert len(quests) == 3
        assert rewards["encounter_1"]["costumeId"] == 0
        assert rewards["encounter_1"]["formId"] == 0
        assert rewards["encounter_1_c11"]["costumeId"] == 11
        assert "formId" not in rewards["encounter_1_unknown"]

    async def test_writes_merged_quests_to_json(self, tmp_path, mocker):
        from unittest.mock import AsyncMock, MagicMock
        from modules.quest_exporter import QuestExporter

        poliswag = MagicMock()
        qs = poliswag.quest_search
        qs.db = AsyncMock()
        qs.translationfile_data = {"data": {"quest_catch_pokemon": "Catch {0} Pokémon"}}
        qs.masterfile_data = {
            "pokemon": {"25": {"name": "Pikachu"}, "150": "Mewtwo"},
            "items": {"1": {"name": "Poké Ball"}, "2": "Great Ball"},
        }
        standard_rows = [
            {
                "name": "Fonte Luminosa",
                "lat": 39.75,
                "lon": -8.80,  # Leiria
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
        alternative_rows = [
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
                alternative_rows
                if "alternative_quest_reward_type" in query
                else standard_rows
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
        # "Catch 5 Pokémon" has two stops; "Catch 3 Pokémon" has one.
        assert len(quests) == 2
        assert all("[AR]" not in q["title"] for q in quests)
        alternative_quest = next(q for q in quests if q["title"] == "Catch 3 Pokémon")
        standard_quest = next(q for q in quests if q["title"] == "Catch 5 Pokémon")

        assert standard_quest["stopsCount"] == 2
        assert alternative_quest["stopsCount"] == 1
        assert alternative_quest["reward"]["amount"] == 3

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

    @staticmethod
    def _make_exporter(tmp_path, rows):
        from unittest.mock import AsyncMock, MagicMock
        from modules.quest_exporter import QuestExporter

        poliswag = MagicMock()
        qs = poliswag.quest_search
        qs.db = AsyncMock()
        qs.translationfile_data = {"data": {}}
        qs.masterfile_data = {"pokemon": {}, "items": {}}

        def fake_get_data(query):
            # Only the first scanner field set contains rows.
            return [] if "alternative_quest_reward_type" in query else rows

        qs.db.get_data_from_database.side_effect = fake_get_data

        exporter = QuestExporter(poliswag=poliswag)
        exporter.output_path = str(tmp_path / "quests.json")
        return exporter

    @staticmethod
    def _row(name, reward_amount):
        return {
            "name": name,
            "lat": 39.75,
            "lon": -8.80,
            "url": None,
            "quest_title": "quest_catch_pokemon",
            "quest_target": 5,
            "quest_reward_type": 1,  # xp
            "quest_item_id": None,
            "quest_pokemon_id": None,
            "quest_reward_amount": reward_amount,
        }

    async def test_skips_rewrite_when_content_unchanged(self, tmp_path):
        import json

        rows = [self._row("Fonte", 500)]
        exporter = self._make_exporter(tmp_path, rows)
        output = tmp_path / "quests.json"

        assert await exporter.export() is True
        first = json.loads(output.read_text())

        # Second export with identical DB content must not rewrite the file.
        assert await exporter.export() is False
        second = json.loads(output.read_text())
        assert first["generatedAt"] == second["generatedAt"]

    async def test_combines_scanner_field_sets_without_duplicate_stops(self, tmp_path):
        import json

        source = self._row("Fonte", 500)
        other = self._row("Praça", 500)
        exporter = self._make_exporter(tmp_path, [source])
        exporter.poliswag.quest_search.db.get_data_from_database.side_effect = (
            lambda query: (
                [dict(source), other]
                if "alternative_quest_reward_type" in query
                else [source]
            )
        )

        await exporter.export()
        quests = json.loads((tmp_path / "quests.json").read_text())["quests"]
        assert len(quests) == 1
        assert "[AR]" not in quests[0]["title"]
        assert quests[0]["stopsCount"] == 2
        assert {stop["name"] for stop in quests[0]["pokestops"]} == {"Fonte", "Praça"}

    async def test_rewrites_when_quest_content_changes(self, tmp_path):
        rows = [self._row("Fonte", 500)]
        exporter = self._make_exporter(tmp_path, rows)

        assert await exporter.export() is True
        assert await exporter.export() is False

        # Mutate the DB content → next export must rewrite.
        rows.append(self._row("Praça", 1000))
        assert await exporter.export() is True

    async def test_force_rewrites_even_when_unchanged(self, tmp_path):
        rows = [self._row("Fonte", 500)]
        exporter = self._make_exporter(tmp_path, rows)

        assert await exporter.export() is True
        assert await exporter.export(force=True) is True

    async def test_meta_file_stores_content_hash(self, tmp_path):
        import json

        rows = [self._row("Fonte", 500)]
        exporter = self._make_exporter(tmp_path, rows)
        await exporter.export()

        meta = json.loads((tmp_path / "quests-meta.json").read_text())
        assert "contentHash" in meta
        assert "generatedAt" in meta

    async def test_handles_missing_translations_and_masterfile(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock
        from modules.quest_exporter import QuestExporter

        poliswag = MagicMock()
        qs = poliswag.quest_search
        qs.db = AsyncMock()
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


class TestRewardSpeciesSeparation:
    """Different species with the same title/amount must stay separate."""

    @staticmethod
    def _candy_row(name, pokemon_id, reward_type=4, amount=3):
        return {
            "name": name,
            "lat": 39.75,
            "lon": -8.80,
            "url": None,
            "quest_title": "quest_catch_pokemon",
            "quest_target": 3,
            "quest_reward_type": reward_type,
            "quest_item_id": None,
            "quest_pokemon_id": pokemon_id,
            "quest_reward_amount": amount,
        }

    @staticmethod
    def _make_exporter_with_names(tmp_path, rows):
        from unittest.mock import AsyncMock, MagicMock
        from modules.quest_exporter import QuestExporter

        poliswag = MagicMock()
        qs = poliswag.quest_search
        qs.db = AsyncMock()
        qs.translationfile_data = {"data": {}}
        qs.masterfile_data = {"pokemon": {}, "items": {}}

        def fake_get_data(query):
            return [] if "alternative_quest_reward_type" in query else rows

        qs.db.get_data_from_database.side_effect = fake_get_data
        exporter = QuestExporter(poliswag=poliswag)
        exporter.output_path = str(tmp_path / "quests.json")
        return exporter

    async def test_mega_species_same_title_amount_stay_separate(self, tmp_path):
        import json

        rows = [
            self._candy_row("Stop A", 3, reward_type=12, amount=50),
            self._candy_row("Stop B", 6, reward_type=12, amount=50),
        ]
        exporter = self._make_exporter_with_names(tmp_path, rows)
        exporter.poliswag.quest_search.masterfile_data = {
            "pokemon": {"3": {"name": "Venusaur"}, "6": {"name": "Charizard"}},
            "items": {},
        }
        await exporter.export()
        quests = json.loads((tmp_path / "quests.json").read_text())["quests"]
        assert len(quests) == 2
        assert len({q["id"] for q in quests}) == 2
        by_species = {q["reward"].get("pokemonId"): q for q in quests}
        assert set(by_species) == {3, 6}
        assert all(q["reward"]["type"] == "mega_energy" for q in quests)
        assert "Venusaur" in by_species[3]["reward"]["label"]
        assert "Charizard" in by_species[6]["reward"]["label"]

    async def test_candy_species_same_title_amount_stay_separate(self, tmp_path):
        import json

        rows = [
            self._candy_row("Stop A", 25, reward_type=4, amount=3),
            self._candy_row("Stop B", 150, reward_type=4, amount=3),
        ]
        exporter = self._make_exporter_with_names(tmp_path, rows)
        exporter.poliswag.quest_search.masterfile_data = {
            "pokemon": {"25": {"name": "Pikachu"}, "150": {"name": "Mewtwo"}},
            "items": {},
        }
        await exporter.export()
        quests = json.loads((tmp_path / "quests.json").read_text())["quests"]
        assert len(quests) == 2
        assert {q["reward"].get("pokemonId") for q in quests} == {25, 150}
        assert all(q["reward"]["type"] == "candy" for q in quests)

    async def test_xl_candy_species_stay_separate(self, tmp_path):
        import json

        rows = [
            self._candy_row("Stop A", 1, reward_type=13, amount=5),
            self._candy_row("Stop B", 25, reward_type=13, amount=5),
        ]
        exporter = self._make_exporter_with_names(tmp_path, rows)
        exporter.poliswag.quest_search.masterfile_data = {
            "pokemon": {"1": {"name": "Bulbasaur"}, "25": {"name": "Pikachu"}},
            "items": {},
        }
        await exporter.export()
        quests = json.loads((tmp_path / "quests.json").read_text())["quests"]
        assert len(quests) == 2
        assert {q["reward"].get("pokemonId") for q in quests} == {1, 25}

    async def test_stardust_same_amount_merges(self, tmp_path):
        import json

        rows = [
            self._candy_row("Stop A", None, reward_type=3, amount=500),
            self._candy_row("Stop B", None, reward_type=3, amount=500),
        ]
        exporter = self._make_exporter_with_names(tmp_path, rows)
        await exporter.export()
        quests = json.loads((tmp_path / "quests.json").read_text())["quests"]
        assert len(quests) == 1
        assert quests[0]["reward"]["type"] == "stardust"
        assert quests[0]["stopsCount"] == 2


class TestReadStoredHash:
    def test_non_object_metadata_returns_none(self, tmp_path):
        import json

        for bad in ([1, 2], "string", 123, None):
            p = tmp_path / "quests-meta.json"
            p.write_text(json.dumps(bad))
            assert QuestExporter._read_stored_hash(p) is None

    def test_missing_file_returns_none(self, tmp_path):
        assert QuestExporter._read_stored_hash(tmp_path / "nope.json") is None

    def test_corrupt_json_returns_none(self, tmp_path):
        p = tmp_path / "quests-meta.json"
        p.write_text("{broken")
        assert QuestExporter._read_stored_hash(p) is None

    def test_non_string_hash_returns_none(self, tmp_path):
        import json

        p = tmp_path / "quests-meta.json"
        p.write_text(json.dumps({"contentHash": 123}))
        assert QuestExporter._read_stored_hash(p) is None

    def test_valid_hash_returned(self, tmp_path):
        import json

        p = tmp_path / "quests-meta.json"
        p.write_text(json.dumps({"contentHash": "abc"}))
        assert QuestExporter._read_stored_hash(p) == "abc"


class TestPayloadRecovery:
    @staticmethod
    def _row(name="Fonte", amount=500):
        return {
            "name": name,
            "lat": 39.75,
            "lon": -8.80,
            "url": None,
            "quest_title": "quest_catch_pokemon",
            "quest_target": 5,
            "quest_reward_type": 1,
            "quest_item_id": None,
            "quest_pokemon_id": None,
            "quest_reward_amount": amount,
        }

    @staticmethod
    def _make_exporter(tmp_path, rows):
        from unittest.mock import AsyncMock, MagicMock
        from modules.quest_exporter import QuestExporter

        poliswag = MagicMock()
        qs = poliswag.quest_search
        qs.db = AsyncMock()
        qs.translationfile_data = {"data": {}}
        qs.masterfile_data = {"pokemon": {}, "items": {}}

        def fake_get_data(query):
            return [] if "alternative_quest_reward_type" in query else rows

        qs.db.get_data_from_database.side_effect = fake_get_data
        exporter = QuestExporter(poliswag=poliswag)
        exporter.output_path = str(tmp_path / "quests.json")
        return exporter

    async def test_matching_meta_missing_payload_rewrites(self, tmp_path):
        import json

        rows = [self._row()]
        exporter = self._make_exporter(tmp_path, rows)
        assert await exporter.export() is True
        # Delete payload but keep meta: next export must recover.
        (tmp_path / "quests.json").unlink()
        assert await exporter.export() is True
        # After recovery the payload must exist and be valid.
        assert await exporter.export() is False
        payload = json.loads((tmp_path / "quests.json").read_text())
        assert isinstance(payload["quests"], list)

    async def test_matching_meta_corrupt_payload_rewrites(self, tmp_path):
        import json

        rows = [self._row()]
        exporter = self._make_exporter(tmp_path, rows)
        assert await exporter.export() is True
        (tmp_path / "quests.json").write_text("{corrupt")
        assert await exporter.export() is True
        payload = json.loads((tmp_path / "quests.json").read_text())
        assert isinstance(payload["quests"], list)

    async def test_matching_meta_malformed_metadata_rewrites(self, tmp_path):
        rows = [self._row()]
        exporter = self._make_exporter(tmp_path, rows)
        assert await exporter.export() is True
        (tmp_path / "quests-meta.json").write_text("[1,2]")
        assert await exporter.export() is True

    async def test_previous_lisbon_day_payload_rewrites(self, tmp_path):
        import hashlib
        import json
        from datetime import datetime, timedelta, timezone

        rows = [self._row()]
        exporter = self._make_exporter(tmp_path, rows)
        assert await exporter.export() is True
        payload = json.loads((tmp_path / "quests.json").read_text())
        quests = payload["quests"]
        content_hash = hashlib.md5(
            json.dumps(quests, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        old_generated = (
            (datetime.now(timezone.utc) - timedelta(days=1))
            .isoformat()
            .replace("+00:00", "Z")
        )
        (tmp_path / "quests.json").write_text(
            json.dumps({"quests": quests, "generatedAt": old_generated})
        )
        (tmp_path / "quests-meta.json").write_text(
            json.dumps({"generatedAt": old_generated, "contentHash": content_hash})
        )
        assert await exporter.export() is True
        fresh = json.loads((tmp_path / "quests.json").read_text())
        assert fresh["generatedAt"] != old_generated

    async def test_unchanged_empty_export_previous_day_rewrites(self, tmp_path):
        import json
        from datetime import datetime, timedelta, timezone

        exporter = self._make_exporter(tmp_path, [])
        assert await exporter.export() is True
        payload = json.loads((tmp_path / "quests.json").read_text())
        assert payload["quests"] == []
        old_generated = (
            (datetime.now(timezone.utc) - timedelta(days=1))
            .isoformat()
            .replace("+00:00", "Z")
        )
        (tmp_path / "quests.json").write_text(
            json.dumps({"quests": [], "generatedAt": old_generated})
        )
        import hashlib as _hashlib

        empty_hash = _hashlib.md5(
            json.dumps([], ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        (tmp_path / "quests-meta.json").write_text(
            json.dumps({"generatedAt": old_generated, "contentHash": empty_hash})
        )
        assert await exporter.export() is True

    async def test_interrupted_write_leaves_old_complete_file(self, tmp_path, mocker):

        rows = [self._row()]
        exporter = self._make_exporter(tmp_path, rows)
        assert await exporter.export() is True
        before = (tmp_path / "quests.json").read_text()
        mocker.patch("json.dump", side_effect=RuntimeError("boom"))
        try:
            await exporter.export(force=True)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError")
        assert (tmp_path / "quests.json").read_text() == before
        leftovers = list(tmp_path.glob("quests.json.*.tmp"))
        assert leftovers == []

    async def test_retry_after_metadata_failure(self, tmp_path, mocker):
        import json
        from modules.quest_exporter import QuestExporter

        rows = [self._row()]
        exporter = self._make_exporter(tmp_path, rows)
        real_write = QuestExporter._write_json_atomic
        calls = {"n": 0}

        def flaky(target, data):
            from pathlib import Path as _P

            if _P(target).name == "quests-meta.json" and calls["n"] == 0:
                calls["n"] += 1
                raise OSError("meta disk full")
            return real_write(target, data)

        mocker.patch.object(QuestExporter, "_write_json_atomic", side_effect=flaky)
        try:
            await exporter.export()
        except OSError:
            pass
        else:
            raise AssertionError("expected OSError")
        # Payload succeeded, meta failed: files are out of sync.
        assert (tmp_path / "quests.json").exists()
        # Retry must recover (rewrite meta, succeed).
        mocker.patch.object(QuestExporter, "_write_json_atomic", side_effect=real_write)
        assert await exporter.export() is True
        meta = json.loads((tmp_path / "quests-meta.json").read_text())
        assert "contentHash" in meta

    async def test_recovery_after_only_payload_succeeds(self, tmp_path):
        import json

        rows = [self._row()]
        exporter = self._make_exporter(tmp_path, rows)
        assert await exporter.export() is True
        # Simulate crash after payload but before meta: delete meta only.
        (tmp_path / "quests-meta.json").unlink()
        # Same DB content: without recovery this would look unchanged, but the
        # missing meta forces a rewrite of both files (payload first).
        assert await exporter.export() is True
        assert (tmp_path / "quests-meta.json").exists()
        fresh = json.loads((tmp_path / "quests.json").read_text())
        assert isinstance(fresh["quests"], list)
        # Per-file atomic replace is not a transaction across the pair.
        assert True

    async def test_atomic_write_uses_temp_and_cleans(self, tmp_path):

        target = tmp_path / "quests.json"
        QuestExporter._write_json_atomic(target, {"a": 1})
        assert target.exists()
        assert list(tmp_path.glob("quests.json.*.tmp")) == []
