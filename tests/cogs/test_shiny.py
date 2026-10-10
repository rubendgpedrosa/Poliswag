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


def test_the_command_is_the_owners_alone_during_the_pilot():
    # Refused silently to everyone else, and !help reads the same check to
    # keep it out of their listing (modules/help_command.py).
    from modules.permissions import is_owner

    assert is_owner in Shiny.shiny.checks


class TestMapLink:
    @pytest.fixture
    def cog(self, cog):
        cog.store.find_linked = AsyncMock(
            return_value={"discord_id": 5, "trainer_name": "JMBoyz"}
        )
        cog.store.set_map_token = AsyncMock()
        return cog

    async def test_the_link_goes_to_the_caller_by_dm_and_only_its_hash_is_stored(
        self, cog
    ):
        import hashlib

        ctx = ctx_for()
        ctx.author.send = AsyncMock()
        await Shiny.shiny.callback(cog, ctx, texto="mapa jmboy")
        cog.store.find_linked.assert_awaited_once_with("jmboy")
        sent = ctx.author.send.call_args.kwargs["embed"].description
        token = sent.split("/shiny/")[1].split()[0].rstrip(">")
        assert len(token) == 64 and "JMBoyz" in sent
        cog.store.set_map_token.assert_awaited_once_with(
            5, hashlib.sha256(token.encode()).hexdigest()
        )
        # The channel only hears that a DM went out, never the link.
        assert token not in said(ctx)

    async def test_without_a_name_it_is_the_callers_own_map(self, cog):
        ctx = ctx_for()
        ctx.author.send = AsyncMock()
        await Shiny.shiny.callback(cog, ctx, texto="mapa")
        cog.store.find_linked.assert_awaited_once_with("rita")

    async def test_an_unknown_player_gets_no_link(self, cog):
        cog.store.find_linked.return_value = None
        ctx = ctx_for()
        ctx.author.send = AsyncMock()
        await Shiny.shiny.callback(cog, ctx, texto="mapa ninguem")
        cog.store.set_map_token.assert_not_awaited()
        ctx.author.send.assert_not_awaited()
        assert "ninguem" in said(ctx)
