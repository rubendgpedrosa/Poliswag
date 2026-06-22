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
