from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.stack_recovery import StackRecovery


@pytest.fixture
def stack_recovery(mocker):
    """A StackRecovery with a mocked poliswag and auto-recreate enabled."""
    sr = StackRecovery(poliswag=MagicMock())
    sr.poliswag.MOD_CHANNEL = None
    mocker.patch.object(
        sr, "get_auto_recreate_enabled", new=AsyncMock(return_value=True)
    )
    return sr


def _at(mocker, now):
    mocker.patch("modules.stack_recovery.time.time", return_value=now)


def _make_stack_recovery():
    sr = StackRecovery(poliswag=MagicMock())
    sr.poliswag.db = AsyncMock()
    return sr


class TestAutoRecreateEnabledCache:
    """Cache is invalidate-on-write: a DB miss caches, a set updates the cache."""

    async def test_second_read_does_not_hit_db_again(self):
        sr = _make_stack_recovery()
        sr.poliswag.db.get_data_from_database.return_value = [
            {"auto_recreate_enabled": 1}
        ]

        assert await sr.get_auto_recreate_enabled() is True
        assert await sr.get_auto_recreate_enabled() is True

        sr.poliswag.db.get_data_from_database.assert_called_once()

    async def test_failed_read_is_not_cached(self):
        sr = _make_stack_recovery()
        sr.poliswag.db.get_data_from_database.side_effect = Exception("db down")

        assert await sr.get_auto_recreate_enabled() is True  # fails open
        assert await sr.get_auto_recreate_enabled() is True

        assert sr.poliswag.db.get_data_from_database.call_count == 2

    async def test_set_updates_cache_without_a_read(self):
        sr = _make_stack_recovery()

        await sr.set_auto_recreate_enabled(False)

        assert await sr.get_auto_recreate_enabled() is False
        sr.poliswag.db.get_data_from_database.assert_not_called()

    async def test_failed_write_does_not_update_cache(self):
        sr = _make_stack_recovery()
        sr.poliswag.db.get_data_from_database.return_value = [
            {"auto_recreate_enabled": 1}
        ]
        sr.poliswag.db.execute_query_to_database.side_effect = Exception("db down")

        await sr.set_auto_recreate_enabled(False)

        # setter failed to persist, so the next read still goes to the DB
        assert await sr.get_auto_recreate_enabled() is True
        sr.poliswag.db.get_data_from_database.assert_called_once()


class TestObserve:
    """Red ladder: recreate at 10 min and 45 min into red, then stop — never reboots."""

    async def test_not_red_resets_tracking(self, stack_recovery, mocker):
        _at(mocker, 10_000)
        stack_recovery._red_since = 9_000
        stack_recovery._recreate_attempts = 1
        assert await stack_recovery.observe(False) is False
        assert stack_recovery._red_since is None
        assert stack_recovery._recreate_attempts == 0

    async def test_fresh_red_does_not_recreate_immediately(
        self, stack_recovery, mocker
    ):
        _at(mocker, 10_000)
        stack_recovery.recreate_services = AsyncMock(return_value=True)
        assert await stack_recovery.observe(True) is False
        stack_recovery.recreate_services.assert_not_called()
        assert stack_recovery._red_since == 10_000
        assert stack_recovery._recreate_attempts == 0

    async def test_first_attempt_fires_at_10min(self, stack_recovery, mocker):
        _at(mocker, 10_000)
        stack_recovery._red_since = 10_000 - 600  # exactly 10 min red
        stack_recovery.recreate_services = AsyncMock(return_value=True)

        assert await stack_recovery.observe(True) is True

        stack_recovery.recreate_services.assert_awaited_once()
        assert stack_recovery._recreate_attempts == 1
        # episode stays armed — the second attempt can still fire at 45 min
        assert stack_recovery._red_since == 10_000 - 600

    async def test_first_attempt_not_yet_due(self, stack_recovery, mocker):
        _at(mocker, 10_000)
        stack_recovery._red_since = 10_000 - 300  # 5 min < 10 min threshold
        stack_recovery.recreate_services = AsyncMock()

        assert await stack_recovery.observe(True) is False

        stack_recovery.recreate_services.assert_not_called()

    async def test_second_attempt_waits_until_45min(self, stack_recovery, mocker):
        _at(mocker, 10_000)
        stack_recovery._red_since = (
            10_000 - 1200
        )  # 20 min red, 1st attempt already used
        stack_recovery._recreate_attempts = 1
        stack_recovery.recreate_services = AsyncMock()

        assert await stack_recovery.observe(True) is False

        stack_recovery.recreate_services.assert_not_called()

    async def test_second_attempt_fires_at_45min(self, stack_recovery, mocker):
        _at(mocker, 10_000)
        stack_recovery._red_since = 10_000 - 2700  # 45 min red
        stack_recovery._recreate_attempts = 1
        stack_recovery.recreate_services = AsyncMock(return_value=True)

        assert await stack_recovery.observe(True) is True

        stack_recovery.recreate_services.assert_awaited_once()
        assert stack_recovery._recreate_attempts == 2

    async def test_stops_attempting_after_both_used(self, stack_recovery, mocker):
        _at(mocker, 10_000)
        stack_recovery._red_since = 10_000 - 999_999  # still red, way past 45 min
        stack_recovery._recreate_attempts = 2
        stack_recovery.recreate_services = AsyncMock()

        assert await stack_recovery.observe(True) is False

        stack_recovery.recreate_services.assert_not_called()
        # no reboot escalation exists — the ladder just waits
        assert stack_recovery._red_since == 10_000 - 999_999
        assert stack_recovery._recreate_attempts == 2

    async def test_disabled_toggle_blocks_ladder(self, stack_recovery, mocker):
        _at(mocker, 10_000)
        stack_recovery._red_since = 10_000 - 600
        mocker.patch.object(
            stack_recovery,
            "get_auto_recreate_enabled",
            new=AsyncMock(return_value=False),
        )
        stack_recovery.recreate_services = AsyncMock()
        assert await stack_recovery.observe(True) is False
        stack_recovery.recreate_services.assert_not_called()

    async def test_failed_recreate_still_counts_the_attempt(
        self, stack_recovery, mocker
    ):
        _at(mocker, 10_000)
        stack_recovery._red_since = 10_000 - 600
        stack_recovery.recreate_services = AsyncMock(return_value=False)
        assert await stack_recovery.observe(True) is False
        # attempt is consumed even on failure, so it doesn't retry every tick
        assert stack_recovery._recreate_attempts == 1
        assert stack_recovery._red_since == 10_000 - 600


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

    async def test_timeout_kills_process_and_reaps_it(self, stack_recovery, mocker):
        mocker.patch("modules.stack_recovery.Config.IS_PRODUCTION", True)
        proc = MagicMock()
        proc.communicate = MagicMock()
        proc.wait = AsyncMock()
        mocker.patch(
            "modules.stack_recovery.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        )
        mocker.patch(
            "modules.stack_recovery.asyncio.wait_for",
            new=AsyncMock(side_effect=TimeoutError()),
        )
        assert await stack_recovery.recreate_services() is False
        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()


class TestNotify:
    async def test_sends_embed_to_mod_channel_when_configured(self, stack_recovery):
        channel = MagicMock()
        channel.send = AsyncMock()
        stack_recovery.poliswag.MOD_CHANNEL = channel
        await stack_recovery._notify("Title", "Description", discord.Color.red())
        channel.send.assert_awaited_once()
        embed = channel.send.await_args.kwargs["embed"]
        assert embed.title == "Title"
        assert embed.description == "Description"

    async def test_no_channel_is_a_noop(self, stack_recovery):
        stack_recovery.poliswag.MOD_CHANNEL = None
        await stack_recovery._notify(
            "Title", "Description", discord.Color.red()
        )  # must not raise

    async def test_send_failure_is_logged_not_raised(self, stack_recovery):
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=RuntimeError("discord down"))
        stack_recovery.poliswag.MOD_CHANNEL = channel
        await stack_recovery._notify(
            "Title", "Description", discord.Color.red()
        )  # must not raise
        stack_recovery.poliswag.utility.log_to_file.assert_called_once()
