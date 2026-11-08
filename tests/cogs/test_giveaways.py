"""Tests for modules.giveaways and cogs.giveaways."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.giveaways import Giveaways
from modules import giveaways

EMOJI = "<:pikaswag:596741274178945036>"
ANNOUNCEMENT = (
    "@everyone Boas treinadores! 👋\n\nVamos sortear um código de acesso, basta "
    f"colocar o emoji {EMOJI} . O sorteio vai realizar-se em <t:1794348000:f>."
)
LINK = "https://discord.com/channels/329321552241754115/378108892816867329/1570000000000000000"


def test_parse_link():
    assert giveaways.parse_link(f"olha {LINK} aqui") == (
        378108892816867329,
        1570000000000000000,
    )
    assert giveaways.parse_link("https://ptb.discord.com/channels/1/22/333") == (
        22,
        333,
    )
    assert giveaways.parse_link("sem link") is None


def test_parse_draw_at_and_emoji_from_the_announcement():
    assert giveaways.parse_draw_at(ANNOUNCEMENT) == datetime(2026, 11, 10, 22, 0)
    assert giveaways.parse_draw_at("1794348000") == datetime(2026, 11, 10, 22, 0)
    assert giveaways.parse_draw_at("amanhã") is None
    assert giveaways.find_emoji(ANNOUNCEMENT) == EMOJI
    assert giveaways.find_emoji("só 🎉") is None
    assert giveaways.unix(datetime(2026, 11, 10, 22, 0)) == 1794348000


def test_pick_never_repeats_a_winner_and_caps_at_the_pool():
    assert giveaways.pick([1, 2, 3], 1, exclude=[1, 2]) == [3]
    assert sorted(giveaways.pick([1, 2], 5)) == [1, 2]
    assert giveaways.pick([1], 1, exclude=[1]) == []
    assert giveaways.winner_ids({"winner_ids": "5,7"}) == [5, 7]
    assert giveaways.winner_ids({"winner_ids": None}) == []


class _Users:
    def __init__(self, users):
        self.users = users

    def __aiter__(self):
        self._it = iter(self.users)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _user(uid, bot=False):
    user = MagicMock()
    user.id = uid
    user.bot = bot
    return user


def _row(**extra):
    row = {
        "message_id": 1570000000000000000,
        "channel_id": 378108892816867329,
        "guild_id": 329321552241754115,
        "emoji": EMOJI,
        "draw_at": datetime(2026, 11, 10, 22, 0),
        "winners": 1,
        "status": "open",
        "winner_ids": None,
        "created_by": 99,
    }
    row.update(extra)
    return row


def _cog(users, closed=1):
    bot = MagicMock()
    bot.utility.log_to_file = MagicMock()
    bot.db.execute_query_to_database = AsyncMock(return_value=closed)
    bot.db.get_data_from_database = AsyncMock(return_value=[])
    reaction = MagicMock()
    reaction.emoji = EMOJI
    reaction.users = MagicMock(side_effect=lambda limit=None: _Users(users))
    other = MagicMock()
    other.emoji = "👍"
    message = MagicMock()
    message.author.id = 50
    message.reactions = [other, reaction]
    message.guild.chunked = True
    message.guild.get_member = MagicMock(
        side_effect=lambda uid: None if uid == 404 else object()
    )
    message.reply = AsyncMock()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    bot.get_channel = MagicMock(return_value=channel)
    creator = MagicMock()
    creator.send = AsyncMock()
    bot.get_user = MagicMock(return_value=creator)
    return Giveaways(bot), bot, message, creator


async def test_participants_skip_bots_the_creator_and_leavers():
    cog, _, _, _ = _cog(
        [_user(1), _user(2, bot=True), _user(99), _user(50), _user(404), _user(3)]
    )
    _, ids = await cog.participants(_row())
    assert ids == [1, 3]


async def test_draw_closes_announces_and_dms_the_creator():
    cog, bot, message, creator = _cog([_user(7)])
    assert await cog.draw(_row()) == [7]
    (sql,) = bot.db.execute_query_to_database.call_args.args
    params = bot.db.execute_query_to_database.call_args.kwargs["params"]
    assert sql == giveaways.CLOSE_SQL
    assert params == ("7", 1, 1570000000000000000, "open")
    reply = message.reply.call_args.kwargs
    assert reply["content"] == "🎉 <@7>"
    assert reply["allowed_mentions"].everyone is False
    assert "<@50>" in reply["embed"].description
    creator.send.assert_awaited_once()


async def test_draw_that_lost_the_race_announces_nothing():
    cog, _, message, creator = _cog([_user(7)], closed=0)
    assert await cog.draw(_row()) is None
    message.reply.assert_not_awaited()
    creator.send.assert_not_awaited()


async def test_reroll_skips_previous_winners():
    cog, bot, _, _ = _cog([_user(7), _user(8)])
    assert await cog.draw(_row(status="drawn", winner_ids="7"), reroll=True) == [8]
    params = bot.db.execute_query_to_database.call_args.kwargs["params"]
    assert params == ("7,8", 2, 1570000000000000000, "drawn")


async def test_no_participants_still_closes_without_a_winner():
    cog, bot, message, _ = _cog([])
    assert await cog.draw(_row()) == []
    assert bot.db.execute_query_to_database.call_args.kwargs["params"][0] is None
    assert message.reply.call_args.kwargs["content"] is None


@pytest.mark.parametrize("statement", [giveaways.CLOSE_SQL])
def test_close_is_guarded_by_the_expected_status(statement):
    assert statement.rstrip().endswith("AND status = %s")
