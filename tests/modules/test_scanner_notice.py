"""Tests for modules.scanner_notice: one live scanner card in the mod channel."""

from types import SimpleNamespace as N
from unittest.mock import AsyncMock, MagicMock

import discord

from modules.scanner_notice import FOOTER, post_scanner_status

BOT = N(id=1)


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
    channel.send = AsyncMock()
    channel.history = MagicMock(return_value=_History(latest))
    return channel


def _card(author_id=1, footer=f"{FOOTER} · atualizado 09/11 08:00"):
    embed = discord.Embed(title="old")
    embed.set_footer(text=footer)
    message = MagicMock()
    message.author = N(id=author_id)
    message.embeds = [embed]
    message.edit = AsyncMock()
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
