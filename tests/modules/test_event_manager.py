"""Tests for modules.event_manager.EventManager.

EventManager has a light __init__ (just sets dicts) so we can construct it
normally with a mocked poliswag. The async methods are exercised with
AsyncMock-patched fetch_data and DB calls replaced on poliswag.db.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.event_manager import EventManager


@pytest.fixture
def em():
    poliswag = MagicMock()
    poliswag.utility.format_datetime_string = (
        lambda s: s.replace("Z", "").replace("T", " ").split(".")[0]
    )
    return EventManager(poliswag=poliswag)


# --- pure-logic helpers -------------------------------------------------------


class TestGetEventTypeKey:
    @pytest.mark.parametrize(
        "event_type,expected",
        [
            ("Community Day", "community-day"),
            ("SPOTLIGHT HOUR", "spotlight-hour"),
            ("Raid Boss", "raid-day"),
            ("Go Battle League", "go-battle"),
            ("PvP League", "go-battle"),
            ("research breakthrough", "research"),
            ("Season Of Light", "season"),
            ("random", "default"),
        ],
    )
    def test_returns_expected_key(self, em, event_type, expected):
        assert em.get_event_type_key(event_type) == expected


class TestGetEventEmoji:
    def test_community_returns_star(self, em):
        assert em.get_event_emoji("Community Day") == "🌟"

    def test_spotlight_returns_flashlight(self, em):
        assert em.get_event_emoji("Spotlight Hour") == "🔦"

    def test_raid_returns_shield(self, em):
        assert em.get_event_emoji("Raid Day") == "🛡️"

    def test_battle_returns_crossed_swords(self, em):
        assert em.get_event_emoji("Battle League") == "⚔️"

    def test_research_returns_magnifier(self, em):
        assert em.get_event_emoji("Research") == "🔍"

    def test_season_returns_leaf(self, em):
        assert em.get_event_emoji("Season Of Light") == "🍂"

    def test_falls_back_to_random_pokemon_emoji(self, em, mocker):
        mocker.patch("modules.event_manager.random.choice", return_value="🎮")
        assert em.get_event_emoji("Misc") == "🎮"


class TestFormatEndTime:
    def test_same_day_shows_only_time(self, em, mocker):
        now = datetime(2024, 5, 10, 10, 0, 0)
        mock_dt_module = MagicMock()
        mock_dt_module.now.return_value = now
        mocker.patch("modules.event_manager.datetime", new=mock_dt_module)
        end = datetime(2024, 5, 10, 18, 30, 0)
        assert em.format_end_time(end) == "Termina às 18:30"

    def test_other_day_shows_portuguese_month(self, em, mocker):
        now = datetime(2024, 5, 10, 10, 0, 0)
        mock_dt_module = MagicMock()
        mock_dt_module.now.return_value = now
        mocker.patch("modules.event_manager.datetime", new=mock_dt_module)
        end = datetime(2024, 8, 3, 18, 30, 0)
        assert em.format_end_time(end) == "Termina a 03 Ago - 18:30"

    def test_custom_verb_is_respected(self, em, mocker):
        now = datetime(2024, 5, 10, 10, 0, 0)
        mock_dt_module = MagicMock()
        mock_dt_module.now.return_value = now
        mocker.patch("modules.event_manager.datetime", new=mock_dt_module)
        end = datetime(2024, 5, 10, 18, 30, 0)
        assert em.format_end_time(end, verb="Começa") == "Começa às 18:30"


class TestBuildUpsertQuery:
    def test_returns_sql_with_eleven_params(self, em):
        event_blob = {"id": "abc", "name": "Community Day"}
        query, params = em.build_upsert_query(
            "Community Day",
            "2024-05-10 09:00:00",
            "2024-05-10 12:00:00",
            "img.png",
            "community-day",
            "https://l",
            event_blob,
        )
        assert "INSERT INTO event" in query
        assert "ON DUPLICATE KEY UPDATE" in query
        assert len(params) == 11
        assert params[0] == "Community Day"
        # extra_data is serialized as JSON in position 6 and 10.
        assert params[6] == params[10]
        assert '"id": "abc"' in params[6]


class TestGetEventLink:
    def test_returns_existing_leekduck_link_verbatim(self, em):
        event = {"link": "https://leekduck.com/events/foo", "name": "Foo"}
        assert em.get_event_link(event) == "https://leekduck.com/events/foo"

    def test_returns_none_for_empty_name(self, em):
        assert em.get_event_link({"name": "   "}) is None

    def test_generic_event_uses_events_path(self, em):
        link = em.get_event_link({"name": "Festival Lights", "event_type": "misc"})
        assert link == "https://www.leekduck.com/events/festival-lights"

    def test_community_day_path(self, em):
        link = em.get_event_link(
            {"name": "Charmander Community Day", "event_type": "community day"}
        )
        assert link == "https://www.leekduck.com/community-day/charmander-community-day"

    def test_spotlight_path(self, em):
        link = em.get_event_link(
            {"name": "Abra Spotlight", "event_type": "spotlight hour"}
        )
        assert link == "https://www.leekduck.com/spotlight-hour/abra-spotlight"

    def test_raid_path(self, em):
        link = em.get_event_link({"name": "Giratina Raid", "event_type": "raid day"})
        assert link == "https://www.leekduck.com/raid-day/giratina-raid"

    def test_research_path(self, em):
        link = em.get_event_link({"name": "Spring Research", "event_type": "research"})
        assert link == "https://www.leekduck.com/research/spring-research"

    def test_season_path(self, em):
        link = em.get_event_link({"name": "Season Of Light", "event_type": "season"})
        assert link == "https://www.leekduck.com/season/season-of-light"

    def test_strips_special_characters(self, em):
        link = em.get_event_link({"name": "Trainer's Day!", "event_type": "misc"})
        assert link == "https://www.leekduck.com/events/trainers-day"


class TestMarkEventNotified:
    def test_start_branch_updates_notification_date(self, em):
        event = {"name": "Community Day"}
        event_date = datetime(2024, 5, 10, 10, 0, 0)
        em.mark_event_notified(event, event_date, is_end=False)
        args, kwargs = em.poliswag.db.execute_query_to_database.call_args
        assert "notification_date" in args[0]
        assert "notification_end_date" not in args[0]
        assert kwargs["params"][1] == "Community Day"
        assert kwargs["params"][2] == "2024-05-10 10:00:00"

    def test_end_branch_updates_notification_end_date(self, em):
        event = {"name": "Community Day"}
        event_date = datetime(2024, 5, 10, 12, 0, 0)
        em.mark_event_notified(event, event_date, is_end=True)
        args, _ = em.poliswag.db.execute_query_to_database.call_args
        assert "notification_end_date" in args[0]
        assert "end = %s" in args[0]


# --- async fetch_events and process_and_store_events --------------------------


class TestFetchEvents:
    async def test_none_response_logs_and_returns(self, em, mocker):
        mocker.patch(
            "modules.event_manager.fetch_data", new=AsyncMock(return_value=None)
        )
        await em.fetch_events()
        assert em.events is None
        em.poliswag.utility.log_to_file.assert_called_once()
        assert (
            em.poliswag.utility.log_to_file.call_args.args[0]
            == "Failed to fetch events from API"
        )

    async def test_json_string_response_is_parsed(self, em, mocker):
        mocker.patch(
            "modules.event_manager.fetch_data",
            new=AsyncMock(
                return_value='[{"name": "x", "start": "2024-01-01T00:00:00"}]'
            ),
        )
        process = mocker.patch.object(em, "process_and_store_events", new=AsyncMock())
        await em.fetch_events()
        assert em.events == [{"name": "x", "start": "2024-01-01T00:00:00"}]
        process.assert_awaited_once()

    async def test_dict_response_is_stored_as_is(self, em, mocker):
        payload = [{"name": "x"}]
        mocker.patch(
            "modules.event_manager.fetch_data", new=AsyncMock(return_value=payload)
        )
        mocker.patch.object(em, "process_and_store_events", new=AsyncMock())
        await em.fetch_events()
        assert em.events is payload

    async def test_exception_during_processing_is_logged(self, em, mocker):
        mocker.patch(
            "modules.event_manager.fetch_data",
            new=AsyncMock(return_value="not-json}"),
        )
        await em.fetch_events()
        # The json.loads raised; error logged.
        log_calls = [c.args[0] for c in em.poliswag.utility.log_to_file.call_args_list]
        assert any("Error processing events" in m for m in log_calls)


class TestProcessAndStoreEvents:
    async def test_does_nothing_when_events_none(self, em):
        em.events = None
        await em.process_and_store_events()
        em.poliswag.db.execute_query_to_database.assert_not_called()

    async def test_upserts_each_event(self, em):
        em.events = [
            {
                "name": "Community Day",
                "start": "2024-05-10T09:00:00Z",
                "end": "2024-05-10T12:00:00Z",
                "image": "img.png",
                "eventType": "community-day",
                "link": "https://x",
            }
        ]
        em.poliswag.db.get_data_from_database.return_value = []  # no future events
        await em.process_and_store_events()
        em.poliswag.db.execute_query_to_database.assert_called_once()
        args, _ = em.poliswag.db.execute_query_to_database.call_args
        assert "INSERT INTO event" in args[0]

    async def test_skips_unannounced_event(self, em):
        em.events = [
            {
                "name": "Unannounced Thing",
                "start": "2024-05-10T09:00:00Z",
                "end": "2024-05-10T12:00:00Z",
            }
        ]
        em.poliswag.db.get_data_from_database.return_value = []
        await em.process_and_store_events()
        em.poliswag.db.execute_query_to_database.assert_not_called()

    async def test_skips_event_missing_required_fields(self, em):
        em.events = [{"name": "Community Day"}]  # no start/end
        em.poliswag.db.get_data_from_database.return_value = []
        await em.process_and_store_events()
        em.poliswag.db.execute_query_to_database.assert_not_called()

    async def test_removes_stale_future_events(self, em):
        em.events = [
            {
                "name": "Community Day",
                "start": "2024-05-10T09:00:00Z",
                "end": "2024-05-10T12:00:00Z",
            }
        ]
        # DB has a different future event that isn't in the API response.
        em.poliswag.db.get_data_from_database.return_value = [
            {"name": "Old Event"},
        ]
        await em.process_and_store_events()
        calls = em.poliswag.db.execute_query_to_database.call_args_list
        delete_calls = [c for c in calls if "DELETE FROM event" in c.args[0]]
        assert len(delete_calls) == 1
        assert delete_calls[0].kwargs["params"][0] == "Old Event"

    async def test_error_storing_single_event_does_not_abort_loop(self, em):
        em.events = [
            {
                "name": "Bad",
                "start": "2024-05-10T09:00:00Z",
                "end": "2024-05-10T12:00:00Z",
            },
            {
                "name": "Good",
                "start": "2024-05-11T09:00:00Z",
                "end": "2024-05-11T12:00:00Z",
            },
        ]
        em.poliswag.db.get_data_from_database.return_value = []
        # First upsert raises, second succeeds.
        em.poliswag.db.execute_query_to_database.side_effect = [
            RuntimeError("boom"),
            None,
        ]
        await em.process_and_store_events()
        log_calls = [c.args[0] for c in em.poliswag.utility.log_to_file.call_args_list]
        assert any("Error storing event Bad" in m for m in log_calls)


# --- check_current_events_changes ---------------------------------------------


class TestCheckCurrentEventsChanges:
    async def test_none_when_no_rows(self, em):
        em.poliswag.db.get_data_from_database.return_value = []
        assert await em.check_current_events_changes() is None

    async def test_active_event_without_notification_is_started(self, em):
        em.poliswag.db.get_data_from_database.return_value = [
            {
                "name": "Community Day",
                "start": "2024-05-10 09:00:00",
                "end": "2024-05-10 12:00:00",
                "event_status": "active",
                "notification_date": None,
                "notification_end_date": None,
            }
        ]
        result = await em.check_current_events_changes()
        assert result is not None
        assert len(result["started"]) == 1
        assert result["ended"] == []
        em.poliswag.db.execute_query_to_database.assert_called_once()

    async def test_ended_event_without_end_notification_is_ended(self, em):
        em.poliswag.db.get_data_from_database.return_value = [
            {
                "name": "Community Day",
                "start": "2024-05-10 09:00:00",
                "end": "2024-05-10 12:00:00",
                "event_status": "ended",
                "notification_date": "already",
                "notification_end_date": None,
            }
        ]
        result = await em.check_current_events_changes()
        assert result["ended"][0]["name"] == "Community Day"
        assert result["started"] == []

    async def test_already_notified_events_produce_none(self, em):
        em.poliswag.db.get_data_from_database.return_value = [
            {
                "name": "Community Day",
                "start": "2024-05-10 09:00:00",
                "end": "2024-05-10 12:00:00",
                "event_status": "active",
                "notification_date": "yes",  # already notified
                "notification_end_date": None,
            }
        ]
        assert await em.check_current_events_changes() is None

    async def test_dry_run_does_not_mark_notified(self, em):
        em.poliswag.db.get_data_from_database.return_value = [
            {
                "name": "Community Day",
                "start": "2024-05-10 09:00:00",
                "end": "2024-05-10 12:00:00",
                "event_status": "active",
                "notification_date": None,
                "notification_end_date": None,
            }
        ]
        # dry_run without at_time goes through normal path.
        result = await em.check_current_events_changes(dry_run=True)
        assert len(result["started"]) == 1
        em.poliswag.db.execute_query_to_database.assert_not_called()

    async def test_dry_run_with_at_time_delegates_to_helper(self, em, mocker):
        helper = mocker.patch.object(
            em, "_dry_run_changes", return_value={"started": [], "ended": []}
        )
        at = datetime(2024, 5, 10, 9, 0, 0)
        result = await em.check_current_events_changes(at_time=at, dry_run=True)
        helper.assert_called_once_with(at)
        assert result == {"started": [], "ended": []}

    async def test_malformed_date_is_logged_and_skipped(self, em):
        em.poliswag.db.get_data_from_database.return_value = [
            {
                "name": "Broken",
                "start": "not-a-date",
                "end": "2024-05-10 12:00:00",
                "event_status": "active",
                "notification_date": None,
                "notification_end_date": None,
            }
        ]
        assert await em.check_current_events_changes() is None
        log_calls = [c.args[0] for c in em.poliswag.utility.log_to_file.call_args_list]
        assert any("Error processing event Broken" in m for m in log_calls)


class TestDryRunChanges:
    def test_returns_started_and_ended_within_window(self, em):
        em.poliswag.db.get_data_from_database.side_effect = [
            [{"name": "Starts Now"}],  # started
            [{"name": "Ends Now"}],  # ended
        ]
        result = em._dry_run_changes(datetime(2024, 5, 10, 9, 0, 0))
        assert result["started"][0]["name"] == "Starts Now"
        assert result["ended"][0]["name"] == "Ends Now"

    def test_returns_none_when_both_empty(self, em):
        em.poliswag.db.get_data_from_database.side_effect = [[], []]
        assert em._dry_run_changes(datetime(2024, 5, 10, 9, 0, 0)) is None


# --- get_weekly_events --------------------------------------------------------


class TestGetWeeklyEvents:
    def _rows(self):
        return [
            {
                "name": "Community Day",
                "start": "2024-05-10 09:00:00",
                "end": "2024-05-10 12:00:00",
                "image": "img1",
                "event_type": "community-day",
                "link": "l1",
            },
            {
                "name": "Community Day",  # exact duplicate
                "start": "2024-05-10 09:00:00",
                "end": "2024-05-10 12:00:00",
                "image": "img1",
                "event_type": "community-day",
                "link": "l1",
            },
        ]

    def test_deduplicates_exact_name(self, em):
        em.poliswag.db.get_data_from_database.return_value = self._rows()
        result = em.get_weekly_events()
        assert len(result) == 1

    def test_prefers_specific_name_over_generic(self, em):
        em.poliswag.db.get_data_from_database.return_value = [
            {
                "name": "Maio Event",  # generic — contains month
                "start": "2024-05-10 09:00:00",
                "end": "2024-05-10 12:00:00",
                "image": "img",
                "event_type": "misc",
                "link": "l",
            },
            {
                "name": "Charmander Community Day",  # specific
                "start": "2024-05-10 09:00:00",
                "end": "2024-05-10 12:00:00",
                "image": "img",
                "event_type": "misc",
                "link": "l",
            },
        ]
        result = em.get_weekly_events()
        assert len(result) == 1
        assert result[0]["name"] == "Charmander Community Day"

    def test_empty_rows_return_empty_list(self, em):
        em.poliswag.db.get_data_from_database.return_value = []
        assert em.get_weekly_events() == []
