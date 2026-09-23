"""Tests for cogs.announcements.

The buttons' work happens in AnnouncementConfirm.handle_publish /
handle_cancel, which the decorated discord.ui buttons only forward to -- so
the tests call those directly instead of going through component dispatch.
"""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.announcements import (
    _MAX_LENGTH,
    _NOT_AUTHOR,
    AnnouncementConfirm,
    Announcements,
    setup,
)

_ADMIN = 111
_TEXT = "@everyone\nBoas treinadores!\n\n**Novidades** em <#799442640910549022>"


@pytest.fixture
def poliswag():
    bot = MagicMock()
    bot.ADMIN_USERS_IDS = [str(_ADMIN)]
    bot.utility.log_to_file = MagicMock()
    channel = MagicMock()
    channel.mention = "<#378108892816867329>"
    posted = MagicMock()
    posted.jump_url = "https://discord.com/channels/1/2/3"
    channel.send = AsyncMock(return_value=posted)
    bot.EVENT_PANEL_CHANNEL = channel
    return bot


def _ctx(author_id=_ADMIN, attachments=()):
    ctx = MagicMock()
    ctx.author.id = author_id
    ctx.message.attachments = list(attachments)
    ctx.send = AsyncMock(return_value=MagicMock())
    return ctx


def _interaction(user_id=_ADMIN):
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _embed_titles(ctx):
    return [
        c.kwargs["embed"].title for c in ctx.send.call_args_list if "embed" in c.kwargs
    ]


class TestAnunciar:
    async def test_only_admins_can_run_it(self, poliswag):
        cog = Announcements(poliswag)
        assert cog.cog_check(_ctx(_ADMIN))
        assert not cog.cog_check(_ctx(999))

    async def test_previews_with_pings_off_and_posts_nothing_yet(self, poliswag):
        ctx = _ctx()
        cog = Announcements(poliswag)
        await cog.anunciar.callback(cog, ctx, texto=_TEXT)

        preview = ctx.send.call_args_list[0].kwargs
        assert preview["content"] == _TEXT
        assert preview["allowed_mentions"].everyone is False
        assert isinstance(
            ctx.send.call_args_list[1].kwargs["view"], AnnouncementConfirm
        )
        poliswag.EVENT_PANEL_CHANNEL.send.assert_not_called()

    async def test_warns_that_everyone_will_be_pinged(self, poliswag):
        ctx = _ctx()
        cog = Announcements(poliswag)
        await cog.anunciar.callback(cog, ctx, texto=_TEXT)
        assert "@everyone" in ctx.send.call_args_list[1].kwargs["embed"].description

    async def test_explains_itself_when_there_is_no_text(self, poliswag):
        ctx = _ctx()
        cog = Announcements(poliswag)
        await cog.anunciar.callback(cog, ctx, texto="   ")
        assert _embed_titles(ctx) == ["📣 Como anunciar"]
        poliswag.EVENT_PANEL_CHANNEL.send.assert_not_called()

    async def test_refuses_text_discord_would_reject(self, poliswag):
        ctx = _ctx()
        cog = Announcements(poliswag)
        await cog.anunciar.callback(cog, ctx, texto="a" * (_MAX_LENGTH + 1))
        assert _embed_titles(ctx) == ["❌ Anúncio demasiado longo"]

    async def test_says_so_when_the_channel_is_missing(self, poliswag):
        poliswag.EVENT_PANEL_CHANNEL = None
        ctx = _ctx()
        cog = Announcements(poliswag)
        await cog.anunciar.callback(cog, ctx, texto=_TEXT)
        assert _embed_titles(ctx) == ["❌ Sem canal de anúncios"]


class TestConfirm:
    async def test_publicar_posts_the_text_as_typed_with_everyone_allowed(
        self, poliswag
    ):
        ctx = _ctx()
        view = AnnouncementConfirm(Announcements(poliswag), ctx, _TEXT)
        interaction = _interaction()
        await view.handle_publish(interaction)

        sent = poliswag.EVENT_PANEL_CHANNEL.send.call_args.kwargs
        assert sent["content"] == _TEXT
        assert sent["allowed_mentions"].everyone is True
        interaction.response.edit_message.assert_awaited_once_with(view=None)
        assert (
            interaction.followup.send.call_args.kwargs["embed"].title
            == "✅ Anúncio publicado"
        )

    async def test_a_second_press_does_not_post_twice(self, poliswag):
        view = AnnouncementConfirm(Announcements(poliswag), _ctx(), _TEXT)
        await view.handle_publish(_interaction())
        await view.handle_publish(_interaction())
        assert poliswag.EVENT_PANEL_CHANNEL.send.await_count == 1

    async def test_cancelar_posts_nothing(self, poliswag):
        view = AnnouncementConfirm(Announcements(poliswag), _ctx(), _TEXT)
        await view.handle_cancel(_interaction())
        await view.handle_publish(_interaction())
        poliswag.EVENT_PANEL_CHANNEL.send.assert_not_called()

    async def test_only_the_author_can_press(self, poliswag):
        view = AnnouncementConfirm(Announcements(poliswag), _ctx(_ADMIN), _TEXT)
        stranger = _interaction(user_id=999)
        assert await view.interaction_check(stranger) is False
        stranger.response.send_message.assert_awaited_once_with(
            _NOT_AUTHOR, ephemeral=True
        )
        assert await view.interaction_check(_interaction(_ADMIN)) is True

    async def test_expiring_posts_nothing_and_says_so(self, poliswag):
        view = AnnouncementConfirm(Announcements(poliswag), _ctx(), _TEXT)
        view.message = MagicMock()
        view.message.edit = AsyncMock()
        await view.on_timeout()
        poliswag.EVENT_PANEL_CHANNEL.send.assert_not_called()
        assert (
            view.message.edit.call_args.kwargs["embed"].title == "⌛ Anúncio expirado"
        )

    async def test_a_refused_post_is_reported_not_raised(self, poliswag):
        poliswag.EVENT_PANEL_CHANNEL.send.side_effect = discord.HTTPException(
            MagicMock(status=403), "Missing Permissions"
        )
        interaction = _interaction()
        view = AnnouncementConfirm(Announcements(poliswag), _ctx(), _TEXT)
        await view.handle_publish(interaction)
        assert (
            interaction.followup.send.call_args.kwargs["embed"].title
            == "❌ Não publiquei o anúncio"
        )

    async def test_attachments_go_with_the_post(self, poliswag):
        attachment = MagicMock()
        attachment.to_file = AsyncMock(return_value=MagicMock(spec=discord.File))
        view = AnnouncementConfirm(
            Announcements(poliswag), _ctx(attachments=[attachment]), _TEXT
        )
        await view.handle_publish(_interaction())
        assert len(poliswag.EVENT_PANEL_CHANNEL.send.call_args.kwargs["files"]) == 1


async def test_setup_adds_the_cog(poliswag):
    poliswag.add_cog = AsyncMock()
    await setup(poliswag)
    assert isinstance(poliswag.add_cog.call_args.args[0], Announcements)
