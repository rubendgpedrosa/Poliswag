import gzip
import io
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.spawn_history import SpawnHistory
from modules.permissions import is_owner


@pytest.fixture
def cog(monkeypatch):
    monkeypatch.setattr("modules.config.Config.MY_ID", 123)
    return SpawnHistory(MagicMock())


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


async def test_export_sends_valid_json_file_to_request_channel(cog, monkeypatch):
    body = {
        "metadata": {"complete_window": False},
        "spawns": [{"encounter_id": "18446744073709551614"}],
    }
    output = io.BytesIO(gzip.compress(json.dumps(body).encode()))
    monkeypatch.setattr("modules.spawn_history.export_json", lambda hours: output)
    captured = []

    async def send(*args, **kwargs):
        captured.append(json.loads(gzip.decompress(kwargs["file"].fp.read())))
        assert kwargs["file"].filename == "spawns-24h.json.gz"
        assert not kwargs["allowed_mentions"].everyone

    ctx = MagicMock(send=AsyncMock(side_effect=send))
    await cog.export.callback(cog, ctx, 24)
    assert captured == [body] and output.closed
