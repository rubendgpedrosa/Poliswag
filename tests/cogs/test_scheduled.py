"""Tests for cogs.scheduled.Scheduled.

This is the heaviest cog — a periodic task loop plus admin commands plus
many private helpers. The strategy:

* Construct a Scheduled with a stub poliswag whose ``db.get_data_from_database``
  returns an empty list so ``_load_digest_date`` short-circuits.
* Avoid starting the real loops by never calling ``cog_load``; ``_tick(cog)``
  runs one pass of each of them, in order, when a test needs a whole minute.
* Invoke command callbacks via ``.callback`` and helpers as bound methods.
* Patch ``datetime`` at the module level with a MagicMock when we need
  deterministic dates (e.g. ``_check_weekly_digest`` must behave as Monday).
"""

import datetime as real_datetime
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs.scheduled import (
    _LOOP_OFFSETS,
    Scheduled,
    _group_error_entries,
    _seconds,
    _step_line,
    setup,
)
from modules.event_notices import EventNotices
from modules.hundo_alerts import HundoAlerts
from modules.permissions import is_mod
from modules.scheduler import Step
from modules.shiny_alerts import ShinyAlerts
from modules.shiny_availability import ShinyAvailability
from modules.shiny_odds import ShinyOdds
from modules.tracking_health import TrackingHealth
from modules.trade_announcer import TradeAnnouncer
from modules.trade_digest import TradeDigest
from modules.trade_dm import TradeDM
from tests.fakes import FakeHandle

# --- fixtures -----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_side_effects(monkeypatch, tmp_path):
    """Ticks here must not touch the live heartbeat or the CDN."""
    from modules.config import Config

    monkeypatch.setattr(Config, "HEARTBEAT_FILE", str(tmp_path / "heartbeat"))

    async def image_as_is(url):
        return url

    # Cards check their picture over HTTP; tests/modules/test_event_images.py
    # covers that. Here every image is taken as it is.
    monkeypatch.setattr("modules.event_notices.usable_image", image_as_is)
    monkeypatch.setattr(
        "modules.shiny_availability.ShinyAvailability.tick", AsyncMock()
    )


def _make_poliswag():
    poliswag = MagicMock()
    poliswag.db.get_data_from_database = AsyncMock(return_value=[])
    poliswag.db.execute_query_to_database = AsyncMock()
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
    poliswag.event_manager.mark_event_notified = AsyncMock()
    poliswag.event_manager.fetch_events = AsyncMock()
    poliswag.event_manager.check_current_events_changes = AsyncMock(return_value=None)
    poliswag.event_manager.get_event_type_key = MagicMock(return_value="community-day")
    poliswag.event_manager.get_event_emoji = MagicMock(return_value="🌟")
    poliswag.event_manager.get_event_link = MagicMock(return_value="http://link")
    poliswag.event_manager.format_end_time = MagicMock(return_value="Termina em 1h")
    poliswag.event_manager.event_colors = {"community-day": 0xFFCC00}
    poliswag.event_manager.get_weekly_events = AsyncMock(return_value=[])
    poliswag.event_stats.get_summary = AsyncMock(return_value=None)
    # No renderer in tests unless one asks: ended events use the text cards.
    poliswag.image_generator.generate_stat_board = AsyncMock(return_value=None)
    poliswag.image_generator.generate_week_board = AsyncMock(return_value=None)
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
    poliswag.account_guard.tick = AsyncMock()
    poliswag.site_db = FakeHandle()
    poliswag.poracle_db = FakeHandle()
    # The services the scheduler runs are built by the bot (main.py). Real
    # ones here too, so a pass exercises them the way production does.
    poliswag.event_notices = EventNotices(poliswag)
    poliswag.trade_announcer = TradeAnnouncer(poliswag)
    poliswag.trade_dm = TradeDM(poliswag)
    poliswag.trade_digest = TradeDigest(poliswag)
    poliswag.hundo_alerts = HundoAlerts(poliswag)
    poliswag.shiny_odds = ShinyOdds(poliswag)
    poliswag.shiny_availability = ShinyAvailability(poliswag)
    poliswag.shiny_alerts = ShinyAlerts(poliswag)
    poliswag.tracking_health = TrackingHealth(poliswag.site_db)
    return poliswag


@pytest.fixture
def cog():
    return Scheduled(_make_poliswag())


async def _tick(cog):
    """One pass of every loop, in order: a minute of the scheduler."""
    for name in cog._runner.loops:
        await cog._pass(name)


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
    def test_gated_to_mods(self):
        # The gate lives on the command (mods_only), not in the body.
        assert is_mod in Scheduled.weeklydigestcmd.checks

    async def test_guild_deletes_message_and_sends(self, cog):
        ctx = make_ctx()
        cog.poliswag.event_notices.send_weekly_digest = AsyncMock(return_value=True)
        await Scheduled.weeklydigestcmd.callback(cog, ctx)
        ctx.message.delete.assert_awaited_once()
        cog.poliswag.event_notices.send_weekly_digest.assert_awaited_once_with(
            channel=ctx.channel
        )

    async def test_dm_skips_delete(self, cog):
        ctx = make_ctx(dm=True)
        cog.poliswag.event_notices.send_weekly_digest = AsyncMock(return_value=True)
        await Scheduled.weeklydigestcmd.callback(cog, ctx)
        ctx.message.delete.assert_not_called()


# --- testeventcmd -------------------------------------------------------------


class TestTestEventCmd:
    def test_gated_to_mods(self):
        assert is_mod in Scheduled.testeventcmd.checks

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
        cog.poliswag.event_notices.send_change_notifications = AsyncMock()
        await Scheduled.testeventcmd.callback(cog, ctx)
        embed = ctx.channel.send.call_args_list[0].kwargs["embed"]
        assert "agora" in embed.description
        cog.poliswag.event_notices.send_change_notifications.assert_awaited_once()

    async def test_guild_channel_deletes_message(self, cog):
        ctx = make_ctx(dm=False)
        cog.poliswag.event_manager.check_current_events_changes = AsyncMock(
            return_value=None
        )
        await Scheduled.testeventcmd.callback(cog, ctx)
        ctx.message.delete.assert_awaited_once()


# --- the loops (one pass of each) ---------------------------------------------


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
        await _tick(cog)
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
        await _tick(cog)
        cog.poliswag.quest_search.generate_pokemon_item_name_map.assert_called_once()

    async def test_each_pass_writes_the_heartbeat(self, cog):
        from modules.config import Config

        await cog._pass("health")
        with open(Config.HEARTBEAT_FILE) as f:
            written = real_datetime.datetime.fromisoformat(f.read())
        # Naive UTC with no "+00:00" suffix, as utcnow() wrote it.
        assert written.tzinfo is None

    async def test_heartbeat_is_as_old_as_the_loop_that_stopped_passing(self, cog):
        import os

        from modules.config import Config

        # The watchdog reads the file's time. Three loops still passing must
        # not hide a fourth that stopped twenty minutes ago.
        stalled = real_datetime.datetime.now().timestamp() - 1200
        cog._runner._passed["alerts"] = stalled
        await cog._pass("health")
        assert os.path.getmtime(Config.HEARTBEAT_FILE) == pytest.approx(stalled)

    async def test_exception_logged_and_swallowed(self, cog, capsys):
        cog._check_version_update = AsyncMock(side_effect=RuntimeError("boom"))
        await _tick(cog)
        cog.poliswag.utility.log_to_file.assert_called()
        out = capsys.readouterr().out
        assert "CRASH" in out

    async def test_a_step_past_its_budget_does_not_hold_up_the_next(self, cog):
        import asyncio

        release = asyncio.Event()

        async def stuck():
            await release.wait()

        after = AsyncMock()
        cog._runner.add_loop(
            "health", [Step("stuck", stuck, budget=0.01), Step("after", after)]
        )
        await cog._pass("health")
        after.assert_awaited_once()
        message, level = cog.poliswag.utility.log_to_file.call_args.args
        assert level == "ERROR" and "stuck" in message
        release.set()
        await asyncio.sleep(0)

    async def test_one_failing_step_does_not_block_the_rest(self, cog):
        # A failure in an earlier step (e.g. the version check) must not
        # starve later steps -- especially _check_workers, which feeds
        # StackRecovery's self-healing.
        cog._refresh_masterfile_data = AsyncMock(side_effect=RuntimeError("boom"))
        cog._check_version_update = AsyncMock()
        cog._check_quest_scan_progress = AsyncMock()
        cog._check_quest_export = AsyncMock()
        cog._check_events = AsyncMock()
        cog._check_workers = AsyncMock()
        cog._update_lure_status = AsyncMock()
        cog._update_accounts_display = AsyncMock()
        cog._check_weekly_digest = AsyncMock()
        cog._check_daily_error_digest = AsyncMock()

        await _tick(cog)

        cog._check_version_update.assert_awaited_once()
        cog._check_workers.assert_awaited_once()
        cog._check_daily_error_digest.assert_awaited_once()


class TestHundoAlertsStep:
    """The real HundoAlerts.tick inside the scheduler, its I/O faked."""

    @staticmethod
    def _quiet_other_steps(cog):
        for name in (
            "_refresh_masterfile_data",
            "_check_version_update",
            "_check_quest_scan_progress",
            "_check_quest_export",
            "_check_events",
            "_check_workers",
            "_update_lure_status",
            "_update_accounts_display",
            "_check_weekly_digest",
            "_check_daily_error_digest",
            "_check_tracking_health",
            "_check_trade_digest",
        ):
            setattr(cog, name, AsyncMock())
        cog.poliswag.trade_announcer.tick = AsyncMock()
        cog.poliswag.trade_dm.tick = AsyncMock()
        cog.poliswag.shiny_alerts.tick = AsyncMock()
        cog.poliswag.shiny_odds.tick = AsyncMock()
        cog.poliswag.shiny_availability.tick = AsyncMock()

    @staticmethod
    def _fake_io(cog, rows_before, rows_after):
        poliswag = cog.poliswag
        poliswag.quest_search.masterfile_data = {
            "pokemon": {"19": {"defaultFormId": 45}}
        }
        poliswag.poracle.reload = AsyncMock()
        poliswag.poracle.health = AsyncMock()
        poliswag.poracle.create_user = AsyncMock()
        poliswag.get_user.return_value.send = AsyncMock()
        alerts = cog.poliswag.hundo_alerts
        alerts._read_players = MagicMock(side_effect=[rows_before, rows_after])
        alerts._cleanup = MagicMock(return_value=False)
        alerts._human = MagicMock(
            return_value={
                "id": "1",
                "type": "discord:user",
                "enabled": 1,
                "admin_disable": 0,
                "current_profile_no": 1,
            }
        )
        alerts._sync_player = MagicMock(return_value=True)
        alerts._record = MagicMock(return_value=True)
        alerts._missing_count = MagicMock(return_value=1499)
        alerts._record_health = MagicMock()

        async def deliver(row, embed):
            await poliswag.get_user.return_value.send(embed=embed)
            return True

        alerts._deliver_confirmation = deliver
        return alerts

    @staticmethod
    def _player(**patch):
        return {
            "discord_id": 1,
            "display_name": "Rui",
            "collecting": "hundo",
            "show_costumes": 1,
            "left_at": None,
            "hundo_dms": 1,
            "hundo_areas": "leiria,marinha",
            "hundo_settings_revision": 2,
            "hundo_confirmed_revision": 1,
            "hundo_dm_refused_revision": 0,
            **patch,
        }

    def _logged(self, cog):
        return [
            " ".join(map(str, c.args))
            for c in cog.poliswag.utility.log_to_file.call_args_list
        ]

    async def test_confirms_syncs_and_reloads_once_then_later_steps_run(self, cog):
        self._quiet_other_steps(cog)
        alerts = self._fake_io(
            cog, [self._player()], [self._player(hundo_confirmed_revision=2)]
        )
        await _tick(cog)
        alerts._record.assert_called_once_with(
            1, 2, delivered=True, settings=(1, "leiria,marinha")
        )
        alerts._sync_player.assert_called_once()
        cog.poliswag.poracle.reload.assert_awaited_once()
        cog._check_trade_digest.assert_awaited_once()
        logged = self._logged(cog)
        assert not any("CRASH" in line for line in logged)
        assert not any("[HUNDO]" in line and "failed" in line for line in logged)

    async def test_transient_confirmation_failure_still_cleans_and_continues(self, cog):
        self._quiet_other_steps(cog)
        alerts = self._fake_io(cog, [self._player()], [self._player()])
        cog.poliswag.get_user.return_value.send.side_effect = discord.HTTPException(
            MagicMock(status=503), "unavailable"
        )
        await _tick(cog)
        alerts._record.assert_not_called()
        alerts._sync_player.assert_not_called()
        assert alerts._cleanup.call_count == 2
        cog._check_trade_digest.assert_awaited_once()
        assert not any("CRASH" in line for line in self._logged(cog))


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


class TestLoopStart:
    async def test_each_loop_waits_for_ready_then_its_own_offset(self, cog, mocker):
        cog.poliswag.wait_until_ready = AsyncMock()
        sleep = mocker.patch("cogs.scheduled.asyncio.sleep", new=AsyncMock())
        await cog.before_health_loop()
        await cog.before_alerts_loop()
        await cog.before_display_loop()
        await cog.before_chores_loop()
        assert cog.poliswag.wait_until_ready.await_count == 4
        assert [call.args[0] for call in sleep.await_args_list] == [
            _LOOP_OFFSETS[name] for name in ("health", "alerts", "display", "chores")
        ]

    def test_offsets_spread_the_loops_over_the_minute(self):
        assert sorted(_LOOP_OFFSETS.values()) == [0, 15, 30, 45]


class TestStepGroups:
    def test_health_is_only_the_map_and_the_account_guard(self, cog):
        # Nothing slow or third-party may sit in front of the self-healing.
        assert [step.name for step in cog._runner.loops["health"]] == [
            "scan_areas",
            "workers",
            "account_guard",
        ]

    def test_the_account_card_has_a_loop_to_itself(self, cog):
        assert [step.name for step in cog._runner.loops["display"]] == ["account_card"]

    def test_every_step_is_in_exactly_one_loop(self, cog):
        names = [step.name for steps in cog._runner.loops.values() for step in steps]
        assert len(names) == len(set(names)) == 22

    def test_alerts_keep_the_order_the_shiny_steps_rely_on(self, cog):
        names = [step.name for step in cog._runner.loops["alerts"]]
        assert names.index("quest_scan") < names.index("quest_export")
        assert names[-3:] == ["shiny_odds", "shiny_availability", "shiny_alerts"]

    def test_events_are_fetched_right_before_they_are_checked(self, cog):
        names = [step.name for step in cog._runner.loops["chores"]]
        assert names.index("events") == names.index("events_fetch") + 1

    def test_third_party_checks_run_every_five_minutes(self, cog):
        every = {step.name: step.every for step in cog._runner.loops["chores"]}
        assert every["game_version"] == every["polisnatch_update"] == 300
        assert every["masterfile"] == 300
        assert every["tracking_health"] == 900
        # Paces itself: a 15 min cache, and a retry each pass while it fails.
        assert every["events_fetch"] == 0
        # Due once a day, and retried each minute until Discord takes them.
        assert every["weekly_digest"] == every["error_digest"] == 0
        assert every["trade_digest"] == 0

    def test_only_the_workers_step_may_take_five_minutes(self, cog):
        budgets = {
            step.name: step.budget
            for steps in cog._runner.loops.values()
            for step in steps
        }
        assert budgets["workers"] == 300
        assert max(v for k, v in budgets.items() if k != "workers") == 180

    async def test_a_step_replaced_on_the_cog_is_the_one_that_runs(self, cog):
        cog._check_workers = AsyncMock()
        await cog._pass("health")
        cog._check_workers.assert_awaited_once()

    async def test_the_version_check_no_longer_runs_the_polisnatch_poll(self, cog):
        cog._check_polisnatch_update = AsyncMock()
        await cog._check_version_update()
        cog._check_polisnatch_update.assert_not_awaited()


class TestTicksCommand:
    def test_gated_to_mods(self):
        assert is_mod in Scheduled.tickscmd.checks

    async def test_lists_every_loop_and_step(self, cog):
        ctx = make_ctx()
        ctx.send = AsyncMock()
        await cog._pass("health")
        await Scheduled.tickscmd.callback(cog, ctx)
        embed = ctx.send.await_args.kwargs["embed"]
        assert [field.name for field in embed.fields] == [
            "Saúde do mapa",
            "Alertas",
            "Painel de contas",
            "Tarefas",
        ]
        assert "`workers`" in embed.fields[0].value
        assert "ainda não correu" in embed.fields[1].value
        assert all(len(field.value) <= 1024 for field in embed.fields)

    def test_a_finished_step_shows_duration_and_age(self):
        step = Step("workers", AsyncMock(), last_started=1000.0, last_duration=1.24)
        assert _step_line(step, 1042.0) == "🟢 `workers` 1,2 s · há 42 s"

    def test_a_failed_step_is_orange_and_counts_its_failures(self):
        step = Step(
            "events",
            AsyncMock(),
            last_started=1000.0,
            last_duration=0.5,
            last_error="RuntimeError: boom",
            failures=3,
        )
        assert _step_line(step, 1010.0) == "🟠 `events` 0,5 s · há 10 s (3 falhas)"

    def test_an_overdue_step_is_red_with_its_interval_and_overruns(self):
        step = Step(
            "masterfile",
            AsyncMock(),
            every=300,
            last_started=1000.0,
            last_duration=200.0,
            overdue=True,
            overruns=1,
            longest=200.0,
        )
        assert _step_line(step, 1100.0) == (
            "🔴 `masterfile` 3 min · há 100 s "
            "(de 5 em 5 min, 1× além do tempo, máx. 3 min)"
        )

    def test_seconds_reads_naturally(self):
        assert [_seconds(v) for v in (0.42, 9.96, 75, 300, 7200)] == [
            "0,4 s",
            "10,0 s",
            "75 s",
            "5 min",
            "2 h",
        ]


# --- _check_version_update ----------------------------------------------------


class TestCheckVersionUpdate:
    async def test_no_new_version_sends_nothing(self, cog):
        cog.poliswag.utility.get_new_pokemongo_version = AsyncMock(return_value=None)
        await cog._check_version_update()
        cog.poliswag.CONVIVIO_CHANNEL.send.assert_not_called()

    async def test_new_version_sends_announcement(self, cog, mocker):
        owner = mocker.patch(
            "cogs.scheduled.notify_owner", new=AsyncMock(return_value=True)
        )
        cog.poliswag.device_manager.working_game_version = MagicMock(
            return_value="0.433.1"
        )
        cog.poliswag.utility.get_new_pokemongo_version = AsyncMock(return_value="0.0.1")
        await cog._check_version_update()
        cog.poliswag.CONVIVIO_CHANNEL.send.assert_awaited_once()
        # The owner gets the lead time before the Play Store updates the phone.
        text = owner.await_args.args[1]
        assert "0.0.1" in text and "0.433.1" in text and "PoliSnatch" in text


class TestCheckPolisnatchUpdate:
    @pytest.fixture(autouse=True)
    def submit_key(self, mocker):
        return mocker.patch(
            "cogs.scheduled.polisnatch_api.read_key", return_value="test-key"
        )

    async def test_no_key_sends_no_request(self, cog, mocker, submit_key):
        submit_key.return_value = ""
        owner = mocker.patch(
            "cogs.scheduled.notify_owner", new=AsyncMock(return_value=True)
        )
        session = mocker.patch("cogs.scheduled.get_session")

        await cog._check_polisnatch_update()
        session.return_value.get.assert_not_called()
        owner.assert_not_called()

    async def test_up_to_date_sends_nothing(self, cog, mocker):
        owner = mocker.patch(
            "cogs.scheduled.notify_owner", new=AsyncMock(return_value=True)
        )
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "supported_version": "0.435.2",
                "lib_changed": False,
                "status": "up_to_date",
            }
        )
        mocker.patch(
            "cogs.scheduled.get_session"
        ).return_value.get.return_value.__aenter__.return_value = mock_resp

        await cog._check_polisnatch_update()
        owner.assert_not_called()

    async def test_lib_changed_sends_owner_alert(self, cog, mocker, tmp_path):
        mocker.patch(
            "modules.config.Config.POLISNATCH_UPDATE_STATE_FILE",
            str(tmp_path / "state.json"),
        )
        owner = mocker.patch(
            "cogs.scheduled.notify_owner", new=AsyncMock(return_value=True)
        )
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "supported_version": "0.437.0",
                "lib_changed": True,
                "lib_sha256": "abcdef1234567890abcdef1234567890abcdef12",
                "status": "staged_on_lab",
                "lab_verified": True,
                "message": "Verified on .223",
            }
        )
        session = mocker.patch("cogs.scheduled.get_session")
        session.return_value.get.return_value.__aenter__.return_value = mock_resp

        await cog._check_polisnatch_update()
        owner.assert_called_once()
        _, sent = session.return_value.get.call_args
        assert sent["headers"] == {"Authorization": "Bearer test-key"}
        args, kwargs = owner.call_args
        assert "0.437.0" in args[1]
        assert "tag" in kwargs and kwargs["tag"] == "polisnatch-update"

        # Subsequent call does not repeat alert
        owner.reset_mock()
        await cog._check_polisnatch_update()
        owner.assert_not_called()

    async def test_same_library_new_version_still_alerts(self, cog, mocker, tmp_path):
        mocker.patch(
            "modules.config.Config.POLISNATCH_UPDATE_STATE_FILE",
            str(tmp_path / "state.json"),
        )
        owner = mocker.patch(
            "cogs.scheduled.notify_owner", new=AsyncMock(return_value=True)
        )
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "supported_version": "0.435.2",
                "mirror_version": "0.435.3",
                "lib_changed": False,
                "action_needed": True,
                "same_lib_as": "0.435.2",
                "status": "profile_needed_same_lib",
                "lab_verified": False,
            }
        )
        mocker.patch(
            "cogs.scheduled.get_session"
        ).return_value.get.return_value.__aenter__.return_value = mock_resp

        await cog._check_polisnatch_update()
        owner.assert_called_once()
        args, kwargs = owner.call_args
        assert "0.435.3" in args[1] and "0.435.3" in kwargs["title"]
        assert "Igual à da `0.435.2`" in args[1]
        assert "Alterada" not in args[1]

    async def test_failed_check_sends_nothing(self, cog, mocker):
        owner = mocker.patch(
            "cogs.scheduled.notify_owner", new=AsyncMock(return_value=True)
        )
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "supported_version": "0.435.2",
                "mirror_version": None,
                "lib_changed": False,
                "action_needed": False,
                "status": "check_failed",
            }
        )
        mocker.patch(
            "cogs.scheduled.get_session"
        ).return_value.get.return_value.__aenter__.return_value = mock_resp

        await cog._check_polisnatch_update()
        owner.assert_not_called()

    async def test_network_error_handled_gracefully(self, cog, mocker):
        owner = mocker.patch(
            "cogs.scheduled.notify_owner", new=AsyncMock(return_value=True)
        )
        mocker.patch("cogs.scheduled.get_session").return_value.get.side_effect = (
            OSError("connection refused")
        )

        await cog._check_polisnatch_update()
        owner.assert_not_called()


# --- _check_quest_scan_progress ----------------------------------------------


def quest_progress(leiria, marinha, completed=False):
    """The scanner_status payload shape: per-area stats plus an overall flag."""
    return {
        "areas": {
            "leiria": {
                "completed": completed,
                "scanned": leiria[0],
                "total": leiria[1],
                "percentage": leiria[2],
            },
            "marinha": {
                "completed": completed,
                "scanned": marinha[0],
                "total": marinha[1],
                "percentage": marinha[2],
            },
        },
        "completed": completed,
    }


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
            return_value=quest_progress((10, 10, 100.0), (8, 8, 100.0), completed=True)
        )
        await cog._check_quest_scan_progress()
        cog.poliswag.quest_search.check_tracked.assert_awaited_once()
        cog.poliswag.quest_exporter.export.assert_awaited_once()
        cog.poliswag.scanner_manager.update_quest_scanning_state.assert_called_once()

    async def test_done_embed_has_one_line_per_active_registry_area(self, cog):
        from modules import scan_areas

        scan_areas.install(
            [
                {
                    "area_key": key,
                    "label": label,
                    "dragonite_area": dragonite,
                    "poracle_area": poracle,
                    "alert_role": "",
                    "sort_order": order,
                    "active": 1,
                }
                for order, (key, label, dragonite, poracle) in enumerate(
                    [
                        ("leiria", "Leiria", "Leiria", "leiria"),
                        ("marinha", "Marinha Grande", "MarinhaGrande", "marinhagrande"),
                        ("moita", "Moita", "Moita", "moita"),
                    ],
                    start=1,
                )
            ],
            {},
        )
        cog.poliswag.scanner_manager.is_day_change.return_value = False
        cog.poliswag.quest_scanning_message = MagicMock()
        cog.poliswag.quest_scanning_message.edit = AsyncMock()
        cog.poliswag.scanner_status.is_quest_scanning_complete = AsyncMock(
            return_value={
                "areas": {
                    "leiria": {
                        "completed": True,
                        "scanned": 10,
                        "total": 10,
                        "percentage": 100.0,
                    },
                    "marinha": {
                        "completed": True,
                        "scanned": 8,
                        "total": 8,
                        "percentage": 100.0,
                    },
                    "moita": {
                        "completed": True,
                        "scanned": 3,
                        "total": 3,
                        "percentage": 100.0,
                    },
                },
                "completed": True,
            }
        )
        await cog._check_quest_scan_progress()
        embed = cog.poliswag.quest_scanning_message.edit.call_args.kwargs["embed"]
        assert "**Leiria:** 10/10 Quests" in embed.description
        assert "**Marinha Grande:** 8/8 Quests" in embed.description
        assert "**Moita:** 3/3 Quests" in embed.description
        assert "`!questleiria <QUEST/ITEM>`" in embed.description
        assert "`!questmarinha <QUEST/ITEM>`" in embed.description
        assert "`!quest moita <QUEST/ITEM>`" in embed.description
        cog.poliswag.scanner_status.record_quest_scan_completion.assert_awaited_once_with(
            {"leiria": 10, "marinha": 8, "moita": 3}
        )

    async def test_in_progress_builds_progress_embed(self, cog):
        cog.poliswag.scanner_manager.is_day_change.return_value = False
        cog.poliswag.quest_scanning_message = MagicMock()
        cog.poliswag.quest_scanning_message.edit = AsyncMock()
        cog.poliswag.scanner_status.is_quest_scanning_complete = AsyncMock(
            return_value=quest_progress((5, 10, 50.0), (2, 8, 25.0))
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
            return_value=quest_progress((1, 10, 10.0), (1, 10, 10.0))
        )
        await cog._check_quest_scan_progress()
        cog.poliswag.QUEST_CHANNEL.send.assert_awaited_once()

    async def test_unchanged_state_with_existing_message_skips_edit(self, cog):
        # Same per-area scanned pair on consecutive ticks, with a tracking
        # message already posted, must not re-render the embed.
        cog.poliswag.scanner_manager.is_day_change.return_value = False
        cog.poliswag.quest_scanning_message = MagicMock()
        cog.poliswag.quest_scanning_message.edit = AsyncMock()
        cog.poliswag.scanner_status.is_quest_scanning_complete = AsyncMock(
            return_value=quest_progress((5, 10, 50.0), (2, 8, 25.0))
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
            return_value=quest_progress((5, 10, 50.0), (2, 8, 25.0))
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
        "percentage,expected_emoji",
        [
            (0.0, "🔍"),
            (30.0, "⏳"),
            (60.0, "⌛"),
            (90.0, "🔜"),
        ],
    )
    def test_emoji_selection(self, cog, percentage, expected_emoji):
        embed = cog._build_progress_embed(
            quest_progress((1, 10, percentage), (1, 10, percentage))
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
        cog.poliswag.event_notices.send_change_notifications = AsyncMock()
        await cog._check_events()
        cog.poliswag.event_notices.send_change_notifications.assert_not_called()

    async def test_with_changes_calls_notifications(self, cog):
        cog.poliswag.event_manager.check_current_events_changes = AsyncMock(
            return_value={"started": [], "ended": []}
        )
        cog.poliswag.event_notices.send_change_notifications = AsyncMock()
        await cog._check_events()
        cog.poliswag.event_notices.send_change_notifications.assert_awaited_once()


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
        cog.poliswag.device_manager.ensure_wifi = AsyncMock()
        await cog._check_workers()
        cog.poliswag.scanner_status.rename_voice_channels.assert_awaited_once_with(
            workers_status
        )
        cog.poliswag.device_manager.ensure_wifi.assert_awaited_once()


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


# --- _check_weekly_digest -----------------------------------------------------


class TestCheckWeeklyDigest:
    async def test_non_monday_skips(self, cog):
        tue = real_datetime.datetime(2026, 4, 7, 10, 0)  # Tuesday
        cog.poliswag.event_notices.send_weekly_digest = AsyncMock()
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = tue
            await cog._check_weekly_digest()
        cog.poliswag.event_notices.send_weekly_digest.assert_not_called()

    async def test_monday_before_9am_skips(self, cog):
        early_mon = real_datetime.datetime(2026, 4, 6, 8, 59)
        cog._last_weekly_digest_monday = None
        cog.poliswag.event_notices.send_weekly_digest = AsyncMock()
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = early_mon
            await cog._check_weekly_digest()
        cog.poliswag.event_notices.send_weekly_digest.assert_not_called()

    async def test_monday_same_day_skips(self, cog):
        mon = real_datetime.datetime(2026, 4, 6, 10, 0)
        cog._last_weekly_digest_monday = mon.date()
        cog.poliswag.event_notices.send_weekly_digest = AsyncMock()
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = mon
            await cog._check_weekly_digest()
        cog.poliswag.event_notices.send_weekly_digest.assert_not_called()

    async def test_new_monday_after_9am_sends_and_persists(self, cog):
        mon = real_datetime.datetime(2026, 4, 6, 10, 0)
        cog._last_weekly_digest_monday = None
        cog.poliswag.event_notices.send_weekly_digest = AsyncMock()
        cog._save_digest_date = AsyncMock()
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = mon
            await cog._check_weekly_digest()
        cog._save_digest_date.assert_called_once_with(mon.date())
        cog.poliswag.event_notices.send_weekly_digest.assert_awaited_once()
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
    """The digest goes to the owner by DM (2026-09-26), not to moderação."""

    @pytest.fixture(autouse=True)
    def _owner(self, cog, monkeypatch):
        from modules.config import Config

        monkeypatch.setattr(Config, "MY_ID", 42)
        self.owner = MagicMock(send=AsyncMock())
        cog.poliswag.get_user = MagicMock(return_value=self.owner)

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
        self.owner.send.assert_not_called()
        assert cog._last_error_digest_at == now
        cog.poliswag.db.execute_query_to_database.assert_called_once()

    async def test_entries_are_summarized_and_sent_to_the_owner(self, cog):
        now = real_datetime.datetime(2026, 4, 7, 9, 30, 0)
        cog.poliswag.utility.read_new_error_entries = MagicMock(
            return_value=["line1", "line2"]
        )
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()
        cog.poliswag.MOD_CHANNEL.send.assert_not_called()
        cog.poliswag.get_user.assert_called_with(42)
        self.owner.send.assert_awaited_once()
        title = self.owner.send.call_args.kwargs["embed"].title
        assert "2 erro" in title

    async def _digest(self, cog, entries, now=None):
        """Run the digest on `entries`; the sent embed."""
        now = now or real_datetime.datetime(2026, 4, 7, 9, 30, 0)
        cog.poliswag.utility.read_new_error_entries = MagicMock(return_value=entries)
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.datetime.strptime = real_datetime.datetime.strptime
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()
        return self.owner.send.call_args.kwargs["embed"]

    async def test_more_than_ten_kinds_notes_the_kinds_left_out(self, cog):
        letters = "abcdefghijklmno"
        embed = await self._digest(
            cog, [f"2026-04-07 08:00:00,000 - ERROR - fail {c}" for c in letters]
        )
        bullets = [line for line in embed.description.split("\n") if line[0] == "•"]
        assert len(bullets) == 10
        assert embed.description.endswith("… e mais 5 tipos de erro.")
        assert "15 erros novos" in embed.title

    async def test_repeated_entries_are_one_bullet_with_count_and_times(self, cog):
        embed = await self._digest(
            cog,
            [
                "2026-04-07 03:26:12,144 - ERROR - Poracle down",
                "2026-04-07 05:00:00,000 - ERROR - Poracle down",
                "2026-04-07 09:00:39,032 - ERROR - Poracle down",
            ],
        )
        assert embed.description == "• 3× Poracle down · 03:26 – 09:00"
        assert "3 erros novos" in embed.title

    async def test_one_entry_shows_its_time_and_singular_title(self, cog):
        embed = await self._digest(
            cog, ["2026-04-07 09:00:39,032 - ERROR - Poracle down"]
        )
        assert embed.description == "• Poracle down · 09:00"
        assert embed.title == "⚠️ 1 erro novo desde o último resumo"

    async def test_times_on_another_day_include_the_day(self, cog):
        embed = await self._digest(
            cog,
            [
                "2026-04-06 18:45:12,729 - ERROR - Slow step took 128s",
                "2026-04-07 03:26:12,144 - ERROR - Slow step took 61s",
            ],
        )
        assert embed.description == (
            "• 2× Slow step took 128s · 06/04 18:45 – 07/04 03:26"
        )

    async def test_markdown_in_a_message_is_escaped(self, cog):
        embed = await self._digest(
            cog, ["2026-04-07 09:00:00,000 - ERROR - Scheduled._update_x failed"]
        )
        assert "Scheduled.\\_update\\_x failed" in embed.description

    async def test_a_long_message_is_trimmed(self, cog):
        embed = await self._digest(
            cog,
            [
                "2026-04-07 09:00:00,000 - ERROR - " + "x" * 1000,
                "2026-04-07 09:01:00,000 - ERROR - short one",
            ],
        )
        first, second = embed.description.split("\n")
        assert len(first) < 320
        assert "…" in first
        assert second == "• short one · 09:01"

    async def test_no_owner_configured_is_a_noop(self, cog, monkeypatch):
        from modules.config import Config

        monkeypatch.setattr(Config, "MY_ID", 0)
        now = real_datetime.datetime(2026, 4, 7, 9, 30, 0)
        cog.poliswag.utility.read_new_error_entries = MagicMock(return_value=["line1"])
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()  # must not raise
        self.owner.send.assert_not_called()

    async def test_a_failed_dm_is_logged_not_raised(self, cog):
        now = real_datetime.datetime(2026, 4, 7, 9, 30, 0)
        self.owner.send = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=403), "closed")
        )
        cog.poliswag.utility.read_new_error_entries = MagicMock(return_value=["line1"])
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()
        cog.poliswag.utility.log_to_file.assert_called()

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


class TestGroupErrorEntries:
    def test_repeats_collapse_with_count_and_first_and_last_time(self):
        groups = _group_error_entries(
            [
                "2026-11-09 03:00:00,001 - ERROR - Poracle down",
                "2026-11-09 04:00:00,002 - ERROR - Poracle down",
                "2026-11-09 05:00:00,003 - ERROR - Poracle down",
            ]
        )
        assert groups == [
            (
                "Poracle down",
                3,
                real_datetime.datetime(2026, 11, 9, 3, 0, 0),
                real_datetime.datetime(2026, 11, 9, 5, 0, 0),
            )
        ]

    def test_messages_differing_only_in_numbers_group_together(self):
        groups = _group_error_entries(
            [
                "2026-11-08 18:45:12,729 - ERROR - Step took 128s (tick 60s)",
                "2026-11-09 03:26:12,144 - ERROR - Step took 61s (tick 60s)",
            ]
        )
        assert len(groups) == 1
        text, count, first, last = groups[0]
        assert text == "Step took 128s (tick 60s)"
        assert count == 2
        assert first.date() == real_datetime.date(2026, 11, 8)
        assert last.date() == real_datetime.date(2026, 11, 9)

    def test_order_of_first_occurrence_is_kept(self):
        groups = _group_error_entries(
            [
                "2026-11-09 01:00:00,000 - ERROR - b",
                "2026-11-09 02:00:00,000 - ERROR - a",
                "2026-11-09 03:00:00,000 - ERROR - b",
                "2026-11-09 04:00:00,000 - CRASH - c",
            ]
        )
        assert [(g[0], g[1]) for g in groups] == [("b", 2), ("a", 1), ("c", 1)]

    def test_a_line_not_in_log_format_is_its_own_group_without_times(self):
        groups = _group_error_entries(
            ["not a log line", "2026-11-09 01:00:00,000 - ERROR - not a log line"]
        )
        assert groups[0] == ("not a log line", 1, None, None)
        assert groups[1][:2] == ("not a log line", 1)
        assert groups[1][2] is not None

    def test_no_entries_is_no_groups(self):
        assert _group_error_entries([]) == []


# --- lifecycle ---------------------------------------------------------------


class TestLifecycle:
    async def test_cog_load_starts_every_loop_and_prints(self, cog, capsys, mocker):
        # Prevent the tasks from actually running in the background.
        loops = [MagicMock() for _ in range(4)]
        mocker.patch.object(cog, "_task_loops", return_value=loops)
        await cog.cog_load()
        for loop in loops:
            loop.start.assert_called_once()
        assert "Scheduled loaded" in capsys.readouterr().out

    async def test_cog_unload_cancels_loops_and_background_runs(
        self, cog, capsys, mocker
    ):
        loops = [MagicMock() for _ in range(4)]
        mocker.patch.object(cog, "_task_loops", return_value=loops)
        cancel_running = mocker.patch.object(cog._runner, "cancel_running")
        await cog.cog_unload()
        for loop in loops:
            loop.cancel.assert_called_once()
        cancel_running.assert_called_once()
        assert "Scheduled unloaded" in capsys.readouterr().out

    def test_the_four_loops_are_the_ones_started(self, cog):
        assert cog._task_loops() == (
            cog.health_loop,
            cog.alerts_loop,
            cog.display_loop,
            cog.chores_loop,
        )


class TestSetup:
    async def test_registers_cog_on_poliswag(self):
        poliswag = MagicMock()
        poliswag.add_cog = AsyncMock()
        await setup(poliswag)
        poliswag.add_cog.assert_awaited_once()
        assert isinstance(poliswag.add_cog.call_args.args[0], Scheduled)


class TestRefreshMasterfileData:
    """Names must reach poliswag.pokemon_name on the first tick after startup
    (QuestSearch loads the masterfile in __init__, so that tick never sees a
    reload), again whenever the masterfile reloads, and not on every tick."""

    @pytest.fixture(autouse=True)
    def reviewed_costume_sync(self):
        with patch(
            "cogs.scheduled.sync_pokemon_costumes", new=AsyncMock(return_value=1)
        ):
            yield

    MASTERFILE = {"pokemon": {"25": {"name": "Pikachu"}}}

    def _prime(self, cog, reloaded):
        cog.poliswag.quest_search.load_translation_data = MagicMock()
        cog.poliswag.quest_search.load_masterfile_data = MagicMock(
            return_value=reloaded
        )
        cog.poliswag.quest_search.generate_pokemon_item_name_map = MagicMock()
        cog.poliswag.quest_search.masterfile_data = self.MASTERFILE
        cog.poliswag.mega_exporter.export = MagicMock()

    async def test_syncs_on_first_tick_without_a_reload(self, cog):
        self._prime(cog, reloaded=False)
        with patch(
            "cogs.scheduled.sync_pokemon_names", new=AsyncMock(return_value=1)
        ) as sync:
            await cog._refresh_masterfile_data()
        sync.assert_awaited_once_with(cog.poliswag.db, self.MASTERFILE)
        assert cog._pokemon_names_synced is True

    async def test_exports_megas_on_first_tick_without_a_reload(self, cog):
        self._prime(cog, reloaded=False)
        with patch("cogs.scheduled.sync_pokemon_names", new=AsyncMock(return_value=1)):
            await cog._refresh_masterfile_data()
            cog.poliswag.mega_exporter.export.assert_called_once()
            await cog._refresh_masterfile_data()
        cog.poliswag.mega_exporter.export.assert_called_once()

    async def test_does_not_resync_on_a_quiet_tick(self, cog):
        self._prime(cog, reloaded=False)
        cog._pokemon_names_synced = True
        with patch("cogs.scheduled.sync_pokemon_names", new=AsyncMock()) as sync:
            await cog._refresh_masterfile_data()
        sync.assert_not_awaited()

    async def test_resyncs_after_a_reload(self, cog):
        self._prime(cog, reloaded=True)
        cog._pokemon_names_synced = True
        with patch(
            "cogs.scheduled.sync_pokemon_names", new=AsyncMock(return_value=1)
        ) as sync:
            await cog._refresh_masterfile_data()
        sync.assert_awaited_once()
        cog.poliswag.mega_exporter.export.assert_called_once()

    async def test_failed_sync_is_retried_next_tick(self, cog):
        self._prime(cog, reloaded=False)
        with patch(
            "cogs.scheduled.sync_pokemon_names",
            new=AsyncMock(side_effect=RuntimeError("no table")),
        ):
            with pytest.raises(RuntimeError):
                await cog._refresh_masterfile_data()
        assert cog._pokemon_names_synced is False

    async def test_no_masterfile_yet_means_no_sync(self, cog):
        self._prime(cog, reloaded=False)
        cog.poliswag.quest_search.masterfile_data = None
        with patch("cogs.scheduled.sync_pokemon_names", new=AsyncMock()) as sync:
            await cog._refresh_masterfile_data()
        sync.assert_not_awaited()
        assert cog._pokemon_names_synced is False


class TestTrackingActivityLogs:
    @pytest.mark.asyncio
    async def test_silence_and_resumption_log_once_without_any_notification(self):
        poliswag = _make_poliswag()
        cog = Scheduled(poliswag)
        old = real_datetime.datetime.now(real_datetime.timezone.utc).replace(
            tzinfo=None
        ) - real_datetime.timedelta(days=5)
        cog.poliswag.tracking_health.last_event_at = AsyncMock(return_value=old)
        with patch(
            "modules.owner_alert.notify_owner", new_callable=AsyncMock
        ) as notify:
            await cog._check_tracking_health()
            assert cog._last_tracking_alert_at is not None
            await cog._check_tracking_health()
            cog.poliswag.tracking_health.last_event_at.return_value = (
                real_datetime.datetime.now(real_datetime.timezone.utc).replace(
                    tzinfo=None
                )
            )
            await cog._check_tracking_health()
            await cog._check_tracking_health()
            notify.assert_not_awaited()
        assert cog._last_tracking_alert_at is None
        logs = poliswag.utility.log_to_file.call_args_list
        assert len(logs) == 2
        assert all(call.args[1] == "INFO" for call in logs)
        assert "Sem visualizações" in logs[0].args[0]
        assert "Atividade no site retomada" in logs[1].args[0]
        assert poliswag.db.execute_query_to_database.await_count == 2
        poliswag.get_user.assert_not_called()
        poliswag.fetch_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_persisted_silence_does_not_repeat_or_send_recovery_dm(self):
        poliswag = _make_poliswag()
        cog = Scheduled(poliswag)
        cog._last_tracking_alert_at = real_datetime.datetime.now(
            real_datetime.timezone.utc
        ).replace(tzinfo=None) - real_datetime.timedelta(days=5)
        cog.poliswag.tracking_health.last_event_at = AsyncMock(return_value=None)
        with patch(
            "modules.owner_alert.notify_owner", new_callable=AsyncMock
        ) as notify:
            await cog._check_tracking_health()
            poliswag.utility.log_to_file.assert_not_called()
            poliswag.db.execute_query_to_database.assert_not_awaited()
            cog.poliswag.tracking_health.last_event_at.return_value = (
                real_datetime.datetime.now(real_datetime.timezone.utc).replace(
                    tzinfo=None
                )
            )
            await cog._check_tracking_health()
            notify.assert_not_awaited()
        assert cog._last_tracking_alert_at is None
        poliswag.utility.log_to_file.assert_called_once()


# --- digest delivery retryability + Discord limits (items 2) ------------------


def _monday_at_10():
    return real_datetime.datetime(2026, 4, 6, 10, 0)


def _daily_at_930():
    return real_datetime.datetime(2026, 4, 7, 9, 30, 0)


class TestWeeklyDigestRetryability:
    async def test_first_send_failure_then_retry(self, cog):
        cog._last_weekly_digest_monday = None
        cog._save_digest_date = AsyncMock()
        cog.poliswag.event_notices.send_weekly_digest = AsyncMock(
            side_effect=[
                discord.HTTPException(MagicMock(status=500), "boom"),
                True,
            ]
        )
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = _monday_at_10()
            await cog._check_weekly_digest()
            assert cog._last_weekly_digest_monday is None
            cog._save_digest_date.assert_not_called()
            await cog._check_weekly_digest()
        assert cog._last_weekly_digest_monday == _monday_at_10().date()
        cog._save_digest_date.assert_called_once_with(_monday_at_10().date())
        assert cog.poliswag.event_notices.send_weekly_digest.await_count == 2

    async def test_successful_dedup_does_not_resend(self, cog):
        mon = _monday_at_10()
        cog._last_weekly_digest_monday = mon.date()
        cog.poliswag.event_notices.send_weekly_digest = AsyncMock()
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = mon
            await cog._check_weekly_digest()
        cog.poliswag.event_notices.send_weekly_digest.assert_not_called()

    async def test_quiet_day_still_marks_without_retry_spam(self, cog):
        cog._last_weekly_digest_monday = None
        cog._save_digest_date = AsyncMock()
        cog.poliswag.event_notices.send_weekly_digest = AsyncMock(return_value=False)
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = _monday_at_10()
            await cog._check_weekly_digest()
            assert cog._last_weekly_digest_monday == _monday_at_10().date()
            cog._save_digest_date.assert_called_once()
            await cog._check_weekly_digest()
        assert cog.poliswag.event_notices.send_weekly_digest.await_count == 1

    async def test_missing_channel_still_marks_without_retry_spam(
        self, cog, monkeypatch
    ):
        from modules.config import Config

        monkeypatch.setattr(Config, "CONVIVIO_CHANNEL_ID", 0)
        cog.poliswag.CONVIVIO_CHANNEL = None
        cog.poliswag.event_manager.get_weekly_events = AsyncMock(
            return_value=[
                {
                    "event_type": "Community Day",
                    "name": "X",
                    "start": "2026-04-06 08:00:00",
                    "end": "2026-04-06 20:00:00",
                }
            ]
        )
        assert (await cog.poliswag.event_notices.send_weekly_digest()) is False
        cog.poliswag.utility.log_to_file.assert_called()
        cog._last_weekly_digest_monday = None
        cog._save_digest_date = AsyncMock()
        cog.poliswag.event_notices.send_weekly_digest = AsyncMock(return_value=False)
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = _monday_at_10()
            await cog._check_weekly_digest()
        assert cog._last_weekly_digest_monday == _monday_at_10().date()

    async def test_configured_but_unresolved_retries_until_delivery(
        self, cog, monkeypatch
    ):
        from modules.config import Config

        monkeypatch.setattr(Config, "CONVIVIO_CHANNEL_ID", 5)
        cog.poliswag.CONVIVIO_CHANNEL = None
        recovered = MagicMock()
        recovered.send = AsyncMock()
        cog.poliswag.fetch_channel = AsyncMock(
            side_effect=[
                discord.HTTPException(MagicMock(status=503), "lookup boom"),
                recovered,
            ]
        )
        cog.poliswag.event_manager.get_weekly_events = AsyncMock(
            return_value=[
                {
                    "event_type": "Community Day",
                    "name": "Retryable",
                    "start": "2026-04-06 08:00:00",
                    "end": "2026-04-06 20:00:00",
                }
            ]
        )
        cog._last_weekly_digest_monday = None
        cog._save_digest_date = AsyncMock()
        with (
            patch("cogs.scheduled.datetime") as mock_dt,
            patch("modules.event_notices.datetime", mock_dt),
        ):
            mock_dt.datetime.now.return_value = _monday_at_10()
            mock_dt.datetime.strptime = real_datetime.datetime.strptime
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_weekly_digest()
            assert cog._last_weekly_digest_monday is None
            cog._save_digest_date.assert_not_called()
            assert cog.poliswag.CONVIVIO_CHANNEL is None
            await cog._check_weekly_digest()
        assert cog._last_weekly_digest_monday == _monday_at_10().date()
        cog._save_digest_date.assert_called_once_with(_monday_at_10().date())
        assert cog.poliswag.CONVIVIO_CHANNEL is recovered
        recovered.send.assert_awaited_once()
        assert recovered.send.call_args.kwargs["allowed_mentions"].everyone is False


class TestDailyErrorDigestRetryability:
    @pytest.fixture(autouse=True)
    def _owner(self, cog, monkeypatch):
        from modules.config import Config

        monkeypatch.setattr(Config, "MY_ID", 42)
        self.owner = MagicMock(send=AsyncMock())
        cog.poliswag.get_user = MagicMock(return_value=self.owner)
        cog.poliswag.fetch_user = AsyncMock(return_value=self.owner)

    async def test_failed_fetch_then_retry(self, cog):
        now = _daily_at_930()
        cog._last_error_digest_at = None
        cog._save_error_digest_at = AsyncMock()
        cog.poliswag.utility.read_new_error_entries = MagicMock(return_value=["line1"])
        cog.poliswag.get_user = MagicMock(return_value=None)
        cog.poliswag.fetch_user = AsyncMock(
            side_effect=[
                discord.HTTPException(MagicMock(status=503), "fetch boom"),
                self.owner,
            ]
        )
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()
            assert cog._last_error_digest_at is None
            cog._save_error_digest_at.assert_not_called()
            await cog._check_daily_error_digest()
        assert cog._last_error_digest_at == now
        cog._save_error_digest_at.assert_called_once_with(now)
        self.owner.send.assert_awaited_once()

    async def test_failed_send_does_not_advance_marker(self, cog):
        now = _daily_at_930()
        cog._last_error_digest_at = None
        cog._save_error_digest_at = AsyncMock()
        cog.poliswag.utility.read_new_error_entries = MagicMock(return_value=["line1"])
        self.owner.send = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=500), "send boom")
        )
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()
        assert cog._last_error_digest_at is None
        cog._save_error_digest_at.assert_not_called()
        cog.poliswag.utility.log_to_file.assert_called()

    async def test_successful_dedup_does_not_resend(self, cog):
        now = _daily_at_930()
        cog._last_error_digest_at = now
        cog.poliswag.utility.read_new_error_entries = MagicMock(return_value=["line1"])
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()
        self.owner.send.assert_not_called()

    async def test_quiet_day_advances_marker_without_send(self, cog):
        now = _daily_at_930()
        cog._last_error_digest_at = None
        cog._save_error_digest_at = AsyncMock()
        cog.poliswag.utility.read_new_error_entries = MagicMock(return_value=[])
        with patch("cogs.scheduled.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.timedelta = real_datetime.timedelta
            await cog._check_daily_error_digest()
        self.owner.send.assert_not_called()
        assert cog._last_error_digest_at == now
        cog._save_error_digest_at.assert_called_once_with(now)


class TestLurePresenceRetryability:
    async def test_failed_presence_then_retry(self, cog):
        cog._last_lure_status_count = 2
        cog.poliswag.lure_watcher.count_active_lures = AsyncMock(return_value=3)
        cog.poliswag.change_presence = AsyncMock(
            side_effect=[
                discord.HTTPException(MagicMock(status=500), "presence boom"),
                None,
            ]
        )
        with pytest.raises(discord.HTTPException):
            await cog._update_lure_status()
        assert cog._last_lure_status_count == 2
        await cog._update_lure_status()
        assert cog._last_lure_status_count == 3
        assert cog.poliswag.change_presence.await_count == 2
