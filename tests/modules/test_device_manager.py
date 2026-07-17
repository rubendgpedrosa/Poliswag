from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.config import Config
from modules.device_manager import DeviceManager


@pytest.fixture
def device_manager():
    """A DeviceManager with a mocked poliswag dependency (so _log is a no-op)."""
    return DeviceManager(poliswag=MagicMock())


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


class TestRebootWithCooldown:
    def _prime(self, device_manager, mocker, *, enabled=True):
        mocker.patch.object(Config, "ADB_DEVICE", "1.2.3.4:5555")
        mocker.patch.object(
            type(device_manager),
            "auto_reboot_enabled",
            new_callable=mocker.PropertyMock,
            return_value=enabled,
        )
        mocker.patch("modules.device_manager.time.time", return_value=10_000)

    async def test_reboots_and_arms_cooldown(self, device_manager, mocker):
        self._prime(device_manager, mocker)
        device_manager.reboot = AsyncMock(return_value=True)
        assert await device_manager.reboot_with_cooldown() is True
        assert device_manager._last_auto_reboot == 10_000

    async def test_cooldown_blocks_second_reboot(self, device_manager, mocker):
        self._prime(device_manager, mocker)
        device_manager._last_auto_reboot = 10_000 - 600  # 10 min < 30 min cooldown
        device_manager.reboot = AsyncMock()
        assert await device_manager.reboot_with_cooldown() is False
        device_manager.reboot.assert_not_called()

    async def test_disabled_toggle_blocks_reboot(self, device_manager, mocker):
        self._prime(device_manager, mocker, enabled=False)
        device_manager.reboot = AsyncMock()
        assert await device_manager.reboot_with_cooldown() is False
        device_manager.reboot.assert_not_called()


class TestAutoRebootIfOffline:
    """Offline watchdog: plain reboot after 15 min offline, shared cooldown."""

    def _prime(self, device_manager, mocker, *, now, offline_since):
        mocker.patch.object(Config, "ADB_DEVICE", "1.2.3.4:5555")
        mocker.patch.object(
            type(device_manager),
            "auto_reboot_enabled",
            new_callable=mocker.PropertyMock,
            return_value=True,
        )
        device_manager.poliswag.account_monitor.is_device_connected = AsyncMock(
            return_value=False
        )
        device_manager.poliswag.MOD_CHANNEL = None
        device_manager._offline_since = offline_since
        mocker.patch("modules.device_manager.time.time", return_value=now)

    async def test_reboots_after_threshold(self, device_manager, mocker):
        self._prime(device_manager, mocker, now=10_000, offline_since=10_000 - 960)
        device_manager.reboot = AsyncMock(return_value=True)

        assert await device_manager.auto_reboot_if_offline() is True

        device_manager.reboot.assert_awaited_once()
        assert device_manager._offline_since is None

    async def test_below_threshold_does_nothing(self, device_manager, mocker):
        self._prime(device_manager, mocker, now=10_000, offline_since=10_000 - 300)
        device_manager.reboot = AsyncMock()

        assert await device_manager.auto_reboot_if_offline() is False

        device_manager.reboot.assert_not_called()

    async def test_cooldown_blocks_repeat_reboot(self, device_manager, mocker):
        self._prime(device_manager, mocker, now=10_000, offline_since=10_000 - 960)
        device_manager._last_auto_reboot = 10_000 - 600
        device_manager.reboot = AsyncMock()

        assert await device_manager.auto_reboot_if_offline() is False

        device_manager.reboot.assert_not_called()

    async def test_device_back_online_resets_tracking(self, device_manager, mocker):
        self._prime(device_manager, mocker, now=10_000, offline_since=9_000)
        device_manager.poliswag.account_monitor.is_device_connected = AsyncMock(
            return_value=True
        )

        assert await device_manager.auto_reboot_if_offline() is False

        assert device_manager._offline_since is None
