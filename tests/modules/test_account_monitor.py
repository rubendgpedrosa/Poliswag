from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.account_monitor import DISABLED_STATUSES, AccountMonitor


@pytest.fixture
def account_monitor():
    """An AccountMonitor with a mocked poliswag dependency.

    poliswag.utility.log_to_file is auto-mocked by MagicMock so error paths
    don't touch the real logger.
    """
    return AccountMonitor(poliswag=MagicMock())


def _mock_fetch(mocker, return_value):
    """Install an AsyncMock replacement for fetch_data inside account_monitor."""
    mock = AsyncMock(return_value=return_value)
    mocker.patch("modules.account_monitor.fetch_data", new=mock)
    return mock


class TestGetAccountStats:
    """AccountMonitor.get_account_stats normalises the account_status payload."""

    async def test_none_payload_returns_zeroed_defaults(self, account_monitor, mocker):
        _mock_fetch(mocker, None)
        result = await account_monitor.get_account_stats()
        assert result == {"in_use": 0, "good": 0, "cooldown": 0, "disabled": 0}

    async def test_empty_dict_payload_returns_zeroed_defaults(
        self, account_monitor, mocker
    ):
        # Empty dict is falsy, so the early-return branch kicks in.
        _mock_fetch(mocker, {})
        result = await account_monitor.get_account_stats()
        assert result == {"in_use": 0, "good": 0, "cooldown": 0, "disabled": 0}

    async def test_healthy_counts_are_passed_through(self, account_monitor, mocker):
        _mock_fetch(
            mocker,
            {"in_use": 12, "good": 45, "cooldown": 7},
        )
        result = await account_monitor.get_account_stats()
        assert result == {"in_use": 12, "good": 45, "cooldown": 7, "disabled": 0}

    async def test_missing_keys_default_to_zero(self, account_monitor, mocker):
        # Only `good` present — the other three should fall back to 0.
        _mock_fetch(mocker, {"good": 10})
        result = await account_monitor.get_account_stats()
        assert result == {"in_use": 0, "good": 10, "cooldown": 0, "disabled": 0}

    async def test_disabled_statuses_are_summed(self, account_monitor, mocker):
        # Three distinct disabled statuses, each with a count, should be summed.
        _mock_fetch(
            mocker,
            {
                "in_use": 1,
                "good": 2,
                "cooldown": 3,
                "banned": 4,
                "warned": 5,
                "suspended": 6,
            },
        )
        result = await account_monitor.get_account_stats()
        assert result == {
            "in_use": 1,
            "good": 2,
            "cooldown": 3,
            "disabled": 4 + 5 + 6,
        }

    async def test_all_disabled_statuses_counted(self, account_monitor, mocker):
        # Every known DISABLED_STATUSES entry should contribute to the sum.
        payload = {status: 1 for status in DISABLED_STATUSES}
        _mock_fetch(mocker, payload)
        result = await account_monitor.get_account_stats()
        assert result["disabled"] == len(DISABLED_STATUSES)

    async def test_unknown_keys_do_not_leak_into_disabled(
        self, account_monitor, mocker
    ):
        # Statuses that aren't in DISABLED_STATUSES must be ignored for the sum.
        _mock_fetch(
            mocker,
            {
                "in_use": 0,
                "good": 0,
                "cooldown": 0,
                "random_status": 99,
                "unexpected": 50,
            },
        )
        result = await account_monitor.get_account_stats()
        assert result["disabled"] == 0

    async def test_partial_disabled_statuses_sum_only_present_ones(
        self, account_monitor, mocker
    ):
        _mock_fetch(
            mocker,
            {"banned": 3, "invalid": 2},  # two of nine DISABLED_STATUSES
        )
        result = await account_monitor.get_account_stats()
        assert result["disabled"] == 5


class TestIsDeviceConnected:
    """AccountMonitor.is_device_connected reports scanner device availability."""

    async def test_none_payload_returns_false(self, account_monitor, mocker):
        _mock_fetch(mocker, None)
        assert await account_monitor.is_device_connected() is False

    async def test_payload_without_devices_key_returns_false(
        self, account_monitor, mocker
    ):
        _mock_fetch(mocker, {"unrelated": "data"})
        assert await account_monitor.is_device_connected() is False

    async def test_empty_devices_list_returns_false(self, account_monitor, mocker):
        _mock_fetch(mocker, {"devices": []})
        assert await account_monitor.is_device_connected() is False

    async def test_all_devices_dead_returns_false(self, account_monitor, mocker):
        _mock_fetch(
            mocker,
            {
                "devices": [
                    {"isAlive": False},
                    {"isAlive": False},
                ]
            },
        )
        assert await account_monitor.is_device_connected() is False

    async def test_single_alive_device_returns_true(self, account_monitor, mocker):
        _mock_fetch(mocker, {"devices": [{"isAlive": True}]})
        assert await account_monitor.is_device_connected() is True

    async def test_any_alive_device_returns_true(self, account_monitor, mocker):
        _mock_fetch(
            mocker,
            {
                "devices": [
                    {"isAlive": False},
                    {"isAlive": False},
                    {"isAlive": True},
                ]
            },
        )
        assert await account_monitor.is_device_connected() is True

    async def test_device_without_isalive_key_defaults_to_false(
        self, account_monitor, mocker
    ):
        _mock_fetch(mocker, {"devices": [{"name": "scanner-01"}]})
        assert await account_monitor.is_device_connected() is False

    # --- RotomNG payload shape (snake_case `is_connected`) ------------------

    async def test_ng_connected_device_returns_true(self, account_monitor, mocker):
        # RotomNG reports connectivity via `is_connected`, not `isAlive`.
        _mock_fetch(mocker, {"devices": [{"is_connected": True}]})
        assert await account_monitor.is_device_connected() is True

    async def test_ng_all_disconnected_returns_false(self, account_monitor, mocker):
        _mock_fetch(
            mocker,
            {"devices": [{"is_connected": False}, {"is_connected": False}]},
        )
        assert await account_monitor.is_device_connected() is False

    async def test_ng_any_connected_device_returns_true(self, account_monitor, mocker):
        _mock_fetch(
            mocker,
            {
                "devices": [
                    {"is_connected": False},
                    {"is_connected": True},
                ]
            },
        )
        assert await account_monitor.is_device_connected() is True


class TestDisabledStatusesConstant:
    """Regression guards for the canonical list of disabled account statuses."""

    def test_contains_core_statuses(self):
        # If any of these disappear, account reporting will silently undercount.
        assert "banned" in DISABLED_STATUSES
        assert "warned" in DISABLED_STATUSES
        assert "suspended" in DISABLED_STATUSES
        assert "disabled" in DISABLED_STATUSES

    def test_has_no_duplicates(self):
        assert len(DISABLED_STATUSES) == len(set(DISABLED_STATUSES))


class _AsyncChannelHistory:
    """An async-iterable stand-in for channel.history()."""

    def __init__(self, messages):
        self._messages = messages

    def __aiter__(self):
        async def gen():
            for m in self._messages:
                yield m

        return gen()


class TestBuildStatusEmbed:
    """AccountMonitor.build_status_embed's severity coloring and fields."""

    def test_ok_level_when_available_share_is_healthy(self, account_monitor):
        embed = account_monitor.build_status_embed(
            {"good": 10, "cooldown": 1, "disabled": 5}, True
        )
        assert embed.color == discord.Color.green()

    def test_crit_level_when_device_disconnected(self, account_monitor):
        embed = account_monitor.build_status_embed(
            {"good": 10, "cooldown": 0, "disabled": 0}, False
        )
        assert embed.color == discord.Color.red()

    def test_crit_level_when_no_accounts_available(self, account_monitor):
        embed = account_monitor.build_status_embed(
            {"good": 0, "cooldown": 5, "disabled": 1}, True
        )
        assert embed.color == discord.Color.red()

    def test_disabled_accounts_alone_do_not_trigger_warning(self, account_monitor):
        # Some accounts sitting in cooldown/disabled at any moment is normal
        # pool churn -- it must not turn the embed yellow on its own as long
        # as the available share is still healthy.
        embed = account_monitor.build_status_embed(
            {"good": 10, "cooldown": 0, "disabled": 8}, True
        )
        assert embed.color == discord.Color.green()

    def test_warn_level_when_available_share_is_thin(self, account_monitor):
        embed = account_monitor.build_status_embed(
            {"good": 2, "cooldown": 8, "disabled": 0}, True
        )
        assert embed.color == discord.Color.gold()

    def test_fields_contain_counts_and_device_text(self, account_monitor):
        embed = account_monitor.build_status_embed(
            {"good": 3, "cooldown": 2, "disabled": 1}, True
        )
        values = {f.name: f.value for f in embed.fields}
        assert values["Disponíveis"] == "**3**"
        assert values["Cooldown"] == "**2**"
        assert values["Desativadas"] == "**1**"
        assert "Conectado" in values["Dispositivo"]

    def test_device_disconnected_field_text(self, account_monitor):
        embed = account_monitor.build_status_embed(
            {"good": 1, "cooldown": 0, "disabled": 0}, False
        )
        values = {f.name: f.value for f in embed.fields}
        assert "Desconectado" in values["Dispositivo"]

    def test_no_description_or_extra_fields(self, account_monitor):
        # Deliberately minimal: just the 3 counts + device, nothing else.
        embed = account_monitor.build_status_embed(
            {"good": 5, "cooldown": 5, "disabled": 0}, True
        )
        assert not embed.description
        assert len(embed.fields) == 4

    def test_timestamp_is_set(self, account_monitor):
        embed = account_monitor.build_status_embed(
            {"good": 1, "cooldown": 0, "disabled": 0}, True
        )
        assert embed.timestamp is not None

    def test_zero_total_accounts_does_not_divide_by_zero(self, account_monitor):
        # good == cooldown == disabled == 0 -> total falls back to 1.
        embed = account_monitor.build_status_embed(
            {"good": 0, "cooldown": 0, "disabled": 0}, True
        )
        assert embed.color == discord.Color.red()


class TestUpdateChannelAccountsStats:
    """AccountMonitor.update_channel_accounts_stats end-to-end branches."""

    async def test_no_channel_is_a_noop(self, account_monitor):
        account_monitor.poliswag.ACCOUNTS_CHANNEL = None
        # Should not explode or touch fetch_data.
        await account_monitor.update_channel_accounts_stats()

    async def _setup_channel(self, account_monitor, existing_messages):
        channel = MagicMock()
        channel.history = MagicMock(
            return_value=_AsyncChannelHistory(existing_messages)
        )
        channel.send = AsyncMock()
        account_monitor.poliswag.ACCOUNTS_CHANNEL = channel
        account_monitor.poliswag.user = MagicMock(name="self_user")
        return channel

    async def test_sends_new_message_when_no_existing(self, account_monitor, mocker):
        channel = await self._setup_channel(account_monitor, existing_messages=[])
        mocker.patch(
            "modules.account_monitor.fetch_data",
            new=AsyncMock(side_effect=[{"good": 5}, {"devices": [{"isAlive": True}]}]),
        )
        await account_monitor.update_channel_accounts_stats()
        channel.send.assert_awaited_once()
        assert "embed" in channel.send.call_args.kwargs

    async def test_edits_existing_message_when_present(self, account_monitor, mocker):
        existing = MagicMock()
        existing.author = None  # will be set after setup
        existing.edit = AsyncMock()
        existing.delete = AsyncMock()
        channel = await self._setup_channel(
            account_monitor, existing_messages=[existing]
        )
        # Authored by the bot itself so it becomes the existing_message.
        existing.author = account_monitor.poliswag.user
        mocker.patch(
            "modules.account_monitor.fetch_data",
            new=AsyncMock(side_effect=[{"good": 5}, {"devices": []}]),
        )
        await account_monitor.update_channel_accounts_stats()
        existing.edit.assert_awaited_once()
        channel.send.assert_not_called()

    async def test_leaves_extra_messages_untouched(self, account_monitor, mocker):
        msg_keep = MagicMock()
        msg_other = MagicMock()
        msg_other.delete = AsyncMock()
        await self._setup_channel(
            account_monitor, existing_messages=[msg_keep, msg_other]
        )
        msg_keep.author = account_monitor.poliswag.user
        msg_keep.edit = AsyncMock()
        msg_other.author = MagicMock()
        mocker.patch(
            "modules.account_monitor.fetch_data",
            new=AsyncMock(side_effect=[{"good": 5}, {"devices": []}]),
        )
        await account_monitor.update_channel_accounts_stats()
        msg_keep.edit.assert_awaited_once()
        msg_other.delete.assert_not_called()

    async def test_exception_is_logged(self, account_monitor, mocker):
        account_monitor.poliswag.ACCOUNTS_CHANNEL = MagicMock()
        account_monitor.poliswag.ACCOUNTS_CHANNEL.history = MagicMock(
            side_effect=RuntimeError("boom")
        )
        await account_monitor.update_channel_accounts_stats()
        log_calls = [
            c.args[0]
            for c in account_monitor.poliswag.utility.log_to_file.call_args_list
        ]
        assert any("update_channel_accounts_stats" in m for m in log_calls)

    async def test_edit_clears_stale_attachments(self, account_monitor, mocker):
        # Regression: a message cached from before this embed-only design
        # may still carry the old rendered-image attachment. .edit() must
        # be told attachments=[] explicitly, or Discord keeps that old file
        # lingering alongside the new embed.
        existing = MagicMock()
        existing.edit = AsyncMock()
        channel = await self._setup_channel(
            account_monitor, existing_messages=[existing]
        )
        existing.author = account_monitor.poliswag.user
        mocker.patch(
            "modules.account_monitor.fetch_data",
            new=AsyncMock(side_effect=[{"good": 5}, {"devices": []}]),
        )
        await account_monitor.update_channel_accounts_stats()
        assert existing.edit.call_args.kwargs["attachments"] == []
        assert channel.send.await_count == 0

    async def test_second_call_skips_history_scan(self, account_monitor, mocker):
        existing = MagicMock()
        existing.edit = AsyncMock()
        channel = await self._setup_channel(
            account_monitor, existing_messages=[existing]
        )
        existing.author = account_monitor.poliswag.user
        mocker.patch(
            "modules.account_monitor.fetch_data",
            new=AsyncMock(side_effect=[{"good": 5}, {"devices": []}] * 2),
        )

        await account_monitor.update_channel_accounts_stats()
        await account_monitor.update_channel_accounts_stats()

        # history() should only be scanned once — the second tick reuses the
        # cached message reference instead of re-scanning the channel.
        channel.history.assert_called_once()
        assert existing.edit.await_count == 2
        assert account_monitor._accounts_message is existing

    async def test_deleted_cached_message_resends_and_recaches(
        self, account_monitor, mocker
    ):
        channel = await self._setup_channel(account_monitor, existing_messages=[])
        stale_message = MagicMock()
        stale_message.edit = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404, reason=""), "gone")
        )
        account_monitor._accounts_message = stale_message
        new_message = MagicMock()
        channel.send = AsyncMock(return_value=new_message)
        mocker.patch(
            "modules.account_monitor.fetch_data",
            new=AsyncMock(side_effect=[{"good": 5}, {"devices": []}]),
        )

        await account_monitor.update_channel_accounts_stats()

        stale_message.edit.assert_awaited_once()
        # No cache hit means no history scan was needed to discover this was stale.
        channel.history.assert_not_called()
        channel.send.assert_awaited_once()
        assert account_monitor._accounts_message is new_message
