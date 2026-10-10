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


def press(custom_id, user_id=123):
    interaction = MagicMock()
    interaction.data = {"custom_id": custom_id}
    interaction.user = MagicMock(id=user_id)
    interaction.message.embeds = [MagicMock()]
    interaction.response.edit_message = AsyncMock()
    return interaction


async def test_a_verdict_is_stored_for_whoever_pressed(cog):
    cog.store.set_verdict = AsyncMock(return_value=True)
    interaction = press("shiny:s:10000000000000000168")
    await cog.on_interaction(interaction)
    cog.store.set_verdict.assert_awaited_once_with(123, "10000000000000000168", "shiny")
    edited = interaction.response.edit_message.call_args.kwargs
    assert edited["view"] is None
    edited["embed"].set_footer.assert_called_once_with(
        text="Resposta registada: era shiny ✅"
    )


async def test_nao_era_is_stored_as_not_shiny(cog):
    cog.store.set_verdict = AsyncMock(return_value=True)
    await cog.on_interaction(press("shiny:n:42"))
    cog.store.set_verdict.assert_awaited_once_with(123, "42", "not_shiny")


async def test_a_second_answer_changes_nothing(cog):
    cog.store.set_verdict = AsyncMock(return_value=False)
    interaction = press("shiny:n:42")
    await cog.on_interaction(interaction)
    interaction.response.edit_message.call_args.kwargs[
        "embed"
    ].set_footer.assert_called_once_with(text="Já tinhas respondido a este alerta.")


@pytest.mark.parametrize("custom_id", ["Leiria", "shiny:x:42", "shiny:s", "Alertas1"])
async def test_other_buttons_are_left_alone(cog, custom_id):
    cog.store.set_verdict = AsyncMock()
    interaction = press(custom_id)
    await cog.on_interaction(interaction)
    cog.store.set_verdict.assert_not_awaited()
    interaction.response.edit_message.assert_not_awaited()
