"""Tests for cogs.moderation.Moderation.

Exercises the on_interaction routing (role-selection custom_ids) and
on_message_delete audit logging. The cog has no commands, only listeners,
which we call directly as bound methods.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.moderation import Moderation, setup


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.ADMIN_USERS_IDS = ["999"]
    poliswag.MOD_CHANNEL = MagicMock()
    poliswag.MOD_CHANNEL.id = 1
    poliswag.MOD_CHANNEL.send = AsyncMock()
    poliswag.utility.send_embed_to_channel = AsyncMock()
    poliswag.QUEST_CHANNEL = MagicMock()
    poliswag.QUEST_CHANNEL.id = 2
    poliswag.user = MagicMock(name="bot_user")
    poliswag.role_manager.response_user_role_selection = AsyncMock()
    return Moderation(poliswag)


class TestOnInteraction:
    async def test_no_data_returns_early(self, cog):
        interaction = MagicMock()
        interaction.data = None
        await cog.on_interaction(interaction)
        cog.poliswag.role_manager.response_user_role_selection.assert_not_called()

    async def test_missing_custom_id_returns_early(self, cog):
        interaction = MagicMock()
        interaction.data = {"other": "x"}
        await cog.on_interaction(interaction)
        cog.poliswag.role_manager.response_user_role_selection.assert_not_called()

    async def test_alertas_prefix_triggers_role_manager(self, cog):
        interaction = MagicMock()
        interaction.data = {"custom_id": "AlertasLeiria"}
        await cog.on_interaction(interaction)
        cog.poliswag.role_manager.response_user_role_selection.assert_awaited_once_with(
            interaction
        )

    @pytest.mark.parametrize(
        "custom_id", ["Leiria", "Marinha", "Remote", "Mystic", "Valor", "Instinct"]
    )
    async def test_known_custom_ids_trigger_role_manager(self, cog, custom_id):
        interaction = MagicMock()
        interaction.data = {"custom_id": custom_id}
        await cog.on_interaction(interaction)
        cog.poliswag.role_manager.response_user_role_selection.assert_awaited_once()

    async def test_unrelated_custom_id_ignored(self, cog):
        interaction = MagicMock()
        interaction.data = {"custom_id": "UnrelatedButton"}
        await cog.on_interaction(interaction)
        cog.poliswag.role_manager.response_user_role_selection.assert_not_called()


class TestOnMessageDelete:
    def _msg(self, channel_id, author_id, author_is_bot=False):
        message = MagicMock()
        message.channel = MagicMock()
        message.channel.id = channel_id
        message.author = MagicMock()
        message.author.id = author_id
        message.content = ""
        message.attachments = []
        if author_is_bot:
            message.author = MagicMock()  # distinct from poliswag.user
        return message

    def _attachment(self, filename, url, content_type=None):
        a = MagicMock()
        a.filename = filename
        a.url = url
        a.content_type = content_type
        return a

    async def test_none_mod_channel_skips(self, cog):
        cog.poliswag.MOD_CHANNEL = None
        await cog.on_message_delete(self._msg(5, 123))
        # No exception, no send.

    async def test_none_quest_channel_skips(self, cog):
        cog.poliswag.QUEST_CHANNEL = None
        await cog.on_message_delete(self._msg(5, 123))

    async def test_message_in_mod_channel_skipped(self, cog):
        await cog.on_message_delete(self._msg(1, 123))
        cog.poliswag.utility.send_embed_to_channel.assert_not_called()

    async def test_message_in_quest_channel_skipped(self, cog):
        await cog.on_message_delete(self._msg(2, 123))
        cog.poliswag.utility.send_embed_to_channel.assert_not_called()

    async def test_admin_deletion_skipped(self, cog):
        await cog.on_message_delete(self._msg(5, 999))
        cog.poliswag.utility.send_embed_to_channel.assert_not_called()

    async def test_bot_own_deletion_skipped(self, cog):
        msg = self._msg(5, 123)
        msg.author = cog.poliswag.user
        await cog.on_message_delete(msg)
        cog.poliswag.utility.send_embed_to_channel.assert_not_called()

    async def test_regular_deletion_sends_audit_embed(self, cog):
        msg = self._msg(5, 123)
        msg.content = "hey"
        await cog.on_message_delete(msg)
        cog.poliswag.utility.send_embed_to_channel.assert_awaited_once()
        channel, embed = cog.poliswag.utility.send_embed_to_channel.call_args.args
        assert channel is cog.poliswag.MOD_CHANNEL
        assert embed.fields[0].value == "hey"

    async def test_empty_content_uses_placeholder_text(self, cog):
        msg = self._msg(5, 123)
        msg.content = ""
        await cog.on_message_delete(msg)
        _, embed = cog.poliswag.utility.send_embed_to_channel.call_args.args
        assert embed.fields[0].value == "*(sem texto)*"

    async def test_image_attachment_is_set_as_embed_image(self, cog):
        msg = self._msg(5, 123)
        msg.attachments = [
            self._attachment("photo.png", "https://cdn/photo.png", "image/png")
        ]
        await cog.on_message_delete(msg)
        _, embed = cog.poliswag.utility.send_embed_to_channel.call_args.args
        assert embed.image.url == "https://cdn/photo.png"

    async def test_image_detected_by_extension_when_content_type_missing(self, cog):
        msg = self._msg(5, 123)
        msg.attachments = [self._attachment("photo.jpeg", "https://cdn/photo.jpeg")]
        await cog.on_message_delete(msg)
        _, embed = cog.poliswag.utility.send_embed_to_channel.call_args.args
        assert embed.image.url == "https://cdn/photo.jpeg"

    async def test_non_image_attachment_listed_as_field_not_embedded(self, cog):
        msg = self._msg(5, 123)
        msg.attachments = [
            self._attachment("report.pdf", "https://cdn/report.pdf", "application/pdf")
        ]
        await cog.on_message_delete(msg)
        _, embed = cog.poliswag.utility.send_embed_to_channel.call_args.args
        assert embed.image.url is None
        assert "report.pdf" in embed.fields[1].value

    async def test_mixed_attachments_embeds_image_and_lists_the_rest(self, cog):
        msg = self._msg(5, 123)
        msg.attachments = [
            self._attachment("report.pdf", "https://cdn/report.pdf", "application/pdf"),
            self._attachment("photo.png", "https://cdn/photo.png", "image/png"),
        ]
        await cog.on_message_delete(msg)
        _, embed = cog.poliswag.utility.send_embed_to_channel.call_args.args
        assert embed.image.url == "https://cdn/photo.png"
        assert "report.pdf" in embed.fields[1].value
        assert "photo.png" not in embed.fields[1].value


class TestLifecycle:
    async def test_cog_load_prints(self, cog, capsys):
        await cog.cog_load()
        assert "Moderation loaded" in capsys.readouterr().out

    async def test_cog_unload_prints(self, cog, capsys):
        await cog.cog_unload()
        assert "Moderation unloaded" in capsys.readouterr().out


class TestSetup:
    async def test_registers_cog_on_poliswag(self):
        poliswag = MagicMock()
        poliswag.add_cog = AsyncMock()
        await setup(poliswag)
        poliswag.add_cog.assert_awaited_once()
        assert isinstance(poliswag.add_cog.call_args.args[0], Moderation)
