"""Tests for modules.scanner_notice: one live scanner card in the mod channel."""

from types import SimpleNamespace as N
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.config import Config
from modules.scanner_notice import FOOTER, post_scanner_status

BOT = N(id=1)


@pytest.fixture(autouse=True)
def card_file(tmp_path, mocker):
    mocker.patch.object(Config, "SCANNER_CARD_FILE", str(tmp_path / "card.json"))


class _History:
    def __init__(self, items):
        self.items = items

    def __aiter__(self):
        self._it = iter(self.items)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _channel(latest):
    channel = MagicMock()
    channel.send = AsyncMock(return_value=N(id=500))
    channel.history = MagicMock(return_value=_History(latest))
    return channel


def _card(author_id=1, footer=f"{FOOTER} · atualizado 09/11 08:00"):
    embed = discord.Embed(title="old")
    embed.set_footer(text=footer)
    message = MagicMock()
    message.author = N(id=author_id)
    message.embeds = [embed]
    message.edit = AsyncMock()
    message.delete = AsyncMock()
    message.id = 400
    return message


async def test_latest_scanner_card_is_edited_not_reposted():
    latest = _card()
    channel = _channel([latest])
    await post_scanner_status(N(user=BOT), channel, discord.Embed(title="new"))
    latest.edit.assert_awaited_once()
    channel.send.assert_not_awaited()
    assert latest.edit.await_args.kwargs["embed"].footer.text.startswith(FOOTER)


async def test_someone_wrote_after_it_so_a_new_card_is_posted():
    channel = _channel([_card(author_id=2)])
    await post_scanner_status(N(user=BOT), channel, discord.Embed(title="new"))
    channel.send.assert_awaited_once()


async def test_other_bot_embeds_are_not_reused():
    channel = _channel([_card(footer="Resultados do scan de quests")])
    await post_scanner_status(N(user=BOT), channel, discord.Embed(title="new"))
    channel.send.assert_awaited_once()


async def test_failed_edit_falls_back_to_a_new_card():
    latest = _card()
    latest.edit = AsyncMock(side_effect=discord.NotFound(MagicMock(), "gone"))
    channel = _channel([latest])
    await post_scanner_status(N(user=BOT), channel, discord.Embed(title="new"))
    channel.send.assert_awaited_once()


async def test_new_card_deletes_the_previous_one():
    old = _card()
    channel = _channel([_card(author_id=2)])
    channel.fetch_message = AsyncMock(return_value=old)
    await post_scanner_status(N(user=BOT), channel, discord.Embed(title="🔴 a"))
    channel.send = AsyncMock(return_value=N(id=501))
    channel.history = MagicMock(return_value=_History([_card(author_id=2)]))
    await post_scanner_status(N(user=BOT), channel, discord.Embed(title="🔴 b"))
    channel.fetch_message.assert_awaited_once_with(500)
    old.delete.assert_awaited_once()


async def test_incident_start_is_kept_until_the_map_is_back(mocker):
    clock = mocker.patch("modules.scanner_notice.time.time", return_value=1_000_000)
    channel = _channel([])
    first = discord.Embed(title="🔴 Mapa em baixo")
    await post_scanner_status(N(user=BOT), channel, first)
    clock.return_value = 1_000_000 + 3600
    later = discord.Embed(title="⚠️ ainda em baixo")
    channel.history = MagicMock(return_value=_History([]))
    await post_scanner_status(N(user=BOT), channel, later)
    assert first.fields[0].value == later.fields[0].value
    clock.return_value = 1_000_000 + 2 * 3600 + 300
    back = discord.Embed(title="🟢 Mapa de volta")
    channel.history = MagicMock(return_value=_History([]))
    await post_scanner_status(N(user=BOT), channel, back)
    assert back.fields[0].name == "Esteve em baixo"
    assert back.fields[0].value.startswith("2 h 05 min")
