"""Tests for cogs.scheduled.Scheduled.

This is the heaviest cog — a periodic task loop plus admin commands plus
many private helpers. The strategy:

* Construct a Scheduled with a stub poliswag whose ``db.get_data_from_database``
  returns an empty list so ``_load_digest_date`` short-circuits.
* Avoid starting the real tasks.loop by never calling ``cog_load``; we call
  ``scheduled_tasks.coro(cog)`` directly when we need to cover it.
* Invoke command callbacks via ``.callback`` and helpers as bound methods.
* Patch ``datetime`` at the module level with a MagicMock when we need
  deterministic dates (e.g. ``_check_weekly_digest`` must behave as Monday).
"""

import datetime as real_datetime
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs.scheduled import Scheduled, setup

# --- fixtures -----------------------------------------------------------------


def _make_poliswag():
    poliswag = MagicMock()
    poliswag.db.get_data_from_database = AsyncMock(return_value=[])
    poliswag.db.execute_query_to_database = AsyncMock()
    poliswag.ADMIN_USERS_IDS = ["42"]
    poliswag.CONVIVIO_CHANNEL = MagicMock()
    poliswag.CONVIVIO_CHANNEL.send = AsyncMock()
    poliswag.QUEST_CHANNEL = MagicMock()
    poliswag.QUEST_CHANNEL.send = AsyncMock()
    poliswag.quest_scanning_message = None
    poliswag.utility.log_to_file = MagicMock()
    poliswag.utility.get_new_pokemongo_version = AsyncMock(return_value=None)
    poliswag.utility.find_quest_scanning_message = AsyncMock(return_value=None)
    poliswag.utility.read_new_error_entries = MagicMock(return_value=[])
    poliswag.MOD_CHANNEL = MagicMock()
    poliswag.MOD_CHANNEL.send = AsyncMock()
    poliswag.lure_watcher.check_new_lures = AsyncMock(return_value=[])
    poliswag.lure_watcher.count_active_lures = AsyncMock(return_value=0)
    poliswag.change_presence = AsyncMock()
    poliswag.event_manager.fetch_events = AsyncMock()
    poliswag.event_manager.check_current_events_changes = AsyncMock(return_value=None)
    poliswag.event_manager.get_event_type_key = MagicMock(return_value="community-day")
    poliswag.event_manager.get_event_emoji = MagicMock(return_value="🌟")
    poliswag.event_manager.get_event_link = MagicMock(return_value="http://link")
    poliswag.event_manager.format_end_time = MagicMock(return_value="Termina em 1h")
    poliswag.event_manager.event_colors = {"community-day": 0xFFCC00}
    poliswag.event_manager.get_weekly_events = AsyncMock(return_value=[])
    poliswag.quest_search.load_translation_data = MagicMock()
    poliswag.quest_search.load_masterfile_data = MagicMock(return_value=False)
    poliswag.quest_search.generate_pokemon_item_name_map = MagicMock()
    poliswag.quest_search.check_tracked = AsyncMock()
    poliswag.quest_exporter.export = AsyncMock()
    poliswag.scanner_manager.is_day_change = AsyncMock(return_value=False)
    poliswag.scanner_manager.update_quest_scanning_state = AsyncMock()
    poliswag.scanner_status.is_quest_scanning_complete = AsyncMock(return_value=None)
    poliswag.scanner_status.record_quest_scan_completion = AsyncMock()
    poliswag.scanner_status.get_workers_with_issues = AsyncMock(
        return_value={"downDevicesLeiria": [], "downDevicesMarinha": []}
    )
    poliswag.scanner_status.rename_voice_channels = AsyncMock()
    poliswag.account_monitor.update_channel_accounts_stats = AsyncMock()
    return poliswag


@pytest.fixture
def cog():
    return Scheduled(_make_poliswag())


def make_ctx(author_id="42", dm=False):
    ctx = MagicMock()
    ctx.author.id = author_id
    if dm:
        ctx.channel = MagicMock(spec=discord.DMChannel)
    else:
        ctx.channel = MagicMock()
        ctx.channel.send = AsyncMock()
    ctx.message.delete = AsyncMock()
    return ctx


# --- _load_digest_date / _save_digest_date -----------------------------------


class TestLoadDigestDate:
    """__init__ can't await, so the date is loaded lazily via cog_load ->
    _load_digest_date instead of at construction time — see cog_load."""

    async def test_returns_none_when_db_empty(self):
        poliswag = _make_poliswag()
        poliswag.db.get_data_from_database = AsyncMock(return_value=[])
        c = Scheduled(poliswag)
        assert c._last_weekly_digest_monday is None
        assert await c._load_digest_date() is None

    async def test_returns_date_when_row_has_date_object(self):
        poliswag = _make_poliswag()
        d = real_datetime.date(2026, 4, 7)
        poliswag.db.get_data_from_database = AsyncMock(
            return_value=[{"last_weekly_digest_date": d}]
        )
        c = Scheduled(poliswag)
        assert await c._load_digest_date() == d

    async def test_parses_isoformat_string(self):
        poliswag = _make_poliswag()
        poliswag.db.get_data_from_database = AsyncMock(
            return_value=[{"last_weekly_digest_date": "2026-04-07"}]
        )
        c = Scheduled(poliswag)
        assert await c._load_digest_date() == real_datetime.date(2026, 4, 7)

    async def test_exception_returns_none(self):
        poliswag = _make_poliswag()
        poliswag.db.get_data_from_database = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        c = Scheduled(poliswag)
        assert await c._load_digest_date() is None

    async def test_none_value_returns_none(self):
        poliswag = _make_poliswag()
        poliswag.db.get_data_from_database = AsyncMock(
            return_value=[{"last_weekly_digest_date": None}]
        )
        c = Scheduled(poliswag)
        assert await c._load_digest_date() is None


class TestSaveDigestDate:
    async def test_calls_update_query(self, cog):
        d = real_datetime.date(2026, 4, 6)
        await cog._save_digest_date(d)
        cog.poliswag.db.execute_query_to_database.assert_called_once()
        _, kwargs = cog.poliswag.db.execute_query_to_database.call_args
        assert kwargs["params"] == ("2026-04-06",)


# --- weeklydigestcmd ----------------------------------------------------------


class TestWeeklyDigestCmd:
    async def test_non_admin_silent(self, cog):
        ctx = make_ctx(author_id="nope")
        await Scheduled.weeklydigestcmd.callback(cog, ctx)
        ctx.channel.send.assert_not_called()

    async def test_guild_deletes_message_and_sends(self, cog):
        ctx = make_ctx()
        cog._send_weekly_digest = AsyncMock(return_value=True)
        await Scheduled.weeklydigestcmd.callback(cog, ctx)
        ctx.message.delete.assert_awaited_once()
        cog._send_weekly_digest.assert_awaited_once_with(channel=ctx.channel)

    async def test_dm_skips_delete(self, cog):
        ctx = make_ctx(dm=True)
        cog._send_weekly_digest = AsyncMock(return_value=True)
        await Scheduled.weeklydigestcmd.callback(cog, ctx)
        ctx.message.delete.assert_not_called()


# --- testeventcmd -------------------------------------------------------------


class TestTestEventCmd:
    async def test_non_admin_silent(self, cog):
        ctx = make_ctx(author_id="nope")
        await Scheduled.testeventcmd.callback(cog, ctx)
        ctx.channel.send.assert_not_called()

    async def test_bad_time_format_sends_hint(self, cog):
        ctx = make_ctx(dm=True)
        ctx.channel.send = AsyncMock()
        await Scheduled.testeventcmd.callback(cog, ctx, time_arg="not-a-time")
        ctx.channel.send.assert_awaited_once()
        embed = ctx.channel.send.call_args.kwargs["embed"]
        assert "Formato inválido" in embed.title

    async def test_valid_time_no_changes(self, cog):
        ctx = make_ctx(dm=True)
        ctx.channel.send = AsyncMock()
        cog.poliswag.event_manager.check_current_events_changes = AsyncMock(
            return_value=None
        )
        await Scheduled.testeventcmd.callback(cog, ctx, time_arg="10:30")
        ctx.channel.send.assert_awaited_once()
        embed = ctx.channel.send.call_args.kwargs["embed"]
        assert "10:30" in embed.description

    async def test_no_time_arg_agora_with_changes_triggers_notifications(self, cog):
        ctx = make_ctx(dm=True)
        ctx.channel.send = AsyncMock()
        cog.poliswag.event_manager.check_current_events_changes = AsyncMock(
            return_value={
                "started": [{"name": "A"}],
                "ended": [{"name": "B"}],
            }
        )
        cog._send_event_change_notifications = AsyncMock()
        await Scheduled.testeventcmd.callback(cog, ctx)
        embed = ctx.channel.send.call_args_list[0].kwargs["embed"]
        assert "agora" in embed.description
        cog._send_event_change_notifications.assert_awaited_once()

    async def test_guild_channel_deletes_message(self, cog):
        ctx = make_ctx(dm=False)
        cog.poliswag.event_manager.check_current_events_changes = AsyncMock(
            return_value=None
        )
        await Scheduled.testeventcmd.callback(cog, ctx)
        ctx.message.delete.assert_awaited_once()


# --- scheduled_tasks loop (via .coro) ----------------------------------------


class TestScheduledTasksLoop:
    async def test_happy_path_calls_all_helpers(self, cog):
        cog._check_version_update = AsyncMock()
        cog._check_quest_scan_progress = AsyncMock()
        cog._check_quest_export = AsyncMock()
        cog._check_events = AsyncMock()
        cog._check_workers = AsyncMock()
        cog._update_lure_status = AsyncMock()
        cog._update_accounts_display = AsyncMock()
        cog._check_weekly_digest = AsyncMock()
        cog._check_daily_error_digest = AsyncMock()
        await cog.scheduled_tasks.coro(cog)
        cog._check_version_update.assert_awaited_once()
        cog._check_quest_scan_progress.assert_awaited_once()
        cog._check_quest_export.assert_awaited_once()
        cog._check_events.assert_awaited_once()
        cog._check_workers.assert_awaited_once()
        cog._update_lure_status.assert_awaited_once()
        cog._update_accounts_display.assert_awaited_once()
        cog._check_weekly_digest.assert_awaited_once()
        cog._check_daily_error_digest.assert_awaited_once()

    async def test_masterfile_refreshed_generates_name_map(self, cog):
        cog.poliswag.quest_search.load_masterfile_data.return_value = True
        cog._check_version_update = AsyncMock()
        cog._check_quest_scan_progress = AsyncMock()
        cog._check_events = AsyncMock()
        cog._check_workers = AsyncMock()
        cog._update_lure_status = AsyncMock()
        cog._update_accounts_display = AsyncMock()
        cog._check_weekly_digest = AsyncMock()
        cog._check_daily_error_digest = AsyncMock()
        await cog.scheduled_tasks.coro(cog)
        cog.poliswag.quest_search.generate_pokemon_item_name_map.assert_called_once()

    async def test_exception_logged_and_swallowed(self, cog, capsys):
        cog._check_version_update = AsyncMock(side_effect=RuntimeError("boom"))
        await cog.scheduled_tasks.coro(cog)
        cog.poliswag.utility.log_to_file.assert_called()
        out = capsys.readouterr().out
        assert "CRASH" in out


class TestCheckQuestExport:
    async def test_exports_on_first_run(self, cog):
        cog._last_quest_export = None
        await cog._check_quest_export()
        cog.poliswag.quest_exporter.export.assert_awaited_once()
        assert cog._last_quest_export is not None

    async def test_skips_when_within_interval(self, cog):
        cog._last_quest_export = real_datetime.datetime.now()
        await cog._check_quest_export()
        cog.poliswag.quest_exporter.export.assert_not_awaited()

    async def test_exports_after_interval_elapsed(self, cog):
        cog._last_quest_export = real_datetime.datetime.now() - real_datetime.timedelta(
            minutes=31
        )
        await cog._check_quest_export()
        cog.poliswag.quest_exporter.export.assert_awaited_once()


class TestBeforeScheduledTasks:
    async def test_waits_until_ready(self, cog):
        cog.poliswag.wait_until_ready = AsyncMock()
        await cog.before_scheduled_tasks()
        cog.poliswag.wait_until_ready.assert_awaited_once()


# --- _check_version_update ----------------------------------------------------


class TestCheckVersionUpdate:
    async def test_no_new_version_sends_nothing(self, cog):
        cog.poliswag.utility.get_new_pokemongo_version = AsyncMock(return_value=None)
        await cog._check_version_update()
        cog.poliswag.CONVIVIO_CHANNEL.send.assert_not_called()

    async def test_new_version_sends_announcement(self, cog):
        cog.poliswag.utility.get_new_pokemongo_version = AsyncMock(return_value="0.0.1")
        await cog._check_version_update()
        cog.poliswag.CONVIVIO_CHANNEL.send.assert_awaited_once()


# --- _check_quest_scan_progress ----------------------------------------------


class TestCheckQuestScanProgress:
    async def test_day_changed_sends_start_message(self, cog):
        cog.poliswag.scanner_manager.is_day_change.return_value = True
        await cog._check_quest_scan_progress()
        cog.poliswag.QUEST_CHANNEL.send.assert_awaited_once()
        assert cog.poliswag.quest_scanning_message is not None

    async def test_find_message_called_when_none(self, cog):
        cog.poliswag.scanner_manager.is_day_change.return_value = False
        cog.poliswag.quest_scanning_message = None
        cog.poliswag.scanner_status.is_quest_scanning_complete = AsyncMock(
            return_value=None
        )
        await cog._check_quest_scan_progress()
        cog.poliswag.utility.find_quest_scanning_message.assert_awaited_once()

    async def test_complete_runs_tracked_and_export(self, cog):
        cog.poliswag.scanner_manager.is_day_change.return_value = False
        cog.poliswag.quest_scanning_message = MagicMock()
        cog.poliswag.quest_scanning_message.edit = AsyncMock()
        cog.poliswag.scanner_status.is_quest_scanning_complete = AsyncMock(
            return_value={
                "leiriaCompleted": True,
                "marinhaCompleted": True,
                "leiriaScanned": 10,
                "leiriaTotal": 10,
                "marinhaScanned": 8,
                "marinhaTotal": 8,
                "leiriaPercentage": 100.0,
                "marinhaPercentage": 100.0,
            }
        )
        await cog._check_quest_scan_progress()
        cog.poliswag.quest_search.check_tracked.assert_awaited_once()
        cog.poliswag.quest_exporter.export.assert_awaited_once()
        cog.poliswag.scanner_manager.update_quest_scanning_state.assert_called_once()

    async def test_in_progress_builds_progress_embed(self, cog):
        cog.poliswag.scanner_manager.is_day_change.return_value = False
        cog.poliswag.quest_scanning_message = MagicMock()
        cog.poliswag.quest_scanning_message.edit = AsyncMock()
        cog.poliswag.scanner_status.is_quest_scanning_complete = AsyncMock(
            return_value={
                "leiriaCompleted": False,
                "marinhaCompleted": False,
                "leiriaScanned": 5,
                "leiriaTotal": 10,
                "marinhaScanned": 2,
                "marinhaTotal": 8,
                "leiriaPercentage": 50.0,
                "marinhaPercentage": 25.0,
            }
        )
        await cog._check_quest_scan_progress()
        cog.poliswag.quest_scanning_message.edit.assert_awaited_once()

    async def test_quest_completed_none_short_circuits(self, cog):
        cog.poliswag.scanner_manager.is_day_change.return_value = False
        msg = MagicMock()
        msg.edit = AsyncMock()
        cog.poliswag.quest_scanning_message = msg
        cog.poliswag.scanner_status.is_quest_scanning_complete = AsyncMock(
            return_value=None
        )
        await cog._check_quest_scan_progress()
        msg.edit.assert_not_called()

    async def test_elif_sends_new_message_when_no_tracking_message(self, cog):
        cog.poliswag.scanner_manager.is_day_change.return_value = False
        # After find_quest_scanning_message we still have None, then build
        # progress embed path ends in the `elif QUEST_CHANNEL` branch.
        cog.poliswag.utility.find_quest_scanning_message = AsyncMock(return_value=None)
        cog.poliswag.scanner_status.is_quest_scanning_complete = AsyncMock(
            return_value={
                "leiriaCompleted": False,
                "marinhaCompleted": False,
                "leiriaScanned": 1,
                "leiriaTotal": 10,
                "marinhaScanned": 1,
                "marinhaTotal": 10,
                "leiriaPercentage": 10.0,
                "marinhaPercentage": 10.0,
            }
        )
        await cog._check_quest_scan_progress()
        cog.poliswag.QUEST_CHANNEL.send.assert_awaited_once()

    async def test_unchanged_state_with_existing_message_skips_edit(self, cog):
        # Same (leiriaScanned, marinhaScanned) pair on consecutive ticks, with
        # a tracking message already posted, must not re-render the embed.
        cog.poliswag.scanner_manager.is_day_change.return_value = False
        cog.poliswag.quest_scanning_message = MagicMock()
        cog.poliswag.quest_scanning_message.edit = AsyncMock()
        cog.poliswag.scanner_status.is_quest_scanning_complete = AsyncMock(
            return_value={
                "leiriaCompleted": False,
                "marinhaCompleted": False,
                "leiriaScanned": 5,
                "leiriaTotal": 10,
                "marinhaScanned": 2,
                "marinhaTotal": 8,
                "leiriaPercentage": 50.0,
                "marinhaPercentage": 25.0,
            }
        )
        await cog._check_quest_scan_progress()
        cog.poliswag.quest_scanning_message.edit.assert_awaited_once()
        await cog._check_quest_scan_progress()
        # Still just the one edit from the first tick.
        cog.poliswag.quest_scanning_message.edit.assert_awaited_once()

    async def test_deleted_message_self_heals_by_resending(self, cog):
        import discord

        cog.poliswag.scanner_manager.is_day_change.return_value = False
        stale_message = MagicMock()
        stale_message.edit = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "Unknown Message")
        )
        cog.poliswag.quest_scanning_message = stale_message
        cog.poliswag.QUEST_CHANNEL.send = AsyncMock(return_value=MagicMock())
        cog.poliswag.scanner_status.is_quest_scanning_complete = AsyncMock(
            return_value={
                "leiriaCompleted": False,
                "marinhaCompleted": False,
                "leiriaScanned": 5,
                "leiriaTotal": 10,
                "marinhaScanned": 2,
                "marinhaTotal": 8,
                "leiriaPercentage": 50.0,
                "marinhaPercentage": 25.0,
            }
        )
        await cog._check_quest_scan_progress()
        stale_message.edit.assert_awaited_once()
        cog.poliswag.QUEST_CHANNEL.send.assert_awaited_once()
        assert (
            cog.poliswag.quest_scanning_message
            is cog.poliswag.QUEST_CHANNEL.send.return_value
        )


# --- _build_progress_embed ----------------------------------------------------


class TestBuildProgressEmbed:
    @pytest.mark.parametrize(
        "leiria_pct,marinha_pct,expected_emoji",
        [
            (0.0, 0.0, "🔍"),
            (30.0, 30.0, "⏳"),
            (60.0, 60.0, "⌛"),
            (90.0, 90.0, "🔜"),
        ],
    )
    def test_emoji_selection(self, cog, leiria_pct, marinha_pct, expected_emoji):
        embed = cog._build_progress_embed(
            {
                "leiriaScanned": 1,
                "leiriaTotal": 10,
                "marinhaScanned": 1,
                "marinhaTotal": 10,
                "leiriaPercentage": leiria_pct,
                "marinhaPercentage": marinha_pct,
            }
        )
        assert expected_emoji in embed.title


# --- _check_events ------------------------------------------------------------


class TestCheckEvents:
    async def test_no_convivio_channel_returns(self, cog):
        cog.poliswag.CONVIVIO_CHANNEL = None
        await cog._check_events()
        # Must not raise.

    async def test_no_changes_returns(self, cog):
        cog.poliswag.event_manager.check_current_events_changes = AsyncMock(
            return_value=None
        )
        cog._send_event_change_notifications = AsyncMock()
        await cog._check_events()
        cog._send_event_change_notifications.assert_not_called()

    async def test_with_changes_calls_notifications(self, cog):
        cog.poliswag.event_manager.check_current_events_changes = AsyncMock(
            return_value={"started": [], "ended": []}
        )
        cog._send_event_change_notifications = AsyncMock()
        await cog._check_events()
        cog._send_event_change_notifications.assert_awaited_once()


# --- _send_event_change_notifications ----------------------------------------


class TestSendEventChangeNotifications:
    async def test_ended_then_started(self, cog):
        channel = MagicMock()
        channel.send = AsyncMock()
        event = {
            "event_type": "Community Day",
            "name": "Test",
            "end": "2026-04-07 20:00:00",
            "image": None,
        }
        changed = {"ended": [event], "started": [event]}
        await cog._send_event_change_notifications(channel, changed)
        # 2 headers + 2 embed sends = 4
        assert channel.send.await_count == 4

    async def test_empty_lists_send_nothing(self, cog):
        channel = MagicMock()
        channel.send = AsyncMock()
        await cog._send_event_change_notifications(
            channel, {"ended": [], "started": []}
        )
        channel.send.assert_not_called()


# --- _build_event_embed -------------------------------------------------------


class TestBuildEventEmbed:
    def test_ended_event_omits_description(self, cog):
        event = {
            "event_type": "Community Day",
            "name": "Test",
            "end": "2026-04-07 20:00:00",
            "image": None,
        }
        embed = cog._build_event_embed(event, is_ended=True)
        assert isinstance(embed, discord.Embed)
        assert "Test" in embed.title
        # Ended notifications are sent *at* the end time, so repeating
        # "Terminou às HH:MM" in the body would duplicate the message timestamp.
        assert embed.description in (None, "")
        cog.poliswag.event_manager.format_end_time.assert_not_called()

    def test_started_event_with_image(self, cog):
        event = {
            "event_type": "Community Day",
            "name": "Test",
            "end": "2026-04-07 20:00:00",
            "image": "http://img",
        }
        embed = cog._build_event_embed(event, is_ended=False)
        assert embed.thumbnail.url == "http://img"
        cog.poliswag.event_manager.format_end_time.assert_called_once_with(
            real_datetime.datetime(2026, 4, 7, 20, 0)
        )


# --- _check_workers -----------------------------------------------------------


class TestCheckWorkers:
    async def test_passes_worker_status_to_rename(self, cog):
        workers_status = {
            "downDevicesLeiria": ["a"],
            "downDevicesMarinha": ["b"],
            "expectedWorkersLeiria": 7,
            "expectedWorkersMarinha": 1,
        }
        cog.poliswag.scanner_status.get_workers_with_issues = AsyncMock(
            return_value=workers_status
        )
        cog.poliswag.device_manager.alert_if_offline = AsyncMock()
        await cog._check_workers()
        cog.poliswag.scanner_status.rename_voice_channels.assert_awaited_once_with(
            workers_status
        )


class TestCheckNewLures:
    async def test_no_new_lures_sends_nothing(self, cog):
        cog.poliswag.lure_watcher.check_new_lures = AsyncMock(return_value=[])
        await cog._check_new_lures()
        cog.poliswag.CONVIVIO_CHANNEL.send.assert_not_called()

    async def test_no_convivio_channel_is_a_noop(self, cog):
        cog.poliswag.CONVIVIO_CHANNEL = None
        cog.poliswag.lure_watcher.check_new_lures = AsyncMock(
            return_value=[
                {
                    "name": "Anfiteatro",
                    "lat": 39.7175,
                    "lon": -8.8022,
                    "area": "Leiria",
                    "lure_name": "Rainy Lure",
                    "expires_at": real_datetime.datetime(2026, 4, 7, 18, 34, 0),
                }
            ]
        )
        await cog._check_new_lures()  # must not raise

    async def test_sends_one_embed_per_new_lure(self, cog):
        cog.poliswag.lure_watcher.check_new_lures = AsyncMock(
            return_value=[
                {
                    "name": "Anfiteatro",
                    "lat": 39.7175,
                    "lon": -8.8022,
                    "area": "Leiria",
                    "lure_name": "Rainy Lure",
                    "expires_at": real_datetime.datetime(2026, 4, 7, 18, 34, 0),
                },
                {
                    "name": "PokéStop",
                    "lat": 39.71,
                    "lon": -8.81,
                    "area": "Marinha Grande",
                    "lure_name": "Normal Lure",
                    "expires_at": real_datetime.datetime(2026, 4, 7, 19, 0, 0),
                },
            ]
        )
        await cog._check_new_lures()
        assert cog.poliswag.CONVIVIO_CHANNEL.send.await_count == 2
        embed = cog.poliswag.CONVIVIO_CHANNEL.send.call_args_list[0].kwargs["embed"]
        assert "Anfiteatro" in embed.title
        assert "Rainy Lure" in embed.title
        assert "Leiria" in embed.description
        assert "18:34" in embed.description
        assert "39.7175,-8.8022" in embed.description
        assert embed.timestamp is None


# --- _update_lure_status -------------------------------------------------------


class TestUpdateLureStatus:
    async def test_sets_presence_on_first_run(self, cog):
        cog.poliswag.lure_watcher.count_active_lures = AsyncMock(return_value=3)
        await cog._update_lure_status()
        cog.poliswag.change_presence.assert_awaited_once()
        activity = cog.poliswag.change_presence.call_args.kwargs["activity"]
        assert activity.type == discord.ActivityType.watching
        assert activity.name == "3 lures active"
        assert cog._last_lure_status_count == 3

    async def test_unchanged_count_skips_presence_update(self, cog):
        cog._last_lure_status_count = 3
        cog.poliswag.lure_watcher.count_active_lures = AsyncMock(return_value=3)
        await cog._update_lure_status()
        cog.poliswag.change_presence.assert_not_called()

    async def test_changed_count_updates_presence(self, cog):
        cog._last_lure_status_count = 3
        cog.poliswag.lure_watcher.count_active_lures = AsyncMock(return_value=5)
        await cog._update_lure_status()
        cog.poliswag.change_presence.assert_awaited_once()
        activity = cog.poliswag.change_presence.call_args.kwargs["activity"]
        assert activity.name == "5 lures active"
        assert cog._last_lure_status_count == 5


# --- _update_accounts_display -------------------------------------------------


class TestUpdateAccountsDisplay:
    async def test_delegates_to_account_monitor(self, cog):
        await cog._update_accounts_display()
        cog.poliswag.account_monitor.update_channel_accounts_stats.assert_awaited_once()


# --- _send_weekly_digest ------------------------------------------------------


class TestSendWeeklyDigest:
    async def test_no_events_returns_false(self, cog):
        cog.poliswag.event_manager.get_weekly_events.return_value = []
        assert (await cog._send_weekly_digest()) is False

    async def test_filters_battle_and_league(self, cog):
        cog.poliswag.event_manager.get_weekly_events.return_value = [
            {
                "event_type": "Go Battle League",
                "name": "GBL",
                "start": "2026-04-07 00:00:00",
                "end": "2026-04-07 23:59:00",
            },
            {
                "event_type": "PvP League",
                "name": "PVP",
                "start": "2026-04-07 00:00:00",
                "end": "2026-04-07 23:59:00",
            },
        ]
        assert (await cog._send_weekly_digest()) is False
        cog.poliswag.CONVIVIO_CHANNEL.send.assert_not_called()

    async def test_ongoing_and_upcoming_send_embed(self, cog):
        now = real_datetime.datetime(2026, 4, 7, 12, 0)
        past = now - real_datetime.timedelta(hours=1)
        future = now + real_datetime.timedelta(days=1, hours=2)
        future_end = future + real_datetime.timedelta(hours=3)
        cog.poliswag.event_manager.get_weekly_events.return_value = [
            {
                "event_type": "Community Day",
                "name": "Ongoing",
                "start": past.strftime("%Y-%m-%d %H:%M:%S"),
                "end": (now + real_datetime.timedelta(hours=2)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            },
            {
                "event_type": "Spotlight Hour",
                "name": "Upcoming",
                "start": future.strftime("%Y-%m-%d %H:%M:%S"),
                "end": future_end.strftime("%Y-%m-%d %H:%M:%S"),
            },
        ]
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.datetime.strptime = real_datetime.datetime.strptime
            mock_dt.timedelta = real_datetime.timedelta
            result = await cog._send_weekly_digest()
        assert result is True
        cog.poliswag.CONVIVIO_CHANNEL.send.assert_awaited_once()

    async def test_today_upcoming_uses_hoje_label(self, cog):
        now = real_datetime.datetime(2026, 4, 7, 9, 0)
        future_today = now + real_datetime.timedelta(hours=6)
        cog.poliswag.event_manager.get_weekly_events.return_value = [
            {
                "event_type": "Raid Hour",
                "name": "Tonight",
                "start": future_today.strftime("%Y-%m-%d %H:%M:%S"),
                "end": (future_today + real_datetime.timedelta(hours=1)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        ]
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.datetime.strptime = real_datetime.datetime.strptime
            mock_dt.timedelta = real_datetime.timedelta
            await cog._send_weekly_digest()
        embed = cog.poliswag.CONVIVIO_CHANNEL.send.call_args.kwargs["embed"]
        assert "HOJE" in embed.description

    async def test_explicit_channel_overrides_default(self, cog):
        now = real_datetime.datetime(2026, 4, 7, 12, 0)
        cog.poliswag.event_manager.get_weekly_events.return_value = [
            {
                "event_type": "Community Day",
                "name": "X",
                "start": (now - real_datetime.timedelta(hours=1)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "end": (now + real_datetime.timedelta(hours=1)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        ]
        override = MagicMock()
        override.send = AsyncMock()
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.datetime.strptime = real_datetime.datetime.strptime
            mock_dt.timedelta = real_datetime.timedelta
            await cog._send_weekly_digest(channel=override)
        override.send.assert_awaited_once()
        cog.poliswag.CONVIVIO_CHANNEL.send.assert_not_called()

    async def test_multi_day_event_end_uses_date_prefix(self, cog):
        now = real_datetime.datetime(2026, 4, 7, 12, 0)
        start = now + real_datetime.timedelta(days=1)
        end = start + real_datetime.timedelta(days=2)
        cog.poliswag.event_manager.get_weekly_events.return_value = [
            {
                "event_type": "Community Day",
                "name": "MultiDay",
                "start": start.strftime("%Y-%m-%d %H:%M:%S"),
                "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            }
        ]
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.datetime.strptime = real_datetime.datetime.strptime
            mock_dt.timedelta = real_datetime.timedelta
            await cog._send_weekly_digest()
        embed = cog.poliswag.CONVIVIO_CHANNEL.send.call_args.kwargs["embed"]
        # Multi-day end formatted as "dd/mm HH:MM" not just "HH:MM"
        assert end.strftime("%d/%m") in embed.description


# --- _check_weekly_digest -----------------------------------------------------


class TestCheckWeeklyDigest:
    async def test_non_monday_skips(self, cog):
        tue = real_datetime.datetime(2026, 4, 7, 10, 0)  # Tuesday
        cog._send_weekly_digest = AsyncMock()
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = tue
            await cog._check_weekly_digest()
        cog._send_weekly_digest.assert_not_called()

    async def test_monday_before_9am_skips(self, cog):
        early_mon = real_datetime.datetime(2026, 4, 6, 8, 59)
        cog._last_weekly_digest_monday = None
        cog._send_weekly_digest = AsyncMock()
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = early_mon
            await cog._check_weekly_digest()
        cog._send_weekly_digest.assert_not_called()

    async def test_monday_same_day_skips(self, cog):
        mon = real_datetime.datetime(2026, 4, 6, 10, 0)
        cog._last_weekly_digest_monday = mon.date()
        cog._send_weekly_digest = AsyncMock()
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = mon
            await cog._check_weekly_digest()
        cog._send_weekly_digest.assert_not_called()

    async def test_new_monday_after_9am_sends_and_persists(self, cog):
        mon = real_datetime.datetime(2026, 4, 6, 10, 0)
        cog._last_weekly_digest_monday = None
        cog._send_weekly_digest = AsyncMock()
        cog._save_digest_date = AsyncMock()
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = mon
            await cog._check_weekly_digest()
        cog._save_digest_date.assert_called_once_with(mon.date())
        cog._send_weekly_digest.assert_awaited_once()
        assert cog._last_weekly_digest_monday == mon.date()


# --- _load_error_digest_at / _save_error_digest_at ----------------------------


class TestLoadErrorDigestAt:
    async def test_returns_none_when_db_empty(self):
        poliswag = _make_poliswag()
        poliswag.db.get_data_from_database = AsyncMock(return_value=[])
        c = Scheduled(poliswag)
        assert await c._load_error_digest_at() is None

    async def test_returns_datetime_when_row_has_datetime_object(self):
        poliswag = _make_poliswag()
        dt = real_datetime.datetime(2026, 4, 7, 9, 0, 0)
        poliswag.db.get_data_from_database = AsyncMock(
            return_value=[{"last_error_digest_at": dt}]
        )
        c = Scheduled(poliswag)
        assert await c._load_error_digest_at() == dt

    async def test_parses_isoformat_string(self):
        poliswag = _make_poliswag()
        poliswag.db.get_data_from_database = AsyncMock(
            return_value=[{"last_error_digest_at": "2026-04-07 09:00:00"}]
        )
        c = Scheduled(poliswag)
        assert await c._load_error_digest_at() == real_datetime.datetime(
            2026, 4, 7, 9, 0, 0
        )

    async def test_exception_returns_none(self):
        poliswag = _make_poliswag()
        poliswag.db.get_data_from_database = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        c = Scheduled(poliswag)
        assert await c._load_error_digest_at() is None

    async def test_none_value_returns_none(self):
        poliswag = _make_poliswag()
        poliswag.db.get_data_from_database = AsyncMock(
            return_value=[{"last_error_digest_at": None}]
        )
        c = Scheduled(poliswag)
        assert await c._load_error_digest_at() is None


class TestSaveErrorDigestAt:
    async def test_calls_update_query(self, cog):
        dt = real_datetime.datetime(2026, 4, 6, 9, 30, 0)
        await cog._save_error_digest_at(dt)
        cog.poliswag.db.execute_query_to_database.assert_called_once()
        _, kwargs = cog.poliswag.db.execute_query_to_database.call_args
        assert kwargs["params"] == ("2026-04-06 09:30:00",)


# --- _check_daily_error_digest -------------------------------------------------


class TestCheckDailyErrorDigest:
    async def test_skips_before_9am(self, cog):
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = real_datetime.datetime(
                2026, 4, 7, 8, 59, 0
            )
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()
        cog.poliswag.utility.read_new_error_entries.assert_not_called()

    async def test_skips_when_already_run_today(self, cog):
        cog._last_error_digest_at = real_datetime.datetime(2026, 4, 7, 9, 5, 0)
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = real_datetime.datetime(
                2026, 4, 7, 10, 0, 0
            )
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()
        cog.poliswag.utility.read_new_error_entries.assert_not_called()

    async def test_no_entries_is_silent_but_still_advances_pointer(self, cog):
        now = real_datetime.datetime(2026, 4, 7, 9, 30, 0)
        cog.poliswag.utility.read_new_error_entries = MagicMock(return_value=[])
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()
        cog.poliswag.MOD_CHANNEL.send.assert_not_called()
        assert cog._last_error_digest_at == now
        cog.poliswag.db.execute_query_to_database.assert_called_once()

    async def test_entries_are_summarized_and_sent(self, cog):
        now = real_datetime.datetime(2026, 4, 7, 9, 30, 0)
        cog.poliswag.MOD_CHANNEL = MagicMock()
        cog.poliswag.MOD_CHANNEL.send = AsyncMock()
        cog.poliswag.utility.read_new_error_entries = MagicMock(
            return_value=["line1", "line2"]
        )
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()
        cog.poliswag.MOD_CHANNEL.send.assert_awaited_once()
        title = cog.poliswag.MOD_CHANNEL.send.call_args.kwargs["embed"].title
        assert "2 erro" in title

    async def test_more_than_ten_entries_notes_the_overflow(self, cog):
        now = real_datetime.datetime(2026, 4, 7, 9, 30, 0)
        cog.poliswag.MOD_CHANNEL = MagicMock()
        cog.poliswag.MOD_CHANNEL.send = AsyncMock()
        cog.poliswag.utility.read_new_error_entries = MagicMock(
            return_value=[f"line{i}" for i in range(15)]
        )
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()
        desc = cog.poliswag.MOD_CHANNEL.send.call_args.kwargs["embed"].description
        assert "e mais 5" in desc

    async def test_no_mod_channel_is_a_noop(self, cog):
        now = real_datetime.datetime(2026, 4, 7, 9, 30, 0)
        cog.poliswag.MOD_CHANNEL = None
        cog.poliswag.utility.read_new_error_entries = MagicMock(return_value=["line1"])
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()  # must not raise

    async def test_uses_one_day_lookback_when_never_run_before(self, cog):
        now = real_datetime.datetime(2026, 4, 7, 9, 30, 0)
        cog._last_error_digest_at = None
        cog.poliswag.utility.read_new_error_entries = MagicMock(return_value=[])
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()
        since_arg = cog.poliswag.utility.read_new_error_entries.call_args.args[0]
        assert since_arg == now - real_datetime.timedelta(days=1)


# --- lifecycle ---------------------------------------------------------------


class TestLifecycle:
    async def test_cog_load_starts_loop_and_prints(self, cog, capsys):
        # Prevent the task from actually running in the background.
        cog.scheduled_tasks.start = MagicMock()
        await cog.cog_load()
        cog.scheduled_tasks.start.assert_called_once()
        assert "Scheduled loaded" in capsys.readouterr().out

    async def test_cog_unload_cancels_loop_and_prints(self, cog, capsys):
        cog.scheduled_tasks.cancel = MagicMock()
        await cog.cog_unload()
        cog.scheduled_tasks.cancel.assert_called_once()
        assert "Scheduled unloaded" in capsys.readouterr().out


class TestSetup:
    async def test_registers_cog_on_poliswag(self):
        poliswag = MagicMock()
        poliswag.add_cog = AsyncMock()
        await setup(poliswag)
        poliswag.add_cog.assert_awaited_once()
        assert isinstance(poliswag.add_cog.call_args.args[0], Scheduled)
