from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.stack_recovery import StackRecovery


@pytest.fixture
def stack_recovery(mocker):
    """A StackRecovery with a mocked poliswag and auto-recreate enabled."""
    sr = StackRecovery(poliswag=MagicMock())
    sr.poliswag.MOD_CHANNEL = None
    sr.poliswag.device_manager.restart_scanner_apps = AsyncMock(return_value=True)
    sr.poliswag.device_manager.phone_health = AsyncMock(
        return_value=("healthy", "Aegis running and injected")
    )
    mocker.patch("modules.stack_recovery.asyncio.sleep", new=AsyncMock())
    mocker.patch.object(sr, "_sessions_allow_reset", new=AsyncMock(return_value=True))
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


# observe() inputs for each situation: (all_red, workers in rotom-ng)
SCANNING = (False, 16)
NO_DATA = (True, 16)  # workers connected, every Dragonite worker down
NO_WORKERS = (True, 0)  # nothing in rotom-ng; phone_health decides the rest
STACK_DOWN = (None, None)  # Dragonite / rotom-ng not answering


@pytest.fixture
def no_compose(stack_recovery):
    """The ladder tests never run docker compose."""
    stack_recovery.recreate_services = AsyncMock(return_value=True)


def _phone(sr, state):
    sr.poliswag.device_manager.phone_health = AsyncMock(return_value=(state, "why"))


async def _tick(sr, mocker, at, situation):
    _at(mocker, at)
    return await sr.observe(*situation)


@pytest.mark.usefixtures("no_compose")
class TestDiagnose:
    """Each tick is sorted into the one problem a restart can (or can't) fix."""

    async def test_scanning_is_no_problem(self, stack_recovery):
        assert await stack_recovery._diagnose(*SCANNING) is None

    async def test_workers_but_no_data_waits(self, stack_recovery):
        assert await stack_recovery._diagnose(*NO_DATA) == "no_data"
        stack_recovery.poliswag.device_manager.phone_health.assert_not_called()

    async def test_unanswered_status_is_the_stack(self, stack_recovery):
        assert await stack_recovery._diagnose(None, 16) == "stack"
        assert await stack_recovery._diagnose(True, None) == "stack"

    async def test_no_workers_and_broken_phone(self, stack_recovery):
        _phone(stack_recovery, "broken")
        assert await stack_recovery._diagnose(*NO_WORKERS) == "phone"

    async def test_no_workers_but_healthy_phone_is_the_link(self, stack_recovery):
        # 2026-09-28: rotom-ng dropped every worker, Aegis stayed injected and
        # reconnected within a minute. Restarting the phone was never needed.
        _phone(stack_recovery, "healthy")
        assert await stack_recovery._diagnose(*NO_WORKERS) == "link"

    async def test_no_workers_and_unreachable_phone(self, stack_recovery):
        _phone(stack_recovery, "unreachable")
        assert await stack_recovery._diagnose(*NO_WORKERS) == "unreachable"

    async def test_no_workers_even_before_dragonite_turns_red(self, stack_recovery):
        # Dragonite counts a worker up for 10 min after its last data; rotom-ng
        # knows at once, so the phone is checked without waiting for red.
        _phone(stack_recovery, "broken")
        assert await stack_recovery._diagnose(False, 0) == "phone"


@pytest.mark.usefixtures("no_compose")
class TestObserve:
    """Rungs at 5 and 15 min into an episode; what each does depends on why."""

    async def test_broken_phone_restarts_only_the_apps_at_5min(
        self, stack_recovery, mocker
    ):
        _phone(stack_recovery, "broken")
        await _tick(stack_recovery, mocker, 1_000, NO_WORKERS)
        assert await _tick(stack_recovery, mocker, 1_000 + 299, NO_WORKERS) is False
        stack_recovery.poliswag.device_manager.restart_scanner_apps.assert_not_called()

        assert await _tick(stack_recovery, mocker, 1_000 + 300, NO_WORKERS) is True

        stack_recovery.poliswag.device_manager.restart_scanner_apps.assert_awaited_once()
        stack_recovery.recreate_services.assert_not_called()
        assert stack_recovery._recovery_attempts == 1

    async def test_broken_phone_gets_everything_at_15min(self, stack_recovery, mocker):
        _phone(stack_recovery, "broken")
        await _tick(stack_recovery, mocker, 1_000, NO_WORKERS)
        await _tick(stack_recovery, mocker, 1_000 + 300, NO_WORKERS)
        await _tick(stack_recovery, mocker, 1_000 + 899, NO_WORKERS)
        stack_recovery.recreate_services.assert_not_called()

        await _tick(stack_recovery, mocker, 1_000 + 900, NO_WORKERS)

        stack_recovery.recreate_services.assert_awaited_once()
        assert (
            stack_recovery.poliswag.device_manager.restart_scanner_apps.await_count == 2
        )

    async def test_stops_after_both_rungs(self, stack_recovery, mocker):
        _phone(stack_recovery, "broken")
        for t in (0, 300, 900, 5_000, 99_999):
            await _tick(stack_recovery, mocker, 1_000 + t, NO_WORKERS)
        stack_recovery.recreate_services.assert_awaited_once()
        assert (
            stack_recovery.poliswag.device_manager.restart_scanner_apps.await_count == 2
        )

    async def test_unanswering_stack_is_recreated_at_5min(self, stack_recovery, mocker):
        await _tick(stack_recovery, mocker, 1_000, STACK_DOWN)
        await _tick(stack_recovery, mocker, 1_000 + 300, STACK_DOWN)
        stack_recovery.recreate_services.assert_awaited_once()
        stack_recovery.poliswag.device_manager.restart_scanner_apps.assert_awaited_once()

    async def test_our_own_recreate_does_not_restart_the_episode(
        self, stack_recovery, mocker
    ):
        # A recreate leaves Dragonite/rotom-ng briefly unanswering; that's the
        # same episode, not a new one, so rung 1 doesn't fire again.
        _phone(stack_recovery, "broken")
        await _tick(stack_recovery, mocker, 1_000, NO_WORKERS)
        await _tick(stack_recovery, mocker, 1_000 + 300, NO_WORKERS)
        await _tick(stack_recovery, mocker, 1_000 + 360, STACK_DOWN)
        await _tick(stack_recovery, mocker, 1_000 + 420, NO_WORKERS)
        assert (
            stack_recovery.poliswag.device_manager.restart_scanner_apps.await_count == 1
        )
        assert stack_recovery._red_since == 1_000

    async def test_healthy_phone_waits_for_aegis_to_reconnect(
        self, stack_recovery, mocker
    ):
        _phone(stack_recovery, "healthy")
        await _tick(stack_recovery, mocker, 1_000, NO_WORKERS)
        await _tick(stack_recovery, mocker, 1_000 + 600, NO_WORKERS)
        stack_recovery.poliswag.device_manager.restart_scanner_apps.assert_not_called()
        stack_recovery.recreate_services.assert_not_called()

    async def test_healthy_phone_still_stuck_at_15min_gets_everything_once(
        self, stack_recovery, mocker
    ):
        _phone(stack_recovery, "healthy")
        await _tick(stack_recovery, mocker, 1_000, NO_WORKERS)
        await _tick(stack_recovery, mocker, 1_000 + 900, NO_WORKERS)
        await _tick(stack_recovery, mocker, 1_000 + 960, NO_WORKERS)
        stack_recovery.recreate_services.assert_awaited_once()
        assert stack_recovery._recovery_attempts == 2

    async def test_late_first_action_uses_up_the_passed_rungs(
        self, stack_recovery, mocker
    ):
        # Waiting on the link until 10 min, then the phone breaks: its first
        # rung fires then, and the next tick doesn't fire rung 2 as well.
        _phone(stack_recovery, "healthy")
        await _tick(stack_recovery, mocker, 1_000, NO_WORKERS)
        _phone(stack_recovery, "broken")
        await _tick(stack_recovery, mocker, 1_000 + 600, NO_WORKERS)
        await _tick(stack_recovery, mocker, 1_000 + 660, NO_WORKERS)
        assert (
            stack_recovery.poliswag.device_manager.restart_scanner_apps.await_count == 1
        )
        stack_recovery.recreate_services.assert_not_called()

    async def test_no_data_never_restarts(self, stack_recovery, mocker):
        for t in (0, 300, 900, 5_000):
            await _tick(stack_recovery, mocker, 1_000 + t, NO_DATA)
        stack_recovery.recreate_services.assert_not_called()
        stack_recovery.poliswag.device_manager.restart_scanner_apps.assert_not_called()

    async def test_unreachable_phone_never_restarts(self, stack_recovery, mocker):
        _phone(stack_recovery, "unreachable")
        for t in (0, 300, 900, 5_000):
            await _tick(stack_recovery, mocker, 1_000 + t, NO_WORKERS)
        stack_recovery.recreate_services.assert_not_called()
        stack_recovery.poliswag.device_manager.restart_scanner_apps.assert_not_called()

    async def test_scanning_resets_the_episode(self, stack_recovery, mocker):
        _phone(stack_recovery, "broken")
        await _tick(stack_recovery, mocker, 1_000, NO_WORKERS)
        await _tick(stack_recovery, mocker, 1_000 + 300, NO_WORKERS)
        await _tick(stack_recovery, mocker, 1_000 + 400, SCANNING)
        assert stack_recovery._red_since is None
        assert stack_recovery._recovery_attempts == 0

    async def test_disabled_toggle_blocks_every_action(self, stack_recovery, mocker):
        mocker.patch.object(
            stack_recovery,
            "get_auto_recreate_enabled",
            new=AsyncMock(return_value=False),
        )
        await _tick(stack_recovery, mocker, 1_000, STACK_DOWN)
        await _tick(stack_recovery, mocker, 1_000 + 900, STACK_DOWN)
        stack_recovery.recreate_services.assert_not_called()

    async def test_failed_recreate_still_restarts_the_phone(
        self, stack_recovery, mocker
    ):
        stack_recovery.recreate_services = AsyncMock(return_value=False)
        await _tick(stack_recovery, mocker, 1_000, STACK_DOWN)
        assert await _tick(stack_recovery, mocker, 1_000 + 300, STACK_DOWN) is False
        stack_recovery.poliswag.device_manager.restart_scanner_apps.assert_awaited_once()
        # used up even on failure, so it doesn't retry every tick
        assert stack_recovery._recovery_attempts == 1


class TestRecreateServices:
    async def test_dev_mode_is_dry_run(self, stack_recovery, mocker):
        mocker.patch("modules.stack_recovery.Config.IS_PRODUCTION", False)
        create = mocker.patch("modules.stack_recovery.asyncio.create_subprocess_exec")
        assert await stack_recovery.recreate_services() is True
        create.assert_not_called()

    async def test_runs_compose_force_recreate(self, stack_recovery, mocker):
        mocker.patch("modules.stack_recovery.Path.exists", return_value=False)
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
        assert args[:7] == (
            "docker-compose",
            "-f",
            "/root/unonwhash/docker-compose.yml",
            "up",
            "-d",
            "--force-recreate",
            # Without it compose also recreates any dependency whose config
            # drifted: on 2026-09-26 that recreated the database mid-recovery.
            "--no-deps",
        )
        assert args[7:9] == ("--pull", "never")
        assert args[9:] == ("dragonite", "rotom-ng")
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


class TestSessionGuard:
    async def test_recovery_notice_after_session_deferral(self, stack_recovery, mocker):
        stack_recovery._red_since = 1000
        stack_recovery._session_wait_announced = True
        recovered = mocker.patch.object(
            stack_recovery, "_announce_recovered", new=AsyncMock()
        )
        assert await _tick(stack_recovery, mocker, 1300, SCANNING) is False
        recovered.assert_awaited_once_with(300)
        assert stack_recovery._session_wait_announced is False

    @pytest.mark.parametrize(
        "accounts,devices,allowed",
        [
            ({"in_use": 0}, {"devices": []}, True),
            ({"in_use": 0}, {"devices": [{"worker_in_use_count": 0}]}, True),
            ({"in_use": 16}, {"devices": [{"worker_in_use_count": 16}]}, False),
            ({"in_use": 0}, {"devices": [{"worker_in_use_count": 1}]}, False),
            ({"in_use": 1}, {"devices": []}, False),
            (None, {"devices": []}, False),
            ({"in_use": 0}, None, False),
            ({}, {"devices": []}, False),
            ({"in_use": False}, {"devices": []}, False),
            ({"in_use": "0"}, {"devices": []}, False),
            ({"in_use": -1}, {"devices": []}, False),
            ({"in_use": 0}, {"devices": [{}]}, False),
            ({"in_use": 0}, {"devices": [{"worker_in_use_count": False}]}, False),
        ],
    )
    async def test_fresh_counts_fail_closed(self, mocker, accounts, devices, allowed):
        recovery = StackRecovery(MagicMock())
        recovery._notify = AsyncMock()
        fetch = mocker.patch(
            "modules.stack_recovery.fetch_data",
            new=AsyncMock(side_effect=[accounts, devices]),
        )
        assert await recovery._sessions_allow_reset() is allowed
        assert [call.args[0] for call in fetch.await_args_list] == [
            "account_status",
            "device_status",
        ]
        assert recovery._notify.await_count == (0 if allowed else 1)

    async def test_live_accounts_block_containers_and_phone_without_spending_rung(
        self, stack_recovery, mocker
    ):
        mocker.patch("modules.stack_recovery.Config.IS_PRODUCTION", True)
        stack_recovery._sessions_allow_reset.return_value = False
        stack_recovery.recreate_services = AsyncMock(return_value=True)
        await _tick(stack_recovery, mocker, 1000, STACK_DOWN)
        assert await _tick(stack_recovery, mocker, 1300, STACK_DOWN) is False
        assert stack_recovery._recovery_attempts == 0
        stack_recovery.recreate_services.assert_not_awaited()
        stack_recovery.poliswag.device_manager.restart_scanner_apps.assert_not_awaited()
        # A later zero-usage window can use the same rung.
        stack_recovery._sessions_allow_reset.return_value = True
        assert await _tick(stack_recovery, mocker, 1360, STACK_DOWN) is True
        assert stack_recovery._recovery_attempts == 1
        stack_recovery.recreate_services.assert_awaited_once()

    async def test_phone_only_recovery_is_also_guarded(self, stack_recovery, mocker):
        mocker.patch("modules.stack_recovery.Config.IS_PRODUCTION", True)
        stack_recovery._sessions_allow_reset.return_value = False
        assert await stack_recovery._recover("apps") is False
        stack_recovery.poliswag.device_manager.restart_scanner_apps.assert_not_awaited()

    async def test_new_sessions_after_container_restart_protect_phone(
        self, stack_recovery, mocker
    ):
        mocker.patch("modules.stack_recovery.Config.IS_PRODUCTION", True)
        stack_recovery._sessions_allow_reset.side_effect = [True, False]
        stack_recovery.recreate_services = AsyncMock(return_value=True)
        assert await stack_recovery._recover("full") is True
        stack_recovery.recreate_services.assert_awaited_once()
        stack_recovery.poliswag.device_manager.restart_scanner_apps.assert_not_awaited()

    async def test_staged_images_do_not_expand_recovery_services(
        self, stack_recovery, mocker, tmp_path
    ):
        import json

        staged = tmp_path / "scanner-updates.staged.json"
        staged.write_text(
            json.dumps(
                {
                    "services": {
                        "dragonite": {
                            "image": "ghcr.io/unownhash/dragonite-public:dragonite-v1.20.17-testing@sha256:"
                            + "a" * 64
                        },
                        "golbat": {
                            "image": "ghcr.io/unownhash/golbat:main@sha256:" + "b" * 64
                        },
                    }
                }
            )
        )
        mocker.patch("modules.stack_recovery.Config.IS_PRODUCTION", True)
        mocker.patch(
            "modules.stack_recovery.Config.UNOWNHASH_COMPOSE_FILE",
            str(tmp_path / "docker-compose.yml"),
        )
        mocker.patch(
            "modules.stack_recovery.Config.RECREATE_SERVICES", "dragonite rotom-ng"
        )
        proc = MagicMock(returncode=0)
        proc.communicate = AsyncMock(return_value=(b"done", None))
        create = mocker.patch(
            "modules.stack_recovery.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        )
        assert await stack_recovery.recreate_services() is True
        args = create.await_args.args
        assert args[3:5] == ("-f", str(staged))
        assert args[-2:] == ("dragonite", "rotom-ng")
        assert "--no-deps" in args
        assert args[-4:-2] == ("--pull", "never")

    async def test_invalid_override_cannot_add_database_restarts(
        self, stack_recovery, mocker, tmp_path
    ):
        import json

        staged = tmp_path / "scanner-updates.staged.json"
        staged.write_text(json.dumps({"services": {"db": {"image": "mariadb:latest"}}}))
        mocker.patch("modules.stack_recovery.Config.IS_PRODUCTION", True)
        mocker.patch(
            "modules.stack_recovery.Config.UNOWNHASH_COMPOSE_FILE",
            str(tmp_path / "docker-compose.yml"),
        )
        proc = MagicMock(returncode=0)
        proc.communicate = AsyncMock(return_value=(b"done", None))
        create = mocker.patch(
            "modules.stack_recovery.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        )
        assert await stack_recovery.recreate_services() is True
        assert str(staged) not in create.await_args.args


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


@pytest.mark.usefixtures("no_compose")
class TestRecoveredAnnouncement:
    async def test_back_after_a_rung_tells_the_mods(self, stack_recovery, mocker):
        stack_recovery._notify = AsyncMock()
        _phone(stack_recovery, "broken")
        await _tick(stack_recovery, mocker, 1_000, NO_WORKERS)
        await _tick(stack_recovery, mocker, 1_000 + 300, NO_WORKERS)

        await _tick(stack_recovery, mocker, 1_000 + 900, SCANNING)

        title, description, color = stack_recovery._notify.await_args_list[-1].args
        assert title == "🟢 Mapa de volta"
        assert "15 min" in description
        assert color == discord.Color.green()

    async def test_back_after_a_waiting_notice_tells_the_mods(
        self, stack_recovery, mocker
    ):
        stack_recovery._notify = AsyncMock()
        await _tick(stack_recovery, mocker, 1_000, NO_DATA)
        await _tick(stack_recovery, mocker, 1_000 + 300, NO_DATA)

        await _tick(stack_recovery, mocker, 1_000 + 600, SCANNING)

        assert stack_recovery._notify.await_args.args[0] == "🟢 Mapa de volta"

    async def test_back_before_any_post_stays_quiet(self, stack_recovery, mocker):
        stack_recovery._notify = AsyncMock()
        await _tick(stack_recovery, mocker, 1_000, NO_WORKERS)
        await _tick(stack_recovery, mocker, 1_000 + 120, SCANNING)
        stack_recovery._notify.assert_not_awaited()


@pytest.mark.usefixtures("no_compose")
class TestWording:
    """Mods read the state (down, why, for how long, what's next)."""

    async def test_phone_rung(self, stack_recovery, mocker):
        stack_recovery._notify = AsyncMock()
        _phone(stack_recovery, "broken")
        await _tick(stack_recovery, mocker, 1_000, NO_WORKERS)
        await _tick(stack_recovery, mocker, 1_000 + 300, NO_WORKERS)

        title, description, color = stack_recovery._notify.await_args.args
        assert title == "🔴 Mapa em baixo há 5 min"
        assert "Pokémod parou no telemóvel" in description
        assert "nova tentativa daqui a **10 min**" in description
        assert color == discord.Color.orange()

    async def test_failed_last_rung_asks_for_a_human(self, stack_recovery, mocker):
        stack_recovery._notify = AsyncMock()
        stack_recovery.poliswag.device_manager.restart_scanner_apps = AsyncMock(
            return_value=False
        )
        await _tick(stack_recovery, mocker, 1_000, STACK_DOWN)
        await _tick(stack_recovery, mocker, 1_000 + 900, STACK_DOWN)

        title, description, color = stack_recovery._notify.await_args.args
        assert title == "🔴 Mapa em baixo há 15 min"
        assert "deu erro" in description and "intervir manualmente" in description
        assert color == discord.Color.red()

    async def test_no_data_says_once_that_it_waits(self, stack_recovery, mocker):
        stack_recovery._notify = AsyncMock()
        for t in (0, 300, 360, 900):
            await _tick(stack_recovery, mocker, 1_000 + t, NO_DATA)

        stack_recovery._notify.assert_awaited_once()
        title, description, _ = stack_recovery._notify.await_args.args
        assert title == "🟠 Mapa sem dados há 5 min"
        assert "Reiniciar não resolve" in description

    async def test_unreachable_phone_asks_for_a_human(self, stack_recovery, mocker):
        stack_recovery._notify = AsyncMock()
        _phone(stack_recovery, "unreachable")
        await _tick(stack_recovery, mocker, 1_000, NO_WORKERS)
        await _tick(stack_recovery, mocker, 1_000 + 300, NO_WORKERS)

        title, description, color = stack_recovery._notify.await_args.args
        assert "Não consigo chegar ao telemóvel" in description
        assert color == discord.Color.red()
