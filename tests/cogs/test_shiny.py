from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.shiny import Shiny


def guild_with(*member_ids):
    guild = MagicMock()
    guild.members = [MagicMock(id=i) for i in member_ids]
    guild.get_member = lambda user_id: user_id if user_id in member_ids else None
    return guild


@pytest.fixture
def cog():
    bot = MagicMock()
    bot.guilds = [guild_with(123)]
    c = Shiny(bot)
    c.store = MagicMock(
        get=AsyncMock(return_value=None), request=AsyncMock(), remove=AsyncMock()
    )
    return c


def ctx_for(author_id=123, name="rita", in_guild=True):
    ctx = MagicMock()
    ctx.author = MagicMock(id=author_id)
    ctx.author.name = name
    ctx.send = AsyncMock()
    ctx.guild = MagicMock() if in_guild else None
    return ctx


def said(ctx):
    return ctx.send.call_args.kwargs["embed"].description


async def test_a_code_is_queued_however_it_was_typed(cog):
    ctx = ctx_for()
    await Shiny.shiny.callback(cog, ctx, texto="1234 5678-9012")
    cog.store.request.assert_awaited_once_with(123, "rita", "123456789012")
    assert "raid ativa" in said(ctx) and "convite de raid" in said(ctx)


async def test_something_that_is_not_a_code_is_refused(cog):
    ctx = ctx_for()
    await Shiny.shiny.callback(cog, ctx, texto="12345")
    cog.store.request.assert_not_awaited()
    assert "12 dígitos" in said(ctx)


async def test_remover_deletes_the_link(cog):
    ctx = ctx_for()
    await Shiny.shiny.callback(cog, ctx, texto="Remover")
    cog.store.remove.assert_awaited_once_with(123)
    assert "Apaguei" in said(ctx)


@pytest.mark.parametrize(
    "row, expected",
    [
        (None, "Ainda não enviaste"),
        ({"status": "pending", "trainer_name": None, "fail_reason": None}, "em fila"),
        (
            {"status": "linked", "trainer_name": "Rita99", "fail_reason": None},
            "**Rita99**",
        ),
        (
            {
                "status": "failed",
                "trainer_name": None,
                "fail_reason": "código não encontrado",
            },
            "código não encontrado",
        ),
    ],
)
async def test_bare_command_reports_the_status(cog, row, expected):
    cog.store.get.return_value = row
    ctx = ctx_for()
    await Shiny.shiny.callback(cog, ctx, texto="")
    assert expected in said(ctx)


async def test_a_dm_from_someone_who_left_the_server_is_turned_away(cog):
    ctx = ctx_for(author_id=999, in_guild=False)
    await Shiny.shiny.callback(cog, ctx, texto="123456789012")
    cog.store.request.assert_not_awaited()
    assert "membros do servidor" in said(ctx)


async def test_a_dm_from_a_member_works(cog):
    ctx = ctx_for(author_id=123, in_guild=False)
    await Shiny.shiny.callback(cog, ctx, texto="123456789012")
    cog.store.request.assert_awaited_once()
