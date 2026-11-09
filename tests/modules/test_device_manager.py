import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.config import Config
from modules.device_manager import DeviceManager


@pytest.fixture
def device_manager():
    """A DeviceManager with a mocked poliswag dependency (so _log is a no-op)."""
    return DeviceManager(poliswag=MagicMock())


class TestAutoRebootEnabledCache:
    """Cache is invalidate-on-write: a DB miss caches, a set updates the cache."""

    async def test_second_read_does_not_hit_db_again(self, device_manager):
        device_manager.poliswag.db = AsyncMock()
        device_manager.poliswag.db.get_data_from_database.return_value = [
            {"auto_reboot_enabled": 1}
        ]

        assert await device_manager.get_auto_reboot_enabled() is True
        assert await device_manager.get_auto_reboot_enabled() is True

        device_manager.poliswag.db.get_data_from_database.assert_called_once()

    async def test_failed_read_is_not_cached(self, device_manager):
        device_manager.poliswag.db = AsyncMock()
        device_manager.poliswag.db.get_data_from_database.side_effect = Exception(
            "db down"
        )

        assert await device_manager.get_auto_reboot_enabled() is True  # fails open
        assert await device_manager.get_auto_reboot_enabled() is True

        assert device_manager.poliswag.db.get_data_from_database.call_count == 2

    async def test_set_updates_cache_without_a_read(self, device_manager):
        device_manager.poliswag.db = AsyncMock()

        await device_manager.set_auto_reboot_enabled(False)

        assert await device_manager.get_auto_reboot_enabled() is False
        device_manager.poliswag.db.get_data_from_database.assert_not_called()

    async def test_failed_write_does_not_update_cache(self, device_manager):
        device_manager.poliswag.db = AsyncMock()
        device_manager.poliswag.db.get_data_from_database.return_value = [
            {"auto_reboot_enabled": 1}
        ]
        device_manager.poliswag.db.execute_query_to_database.side_effect = Exception(
            "db down"
        )

        await device_manager.set_auto_reboot_enabled(False)

        # setter failed to persist, so the next read still goes to the DB
        assert await device_manager.get_auto_reboot_enabled() is True
        device_manager.poliswag.db.get_data_from_database.assert_called_once()


class TestRun:
    async def test_missing_device_raises(self, device_manager, mocker):
        mocker.patch.object(Config, "ADB_DEVICE", None)
        with pytest.raises(RuntimeError):
            await device_manager.run("shell", "echo", "hi")

    async def test_healthy_device_skips_recovery(self, device_manager, mocker):
        mocker.patch.object(Config, "ADB_DEVICE", "1.2.3.4:5555")
        device_manager._adb = AsyncMock(return_value=("ok", "", 0))
        device_manager._device_state = AsyncMock(return_value="device")
        device_manager._recover_session = AsyncMock()

        out = await device_manager.run("shell", "echo", "hi")

        device_manager._recover_session.assert_not_called()
        assert out == ("ok", "", 0)
        # the actual command is run against the targeted device
        device_manager._adb.assert_any_call(
            "-s", "1.2.3.4:5555", "shell", "echo", "hi", timeout=15
        )

    @pytest.mark.parametrize("bad_state", ["unauthorized", "offline", ""])
    async def test_stale_session_triggers_recovery(
        self, device_manager, mocker, bad_state
    ):
        mocker.patch.object(Config, "ADB_DEVICE", "1.2.3.4:5555")
        device_manager._adb = AsyncMock(return_value=("ok", "", 0))
        device_manager._device_state = AsyncMock(return_value=bad_state)
        device_manager._recover_session = AsyncMock()

        await device_manager.run("reboot")

        device_manager._recover_session.assert_awaited_once_with("1.2.3.4:5555")
        # the command still runs after the re-handshake
        device_manager._adb.assert_any_call("-s", "1.2.3.4:5555", "reboot", timeout=15)


class TestRecoverSession:
    async def test_issues_full_rehandshake(self, device_manager):
        device_manager._adb = AsyncMock(return_value=("", "", 0))

        await device_manager._recover_session("1.2.3.4:5555")

        calls = [c.args for c in device_manager._adb.await_args_list]
        assert ("disconnect", "1.2.3.4:5555") in calls
        assert ("kill-server",) in calls
        assert ("start-server",) in calls
        assert ("connect", "1.2.3.4:5555") in calls

    async def test_step_failure_is_swallowed(self, device_manager):
        # every step blows up — recovery must not propagate the error
        device_manager._adb = AsyncMock(side_effect=RuntimeError("boom"))
        await device_manager._recover_session("1.2.3.4:5555")


class TestDeviceState:
    async def test_returns_state_on_success(self, device_manager):
        device_manager._adb = AsyncMock(return_value=("device", "", 0))
        assert await device_manager._device_state("d") == "device"

    async def test_empty_on_nonzero_rc(self, device_manager):
        device_manager._adb = AsyncMock(
            return_value=("", "error: device unauthorized", 1)
        )
        assert await device_manager._device_state("d") == ""

    async def test_empty_on_timeout(self, device_manager):
        device_manager._adb = AsyncMock(side_effect=RuntimeError("timeout"))
        assert await device_manager._device_state("d") == ""


class TestRestartApp:
    async def test_force_stop_then_start(self, device_manager, mocker):
        device_manager.run = AsyncMock(return_value=("", "", 0))
        assert await device_manager.restart_app() is True
        calls = device_manager.run.await_args_list
        assert calls[0].args == (
            "shell",
            "am",
            "force-stop",
            DeviceManager.POGO_PACKAGE,
        )
        assert calls[1].args == (
            "shell",
            "am",
            "start",
            "-n",
            DeviceManager.POGO_ACTIVITY,
        )

    async def test_failed_force_stop_short_circuits(self, device_manager):
        device_manager.run = AsyncMock(return_value=("", "err", 1))
        assert await device_manager.restart_app() is False
        assert device_manager.run.await_count == 1

    async def test_runtime_error_returns_false(self, device_manager):
        device_manager.run = AsyncMock(side_effect=RuntimeError("timeout"))
        assert await device_manager.restart_app() is False


class TestRestartScannerApps:
    """Phone rung for PoliSnatch: game to the front, or the loader when the
    game is gone; never a force-stop (a Connect is the loader's job)."""

    @pytest.fixture(autouse=True)
    def _no_settle_delay(self, mocker):
        mocker.patch(
            "modules.device_manager.asyncio.sleep", new=AsyncMock(return_value=None)
        )

    async def test_running_game_is_brought_to_the_front(self, device_manager):
        device_manager.run = AsyncMock(side_effect=[("31441", "", 0), ("", "", 0)])
        assert await device_manager.restart_scanner_apps() is True
        last = device_manager.run.await_args_list[-1].args[-1]
        assert last == (
            f"monkey -p {DeviceManager.POGO_PACKAGE} "
            "-c android.intent.category.LAUNCHER 1"
        )

    async def test_no_game_starts_the_loader(self, device_manager):
        device_manager.run = AsyncMock(side_effect=[("", "", 1), ("", "", 0)])
        assert await device_manager.restart_scanner_apps() is True
        last = device_manager.run.await_args_list[-1].args[-1]
        assert DeviceManager.POLISNATCH_PACKAGE in last

    async def test_never_force_stops(self, device_manager):
        device_manager.run = AsyncMock(side_effect=[("31441", "", 0), ("", "", 0)])
        await device_manager.restart_scanner_apps()
        commands = [c.args[-1] for c in device_manager.run.await_args_list]
        assert not any("force-stop" in command for command in commands)

    async def test_monkey_abort_is_a_failure(self, device_manager):
        device_manager.run = AsyncMock(
            side_effect=[("31441", "", 0), ("** monkey aborted", "", 0)]
        )
        assert await device_manager.restart_scanner_apps() is False

    async def test_runtime_error_returns_false(self, device_manager):
        device_manager.run = AsyncMock(side_effect=RuntimeError("timeout"))
        assert await device_manager.restart_scanner_apps() is False


def _health_output(loader="5428", pogo="31441", version="0.435.1"):
    """What the phone_health shell one-liner prints on the device."""
    lines = [f"loader={loader}", f"pogo={pogo}"]
    if version:
        lines.append(f"    versionName={version}")
    return "\n".join(lines)


@pytest.fixture
def version_file(tmp_path, mocker):
    path = tmp_path / "version.json"
    mocker.patch.object(Config, "SCANNER_GAME_VERSION_FILE", str(path))
    return path


class TestPhoneHealth:
    """Whether the phone can scan, and why not, read from the phone."""

    async def test_healthy_when_loader_and_game_run(self, device_manager, version_file):
        version_file.write_text('{"version": "0.435.1"}')
        device_manager.run = AsyncMock(return_value=(_health_output(), "", 0))
        state, _ = await device_manager.phone_health()
        assert state == "healthy"

    async def test_one_adb_call_reads_processes_and_version(
        self, device_manager, version_file
    ):
        device_manager.run = AsyncMock(return_value=(_health_output(), "", 0))
        await device_manager.phone_health()
        device_manager.run.assert_awaited_once()
        command = device_manager.run.await_args.args[-1]
        assert f"pidof {DeviceManager.POLISNATCH_PACKAGE}" in command
        assert "versionName" in command

    async def test_game_updated_when_build_differs_from_the_working_one(
        self, device_manager, version_file
    ):
        """2026-11-09: the Play Store moved the phone from 0.433.1 to 0.435.1."""
        version_file.write_text('{"version": "0.433.1"}')
        device_manager.run = AsyncMock(
            return_value=(_health_output(pogo="", version="0.435.1"), "", 0)
        )
        state, reason = await device_manager.phone_health()
        assert state == "game_updated"
        assert "0.435.1" in reason and "0.433.1" in reason

    async def test_unknown_working_version_is_not_an_update(
        self, device_manager, version_file
    ):
        device_manager.run = AsyncMock(return_value=(_health_output(pogo=""), "", 0))
        state, reason = await device_manager.phone_health()
        assert state == "broken" and "Pokémon GO" in reason

    async def test_broken_when_loader_is_gone(self, device_manager, version_file):
        device_manager.run = AsyncMock(return_value=(_health_output(loader=""), "", 0))
        state, reason = await device_manager.phone_health()
        assert state == "broken" and "PoliSnatch" in reason

    async def test_unreachable_when_adb_fails(self, device_manager, version_file):
        device_manager.run = AsyncMock(side_effect=RuntimeError("expirou"))
        state, _ = await device_manager.phone_health()
        assert state == "unreachable"

    async def test_unreachable_when_the_shell_never_ran(
        self, device_manager, version_file
    ):
        device_manager.run = AsyncMock(return_value=("", "error: device offline", 1))
        state, _ = await device_manager.phone_health()
        assert state == "unreachable"


class TestRememberWorkingVersion:
    async def test_records_the_build_while_scanning(self, device_manager, version_file):
        device_manager.run = AsyncMock(return_value=("    versionName=0.435.1", "", 0))
        await device_manager.remember_working_version()
        assert device_manager.working_game_version() == "0.435.1"

    async def test_reads_the_phone_at_most_hourly(self, device_manager, version_file):
        device_manager.run = AsyncMock(return_value=("    versionName=0.435.1", "", 0))
        await device_manager.remember_working_version()
        await device_manager.remember_working_version()
        device_manager.run.assert_awaited_once()


class TestAlertIfOffline:
    """Offline watchdog: alert-only after 15 min offline — never reboots."""

    def _prime(self, device_manager, mocker, *, now, offline_since):
        mocker.patch.object(Config, "ADB_DEVICE", "1.2.3.4:5555")
        mocker.patch.object(
            device_manager,
            "get_auto_reboot_enabled",
            new=AsyncMock(return_value=True),
        )
        device_manager.poliswag.account_monitor.is_device_connected = AsyncMock(
            return_value=False
        )
        device_manager.poliswag.MOD_CHANNEL = None
        device_manager._offline_since = offline_since
        mocker.patch("modules.device_manager.time.time", return_value=now)

    async def test_alerts_after_threshold(self, device_manager, mocker):
        self._prime(device_manager, mocker, now=10_000, offline_since=10_000 - 960)
        device_manager.reboot = AsyncMock()

        assert await device_manager.alert_if_offline() is True

        device_manager.reboot.assert_not_called()

    async def test_below_threshold_does_nothing(self, device_manager, mocker):
        self._prime(device_manager, mocker, now=10_000, offline_since=10_000 - 300)
        device_manager.reboot = AsyncMock()

        assert await device_manager.alert_if_offline() is False

        device_manager.reboot.assert_not_called()

    async def test_repeat_alert_throttled(self, device_manager, mocker):
        self._prime(device_manager, mocker, now=10_000, offline_since=10_000 - 960)
        device_manager._last_notification_time = 10_000 - 600  # well under 1h cadence

        assert await device_manager.alert_if_offline() is False

    async def test_device_back_online_resets_tracking(self, device_manager, mocker):
        self._prime(device_manager, mocker, now=10_000, offline_since=9_000)
        device_manager.poliswag.account_monitor.is_device_connected = AsyncMock(
            return_value=True
        )

        assert await device_manager.alert_if_offline() is False

        assert device_manager._offline_since is None

    async def test_back_online_after_an_alert_tells_the_mods(
        self, device_manager, mocker
    ):
        self._prime(device_manager, mocker, now=10_000, offline_since=10_000 - 1800)
        device_manager._last_notification_time = 10_000 - 900
        device_manager.poliswag.account_monitor.is_device_connected = AsyncMock(
            return_value=True
        )
        device_manager._notify = AsyncMock()

        await device_manager.alert_if_offline()

        device_manager._notify.assert_awaited_once()
        assert "30 min" in device_manager._notify.await_args.args[0]

    async def test_back_online_without_an_alert_stays_quiet(
        self, device_manager, mocker
    ):
        self._prime(device_manager, mocker, now=10_000, offline_since=10_000 - 300)
        device_manager.poliswag.account_monitor.is_device_connected = AsyncMock(
            return_value=True
        )
        device_manager._notify = AsyncMock()

        await device_manager.alert_if_offline()

        device_manager._notify.assert_not_awaited()

    async def test_no_configured_device_is_a_noop(self, device_manager, mocker):
        mocker.patch.object(Config, "ADB_DEVICE", "")
        assert await device_manager.alert_if_offline() is False

    async def test_auto_reboot_disabled_is_a_noop(self, device_manager, mocker):
        mocker.patch.object(Config, "ADB_DEVICE", "1.2.3.4:5555")
        mocker.patch.object(
            device_manager,
            "get_auto_reboot_enabled",
            new=AsyncMock(return_value=False),
        )
        assert await device_manager.alert_if_offline() is False

    async def test_first_offline_tick_only_records_timestamp(
        self, device_manager, mocker
    ):
        self._prime(device_manager, mocker, now=10_000, offline_since=None)
        assert await device_manager.alert_if_offline() is False
        assert device_manager._offline_since == 10_000


class TestNotify:
    async def test_sends_to_mod_channel_when_configured(self, device_manager):
        channel = MagicMock()
        channel.send = AsyncMock()
        device_manager.poliswag.MOD_CHANNEL = channel
        await device_manager._notify("hello")
        embed = channel.send.await_args.kwargs["embed"]
        assert embed.description == "hello"

    async def test_no_channel_is_a_noop(self, device_manager):
        device_manager.poliswag.MOD_CHANNEL = None
        await device_manager._notify("hello")  # must not raise

    async def test_send_failure_is_logged_not_raised(self, device_manager):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=RuntimeError("discord down"))
        device_manager.poliswag.MOD_CHANNEL = channel
        await device_manager._notify("hello")  # must not raise
        device_manager.poliswag.utility.log_to_file.assert_called_once()


class TestNextNotifyInterval:
    def test_hourly_while_under_six_hours(self, device_manager):
        assert device_manager._next_notify_interval(0) == 3600
        assert device_manager._next_notify_interval(6 * 3600 - 1) == 3600

    def test_every_six_hours_after(self, device_manager):
        assert device_manager._next_notify_interval(6 * 3600) == 6 * 3600
        assert device_manager._next_notify_interval(100 * 3600) == 6 * 3600


class TestAdb:
    async def test_returns_stripped_stdout_stderr_and_rc(self, device_manager, mocker):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"out\n", b"err\n"))
        proc.returncode = 0
        mocker.patch(
            "modules.device_manager.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        )
        stdout, stderr, rc = await device_manager._adb("get-state")
        assert (stdout, stderr, rc) == ("out", "err", 0)

    async def test_timeout_kills_and_reaps_process_then_raises(
        self, device_manager, mocker
    ):
        proc = MagicMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        mocker.patch(
            "modules.device_manager.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        )
        with pytest.raises(RuntimeError, match="expirou"):
            await device_manager._adb("get-state", timeout=1)
        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()


class TestGetModel:
    async def test_returns_model_string(self, device_manager):
        device_manager.run = AsyncMock(return_value=("Pixel 6", "", 0))
        assert await device_manager.get_model() == "Pixel 6"

    async def test_returns_none_on_nonzero_rc(self, device_manager):
        device_manager.run = AsyncMock(return_value=("", "err", 1))
        assert await device_manager.get_model() is None

    async def test_returns_none_on_empty_stdout(self, device_manager):
        device_manager.run = AsyncMock(return_value=("", "", 0))
        assert await device_manager.get_model() is None

    async def test_returns_none_and_logs_on_runtime_error(self, device_manager):
        device_manager.run = AsyncMock(side_effect=RuntimeError("timeout"))
        assert await device_manager.get_model() is None
        device_manager.poliswag.utility.log_to_file.assert_called_once()


class TestLogcatFiltered:
    async def test_returns_output_from_stdout(self, device_manager):
        device_manager.run = AsyncMock(return_value=("line1\nline2", "", 0))
        result = await device_manager.logcat_filtered(5)
        assert result == "line1\nline2"
        args = device_manager.run.await_args.args
        assert args[0] == "shell"
        assert "tail -5" in args[1]

    async def test_falls_back_to_stderr_when_stdout_empty(self, device_manager):
        device_manager.run = AsyncMock(return_value=("", "grep: no matches", 0))
        assert await device_manager.logcat_filtered() == "grep: no matches"

    async def test_returns_placeholder_when_both_empty(self, device_manager):
        device_manager.run = AsyncMock(return_value=("", "", 0))
        result = await device_manager.logcat_filtered()
        assert "aegis" in result

    async def test_returns_error_string_on_runtime_error(self, device_manager):
        device_manager.run = AsyncMock(side_effect=RuntimeError("timeout"))
        result = await device_manager.logcat_filtered()
        assert "Erro" in result
        device_manager.poliswag.utility.log_to_file.assert_called_once()


class TestReboot:
    async def test_returns_true_on_rc_zero(self, device_manager):
        device_manager.run = AsyncMock(return_value=("", "", 0))
        assert await device_manager.reboot() is True

    async def test_returns_false_on_nonzero_rc(self, device_manager):
        device_manager.run = AsyncMock(return_value=("", "err", 1))
        assert await device_manager.reboot() is False

    async def test_returns_false_and_logs_on_runtime_error(self, device_manager):
        device_manager.run = AsyncMock(side_effect=RuntimeError("timeout"))
        assert await device_manager.reboot() is False
        device_manager.poliswag.utility.log_to_file.assert_called_once()
