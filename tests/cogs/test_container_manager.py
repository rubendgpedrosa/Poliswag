"""Tests for cogs.container_manager.ContainerManagerCog.

The module reads ``MY_ID`` and ``SCANNER_CONTAINER_NAME`` from env via Config at
import time, so we seed them before importing anything from the project.
"""

import os

os.environ.setdefault("MY_ID", "111")
os.environ.setdefault("SCANNER_CONTAINER_NAME", "test-scanner")

from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402
from discord.ext import commands  # noqa: E402

from cogs.container_manager import ContainerManagerCog  # noqa: E402
from modules.config import Config  # noqa: E402


@pytest.fixture
def cog():
    poliswag = MagicMock()
    return ContainerManagerCog(poliswag)


def make_ctx(author_id=None):
    if author_id is None:
        author_id = Config.MY_ID
    ctx = MagicMock()
    ctx.author.id = author_id
    ctx.send = AsyncMock()
    # Commands that do `msg = await ctx.send(...)` then `await msg.edit(...)`
    # need the returned message's .edit to be awaitable too.
    ctx.send.return_value.edit = AsyncMock()
    return ctx


class TestCogCheck:
    def test_owner_passes(self, cog):
        assert cog.cog_check(make_ctx(author_id=Config.MY_ID)) is True

    def test_non_owner_rejected(self, cog):
        assert cog.cog_check(make_ctx(author_id=999)) is False


class TestContainerGroup:
    async def test_fallback_invocation_sends_help(self, cog):
        ctx = make_ctx()
        await ContainerManagerCog.container.callback(cog, ctx)
        ctx.send.assert_awaited_once()
        assert "Invalid container command" in ctx.send.call_args.args[0]


class TestStartContainer:
    async def test_success_path_sends_two_messages(self, cog):
        ctx = make_ctx()
        await ContainerManagerCog.start_container.callback(cog, ctx)
        cog.poliswag.scanner_manager.change_scanner_status.assert_called_once_with(
            "start"
        )
        assert ctx.send.await_count == 2

    async def test_exception_logs_and_sends_error(self, cog):
        ctx = make_ctx()
        cog.poliswag.scanner_manager.change_scanner_status.side_effect = RuntimeError(
            "docker offline"
        )
        await ContainerManagerCog.start_container.callback(cog, ctx)
        cog.poliswag.utility.log_to_file.assert_called()
        assert ctx.send.await_count == 2


class TestStopContainer:
    async def test_success_path_sends_two_messages(self, cog):
        ctx = make_ctx()
        await ContainerManagerCog.stop_container.callback(cog, ctx)
        cog.poliswag.scanner_manager.change_scanner_status.assert_called_once_with(
            "stop"
        )
        assert ctx.send.await_count == 2

    async def test_exception_logs_and_sends_error(self, cog):
        ctx = make_ctx()
        cog.poliswag.scanner_manager.change_scanner_status.side_effect = RuntimeError(
            "docker offline"
        )
        await ContainerManagerCog.stop_container.callback(cog, ctx)
        cog.poliswag.utility.log_to_file.assert_called()
        assert ctx.send.await_count == 2


class TestStatusCmd:
    def _full_data(self, **overrides):
        data = {
            "last_pokemon_seconds_ago": 30,
            "devices": [
                {"origin": "MITM-1", "is_alive": True, "last_msg_seconds_ago": 5},
            ],
            "workers": [
                {
                    "worker_id": "leiria-worker-1",
                    "area": "Leiria",
                    "status": "Executing Worker",
                    "last_data_seconds_ago": 10,
                },
            ],
            "accounts": {"good": 5, "in_use": 2, "cooldown": 1, "disabled": 0},
        }
        data.update(overrides)
        return data

    async def test_success_sends_embed_with_all_sections(self, cog):
        ctx = make_ctx()
        cog.poliswag.scanner_status.get_full_status = AsyncMock(
            return_value=self._full_data()
        )
        await ContainerManagerCog.status_cmd.callback(cog, ctx)
        msg = ctx.send.return_value
        msg.edit.assert_awaited_once()
        embed = msg.edit.call_args.kwargs["embed"]
        field_values = "\n".join(f.value for f in embed.fields)
        assert "Último pokémon há **30s**" in field_values
        assert "MITM-1" in field_values
        assert "leiria-worker-1" in field_values
        assert "Boas: **5**" in field_values

    async def test_exception_logs_and_edits_error(self, cog):
        ctx = make_ctx()
        cog.poliswag.scanner_status.get_full_status = AsyncMock(
            side_effect=RuntimeError("dragonite unreachable")
        )
        await ContainerManagerCog.status_cmd.callback(cog, ctx)
        cog.poliswag.utility.log_to_file.assert_called_once()
        ctx.send.return_value.edit.assert_awaited_once()
        assert (
            "Erro ao recolher estado"
            in ctx.send.return_value.edit.call_args.kwargs["content"]
        )

    async def test_stale_pokemon_marks_red(self, cog):
        ctx = make_ctx()
        cog.poliswag.scanner_status.get_full_status = AsyncMock(
            return_value=self._full_data(last_pokemon_seconds_ago=900)
        )
        await ContainerManagerCog.status_cmd.callback(cog, ctx)
        embed = ctx.send.return_value.edit.call_args.kwargs["embed"]
        assert "STALE" in embed.fields[0].value

    async def test_unknown_pokemon_age_shown(self, cog):
        ctx = make_ctx()
        cog.poliswag.scanner_status.get_full_status = AsyncMock(
            return_value=self._full_data(last_pokemon_seconds_ago=None)
        )
        await ContainerManagerCog.status_cmd.callback(cog, ctx)
        embed = ctx.send.return_value.edit.call_args.kwargs["embed"]
        assert "Desconhecido" in embed.fields[0].value

    async def test_empty_devices_and_workers_show_placeholders(self, cog):
        ctx = make_ctx()
        cog.poliswag.scanner_status.get_full_status = AsyncMock(
            return_value=self._full_data(devices=[], workers=[])
        )
        await ContainerManagerCog.status_cmd.callback(cog, ctx)
        embed = ctx.send.return_value.edit.call_args.kwargs["embed"]
        assert "_sem dispositivos_" in embed.fields[1].value
        assert "_sem workers_" in embed.fields[2].value


class TestRecreateContainers:
    async def test_success_edits_confirmation(self, cog):
        ctx = make_ctx()
        cog.poliswag.stack_recovery.recreate_services = AsyncMock(return_value=True)
        await ContainerManagerCog.recreate_containers.callback(cog, ctx)
        msg = ctx.send.return_value
        assert "recriados" in msg.edit.call_args.kwargs["content"]
        cog.poliswag.utility.log_to_file.assert_called_once()

    async def test_failure_edits_error(self, cog):
        ctx = make_ctx()
        cog.poliswag.stack_recovery.recreate_services = AsyncMock(return_value=False)
        await ContainerManagerCog.recreate_containers.callback(cog, ctx)
        msg = ctx.send.return_value
        assert "Falha" in msg.edit.call_args.kwargs["content"]
        log_call = cog.poliswag.utility.log_to_file.call_args
        assert log_call.args[1] == "ERROR"


class TestContainerAutorecreate:
    async def test_no_state_reports_current_status(self, cog):
        ctx = make_ctx()
        cog.poliswag.stack_recovery.get_auto_recreate_enabled = AsyncMock(
            return_value=True
        )
        await ContainerManagerCog.container_autorecreate.callback(cog, ctx, None)
        assert "activada" in ctx.send.call_args.args[0]

    async def test_on_enables_and_confirms(self, cog):
        ctx = make_ctx()
        cog.poliswag.stack_recovery.set_auto_recreate_enabled = AsyncMock()
        await ContainerManagerCog.container_autorecreate.callback(cog, ctx, "on")
        cog.poliswag.stack_recovery.set_auto_recreate_enabled.assert_awaited_once_with(
            True
        )
        assert "activada" in ctx.send.call_args.args[0]

    async def test_off_disables_and_confirms(self, cog):
        ctx = make_ctx()
        cog.poliswag.stack_recovery.set_auto_recreate_enabled = AsyncMock()
        await ContainerManagerCog.container_autorecreate.callback(cog, ctx, "off")
        cog.poliswag.stack_recovery.set_auto_recreate_enabled.assert_awaited_once_with(
            False
        )
        assert "desactivada" in ctx.send.call_args.args[0]

    async def test_invalid_state_sends_error(self, cog):
        ctx = make_ctx()
        cog.poliswag.stack_recovery.set_auto_recreate_enabled = AsyncMock()
        await ContainerManagerCog.container_autorecreate.callback(cog, ctx, "bogus")
        cog.poliswag.stack_recovery.set_auto_recreate_enabled.assert_not_awaited()
        assert "inválido" in ctx.send.call_args.args[0]


class TestDeviceGroup:
    async def test_fallback_invocation_sends_help(self, cog):
        ctx = make_ctx()
        await ContainerManagerCog.device.callback(cog, ctx)
        assert "!device status" in ctx.send.call_args.args[0]


class TestDeviceRestartapp:
    async def test_success_edits_confirmation(self, cog):
        ctx = make_ctx()
        cog.poliswag.device_manager.restart_app = AsyncMock(return_value=True)
        await ContainerManagerCog.device_restartapp.callback(cog, ctx)
        msg = ctx.send.return_value
        assert "reiniciada" in msg.edit.call_args.kwargs["content"]
        cog.poliswag.utility.log_to_file.assert_called_once()

    async def test_failure_edits_error(self, cog):
        ctx = make_ctx()
        cog.poliswag.device_manager.restart_app = AsyncMock(return_value=False)
        await ContainerManagerCog.device_restartapp.callback(cog, ctx)
        msg = ctx.send.return_value
        assert "Falha" in msg.edit.call_args.kwargs["content"]


class TestDeviceStatus:
    async def test_connected_shows_model(self, cog, mocker):
        mocker.patch.object(Config, "ADB_DEVICE", "1.2.3.4:5555")
        ctx = make_ctx()
        cog.poliswag.device_manager.get_model = AsyncMock(return_value="Pixel 6")
        await ContainerManagerCog.device_status.callback(cog, ctx)
        msg = ctx.send.return_value
        embed = msg.edit.call_args.kwargs["embed"]
        assert "ligado" in embed.title
        assert "Pixel 6" in embed.description
        cog.poliswag.utility.log_to_file.assert_called_once()

    async def test_disconnected_shows_no_response(self, cog, mocker):
        mocker.patch.object(Config, "ADB_DEVICE", "")
        ctx = make_ctx()
        cog.poliswag.device_manager.get_model = AsyncMock(return_value=None)
        await ContainerManagerCog.device_status.callback(cog, ctx)
        msg = ctx.send.return_value
        embed = msg.edit.call_args.kwargs["embed"]
        assert "Sem ligação" in embed.title
        assert "não configurado" in embed.description


class TestDeviceLogcat:
    async def test_rejects_out_of_range_lines(self, cog):
        ctx = make_ctx()
        await ContainerManagerCog.device_logcat.callback(cog, ctx, 0)
        assert "entre 1 e 200" in ctx.send.call_args.args[0]
        cog.poliswag.device_manager.logcat_filtered.assert_not_called()

    async def test_rejects_too_many_lines(self, cog):
        ctx = make_ctx()
        await ContainerManagerCog.device_logcat.callback(cog, ctx, 201)
        assert "entre 1 e 200" in ctx.send.call_args.args[0]

    async def test_default_ten_lines_and_normal_output(self, cog):
        ctx = make_ctx()
        cog.poliswag.device_manager.logcat_filtered = AsyncMock(
            return_value="short output"
        )
        await ContainerManagerCog.device_logcat.callback(cog, ctx)
        cog.poliswag.device_manager.logcat_filtered.assert_awaited_once_with(10)
        msg = ctx.send.return_value
        assert "short output" in msg.edit.call_args.kwargs["content"]

    async def test_long_output_is_truncated(self, cog):
        ctx = make_ctx()
        cog.poliswag.device_manager.logcat_filtered = AsyncMock(return_value="x" * 2000)
        await ContainerManagerCog.device_logcat.callback(cog, ctx, 50)
        msg = ctx.send.return_value
        content = msg.edit.call_args.kwargs["content"]
        assert content.startswith("```\n…")
        # 1897 chars of "x" plus the leading ellipsis marker.
        assert content.count("x") == 1897


class TestDeviceAutoreboot:
    async def test_on_enables_and_confirms(self, cog):
        ctx = make_ctx()
        cog.poliswag.device_manager.set_auto_reboot_enabled = AsyncMock()
        await ContainerManagerCog.device_autoreboot.callback(cog, ctx, "on")
        cog.poliswag.device_manager.set_auto_reboot_enabled.assert_awaited_once_with(
            True
        )
        assert "activado" in ctx.send.call_args.args[0]

    async def test_off_disables_and_confirms(self, cog):
        ctx = make_ctx()
        cog.poliswag.device_manager.set_auto_reboot_enabled = AsyncMock()
        await ContainerManagerCog.device_autoreboot.callback(cog, ctx, "off")
        cog.poliswag.device_manager.set_auto_reboot_enabled.assert_awaited_once_with(
            False
        )
        assert "desactivado" in ctx.send.call_args.args[0]

    async def test_unrecognised_state_reports_current_status(self, cog):
        ctx = make_ctx()
        cog.poliswag.device_manager.get_auto_reboot_enabled = AsyncMock(
            return_value=False
        )
        await ContainerManagerCog.device_autoreboot.callback(cog, ctx, "status")
        assert "desactivado" in ctx.send.call_args.args[0]


class TestDeviceReboot:
    async def test_success_edits_confirmation(self, cog, mocker):
        mocker.patch.object(Config, "ADB_DEVICE", "1.2.3.4:5555")
        ctx = make_ctx()
        cog.poliswag.device_manager.reboot = AsyncMock(return_value=True)
        await ContainerManagerCog.device_reboot.callback(cog, ctx)
        msg = ctx.send.return_value
        assert "1.2.3.4:5555" in msg.edit.call_args.kwargs["content"]
        cog.poliswag.utility.log_to_file.assert_called_once()

    async def test_failure_edits_error(self, cog):
        ctx = make_ctx()
        cog.poliswag.device_manager.reboot = AsyncMock(return_value=False)
        await ContainerManagerCog.device_reboot.callback(cog, ctx)
        msg = ctx.send.return_value
        assert "Falha no reboot" in msg.edit.call_args.kwargs["content"]
        cog.poliswag.utility.log_to_file.assert_called_once()


class TestCogCommandError:
    async def test_check_failure_sends_unauthorized(self, cog):
        ctx = make_ctx()
        err = commands.CheckFailure("no")
        await cog.cog_command_error(ctx, err)
        ctx.send.assert_awaited_once_with("You are not authorized to use this command.")

    async def test_command_not_found_sends_help(self, cog):
        ctx = make_ctx()
        err = commands.CommandNotFound("huh")
        await cog.cog_command_error(ctx, err)
        assert "Invalid container command" in ctx.send.call_args.args[0]

    async def test_other_error_logs_and_sends(self, cog):
        ctx = make_ctx()
        err = RuntimeError("explosion")
        await cog.cog_command_error(ctx, err)
        cog.poliswag.utility.log_to_file.assert_called()
        ctx.send.assert_awaited_once()


class TestLifecycle:
    async def test_cog_load_prints(self, cog, capsys):
        await cog.cog_load()
        assert "ContainerManagerCog loaded" in capsys.readouterr().out

    async def test_cog_unload_prints(self, cog, capsys):
        await cog.cog_unload()
        assert "ContainerManagerCog unloaded" in capsys.readouterr().out
