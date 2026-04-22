from unittest.mock import AsyncMock, MagicMock

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
        account_monitor.poliswag.image_generator.generate_image_from_account_stats = (
            AsyncMock(return_value=b"PNGDATA")
        )
        await account_monitor.update_channel_accounts_stats()
        channel.send.assert_awaited_once()

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
        account_monitor.poliswag.image_generator.generate_image_from_account_stats = (
            AsyncMock(return_value=b"PNG")
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
        account_monitor.poliswag.image_generator.generate_image_from_account_stats = (
            AsyncMock(return_value=b"PNG")
        )
        await account_monitor.update_channel_accounts_stats()
        msg_keep.edit.assert_awaited_once()
        msg_other.delete.assert_not_called()

    async def test_bails_early_when_image_missing(self, account_monitor, mocker):
        channel = await self._setup_channel(account_monitor, existing_messages=[])
        mocker.patch(
            "modules.account_monitor.fetch_data",
            new=AsyncMock(side_effect=[{"good": 5}, {"devices": []}]),
        )
        account_monitor.poliswag.image_generator.generate_image_from_account_stats = (
            AsyncMock(return_value=None)
        )
        await account_monitor.update_channel_accounts_stats()
        channel.send.assert_not_called()
        # An error is logged describing the image failure.
        log_calls = [
            c.args[0]
            for c in account_monitor.poliswag.utility.log_to_file.call_args_list
        ]
        assert any("Error generating account image" in m for m in log_calls)

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
