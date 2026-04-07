"""Tests for cogs.moderation.Moderation.

Exercises the on_interaction routing (role-selection custom_ids) and
on_message_delete audit logging. The cog has no commands, only listeners,
which we call directly as bound methods.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.moderation import Moderation


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.ADMIN_USERS_IDS = ["999"]
    poliswag.MOD_CHANNEL = MagicMock()
    poliswag.MOD_CHANNEL.id = 1
    poliswag.MOD_CHANNEL.send = AsyncMock()
    poliswag.QUEST_CHANNEL = MagicMock()
    poliswag.QUEST_CHANNEL.id = 2
    poliswag.user = MagicMock(name="bot_user")
    poliswag.role_manager.restart_response_user_role_selection = AsyncMock()
    return Moderation(poliswag)


class TestOnInteraction:
    async def test_no_data_returns_early(self, cog):
        interaction = MagicMock()
        interaction.data = None
        await cog.on_interaction(interaction)
        cog.poliswag.role_manager.restart_response_user_role_selection.assert_not_called()

    async def test_missing_custom_id_returns_early(self, cog):
        interaction = MagicMock()
        interaction.data = {"other": "x"}
        await cog.on_interaction(interaction)
        cog.poliswag.role_manager.restart_response_user_role_selection.assert_not_called()

    async def test_alertas_prefix_triggers_role_manager(self, cog):
        interaction = MagicMock()
        interaction.data = {"custom_id": "AlertasLeiria"}
        await cog.on_interaction(interaction)
        cog.poliswag.role_manager.restart_response_user_role_selection.assert_awaited_once_with(
            interaction
        )

    @pytest.mark.parametrize(
        "custom_id", ["Leiria", "Marinha", "Remote", "Mystic", "Valor", "Instinct"]
    )
    async def test_known_custom_ids_trigger_role_manager(self, cog, custom_id):
        interaction = MagicMock()
        interaction.data = {"custom_id": custom_id}
        await cog.on_interaction(interaction)
        cog.poliswag.role_manager.restart_response_user_role_selection.assert_awaited_once()

    async def test_unrelated_custom_id_ignored(self, cog):
        interaction = MagicMock()
        interaction.data = {"custom_id": "UnrelatedButton"}
        await cog.on_interaction(interaction)
        cog.poliswag.role_manager.restart_response_user_role_selection.assert_not_called()


class TestOnMessageDelete:
    def _msg(self, channel_id, author_id, author_is_bot=False):
        message = MagicMock()
        message.channel = MagicMock()
        message.channel.id = channel_id
        message.author = MagicMock()
        message.author.id = author_id
        if author_is_bot:
            message.author = MagicMock()  # distinct from poliswag.user
        return message

    async def test_none_mod_channel_skips(self, cog):
        cog.poliswag.MOD_CHANNEL = None
        await cog.on_message_delete(self._msg(5, 123))
        # No exception, no send.

    async def test_none_quest_channel_skips(self, cog):
        cog.poliswag.QUEST_CHANNEL = None
        await cog.on_message_delete(self._msg(5, 123))

    async def test_message_in_mod_channel_skipped(self, cog):
        await cog.on_message_delete(self._msg(1, 123))
        cog.poliswag.MOD_CHANNEL.send.assert_not_called()

    async def test_message_in_quest_channel_skipped(self, cog):
        await cog.on_message_delete(self._msg(2, 123))
        cog.poliswag.MOD_CHANNEL.send.assert_not_called()

    async def test_admin_deletion_skipped(self, cog):
        await cog.on_message_delete(self._msg(5, 999))
        cog.poliswag.MOD_CHANNEL.send.assert_not_called()

    async def test_bot_own_deletion_skipped(self, cog):
        msg = self._msg(5, 123)
        msg.author = cog.poliswag.user
        await cog.on_message_delete(msg)
        cog.poliswag.MOD_CHANNEL.send.assert_not_called()

    async def test_regular_deletion_sends_audit_embed(self, cog):
        msg = self._msg(5, 123)
        msg.content = "hey"
        await cog.on_message_delete(msg)
        cog.poliswag.MOD_CHANNEL.send.assert_awaited_once()
        _, kwargs = cog.poliswag.MOD_CHANNEL.send.call_args
        assert "embed" in kwargs


class TestLifecycle:
    async def test_cog_load_prints(self, cog, capsys):
        await cog.cog_load()
        assert "Moderation loaded" in capsys.readouterr().out

    async def test_cog_unload_prints(self, cog, capsys):
        await cog.cog_unload()
        assert "Moderation unloaded" in capsys.readouterr().out
