"""Tests for cogs.moderation.Moderation.

Exercises the on_interaction routing (role-selection custom_ids) and
on_message_delete audit logging. The cog has no commands, only listeners,
which we call directly as bound methods.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.moderation import Moderation, setup
from modules.config import Config


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
    poliswag.db = AsyncMock()
    poliswag.db.get_data_from_database = AsyncMock(return_value=[])
    poliswag.db.execute_query_to_database = AsyncMock()
    poliswag.TRAP_CHANNEL = MagicMock()
    poliswag.TRAP_CHANNEL.id = 3
    poliswag.TRAP_CHANNEL.send = AsyncMock()
    return Moderation(poliswag)


def _member(author_id, is_bot=False):
    member = MagicMock()
    member.id = author_id
    member.bot = is_bot
    member.kick = AsyncMock()
    return member


def _trap_msg(author_id=555, content="spam", is_bot=False):
    message = MagicMock()
    message.channel = MagicMock()
    message.channel.id = 3
    message.author = _member(author_id, is_bot=is_bot)
    message.content = content
    message.delete = AsyncMock()
    return message


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


class TestLoadTrapKickCount:
    async def test_returns_zero_when_db_empty(self, cog):
        cog.poliswag.db.get_data_from_database = AsyncMock(return_value=[])
        assert await cog._load_trap_kick_count() == 0

    async def test_returns_stored_count(self, cog):
        cog.poliswag.db.get_data_from_database = AsyncMock(
            return_value=[{"trap_kick_count": 7}]
        )
        assert await cog._load_trap_kick_count() == 7

    async def test_none_value_returns_zero(self, cog):
        cog.poliswag.db.get_data_from_database = AsyncMock(
            return_value=[{"trap_kick_count": None}]
        )
        assert await cog._load_trap_kick_count() == 0

    async def test_exception_returns_zero_and_logs(self, cog):
        cog.poliswag.db.get_data_from_database = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        assert await cog._load_trap_kick_count() == 0
        cog.poliswag.utility.log_to_file.assert_called_once()


class TestSaveTrapKickCount:
    async def test_calls_update_query(self, cog):
        await cog._save_trap_kick_count(5)
        cog.poliswag.db.execute_query_to_database.assert_called_once()
        _, kwargs = cog.poliswag.db.execute_query_to_database.call_args
        assert kwargs["params"] == (5,)


class TestGetOrCreateTrapMessage:
    async def test_returns_cached_message_without_touching_history(self, cog):
        cog._trap_message = MagicMock(name="cached")
        result = await cog._get_or_create_trap_message(cog.poliswag.TRAP_CHANNEL)
        assert result is cog._trap_message
        cog.poliswag.TRAP_CHANNEL.history.assert_not_called()

    async def test_finds_existing_bot_message_in_history(self, cog):
        own_message = MagicMock()
        own_message.author = cog.poliswag.user
        other_message = MagicMock()
        other_message.author = _member(111)

        async def _history(limit):
            for m in [other_message, own_message]:
                yield m

        cog.poliswag.TRAP_CHANNEL.history = _history
        result = await cog._get_or_create_trap_message(cog.poliswag.TRAP_CHANNEL)
        assert result is own_message
        cog.poliswag.TRAP_CHANNEL.send.assert_not_called()

    async def test_creates_new_message_when_none_found(self, cog):
        async def _history(limit):
            return
            yield  # pragma: no cover - makes this an async generator

        cog.poliswag.TRAP_CHANNEL.history = _history
        posted = MagicMock()
        cog.poliswag.TRAP_CHANNEL.send = AsyncMock(return_value=posted)
        result = await cog._get_or_create_trap_message(cog.poliswag.TRAP_CHANNEL)
        assert result is posted
        cog.poliswag.TRAP_CHANNEL.send.assert_awaited_once()


class TestOnMessageTrap:
    async def test_no_trap_channel_configured_is_a_noop(self, cog):
        cog.poliswag.TRAP_CHANNEL = None
        msg = _trap_msg()
        await cog.on_message(msg)
        msg.delete.assert_not_called()

    async def test_message_outside_trap_channel_ignored(self, cog):
        msg = _trap_msg()
        msg.channel.id = 999
        await cog.on_message(msg)
        msg.delete.assert_not_called()

    async def test_bot_author_ignored(self, cog):
        msg = _trap_msg(is_bot=True)
        await cog.on_message(msg)
        msg.delete.assert_not_called()

    async def test_admin_author_ignored(self, cog):
        msg = _trap_msg(author_id=999)  # matches fixture's ADMIN_USERS_IDS
        await cog.on_message(msg)
        msg.delete.assert_not_called()

    async def test_regular_user_deleted_and_kicked(self, cog):
        cog._get_or_create_trap_message = AsyncMock(
            return_value=MagicMock(edit=AsyncMock())
        )
        msg = _trap_msg(author_id=123)
        await cog.on_message(msg)
        msg.delete.assert_awaited_once()
        msg.author.kick.assert_awaited_once()
        assert cog._trap_kick_count == 1
        cog.poliswag.db.execute_query_to_database.assert_called_once()

    async def test_counter_message_updated_with_new_count(self, cog):
        trap_message = MagicMock(edit=AsyncMock())
        cog._get_or_create_trap_message = AsyncMock(return_value=trap_message)
        cog._trap_kick_count = 4
        await cog.on_message(_trap_msg(author_id=123))
        trap_message.edit.assert_awaited_once()
        assert "5" in trap_message.edit.call_args.kwargs["content"]

    async def test_delete_failure_still_attempts_kick(self, cog):
        import discord

        cog._get_or_create_trap_message = AsyncMock(
            return_value=MagicMock(edit=AsyncMock())
        )
        msg = _trap_msg(author_id=123)
        msg.delete = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "nope"))
        await cog.on_message(msg)
        msg.author.kick.assert_awaited_once()
        cog.poliswag.utility.log_to_file.assert_called()

    async def test_kick_failure_does_not_increment_counter(self, cog):
        import discord

        cog._get_or_create_trap_message = AsyncMock(
            return_value=MagicMock(edit=AsyncMock())
        )
        msg = _trap_msg(author_id=123)
        msg.author.kick = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(), "missing perms")
        )
        await cog.on_message(msg)
        assert cog._trap_kick_count == 0
        cog.poliswag.db.execute_query_to_database.assert_not_called()

    async def test_mod_channel_notified_with_result(self, cog):
        cog._get_or_create_trap_message = AsyncMock(
            return_value=MagicMock(edit=AsyncMock())
        )
        msg = _trap_msg(author_id=123, content="buy crypto now")
        await cog.on_message(msg)
        cog.poliswag.utility.send_embed_to_channel.assert_awaited_once()
        channel, embed = cog.poliswag.utility.send_embed_to_channel.call_args.args
        assert channel is cog.poliswag.MOD_CHANNEL
        assert "efectuado" in embed.fields[0].value
        assert "buy crypto now" in embed.fields[0].value

    async def test_no_mod_channel_is_a_noop(self, cog):
        cog.poliswag.MOD_CHANNEL = None
        cog._get_or_create_trap_message = AsyncMock(
            return_value=MagicMock(edit=AsyncMock())
        )
        await cog.on_message(_trap_msg(author_id=123))  # must not raise
        cog.poliswag.utility.send_embed_to_channel.assert_not_called()

    async def test_counter_message_edit_failure_is_logged_not_raised(self, cog):
        import discord

        trap_message = MagicMock()
        trap_message.edit = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(), "nope")
        )
        cog._get_or_create_trap_message = AsyncMock(return_value=trap_message)
        await cog.on_message(_trap_msg(author_id=123))  # must not raise
        cog.poliswag.utility.log_to_file.assert_called()


class TestEnsureTrapWarningPosted:
    async def test_no_trap_channel_id_configured_is_a_noop(self, cog, mocker):
        mocker.patch.object(Config, "TRAP_CHANNEL_ID", 0)
        await cog._ensure_trap_warning_posted()
        cog.poliswag.fetch_channel.assert_not_called()

    async def test_fetches_channel_and_ensures_message(self, cog, mocker):
        mocker.patch.object(Config, "TRAP_CHANNEL_ID", 3)
        fetched_channel = MagicMock()
        cog.poliswag.fetch_channel = AsyncMock(return_value=fetched_channel)
        cog._get_or_create_trap_message = AsyncMock()
        await cog._ensure_trap_warning_posted()
        cog.poliswag.fetch_channel.assert_awaited_once_with(3)
        cog._get_or_create_trap_message.assert_awaited_once_with(fetched_channel)

    async def test_fetch_failure_is_logged_not_raised(self, cog, mocker):
        import discord

        mocker.patch.object(Config, "TRAP_CHANNEL_ID", 3)
        cog.poliswag.fetch_channel = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(), "not found")
        )
        await cog._ensure_trap_warning_posted()  # must not raise
        cog.poliswag.utility.log_to_file.assert_called_once()


class TestLifecycle:
    async def test_cog_load_prints(self, cog, capsys, mocker):
        mocker.patch.object(Config, "TRAP_CHANNEL_ID", 0)
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
