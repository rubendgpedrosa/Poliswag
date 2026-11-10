"""Tests for modules.giveaways and cogs.giveaways."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs import giveaways as cog_module
from cogs.giveaways import Giveaways
from modules import giveaways

EMOJI = "<:pikaswag:596741274178945036>"
ANNOUNCEMENT = (
    "@everyone Boas treinadores! 👋\n\nVamos sortear um código de acesso, basta "
    f"colocar o emoji {EMOJI} . O sorteio vai realizar-se em <t:1794348000:f>."
)
LINK = "https://discord.com/channels/329321552241754115/378108892816867329/1570000000000000000"
GIVEAWAY = 1570000000000000000
REVIEW = 1600000000000000000
AUTHOR = 50
MOD = 77


# --- module ----------------------------------------------------------------


def test_parse_link():
    assert giveaways.parse_link(f"olha {LINK} aqui") == (378108892816867329, GIVEAWAY)
    assert giveaways.parse_link("https://ptb.discord.com/channels/1/22/333") == (
        22,
        333,
    )
    assert giveaways.parse_link("sem link") is None
    assert giveaways.parse_links(f"{LINK} e https://discord.com/channels/1/2/3") == [
        (378108892816867329, GIVEAWAY),
        (2, 3),
    ]


def test_parse_draw_at_and_emoji_from_the_announcement():
    assert giveaways.parse_draw_at(ANNOUNCEMENT) == datetime(2026, 11, 10, 22, 0)
    assert giveaways.parse_draw_at("1794348000") == datetime(2026, 11, 10, 22, 0)
    assert giveaways.parse_draw_at("amanhã") is None
    assert giveaways.find_emoji(ANNOUNCEMENT) == EMOJI
    assert giveaways.find_emoji("só 🎉") is None
    assert giveaways.unix(datetime(2026, 11, 10, 22, 0)) == 1794348000


def test_entries_read_the_old_comma_lists_and_json():
    assert giveaways.entries("5,7") == [5, 7]
    assert giveaways.entries(None) == []
    assert giveaways.entries('[5, "Nuno"]') == [5, "Nuno"]
    assert giveaways.dump([5, "Capitão"]) == '[5, "Capitão"]'
    assert giveaways.dump([]) is None
    assert giveaways.winner_ids({"winner_ids": "5,7"}) == [5, 7]


def test_merge_names_ignores_case_and_spaces():
    assert giveaways.merge_names(
        ["Ana", "Scooby Jew"], ["ana", "ScoobyJew", "Nuno"]
    ) == [
        "Ana",
        "Scooby Jew",
        "Nuno",
    ]


def test_pool_counts_a_campfire_name_that_reacted_once():
    aliases = {"ricmat": 1, "redpt": 2}
    pool, duplicates = giveaways.pool(
        [1, 2, 3], ["Ricmat", "Nuno", "nuno", "Host"], aliases, {"host"}
    )
    assert pool == [1, 2, 3, "Nuno"]
    assert duplicates == ["Ricmat"]


def test_pick_never_repeats_a_winner_and_caps_at_the_pool():
    assert giveaways.pick([1, 2, 3], 1, exclude=[1, 2]) == [3]
    assert sorted(giveaways.pick([1, 2], 5)) == [1, 2]
    assert giveaways.pick([1], 1, exclude=[1]) == []
    # A normal reroll avoids who was showing, unless nobody else is left.
    assert giveaways.pick([1, 2], 1, avoid=[1]) == [2]
    assert giveaways.pick([1], 1, avoid=[1]) == [1]


def test_guarded_updates():
    for statement in (giveaways.REROLL_SQL, giveaways.PUBLISH_SQL):
        assert "review_message_id = %s" in statement
        assert statement.rstrip().endswith("pending_ids <=> %s")
    assert giveaways.REVIEW_SQL.rstrip().endswith("AND awaiting_review = %s")


# --- cog -------------------------------------------------------------------


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


def _user(uid, bot=False, name=None):
    user = MagicMock()
    user.id = uid
    user.bot = bot
    user.nick = None
    user.global_name = name
    user.display_name = name or f"user{uid}"
    user.name = name or f"user{uid}"
    return user


def _row(**extra):
    row = {
        "message_id": GIVEAWAY,
        "channel_id": 378108892816867329,
        "guild_id": 329321552241754115,
        "emoji": EMOJI,
        "draw_at": datetime(2026, 11, 10, 22, 0),
        "winners": 1,
        "status": "open",
        "winner_ids": None,
        "created_by": 99,
        "awaiting_review": 0,
        "pending_ids": None,
        "excluded_ids": None,
        "review_message_id": None,
        "campfire_names": None,
        "participants": None,
    }
    row.update(extra)
    return row


def _cog(users, row=None, author_bot=False, changed=1, trainers=()):
    bot = MagicMock()
    bot.utility.log_to_file = MagicMock()
    bot.ADMIN_USERS_IDS = [str(MOD)]
    bot.db.execute_query_to_database = AsyncMock(return_value=changed)

    async def select(query, params=None, retries=3):
        if query == giveaways.TRAINERS_SQL:
            return [{"discord_id": d, "trainer_name": n} for d, n in trainers]
        if query == giveaways.SELECT_SQL:
            return [row] if row else []
        return []

    bot.db.get_data_from_database = AsyncMock(side_effect=select)
    reaction = MagicMock()
    reaction.emoji = EMOJI
    reaction.users = MagicMock(side_effect=lambda limit=None: _Users(users))
    other = MagicMock()
    other.emoji = "👍"
    message = MagicMock()
    message.author = _user(AUTHOR, bot=author_bot, name="Rekrad")
    message.reactions = [other, reaction]
    message.guild.chunked = True
    message.guild.get_member = MagicMock(
        side_effect=lambda uid: None if uid == 404 else object()
    )
    message.reply = AsyncMock(return_value=SimpleNamespace(id=888))
    message.delete = AsyncMock()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    message.channel = channel
    mod_channel = MagicMock()
    mod_channel.send = AsyncMock(return_value=SimpleNamespace(id=REVIEW))
    bot.MOD_CHANNEL = mod_channel
    bot.get_channel = MagicMock(return_value=channel)
    return Giveaways(bot), bot, message, mod_channel


def _calls(bot, sql):
    return [
        c.kwargs["params"]
        for c in bot.db.execute_query_to_database.call_args_list
        if c.args[0] == sql
    ]


async def test_participants_skip_bots_the_creator_and_leavers():
    cog, _, _, _ = _cog(
        [_user(1), _user(2, bot=True), _user(99), _user(AUTHOR), _user(404), _user(3)]
    )
    _, ids, names = await cog.participants(_row())
    assert ids == [1, 3]
    assert set(names) == {1, 3}


async def test_campfire_names_join_the_pool_once():
    row = _row(campfire_names=json.dumps(["Ricmat", "Nuno", "Rekrad", "Alfa"]))
    cog, _, _, _ = _cog([_user(1, name="Ricmat"), _user(2)], trainers=[(2, "Alfa")])
    _, pool, ids, duplicates = await cog.entries(row)
    # Ricmat reacted (Discord name), Alfa reacted (linked trainer name), and
    # Rekrad wrote the announcement.
    assert pool == [1, 2, "Nuno"]
    assert ids == [1, 2]
    assert duplicates == ["Ricmat", "Alfa"]


async def test_draw_only_posts_a_review_in_moderation():
    cog, bot, message, mod_channel = _cog([_user(7)])
    assert await cog.start_review(_row()) == [7]
    (params,) = _calls(bot, giveaways.REVIEW_SQL)
    assert params == ("[7]", None, 1, GIVEAWAY, "open", 0)
    message.reply.assert_not_awaited()
    sent = mod_channel.send.call_args.kwargs
    ids = [item.custom_id for item in sent["view"].children]
    assert ids == [f"sorteio:{a}:{GIVEAWAY}" for a in cog_module._ACTIONS]
    assert sent["view"].timeout is None
    assert "<@7>" in sent["embed"].description
    assert _calls(bot, giveaways.REVIEW_MESSAGE_SQL) == [(REVIEW, GIVEAWAY)]


async def test_draw_that_lost_the_race_posts_nothing():
    cog, _, _, mod_channel = _cog([_user(7)], changed=0)
    assert await cog.start_review(_row()) is None
    mod_channel.send.assert_not_awaited()


def _interaction(user_id, action):
    interaction = MagicMock()
    interaction.data = {"custom_id": f"sorteio:{action}:{GIVEAWAY}"}
    interaction.user = _user(user_id)
    interaction.message.id = REVIEW
    interaction.message.embeds = [MagicMock()]
    interaction.message.edit = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _review_row(**extra):
    values = {
        "status": "drawn",
        "awaiting_review": 1,
        "pending_ids": "[7]",
        "review_message_id": REVIEW,
        "participants": 2,
    }
    values.update(extra)
    return _row(**values)


async def test_only_the_announcer_can_publish():
    cog, bot, message, _ = _cog([_user(7), _user(8)], row=_review_row())
    interaction = _interaction(MOD, "publish")
    await cog.on_interaction(interaction)
    assert "Só <@50>" in interaction.followup.send.call_args.args[0]
    message.reply.assert_not_awaited()

    interaction = _interaction(AUTHOR, "publish")
    await cog.on_interaction(interaction)
    assert _calls(bot, giveaways.PUBLISH_SQL) == [
        ("[7]", None, GIVEAWAY, REVIEW, "[7]")
    ]
    assert _calls(bot, giveaways.REPLY_SQL) == [(888, GIVEAWAY)]
    message.delete.assert_not_awaited()
    reply = message.reply.call_args.kwargs
    assert reply["content"] == "🎉 <@7>"
    assert reply["allowed_mentions"].everyone is False
    assert interaction.message.edit.call_args.kwargs["view"] is None


async def test_mods_publish_when_poliswag_wrote_the_announcement():
    cog, bot, message, _ = _cog([_user(7)], row=_review_row(), author_bot=True)
    await cog.on_interaction(_interaction(MOD, "publish"))
    message.reply.assert_awaited_once()
    assert "<@99>" in message.reply.call_args.kwargs["embed"].description


async def test_reroll_without_exclusion_and_with_it():
    cog, bot, _, _ = _cog([_user(7), _user(8)], row=_review_row())
    await cog.on_interaction(_interaction(MOD, "reroll"))
    assert _calls(bot, giveaways.REROLL_SQL) == [
        ("[8]", None, 2, GIVEAWAY, REVIEW, "[7]")
    ]

    cog, bot, _, _ = _cog([_user(7), _user(8)], row=_review_row())
    await cog.on_interaction(_interaction(AUTHOR, "exclude"))
    assert _calls(bot, giveaways.REROLL_SQL) == [
        ("[8]", "[7]", 2, GIVEAWAY, REVIEW, "[7]")
    ]


async def test_members_cannot_press_and_old_reviews_are_dead():
    cog, bot, _, _ = _cog([_user(7), _user(8)], row=_review_row())
    interaction = _interaction(12345, "stop")
    await cog.on_interaction(interaction)
    assert _calls(bot, giveaways.STOP_SQL) == []
    assert "mods" in interaction.followup.send.call_args.args[0]

    cog, bot, _, _ = _cog([_user(7)], row=_review_row(review_message_id=1))
    interaction = _interaction(AUTHOR, "publish")
    await cog.on_interaction(interaction)
    assert _calls(bot, giveaways.PUBLISH_SQL) == []
    assert "já não está ativa" in interaction.followup.send.call_args.args[0]


async def test_cancel_button_stops_the_review():
    cog, bot, message, _ = _cog([_user(7)], row=_review_row())
    interaction = _interaction(MOD, "stop")
    await cog.on_interaction(interaction)
    assert _calls(bot, giveaways.STOP_SQL) == [(GIVEAWAY, REVIEW)]
    message.reply.assert_not_awaited()


async def test_reroll_after_publishing_reopens_a_review_for_one_more():
    row = _row(status="drawn", winner_ids="7", winners=1)
    cog, bot, _, mod_channel = _cog([_user(7), _user(8)], row=row)
    assert await cog.start_review(row, replacing=[7]) == [8]
    assert _calls(bot, giveaways.REVIEW_SQL) == [
        ("[8]", "[7]", 2, GIVEAWAY, "drawn", 0)
    ]
    assert (
        "A substituir: <@7>" in mod_channel.send.call_args.kwargs["embed"].description
    )
    assert mod_channel.send.call_args.kwargs["embed"].title.startswith("🔁")


async def test_announcing_a_campfire_winner_pings_nobody():
    cog, _, message, _ = _cog([])
    await cog._announce(message, _row(), ["Nuno"], ["Nuno"], 3)
    reply = message.reply.call_args.kwargs
    assert reply["content"] is None
    assert "**Nuno** (Campfire)" in reply["embed"].description


@pytest.mark.parametrize(
    "content_type, filename, expected",
    [
        ("image/png", "a.png", "image/png"),
        (None, "captura.JPG", "image/jpeg"),
        ("application/pdf", "a.pdf", None),
    ],
)
def test_media_type(content_type, filename, expected):
    attachment = SimpleNamespace(content_type=content_type, filename=filename)
    assert cog_module._media_type(attachment) == expected


async def test_lista_shows_discord_and_campfire_names_without_pings():
    row = _row(campfire_names=json.dumps(["Nuno", "Ricmat"]))
    cog, _, _, _ = _cog([_user(1, name="Ricmat"), _user(2, name="Ana")], row=row)
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.message.reference = None
    await cog.lista.callback(cog, ctx, texto=LINK)
    sent = ctx.send.call_args.kwargs
    assert sent["allowed_mentions"].users is False
    text = sent["embed"].description
    assert "**3** participantes: 2 pela reação" in text
    assert "1. Ricmat (Discord)\n2. Ana (Discord)\n3. Nuno (Campfire)" in text


async def test_publishing_a_replacement_swaps_the_public_reply():
    row = _review_row(
        winner_ids="[7]",
        replacing_ids="[7]",
        pending_ids="[8]",
        announcement_reply_id=555,
    )
    cog, bot, message, _ = _cog([_user(7), _user(8)], row=row)
    await cog.on_interaction(_interaction(AUTHOR, "publish"))
    # 7 already had the prize: out of the winners, into the excluded.
    assert _calls(bot, giveaways.PUBLISH_SQL) == [
        ("[8]", "[7]", GIVEAWAY, REVIEW, "[8]")
    ]
    bot.get_channel.return_value.fetch_message.assert_awaited_with(555)
    message.delete.assert_awaited_once()
    reply = message.reply.call_args.kwargs
    assert reply["content"] == "🎉 <@8>"
    assert reply["embed"].title == "🎉 Sorteio terminado!"
    assert "<@7>" not in reply["embed"].description
    assert _calls(bot, giveaways.REPLY_SQL) == [(888, GIVEAWAY)]


def _ctx(mentions=()):
    ctx = MagicMock()
    ctx.message.mentions = [SimpleNamespace(id=m) for m in mentions]
    return ctx


def test_who_to_replace():
    assert cog_module._who_to_replace(_ctx(), "", [7]) == [7]
    assert cog_module._who_to_replace(_ctx(), "", [7, 8]) is None
    assert cog_module._who_to_replace(_ctx([8]), "<@8>", [7, 8]) == [8]
    assert cog_module._who_to_replace(_ctx(), f"{LINK} nuno", [7, "Nuno"]) == ["Nuno"]


async def test_reroll_with_several_winners_asks_who():
    row = _row(status="drawn", winner_ids="[7, 8]", winners=2)
    cog, bot, _, mod_channel = _cog([_user(7), _user(8), _user(9)], row=row)
    ctx = _ctx()
    ctx.send = AsyncMock()
    ctx.message.reference = None
    await cog.reroll.callback(cog, ctx, texto=LINK)
    assert ctx.send.call_args.kwargs["embed"].title == "❓ Quem substituir?"
    assert _calls(bot, giveaways.REVIEW_SQL) == []
    mod_channel.send.assert_not_awaited()
