from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.pokedex import Pokedex
from modules.trade_codes import is_valid, normalise


@pytest.fixture
def poliswag():
    bot = MagicMock()
    bot.trade_player_store = MagicMock(
        upsert=AsyncMock(),
        refresh_identity=AsyncMock(),
        set_left=AsyncMock(),
        clear_left=AsyncMock(),
        reconcile=AsyncMock(return_value=([], [])),
    )
    # The default caller is a member, which is what every test but the
    # membership ones is about.
    bot.guilds = [guild_with(123)]
    return bot


@pytest.fixture
def cog(poliswag):
    return Pokedex(poliswag)


def ctx_for(author, in_guild=True):
    ctx = MagicMock()
    ctx.author = author
    ctx.send = AsyncMock()
    ctx.guild = MagicMock() if in_guild else None
    ctx.message = MagicMock(delete=AsyncMock())
    return ctx


def guild_with(*member_ids):
    """A guild whose member cache holds exactly these ids, and nobody else."""
    members = {i: member(i) for i in member_ids}
    guild = MagicMock()
    guild.members = list(members.values())
    guild.get_member = lambda user_id: members.get(user_id)
    return guild


def member(discord_id=123, name="jmboyz", display_name="JMBoyz"):
    m = MagicMock()
    m.id = discord_id
    m.name = name
    m.display_name = display_name
    m.display_avatar.url = "https://cdn/a.png"
    m.send = AsyncMock()
    return m


async def test_trocas_dms_a_valid_code_and_stores_its_hash(cog, poliswag):
    author = member()
    await Pokedex.pokedex.callback(cog, ctx_for(author))

    # The code now travels alone, in its own copyable message.
    code = author.send.call_args_list[1].args[0]
    assert is_valid(code)
    poliswag.trade_player_store.upsert.assert_awaited_once()
    kwargs = poliswag.trade_player_store.upsert.await_args[1]
    assert kwargs["discord_id"] == 123
    assert kwargs["username"] == "jmboyz"
    # The code itself is never stored.
    assert normalise(code) not in str(kwargs)
    assert len(kwargs["code_hash"]) == 64


async def test_trocas_dm_includes_the_login_link(cog):
    author = member()
    await Pokedex.pokedex.callback(cog, ctx_for(author))
    assert "/entrar/" in author.send.call_args_list[0].kwargs["embed"].description


async def test_trocas_confirms_in_channel_without_the_code(cog):
    author = member()
    ctx = ctx_for(author)
    await Pokedex.pokedex.callback(cog, ctx)
    reply = ctx.send.call_args[1]["embed"].description
    assert not any(is_valid(word) for word in reply.split())


async def test_trocas_reports_closed_dms_and_stores_nothing(cog, poliswag):
    author = member()
    author.send.side_effect = discord.Forbidden(MagicMock(status=403), "closed")
    ctx = ctx_for(author)

    await Pokedex.pokedex.callback(cog, ctx)

    poliswag.trade_player_store.upsert.assert_not_awaited()
    assert "DM" in ctx.send.call_args[1]["embed"].description


async def test_member_remove_marks_them_as_left(cog, poliswag):
    await cog.on_member_remove(member())
    poliswag.trade_player_store.set_left.assert_awaited_once_with(123)


async def test_member_join_clears_left(cog, poliswag):
    await cog.on_member_join(member())
    poliswag.trade_player_store.clear_left.assert_awaited_once_with(123)


async def test_member_update_refreshes_identity(cog, poliswag):
    await cog.on_member_update(member(), member(name="jm", display_name="JM"))
    poliswag.trade_player_store.refresh_identity.assert_awaited_once_with(
        123, "jm", "JM", "https://cdn/a.png"
    )


async def test_reconcile_passes_the_guild_member_ids(cog, poliswag):
    guild = MagicMock()
    guild.members = [member(1), member(2)]
    poliswag.guilds = [guild]

    await cog.reconcile()

    poliswag.trade_player_store.reconcile.assert_awaited_once_with({1, 2})


async def test_trocas_deletes_the_command_message_in_a_channel(cog):
    ctx = ctx_for(member())
    await Pokedex.pokedex.callback(cog, ctx)
    ctx.message.delete.assert_awaited_once()


async def test_trocas_deletes_the_command_before_sending_the_dm(cog):
    """The code must never sit beside a visible !trocas in the channel."""
    order = []
    author = member()
    ctx = ctx_for(author)
    ctx.message.delete.side_effect = lambda *a, **k: order.append("delete")
    author.send.side_effect = lambda *a, **k: order.append("dm")

    await Pokedex.pokedex.callback(cog, ctx)

    assert order[0] == "delete"
    assert order[1:] == ["dm", "dm"]


async def test_trocas_survives_missing_manage_messages(cog, poliswag):
    ctx = ctx_for(member())
    ctx.message.delete.side_effect = discord.Forbidden(
        MagicMock(status=403), "no perms"
    )

    await Pokedex.pokedex.callback(cog, ctx)

    poliswag.trade_player_store.upsert.assert_awaited_once()


async def test_trocas_channel_confirmation_self_destructs(cog):
    ctx = ctx_for(member())
    await Pokedex.pokedex.callback(cog, ctx)
    assert ctx.send.call_args[1]["delete_after"] > 0


async def test_trocas_in_a_dm_deletes_nothing_and_adds_no_second_message(cog, poliswag):
    author = member()
    ctx = ctx_for(author, in_guild=False)

    await Pokedex.pokedex.callback(cog, ctx)

    ctx.message.delete.assert_not_awaited()
    ctx.send.assert_not_awaited()
    # Explanation plus the bare code, and nothing in the channel.
    assert author.send.await_count == 2
    poliswag.trade_player_store.upsert.assert_awaited_once()


async def test_trocas_in_a_dm_reports_nothing_to_delete_on_forbidden(cog):
    """A DM channel message can't be deleted by the bot; it must not crash."""
    author = member()
    ctx = ctx_for(author, in_guild=False)
    ctx.message.delete.side_effect = AssertionError("must not be called in a DM")

    await Pokedex.pokedex.callback(cog, ctx)

    assert author.send.await_count == 2


async def test_the_code_arrives_in_a_message_of_its_own(cog):
    """Long-press -> Copy Text must yield the code and nothing else."""
    author = member()
    await Pokedex.pokedex.callback(cog, ctx_for(author))

    bare = [
        call.args[0]
        for call in author.send.call_args_list
        if call.args and isinstance(call.args[0], str)
    ]
    assert len(bare) == 1
    assert is_valid(bare[0])
    assert bare[0] == bare[0].strip()
    assert "`" not in bare[0]


async def test_the_explanation_arrives_before_the_code(cog):
    author = member()
    await Pokedex.pokedex.callback(cog, ctx_for(author))

    first, second = author.send.call_args_list
    assert "embed" in first.kwargs
    assert second.args and isinstance(second.args[0], str)


async def test_the_explanation_carries_the_login_link_not_the_code(cog):
    author = member()
    await Pokedex.pokedex.callback(cog, ctx_for(author))

    described = author.send.call_args_list[0].kwargs["embed"].description
    assert "/entrar/" in described
    # The dashed code belongs to the copyable message; only the link's own
    # undashed copy may appear here.
    assert not any(is_valid(word) and "-" in word for word in described.split())


def test_trocas_still_reaches_the_command():
    """Half the server learned !trocas on day one; it must keep working."""
    assert Pokedex.pokedex.name == "pokedex"
    assert {"trades", "trocas"} <= set(Pokedex.pokedex.aliases)


async def test_trocas_in_a_dm_refuses_someone_who_left_the_server(cog, poliswag):
    """The DM channel outlives the membership, so the command must not.

    Without this, upsert() would clear left_at and hand a departed — or
    removed — player a working login straight back.
    """
    author = member(999)
    poliswag.guilds = [guild_with(123)]
    ctx = ctx_for(author, in_guild=False)

    await Pokedex.pokedex.callback(cog, ctx)

    author.send.assert_not_awaited()
    poliswag.trade_player_store.upsert.assert_not_awaited()
    ctx.send.assert_awaited_once()


async def test_trocas_in_a_channel_never_asks_about_membership(cog, poliswag):
    """Posting in a guild channel is the proof; the cache isn't consulted."""
    author = member(999)
    poliswag.guilds = [guild_with(123)]

    await Pokedex.pokedex.callback(cog, ctx_for(author))

    poliswag.trade_player_store.upsert.assert_awaited_once()


async def test_trocas_in_a_dm_proceeds_when_no_members_are_cached(cog, poliswag):
    """An empty cache is the bot's problem, not the player's — fail open."""
    author = member(999)
    poliswag.guilds = []

    await Pokedex.pokedex.callback(cog, ctx_for(author, in_guild=False))

    poliswag.trade_player_store.upsert.assert_awaited_once()


# --- !resumo: the digest on demand, in a DM ---------------------------------
#
# The scheduled post is silent on a quiet day, which is correct and also means
# a working digest and a broken one look identical from the channel. This is
# how you tell them apart without waiting for 09:00.


def digest_row(**patch):
    out = {
        "discord_id": 1,
        "display_name": "Faynn",
        "list": "want",
        "category": "shiny",
        "pokemon_id": 60,
        "form_id": 0,
        "pokemon_name": "Poliwag",
        "form_name": None,
    }
    out.update(patch)
    return out


async def test_resumo_dms_the_digest(cog, monkeypatch):
    author = member()
    monkeypatch.setattr("cogs.pokedex.rows_since", lambda days: [digest_row()])

    await cog.resumo.callback(cog, ctx_for(author), 2)

    author.send.assert_awaited()
    embed = author.send.await_args.kwargs["embed"]
    assert "Poliwag" in embed.fields[0].value


async def test_resumo_says_so_when_there_is_nothing(cog, monkeypatch):
    author = member()
    monkeypatch.setattr("cogs.pokedex.rows_since", lambda days: [])

    await cog.resumo.callback(cog, ctx_for(author), 1)

    embed = author.send.await_args.kwargs["embed"]
    assert "sem novidades" in embed.description.lower()


async def test_resumo_keeps_the_window_sane(cog, monkeypatch):
    asked = []
    monkeypatch.setattr(
        "cogs.pokedex.rows_since", lambda days: asked.append(days) or []
    )

    await cog.resumo.callback(cog, ctx_for(member()), 0)
    await cog.resumo.callback(cog, ctx_for(member()), 999)

    assert asked == [1, 30]


async def test_resumo_is_for_members_only(cog, poliswag, monkeypatch):
    poliswag.guilds = [guild_with(456)]
    called = []
    monkeypatch.setattr(
        "cogs.pokedex.rows_since", lambda days: called.append(days) or []
    )
    author = member(123)
    ctx = ctx_for(author, in_guild=False)

    await cog.resumo.callback(cog, ctx, 1)

    assert called == []
    ctx.send.assert_awaited()


async def test_resumo_falls_back_to_the_channel_when_dms_are_shut(cog, monkeypatch):
    monkeypatch.setattr("cogs.pokedex.rows_since", lambda days: [digest_row()])
    author = member()
    author.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    ctx = ctx_for(author)

    await cog.resumo.callback(cog, ctx, 1)

    ctx.send.assert_awaited()
