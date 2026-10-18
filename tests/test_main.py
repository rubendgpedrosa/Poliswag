"""Tests for Poliswag.get_channels independent resolution."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from main import Poliswag
from modules.config import Config


def _make_bot(monkeypatch, ids):
    bot = Poliswag.__new__(Poliswag)
    bot.utility = MagicMock()
    bot.utility.log_to_file = MagicMock()
    bot.fetch_channel = AsyncMock()
    for attr in (
        "QUEST_CHANNEL",
        "CONVIVIO_CHANNEL",
        "MOD_CHANNEL",
        "ACCOUNTS_CHANNEL",
        "TRAP_CHANNEL",
        "EVENT_PANEL_CHANNEL",
    ):
        setattr(bot, attr, None)
    monkeypatch.setattr(Config, "QUEST_CHANNEL_ID", ids.get("QUEST_CHANNEL", 1))
    monkeypatch.setattr(Config, "CONVIVIO_CHANNEL_ID", ids.get("CONVIVIO_CHANNEL", 2))
    monkeypatch.setattr(Config, "MOD_CHANNEL_ID", ids.get("MOD_CHANNEL", 3))
    monkeypatch.setattr(Config, "ACCOUNTS_CHANNEL_ID", ids.get("ACCOUNTS_CHANNEL", 4))
    monkeypatch.setattr(Config, "TRAP_CHANNEL_ID", ids.get("TRAP_CHANNEL", 5))
    monkeypatch.setattr(
        Config, "EVENT_PANEL_CHANNEL_ID", ids.get("EVENT_PANEL_CHANNEL", 6)
    )
    return bot


def _discord_error(kind, message="boom"):
    response = MagicMock(status=403)
    if kind == "forbidden":
        return discord.Forbidden(response, message)
    if kind == "notfound":
        response.status = 404
        return discord.NotFound(response, message)
    return discord.HTTPException(response, message)


@pytest.mark.asyncio
async def test_first_fetch_failure_still_resolves_later_channels(monkeypatch):
    bot = _make_bot(monkeypatch, {})
    sentinel = object()
    bot.fetch_channel = AsyncMock(
        side_effect=[
            _discord_error("forbidden"),
            sentinel,
            sentinel,
            sentinel,
            sentinel,
            sentinel,
        ]
    )
    await bot.get_channels()
    assert bot.QUEST_CHANNEL is None
    assert bot.CONVIVIO_CHANNEL is sentinel
    assert bot.fetch_channel.await_count == 6
    logged = " ".join(str(c.args) for c in bot.utility.log_to_file.call_args_list)
    assert "QUEST_CHANNEL" in logged


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["forbidden", "notfound", "http"])
async def test_each_lookup_failure_clears_and_continues(monkeypatch, kind):
    bot = _make_bot(monkeypatch, {})
    channels = [object() for _ in range(6)]
    bot.fetch_channel = AsyncMock(
        side_effect=[
            channels[0],
            _discord_error(kind),
            channels[2],
            channels[3],
            channels[4],
            channels[5],
        ]
    )
    await bot.get_channels()
    assert bot.QUEST_CHANNEL is channels[0]
    assert bot.CONVIVIO_CHANNEL is None
    assert bot.MOD_CHANNEL is channels[2]
    assert bot.fetch_channel.await_count == 6


@pytest.mark.asyncio
async def test_unset_ids_clear_and_continue(monkeypatch):
    bot = _make_bot(
        monkeypatch,
        {"QUEST_CHANNEL": 0, "CONVIVIO_CHANNEL": 2, "MOD_CHANNEL": 0},
    )
    bot.QUEST_CHANNEL = MagicMock()
    bot.MOD_CHANNEL = MagicMock()
    resolved = object()
    bot.fetch_channel = AsyncMock(return_value=resolved)
    await bot.get_channels()
    assert bot.QUEST_CHANNEL is None
    assert bot.CONVIVIO_CHANNEL is resolved
    assert bot.MOD_CHANNEL is None


@pytest.mark.asyncio
async def test_reconnect_failure_clears_previously_resolved_channel(monkeypatch):
    bot = _make_bot(monkeypatch, {})
    first = object()
    bot.fetch_channel = AsyncMock(return_value=first)
    await bot.get_channels()
    assert bot.QUEST_CHANNEL is first
    bot.fetch_channel = AsyncMock(
        side_effect=[_discord_error("notfound")] + [object() for _ in range(5)]
    )
    await bot.get_channels()
    assert bot.QUEST_CHANNEL is None
    assert bot.CONVIVIO_CHANNEL is not None


@pytest.mark.asyncio
async def test_cancellation_propagates(monkeypatch):
    bot = _make_bot(monkeypatch, {})
    bot.fetch_channel = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await bot.get_channels()


@pytest.mark.asyncio
async def test_programmer_error_propagates(monkeypatch):
    bot = _make_bot(monkeypatch, {})
    bot.fetch_channel = AsyncMock(side_effect=RuntimeError("programmer bug"))
    with pytest.raises(RuntimeError):
        await bot.get_channels()


@pytest.mark.asyncio
async def test_log_mentions_channel_without_credentials(monkeypatch):
    bot = _make_bot(monkeypatch, {"QUEST_CHANNEL": 12345})
    bot.fetch_channel = AsyncMock(side_effect=_discord_error("forbidden", "no access"))
    await bot.get_channels()
    assert bot.utility.log_to_file.called
    logged_text = " ".join(
        str(a) for c in bot.utility.log_to_file.call_args_list for a in c.args
    )
    assert "QUEST_CHANNEL" in logged_text
    assert "DISCORD_API_KEY" not in logged_text
    assert "token" not in logged_text.lower()
