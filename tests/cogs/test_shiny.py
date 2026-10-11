from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.shiny import Shiny
from modules.config import Config
from modules.shiny_channels import STARTUP_TEXT, intro_embed


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
    interaction.message.components = []
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
        return cog

    async def test_the_named_link_goes_to_the_caller_by_dm(self, cog):
        ctx = ctx_for()
        ctx.author.send = AsyncMock()
        await Shiny.shiny.callback(cog, ctx, texto="mapa jmboy")
        cog.store.find_linked.assert_awaited_once_with("jmboy")
        sent = ctx.author.send.call_args.kwargs["embed"].description
        url = f"{Config.POKEDEX_URL.rstrip('/')}/mapa/JMBoyz"
        assert url in sent and "/shiny/" not in sent
        assert [call[0] for call in cog.store.method_calls] == ["find_linked"]
        # The channel only hears that a DM went out, never the link.
        assert url not in said(ctx)

    @pytest.mark.parametrize(
        "name, encoded",
        [
            ("Ana Silva", "Ana%20Silva"),
            ("Érica", "%C3%89rica"),
            ("A%20/B", "A%2520%2FB"),
        ],
    )
    def test_named_urls_escape_the_trainer_as_one_path_segment(self, name, encoded):
        assert (
            Shiny._map_url({"trainer_name": name})
            == f"{Config.POKEDEX_URL.rstrip('/')}/mapa/{encoded}"
        )

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
        ctx.author.send.assert_not_awaited()
        assert "ninguem" in said(ctx)


async def test_a_verdict_keeps_the_map_buttons_and_drops_its_own(cog):
    # Poracle's alert has a row of map links and a row with the two verdict
    # buttons. Answering removes only the second.
    cog.store.set_verdict = AsyncMock(return_value=True)
    interaction = press("shiny:s:42")
    link = MagicMock(
        url="https://maps.example/x", label="Google", emoji=None, custom_id=None
    )
    yes = MagicMock(url=None, label="Era Shiny", custom_id="shiny:s:42")
    no = MagicMock(url=None, label="Não era", custom_id="shiny:n:42")
    interaction.message.components = [
        MagicMock(children=[link]),
        MagicMock(children=[yes, no]),
    ]
    await cog.on_interaction(interaction)
    view = interaction.response.edit_message.call_args.kwargs["view"]
    assert [(b.label, b.url) for b in view.children] == [
        ("Google", "https://maps.example/x")
    ]


class TestOwnChannel:
    @pytest.fixture
    def cog(self, cog):
        cog.store.find_linked = AsyncMock(
            return_value={
                "discord_id": 5,
                "trainer_name": "JMBoyz",
                "channel_id": None,
                "shiny_access": 1,
            }
        )
        cog.store.set_channel = AsyncMock()
        cog.store.activate_from_channel = AsyncMock()
        cog.store.set_channel_intro = AsyncMock()
        cog.store.set_channel_intro_state = AsyncMock()
        cog.store.set_channel_archived = AsyncMock()
        return cog

    @staticmethod
    def guild_ctx(member_id=5):
        ctx = ctx_for()
        member = MagicMock(id=member_id)
        member.name = "jmboy"
        channel = MagicMock(id=777, mention="<#777>")
        pinned = MagicMock(pin=AsyncMock())
        channel.send = AsyncMock(return_value=pinned)
        ctx.guild.get_member = lambda user_id: member if user_id == member_id else None
        ctx.guild.get_channel = lambda _id: None
        ctx.guild.categories = []
        ctx.guild.create_category = AsyncMock(return_value=MagicMock())
        ctx.guild.create_text_channel = AsyncMock(return_value=channel)
        return ctx, member, channel, pinned

    async def test_creates_a_private_channel_with_the_player_in_it(self, cog):
        ctx, member, channel, pinned = self.guild_ctx()
        await Shiny.shiny.callback(cog, ctx, texto="canal jmboy")
        name = ctx.guild.create_text_channel.call_args.args[0]
        overwrites = ctx.guild.create_text_channel.call_args.kwargs["overwrites"]
        assert name == "shiny-jmboy"
        assert overwrites[ctx.guild.default_role].view_channel is False
        assert overwrites[member].view_channel is True
        assert overwrites[member].send_messages is False
        assert overwrites[member].read_message_history is True
        for permission in (
            "send_messages_in_threads",
            "create_public_threads",
            "create_private_threads",
            "add_reactions",
        ):
            assert getattr(overwrites[member], permission) is False
        assert overwrites[ctx.guild.me].send_messages is True
        cog.store.set_channel.assert_awaited_once_with(5, 777)
        assert "<#777>" in said(ctx)

    async def test_the_map_link_is_posted_there_and_pinned(self, cog):
        ctx, _, channel, pinned = self.guild_ctx()
        await Shiny.shiny.callback(cog, ctx, texto="canal jmboy")
        assert channel.send.call_args.kwargs["content"] == STARTUP_TEXT
        posted = channel.send.call_args.kwargs["embed"]
        assert posted.to_dict()["title"] == intro_embed().to_dict()["title"]
        assert posted.description == intro_embed().description
        assert "/shiny/" not in posted.description
        buttons = channel.send.call_args.kwargs["view"].children
        assert [(b.label, b.url) for b in buttons] == [
            ("Ver mapa de shinies", f"{Config.POKEDEX_URL.rstrip('/')}/mapa/JMBoyz")
        ]
        pinned.pin.assert_awaited_once()

    async def test_owner_can_post_in_another_players_channel(self, cog, monkeypatch):
        monkeypatch.setattr(Config, "MY_ID", 123)
        ctx, member, _, _ = self.guild_ctx()
        owner = MagicMock(id=123)
        ctx.guild.get_member = lambda user_id: {5: member, 123: owner}.get(user_id)
        await Shiny.shiny.callback(cog, ctx, texto="canal jmboy")
        overwrites = ctx.guild.create_text_channel.call_args.kwargs["overwrites"]
        assert overwrites[member].send_messages is False
        assert overwrites[owner].view_channel is True
        assert overwrites[owner].send_messages is True
        assert overwrites[owner].send_messages_in_threads is True

    async def test_owners_own_channel_is_writable(self, cog, monkeypatch):
        monkeypatch.setattr(Config, "MY_ID", 5)
        ctx, owner, _, _ = self.guild_ctx()
        await Shiny.shiny.callback(cog, ctx, texto="canal jmboy")
        overwrite = ctx.guild.create_text_channel.call_args.kwargs["overwrites"][owner]
        assert overwrite.send_messages is True
        assert overwrite.add_reactions is True

    async def test_a_player_who_already_has_one_gets_no_second(self, cog):
        ctx, _, _, _ = self.guild_ctx()
        cog.store.find_linked.return_value["channel_id"] = 777
        ctx.guild.get_channel = lambda _id: MagicMock(mention="<#777>")
        await Shiny.shiny.callback(cog, ctx, texto="canal jmboy")
        ctx.guild.create_text_channel.assert_not_awaited()
        assert "<#777>" in said(ctx)

    async def test_a_player_not_in_the_server_gets_none(self, cog):
        ctx, _, _, _ = self.guild_ctx(member_id=99)
        await Shiny.shiny.callback(cog, ctx, texto="canal jmboy")
        ctx.guild.create_text_channel.assert_not_awaited()
        assert "servidor" in said(ctx)

    async def test_it_has_to_be_asked_for_in_the_server(self, cog):
        ctx = ctx_for(in_guild=False)
        await Shiny.shiny.callback(cog, ctx, texto="canal jmboy")
        cog.store.find_linked.assert_not_awaited()
        assert "servidor" in said(ctx)

    async def test_missing_permission_is_said_plainly(self, cog):
        ctx, _, _, _ = self.guild_ctx()
        ctx.guild.create_text_channel.side_effect = discord.Forbidden(
            MagicMock(status=403), "Missing Permissions"
        )
        await Shiny.shiny.callback(cog, ctx, texto="canal jmboy")
        cog.store.set_channel.assert_not_awaited()
        assert "permissão" in said(ctx)
