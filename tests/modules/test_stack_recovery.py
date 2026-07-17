from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.stack_recovery import StackRecovery


@pytest.fixture
def stack_recovery(mocker):
    """A StackRecovery with a mocked poliswag and auto-recreate enabled."""
    sr = StackRecovery(poliswag=MagicMock())
    sr.poliswag.MOD_CHANNEL = None
    sr.poliswag.device_manager.reboot_with_cooldown = AsyncMock(return_value=False)
    mocker.patch.object(
        type(sr),
        "auto_recreate_enabled",
        new_callable=mocker.PropertyMock,
        return_value=True,
    )
    return sr


def _at(mocker, now):
    mocker.patch("modules.stack_recovery.time.time", return_value=now)


class TestObserve:
    """Red ladder: recreate on first red tick, device reboot once it persists."""

    async def test_not_red_resets_tracking(self, stack_recovery, mocker):
        _at(mocker, 10_000)
        stack_recovery._red_since = 9_000
        assert await stack_recovery.observe(False) is False
        assert stack_recovery._red_since is None

    async def test_first_red_tick_recreates_immediately(self, stack_recovery, mocker):
        _at(mocker, 10_000)
        stack_recovery.recreate_services = AsyncMock(return_value=True)
        assert await stack_recovery.observe(True) is True
        stack_recovery.recreate_services.assert_awaited_once()
        # red clock stays armed — reboot escalation counts from first red tick
        assert stack_recovery._red_since == 10_000

    async def test_recreate_cooldown_blocks_repeat(self, stack_recovery, mocker):
        _at(mocker, 10_000)
        stack_recovery._red_since = 10_000 - 300
        stack_recovery._last_recreate = 10_000 - 600  # 10 min < 30 min cooldown
        stack_recovery.recreate_services = AsyncMock()
        assert await stack_recovery.observe(True) is False
        stack_recovery.recreate_services.assert_not_called()

    async def test_persistent_red_reboots_device(self, stack_recovery, mocker):
        _at(mocker, 10_000)
        stack_recovery._red_since = 10_000 - 960  # 16 min red
        stack_recovery._last_recreate = 10_000 - 900  # recreate already fired
        stack_recovery.recreate_services = AsyncMock()
        reboot = stack_recovery.poliswag.device_manager.reboot_with_cooldown
        reboot.return_value = True

        assert await stack_recovery.observe(True) is True

        stack_recovery.recreate_services.assert_not_called()
        reboot.assert_awaited_once()
        # successful reboot restarts the episode so the boot gets a window
        assert stack_recovery._red_since is None

    async def test_reboot_cooldown_leaves_episode_armed(self, stack_recovery, mocker):
        _at(mocker, 10_000)
        stack_recovery._red_since = 10_000 - 960
        stack_recovery._last_recreate = 10_000 - 900
        reboot = stack_recovery.poliswag.device_manager.reboot_with_cooldown
        reboot.return_value = False  # blocked by cooldown / disabled / failed

        assert await stack_recovery.observe(True) is False

        assert stack_recovery._red_since == 10_000 - 960

    async def test_red_below_reboot_threshold_waits(self, stack_recovery, mocker):
        _at(mocker, 10_000)
        stack_recovery._red_since = 10_000 - 600  # 10 min < 15 min threshold
        stack_recovery._last_recreate = 10_000 - 540
        reboot = stack_recovery.poliswag.device_manager.reboot_with_cooldown

        assert await stack_recovery.observe(True) is False

        reboot.assert_not_called()

    async def test_disabled_toggle_blocks_ladder(self, stack_recovery, mocker):
        _at(mocker, 10_000)
        mocker.patch.object(
            type(stack_recovery),
            "auto_recreate_enabled",
            new_callable=mocker.PropertyMock,
            return_value=False,
        )
        stack_recovery.recreate_services = AsyncMock()
        assert await stack_recovery.observe(True) is False
        stack_recovery.recreate_services.assert_not_called()

    async def test_failed_recreate_still_allows_later_reboot(
        self, stack_recovery, mocker
    ):
        _at(mocker, 10_000)
        stack_recovery.recreate_services = AsyncMock(return_value=False)
        assert await stack_recovery.observe(True) is False
        # red clock armed from this tick — the reboot rung can still fire later
        assert stack_recovery._red_since == 10_000


class TestRecreateServices:
    async def test_dev_mode_is_dry_run(self, stack_recovery, mocker):
        mocker.patch("modules.stack_recovery.Config.IS_PRODUCTION", False)
        create = mocker.patch("modules.stack_recovery.asyncio.create_subprocess_exec")
        assert await stack_recovery.recreate_services() is True
        create.assert_not_called()

    async def test_runs_compose_force_recreate(self, stack_recovery, mocker):
        mocker.patch("modules.stack_recovery.Config.IS_PRODUCTION", True)
        mocker.patch(
            "modules.stack_recovery.Config.RECREATE_SERVICES", "dragonite rotom-ng"
        )
        mocker.patch(
            "modules.stack_recovery.Config.UNOWNHASH_COMPOSE_FILE",
            "/root/unonwhash/docker-compose.yml",
        )
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"done", None))
        proc.returncode = 0
        create = mocker.patch(
            "modules.stack_recovery.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        )
        assert await stack_recovery.recreate_services() is True
        args = create.await_args.args
        assert args[:6] == (
            "docker-compose",
            "-f",
            "/root/unonwhash/docker-compose.yml",
            "up",
            "-d",
            "--force-recreate",
        )
        assert args[6:] == ("dragonite", "rotom-ng")
        # ${PWD} interpolation in the stack compose file: both cwd and the PWD
        # env var must point at the stack dir or bind mounts resolve blank.
        kwargs = create.await_args.kwargs
        assert kwargs["cwd"] == "/root/unonwhash"
        assert kwargs["env"]["PWD"] == "/root/unonwhash"

    async def test_nonzero_exit_returns_false(self, stack_recovery, mocker):
        mocker.patch("modules.stack_recovery.Config.IS_PRODUCTION", True)
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"boom", None))
        proc.returncode = 1
        mocker.patch(
            "modules.stack_recovery.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        )
        assert await stack_recovery.recreate_services() is False

    async def test_spawn_failure_returns_false(self, stack_recovery, mocker):
        mocker.patch("modules.stack_recovery.Config.IS_PRODUCTION", True)
        mocker.patch(
            "modules.stack_recovery.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("docker-compose")),
        )
        assert await stack_recovery.recreate_services() is False
