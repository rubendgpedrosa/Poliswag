from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.event_stats import EventStats


@pytest.fixture
def event_stats():
    poliswag = MagicMock()
    poliswag.quest_search.db = AsyncMock()
    poliswag.quest_search.pokemon_name_map = {"1": "nickit"}
    poliswag.quest_search.get_pokemon_id_by_pokemon_name_map = MagicMock(
        side_effect=lambda kw: [
            pid
            for pid, name in poliswag.quest_search.pokemon_name_map.items()
            if kw.lower() in name
        ]
    )
    poliswag.utility.log_to_file = MagicMock()
    return EventStats(poliswag)


def _event(event_type, name, start="2026-08-16 14:00:00", end="2026-08-16 17:00:00"):
    return {"event_type": event_type, "name": name, "start": start, "end": end}


class TestGetSummary:
    async def test_returns_none_for_unrelated_event_type(self, event_stats):
        event = _event("go-battle-league", "Great League Edition")
        assert await event_stats.get_summary(event) is None

    async def test_returns_none_when_start_missing(self, event_stats):
        event = {"event_type": "raid-day", "name": "X", "end": "2026-08-16 17:00:00"}
        assert await event_stats.get_summary(event) is None

    async def test_returns_none_when_end_missing(self, event_stats):
        event = {"event_type": "raid-day", "name": "X", "start": "2026-08-16 14:00:00"}
        assert await event_stats.get_summary(event) is None

    @pytest.mark.parametrize("event_type", ["raid-day", "raid-hour", "raid-battles"])
    async def test_raid_summary_for_any_raid_event_type(self, event_stats, event_type):
        event_stats.poliswag.quest_search.db.get_data_from_database.return_value = [
            {"total": 42}
        ]
        event = _event(event_type, "Super Mega Raid Day")
        result = await event_stats.get_summary(event)
        assert result == "🥊 **42** raids durante o evento."

    async def test_community_day_summary(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database = AsyncMock(
            side_effect=[[{"total": 500}], [{"total": 3}], [{"total": 2}]]
        )
        event = _event("community-day", "Nickit Community Day")
        result = await event_stats.get_summary(event)
        assert result == ("🐾 **500** spawns\n💯 **3** 100% IV\n0️⃣ **2** 0% IV")

    async def test_community_day_returns_none_when_species_unresolved(
        self, event_stats
    ):
        # "November" isn't in the (empty-for-this-word) pokemon name map.
        event = _event("community-day", "November Community Day")
        assert await event_stats.get_summary(event) is None
        event_stats.poliswag.quest_search.db.get_data_from_database.assert_not_called()

    async def test_community_day_with_no_species_text_returns_none(self, event_stats):
        event = _event("community-day", "Community Day")
        assert await event_stats.get_summary(event) is None

    async def test_spotlight_hour_summary(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database = AsyncMock(
            side_effect=[[{"total": 80}], [{"total": 1}], [{"total": 0}]]
        )
        event = _event("pokemon-spotlight-hour", "Nickit Spotlight Hour")
        result = await event_stats.get_summary(event)
        assert result == ("🐾 **80** spawns\n💯 **1** 100% IV\n0️⃣ **0** 0% IV")

    async def test_spotlight_hour_returns_none_when_species_unresolved(
        self, event_stats
    ):
        event = _event("pokemon-spotlight-hour", "November Spotlight Hour")
        assert await event_stats.get_summary(event) is None
        event_stats.poliswag.quest_search.db.get_data_from_database.assert_not_called()

    async def test_exception_during_query_is_caught_and_logged(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database = AsyncMock(
            side_effect=Exception("db down")
        )
        event = _event("raid-day", "Super Mega Raid Day")
        result = await event_stats.get_summary(event)
        assert result is None
        event_stats.poliswag.utility.log_to_file.assert_called_once()


class TestSum:
    async def test_returns_zero_when_no_rows(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database = AsyncMock(
            return_value=[]
        )
        total = await event_stats._sum("raid_stats", None, "2026-08-16", "2026-08-16")
        assert total == 0

    async def test_includes_pokemon_id_filter_only_when_given(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database.return_value = [
            {"total": 1}
        ]
        await event_stats._sum("pokemon_stats", 25, "2026-08-16", "2026-08-16")
        call_args = (
            event_stats.poliswag.quest_search.db.get_data_from_database.call_args
        )
        query = call_args.args[0]
        assert "pokemon_id = %s" in query
        assert call_args.kwargs["params"][-1] == 25

    async def test_omits_pokemon_id_filter_when_none(self, event_stats):
        event_stats.poliswag.quest_search.db.get_data_from_database.return_value = [
            {"total": 1}
        ]
        await event_stats._sum("raid_stats", None, "2026-08-16", "2026-08-16")
        call_args = (
            event_stats.poliswag.quest_search.db.get_data_from_database.call_args
        )
        query = call_args.args[0]
        assert "pokemon_id" not in query
        assert call_args.kwargs["params"] == (
            "2026-08-16",
            "2026-08-16",
            "Leiria",
            "MarinhaGrande",
        )


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
