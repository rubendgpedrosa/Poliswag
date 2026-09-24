import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.event_stats import EventStats


@pytest.fixture
def event_stats():
    poliswag = MagicMock()
    poliswag.quest_search.db = AsyncMock()
    poliswag.quest_search.pokemon_name_map = {"1": "nickit", "889": "zamazenta"}
    poliswag.quest_search.get_pokemon_id_by_pokemon_name_map = MagicMock(
        side_effect=lambda kw: [
            pid
            for pid, name in poliswag.quest_search.pokemon_name_map.items()
            if kw.lower() in name
        ]
    )
    poliswag.utility.log_to_file = MagicMock()
    return EventStats(poliswag)


def _event(
    event_type, name, start="2026-08-16 14:00:00", end="2026-08-16 17:00:00", extra=None
):
    event = {"event_type": event_type, "name": name, "start": start, "end": end}
    if extra is not None:
        event["extra_data"] = json.dumps({"extraData": extra})
    return event


def _raid(area, level, pokemon_id, total):
    return {"area": area, "level": level, "pokemon_id": pokemon_id, "total": total}


class TestGetSummary:
    @pytest.mark.parametrize(
        "event_type",
        ["go-battle-league", "research", "event", "max-mondays"],
    )
    async def test_returns_none_for_types_without_stats(self, event_stats, event_type):
        event = _event(event_type, "Mega Venusaur in Mega Raids")
        assert await event_stats.get_summary(event) is None
        event_stats.poliswag.quest_search.db.get_data_from_database.assert_not_called()

    async def test_returns_none_when_start_missing(self, event_stats):
        event = {"event_type": "raid-day", "name": "X", "end": "2026-08-16 17:00:00"}
        assert await event_stats.get_summary(event) is None

    async def test_returns_none_when_end_missing(self, event_stats):
        event = {"event_type": "raid-day", "name": "X", "start": "2026-08-16 14:00:00"}
        assert await event_stats.get_summary(event) is None

    async def test_exception_during_query_is_caught_and_logged(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database = AsyncMock(
            side_effect=Exception("db down")
        )
        event = _event("raid-day", "Super Mega Raid Day")
        result = await event_stats.get_summary(event)
        assert result is None
        event_stats.poliswag.utility.log_to_file.assert_called_once()


class TestRaidSummary:
    @pytest.mark.parametrize("event_type", ["raid-hour", "raid-day"])
    async def test_names_the_tier_that_grew_and_its_bosses(
        self, event_stats, event_type
    ):
        event_stats.poliswag.quest_search.db.get_data_from_database = AsyncMock(
            side_effect=[
                [
                    _raid("Leiria", 5, 889, 190),
                    _raid("MarinhaGrande", 5, 889, 61),
                    _raid("Leiria", 3, 6, 300),
                    _raid("Leiria", 1, 25, 40),
                ],
                [_raid("Leiria", 5, 888, 77), _raid("Leiria", 3, 6, 290)],
            ]
        )
        event = _event(
            event_type,
            "Zamazenta Raid Hour",
            start="2026-09-16 18:00:00",
            end="2026-09-16 19:00:00",
        )
        result = await event_stats.get_summary(event)
        assert result.text.startswith("🥊 **251** raids 5★ · Zamazenta\n")
        assert "📈 **3,3×** o dia anterior (77)" in result.text
        assert result.headline == (
            "🥊 **251** raids 5★ · Zamazenta\n📈 **3,3×** o dia anterior (77)"
        )
        assert result.areas == (
            ("📍 Leiria", "190 raids"),
            ("📍 Marinha Grande", "61 raids"),
        )
        assert result.note == (
            "Totais diários de 16/09 · incluem atividade fora do horário do evento."
        )
        day_before = (
            event_stats.poliswag.quest_search.db.get_data_from_database.await_args_list[
                1
            ]
        )
        assert day_before.kwargs["params"][:2] == ("2026-09-15", "2026-09-15")

    async def test_without_the_day_before_there_is_no_comparison(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database = AsyncMock(
            side_effect=[[_raid("Leiria", 6, 3, 12)], []]
        )
        result = await event_stats.get_summary(
            _event("raid-day", "Super Mega Raid Day")
        )
        assert result.text.startswith("🥊 **12** raids Mega · #3\n")
        assert "o dia anterior" not in result.text
        assert ("📍 Marinha Grande", "sem dados") in result.areas

    async def test_lists_three_bosses_then_a_count(self, event_stats):
        rows = [_raid("Leiria", 5, pid, 10 - pid) for pid in (1, 2, 3, 4, 5)]
        event_stats.poliswag.quest_search.db.get_data_from_database = AsyncMock(
            side_effect=[rows, []]
        )
        result = await event_stats.get_summary(_event("raid-hour", "Raid Hour"))
        assert "· Nickit, #2, #3 +2\n" in result.text

    async def test_no_rows_is_no_summary(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database.return_value = []
        assert await event_stats.get_summary(_event("raid-day", "Raid Day")) is None


class TestSpeciesSummary:
    async def test_community_day_summary(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database = AsyncMock(
            side_effect=[
                [
                    {"area": "Leiria", "total": 500},
                    {"area": "MarinhaGrande", "total": 1000},
                ],
                [{"area": "Leiria", "total": 3}],
                [{"area": "MarinhaGrande", "total": 2}],
                [{"area": "Leiria", "total": 100}],
            ]
        )
        event = _event("community-day", "Nickit Community Day")
        result = await event_stats.get_summary(event)
        assert "🐾 **1 500** spawns · Nickit" in result.text
        assert "📈 **15×** o dia anterior (100)" in result.text
        assert ("📍 Leiria", "500 spawns\n💯 3 100IV\n0️⃣ 0 0IV") in result.areas
        assert (
            "📍 Marinha Grande",
            "1 000 spawns\n💯 0 100IV\n0️⃣ 2 0IV",
        ) in result.areas

    async def test_spotlight_hour_summary(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database = AsyncMock(
            side_effect=[
                [{"area": "Leiria", "total": 80}],
                [{"area": "Leiria", "total": 1}],
                [],
                [],
            ]
        )
        event = _event("pokemon-spotlight-hour", "Nickit Spotlight Hour")
        result = await event_stats.get_summary(event)
        assert "🐾 **80** spawns · Nickit" in result.text
        assert "o dia anterior" not in result.text
        assert ("📍 Leiria", "80 spawns\n💯 1 100IV\n0️⃣ 0 0IV") in result.areas

    async def test_species_come_from_scrapedduck_when_the_name_has_none(
        self, event_stats
    ):
        event_stats.poliswag.quest_search.db.get_data_from_database = AsyncMock(
            side_effect=[[{"area": "Leiria", "total": 80}], [], [], []]
        )
        extra = {
            "spotlight": {
                "name": "Houndour",
                "image": "https://cdn/pokemon_icons/pokemon_icon_228_00.png",
                "list": [
                    {
                        "name": "Houndour",
                        "image": "https://cdn/pokemon_icons/pokemon_icon_228_00.png",
                    },
                    {
                        "name": "Houndoom",
                        "image": "https://cdn/pokemon_icons/pokemon_icon_229_00.png",
                    },
                ],
            }
        }
        event = _event(
            "pokemon-spotlight-hour",
            "Houndour and Houndoom Spotlight Hour",
            extra=extra,
        )
        result = await event_stats.get_summary(event)
        assert "🐾 **80** spawns · Houndour, Houndoom" in result.text
        call = (
            event_stats.poliswag.quest_search.db.get_data_from_database.await_args_list[
                0
            ]
        )
        assert "pokemon_id IN (%s, %s)" in call.args[0]
        assert call.kwargs["params"][-2:] == (228, 229)

    async def test_community_day_spawns_from_scrapedduck(self, event_stats):
        extra = {
            "communityday": {
                "spawns": [{"name": "Gible", "image": "x/pokemon_icon_443_00.png"}]
            }
        }
        assert event_stats._featured_species(
            _event("community-day", "Gible Community Day Classic", extra=extra), None
        ) == [(443, "Gible")]

    async def test_unresolved_species_is_no_summary(self, event_stats):
        event = _event("community-day", "November Community Day")
        assert await event_stats.get_summary(event) is None
        event_stats.poliswag.quest_search.db.get_data_from_database.assert_not_called()

    async def test_no_species_text_is_no_summary(self, event_stats):
        assert (
            await event_stats.get_summary(_event("community-day", "Community Day"))
            is None
        )

    async def test_no_rows_is_no_summary(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database.return_value = []
        event = _event("community-day", "Nickit Community Day")
        assert await event_stats.get_summary(event) is None


class TestSpeciesFromExtra:
    def test_unparseable_or_missing_extra_is_nothing(self, event_stats):
        assert event_stats._species_from_extra(None) == []
        assert event_stats._species_from_extra("{not json") == []
        assert (
            event_stats._species_from_extra(json.dumps({"extraData": {"generic": {}}}))
            == []
        )

    def test_a_spotlight_without_a_list_uses_its_own_icon(self, event_stats):
        extra = json.dumps(
            {
                "extraData": {
                    "spotlight": {
                        "name": "Rattata",
                        "image": "x/pokemon_icon_019_00.png",
                    }
                }
            }
        )
        assert event_stats._species_from_extra(extra) == [(19, "Rattata")]

    def test_reads_the_newer_icon_naming(self, event_stats):
        image = "https://cdn/pokemon_icons/pm4.fGOGGLES_2026.icon.png"
        extra = json.dumps(
            {
                "extraData": {
                    "spotlight": {
                        "name": "Charmander wearing Friede's goggles",
                        "image": image,
                    }
                }
            }
        )
        assert event_stats._species_from_extra(extra) == [
            (4, "Charmander wearing Friede's goggles")
        ]


class TestByArea:
    async def test_grouped_query_filters_species_and_areas(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database.return_value = []
        await event_stats._by_area("pokemon_stats", [25], "2026-09-01", "2026-09-02")
        call = event_stats.poliswag.quest_search.db.get_data_from_database.call_args
        assert "GROUP BY area" in call.args[0]
        assert "pokemon_id IN (%s)" in call.args[0]
        assert call.kwargs["params"] == (
            "2026-09-01",
            "2026-09-02",
            "Leiria",
            "MarinhaGrande",
            25,
        )

    async def test_omits_pokemon_filter_when_none(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database.return_value = []
        await event_stats._by_area("raid_stats", None, "2026-08-16", "2026-08-16")
        call = event_stats.poliswag.quest_search.db.get_data_from_database.call_args
        assert "pokemon_id" not in call.args[0]


class TestVersus:
    def test_formats(self, event_stats):
        assert event_stats._versus(251, 77) == "📈 **3,3×** o dia anterior (77)"
        assert event_stats._versus(200, 100) == "📈 **2×** o dia anterior (100)"
        assert event_stats._versus(80, 100) == "📉 **0,8×** o dia anterior (100)"
        assert event_stats._versus(80, 0) is None


class TestResolvePokemonId:
    def test_exact_match_wins_over_ambiguous_substrings(self, event_stats):
        event_stats.poliswag.quest_search.pokemon_name_map = {
            "1": "nickit",
            "2": "nickit evolved",
        }
        event_stats.poliswag.quest_search.get_pokemon_id_by_pokemon_name_map = (
            MagicMock(return_value=["1", "2"])
        )
        assert event_stats._resolve_pokemon_id("Nickit") == 1

    def test_single_substring_match_used_when_no_exact(self, event_stats):
        event_stats.poliswag.quest_search.pokemon_name_map = {"1": "nickit"}
        event_stats.poliswag.quest_search.get_pokemon_id_by_pokemon_name_map = (
            MagicMock(return_value=["1"])
        )
        assert event_stats._resolve_pokemon_id("Nick") == 1

    def test_ambiguous_matches_with_no_exact_return_none(self, event_stats):
        event_stats.poliswag.quest_search.pokemon_name_map = {
            "1": "nickit",
            "2": "nicktoon",
        }
        event_stats.poliswag.quest_search.get_pokemon_id_by_pokemon_name_map = (
            MagicMock(return_value=["1", "2"])
        )
        assert event_stats._resolve_pokemon_id("nick") is None

    def test_no_matches_returns_none(self, event_stats):
        event_stats.poliswag.quest_search.get_pokemon_id_by_pokemon_name_map = (
            MagicMock(return_value=[])
        )
        assert event_stats._resolve_pokemon_id("zzz") is None


class TestPeriodNote:
    def test_one_day_and_several(self, event_stats):
        assert event_stats._period_note("2026-09-16", "2026-09-16").startswith(
            "Totais diários de 16/09 ·"
        )
        assert event_stats._period_note("2026-09-16", "2026-09-22").startswith(
            "Totais diários de 16/09 a 22/09 ·"
        )


def _rot(area, level, pokemon_id, date, total):
    return {
        "area": area,
        "level": level,
        "pokemon_id": pokemon_id,
        "date": date,
        "total": total,
    }


class TestRotationSummary:
    """A weekly raid rotation: the named boss's raids over the days counted."""

    async def test_counts_the_named_boss_per_area_and_per_day(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database = AsyncMock(
            return_value=[
                _rot("Leiria", 5, 889, "2026-09-17", 300),
                _rot("Leiria", 5, 889, "2026-09-18", 200),
                _rot("MarinhaGrande", 5, 889, "2026-09-18", 100),
                # Another 5★ boss the same week: not this rotation.
                _rot("Leiria", 5, 1, "2026-09-18", 999),
            ]
        )
        event = _event(
            "raid-battles",
            "Zamazenta (Hero of Many Battles) in 5-star Raid Battles",
            start="2026-09-16 06:00:00",
            end="2026-09-22 22:00:00",
        )
        result = await event_stats.get_summary(event)
        assert result.headline == "🥊 **600** raids 5★ · Zamazenta\n📅 ~300 por dia"
        assert result.areas == (
            ("📍 Leiria", "500 raids"),
            ("📍 Marinha Grande", "100 raids"),
        )
        # Golbat had lost the 16th: the note says which days were counted,
        # and a rotation spans whole days, so no "outside the event" caveat.
        assert result.note == "Totais diários de 17/09 a 18/09."
        params = event_stats.poliswag.quest_search.db.get_data_from_database.call_args.kwargs[
            "params"
        ]
        assert params[-2:] == (5, 8)

    @pytest.mark.parametrize(
        "tier, levels",
        [
            ("5-star Raid Battles", (5, 8)),
            ("3-star Raid Battles", (3,)),
            ("Mega Raids", (6, 7)),
            ("Shadow Raids", (11, 12, 13, 14, 15)),
            ("Super Mega Raids", (16,)),
            ("Max Battles", None),
        ],
    )
    def test_tier_from_the_name(self, event_stats, tier, levels):
        assert event_stats._rotation_tier(tier) == levels

    async def test_no_boss_named_is_no_summary(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database = AsyncMock(
            return_value=[_rot("Leiria", 6, 3, "2026-09-18", 50)]
        )
        event = _event("raid-battles", "Mega Venusaur in Mega Raids")
        assert await event_stats.get_summary(event) is None

    async def test_a_name_without_a_tier_is_no_summary(self, event_stats):
        event = _event("raid-battles", "Raid Weekend")
        assert await event_stats.get_summary(event) is None
        event_stats.poliswag.quest_search.db.get_data_from_database.assert_not_called()
