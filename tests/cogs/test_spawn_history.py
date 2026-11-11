import gzip
import io
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.spawn_history import SpawnHistory
from modules.permissions import is_owner
from tests.fakes import FakeHandle


@pytest.fixture
def cog(monkeypatch):
    monkeypatch.setattr("modules.config.Config.MY_ID", 123)
    poliswag = MagicMock()
    poliswag.db = FakeHandle()
    return SpawnHistory(poliswag)


def test_all_history_commands_are_owner_only(cog):
    assert all(is_owner in command.checks for command in cog.walk_commands())
    ctx = MagicMock()
    ctx.author.id = 999
    assert not cog.cog_check(ctx)


async def test_collection_recovers_from_failure(cog, monkeypatch):
    capture = MagicMock(side_effect=[RuntimeError("secret"), 123])
    monkeypatch.setattr("modules.spawn_history.capture", capture)
    await cog.collect.coro(cog)
    await cog.collect.coro(cog)
    assert capture.call_count == 2
    assert "secret" not in cog.poliswag.utility.log_to_file.call_args.args[0]
    # Each capture is a unit of its own (its own connection) with the
    # collector's 15 s read timeout.
    assert cog.poliswag.db.units == [
        (capture, (), {"read_timeout": 15}),
        (capture, (), {"read_timeout": 15}),
    ]


async def test_export_sends_valid_json_file_to_request_channel(cog, monkeypatch):
    body = {
        "metadata": {"complete_window": False},
        "spawns": [{"encounter_id": "18446744073709551614"}],
    }
    output = io.BytesIO(gzip.compress(json.dumps(body).encode()))
    monkeypatch.setattr(
        "modules.spawn_history.export_json", lambda connection, hours: output
    )
    captured = []

    async def send(*args, **kwargs):
        captured.append(json.loads(gzip.decompress(kwargs["file"].fp.read())))
        assert kwargs["file"].filename == "spawns-24h.json.gz"
        assert not kwargs["allowed_mentions"].everyone

    ctx = MagicMock(send=AsyncMock(side_effect=send))
    await cog.export.callback(cog, ctx, 24)
    assert captured == [body] and output.closed
    assert cog.poliswag.db.units[0][1:] == ((24,), {"read_timeout": 15})


async def test_status_reads_through_a_unit(cog, monkeypatch):
    status = MagicMock(return_value={"available": False})
    monkeypatch.setattr("modules.spawn_history.status", status)
    ctx = MagicMock(send=AsyncMock())
    await cog.history.callback(cog, ctx, 6)
    status.assert_called_once_with(cog.poliswag.db.connection, 6)
    assert cog.poliswag.db.units == [(status, (6,), {"read_timeout": 15})]
    assert ctx.send.call_args.args[0] == "A recolha ainda não começou."


async def test_leaderboard_reads_through_a_unit(cog, monkeypatch):
    data = {"metadata": {"note": "nota"}, "trainers": []}
    board = AsyncMock(return_value=data)
    monkeypatch.setattr("modules.spawn_history.leaderboard", board)
    monkeypatch.setattr("modules.scan_areas.ensure", AsyncMock(return_value=True))
    ctx = MagicMock(send=AsyncMock())
    await cog.report.callback(cog, ctx, 12)
    board.assert_awaited_once_with(cog.poliswag.db, 12)
    assert ctx.send.call_args.args[0] == "nota"


@pytest.mark.parametrize("command", ["history", "export", "report"])
async def test_an_invalid_period_never_opens_a_connection(cog, monkeypatch, command):
    monkeypatch.setattr("modules.scan_areas.ensure", AsyncMock(return_value=True))
    ctx = MagicMock(send=AsyncMock())
    with pytest.raises(ValueError, match="entre 1 e 48"):
        await getattr(cog, command).callback(cog, ctx, 49)
    assert cog.poliswag.db.units == []


async def test_manual_image_only_sends_to_owner(cog, monkeypatch):
    owner = MagicMock(send=AsyncMock())
    cog.poliswag.get_user.return_value = None
    cog.poliswag.fetch_user = AsyncMock(return_value=owner)
    cog.leaderboard.avatars = AsyncMock(return_value={})
    monkeypatch.setattr("modules.scan_areas.ensure", AsyncMock())
    monkeypatch.setattr(
        "modules.spawn_history.leaderboard",
        AsyncMock(
            return_value={"metadata": {"available": True, "complete_window": True}}
        ),
    )
    monkeypatch.setattr("modules.shiny_leaderboard.render_report", lambda *args: b"png")
    ctx = MagicMock(send=AsyncMock())
    await cog.image_report.callback(cog, ctx)
    cog.poliswag.get_user.assert_called_once_with(123)
    cog.poliswag.fetch_user.assert_awaited_once_with(123)
    cog.poliswag.get_channel.assert_not_called()
    owner.send.assert_awaited_once()
    ctx.send.assert_not_awaited()


async def test_image_errors_are_reported_without_private_details(cog):
    ctx = MagicMock(send=AsyncMock())
    await cog.cog_command_error(ctx, RuntimeError("private details"))
    assert ctx.send.call_args.args[0] == "Não consegui ler o histórico de spawns."
    await cog.cog_command_error(ctx, ValueError("A recolha ainda não começou."))
    assert ctx.send.call_args.args[0] == "A recolha ainda não começou."
