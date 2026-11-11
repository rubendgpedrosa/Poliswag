from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.account_monitor import (
    ACCOUNT_CARD_ERROR_AFTER,
    CARD_REFRESH_SECONDS,
    OUT_STATUSES,
    AccountMonitor,
    _device_name,
    _worker_counts,
    summarize_accounts,
)
from modules.config import Config


@pytest.fixture
def account_monitor():
    """An AccountMonitor with a mocked poliswag dependency.

    poliswag.utility.log_to_file is auto-mocked by MagicMock so error paths
    don't touch the real logger. scanner_status.get_iv_verification_by_area
    defaults to no data -- tests that care about its content override it.
    """
    poliswag = MagicMock()
    poliswag.scanner_status.get_iv_verification_by_area = AsyncMock(return_value={})
    return AccountMonitor(poliswag=poliswag)


def _core(result):
    """The four keys every caller of get_account_stats relies on."""
    return {k: result[k] for k in ("in_use", "good", "cooldown", "disabled")}


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
        assert _core(result) == {"in_use": 12, "good": 45, "cooldown": 7, "disabled": 0}

    async def test_missing_keys_default_to_zero(self, account_monitor, mocker):
        # Only `good` present — the other three should fall back to 0.
        _mock_fetch(mocker, {"good": 10})
        result = await account_monitor.get_account_stats()
        assert _core(result) == {"in_use": 0, "good": 10, "cooldown": 0, "disabled": 0}

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
        assert _core(result) == {
            "in_use": 1,
            "good": 2,
            "cooldown": 3,
            "disabled": 4 + 5 + 6,
        }

    async def test_all_disabled_statuses_counted(self, account_monitor, mocker):
        # Every known OUT_STATUSES entry should contribute to the sum.
        payload = {status: 1 for status in OUT_STATUSES}
        _mock_fetch(mocker, payload)
        result = await account_monitor.get_account_stats()
        assert result["disabled"] == len(OUT_STATUSES)

    async def test_unknown_keys_do_not_leak_into_disabled(
        self, account_monitor, mocker
    ):
        # Statuses that aren't in OUT_STATUSES must be ignored for the sum.
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
            {"banned": 3, "invalid": 2},  # two of the OUT_STATUSES
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


class TestConnectedWorkerCount:
    """Aegis workers registered in rotom-ng; None when rotom-ng didn't answer."""

    async def test_unreachable_rotom_is_none_not_zero(self, account_monitor, mocker):
        _mock_fetch(mocker, None)
        assert await account_monitor.connected_worker_count() is None

    async def test_sums_connected_devices_only(self, account_monitor, mocker):
        _mock_fetch(
            mocker,
            {
                "devices": [
                    {"is_connected": True, "worker_count": 16},
                    {"is_connected": False, "worker_count": 16},
                ]
            },
        )
        assert await account_monitor.connected_worker_count() == 16

    async def test_offline_phone_is_zero_workers(self, account_monitor, mocker):
        _mock_fetch(mocker, {"devices": [{"is_connected": False, "worker_count": 16}]})
        assert await account_monitor.connected_worker_count() == 0


class TestOutStatusesConstant:
    """Regression guards for the canonical list of disabled account statuses."""

    def test_contains_core_statuses(self):
        # If any of these disappear, account reporting will silently undercount.
        assert "banned" in OUT_STATUSES
        assert "warned" in OUT_STATUSES
        assert "suspended" in OUT_STATUSES
        assert "disabled" in OUT_STATUSES

    def test_has_no_duplicates(self):
        assert len(OUT_STATUSES) == len(set(OUT_STATUSES))


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
        assert "file" in channel.send.call_args.kwargs

    async def test_passes_area_performance_to_image_generator(
        self, account_monitor, mocker
    ):
        await self._setup_channel(account_monitor, existing_messages=[])
        mocker.patch(
            "modules.account_monitor.fetch_data",
            new=AsyncMock(side_effect=[{"good": 5}, {"devices": []}]),
        )
        area_data = {"Leiria": {"total": 100, "iv": 99}}
        account_monitor.poliswag.scanner_status.get_iv_verification_by_area = AsyncMock(
            return_value=area_data
        )
        account_monitor.poliswag.image_generator.generate_image_from_account_stats = (
            AsyncMock(return_value=b"PNG")
        )
        await account_monitor.update_channel_accounts_stats()
        call_args = (
            account_monitor.poliswag.image_generator.generate_image_from_account_stats.call_args
        )
        assert call_args.args[1] == area_data

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
        # The renderer logs the detail itself; this only counts the failure,
        # without the old bare "Error generating account image" line.
        account_monitor.poliswag.utility.log_to_file.assert_not_called()
        assert account_monitor._card_failures.count == 1

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

    async def test_edit_replaces_embed_and_attachment(self, account_monitor, mocker):
        # Regression: a message cached from an earlier design (either a
        # stale image, or the brief plain-embed version) must be fully
        # replaced -- embed=None clears a leftover embed, and passing the
        # new file via attachments=[...] replaces any old attachment,
        # since .edit() otherwise keeps whatever wasn't explicitly passed.
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
        account_monitor.poliswag.image_generator.generate_image_from_account_stats = (
            AsyncMock(return_value=b"PNG")
        )
        await account_monitor.update_channel_accounts_stats()
        assert existing.edit.call_args.kwargs["embed"] is None
        assert len(existing.edit.call_args.kwargs["attachments"]) == 1
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
            new=AsyncMock(
                side_effect=[
                    {"good": 5},
                    {"devices": []},
                    {"good": 6},
                    {"devices": []},
                ]
            ),
        )
        account_monitor.poliswag.image_generator.generate_image_from_account_stats = (
            AsyncMock(return_value=b"PNG")
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
        account_monitor.poliswag.image_generator.generate_image_from_account_stats = (
            AsyncMock(return_value=b"PNG")
        )

        await account_monitor.update_channel_accounts_stats()

        stale_message.edit.assert_awaited_once()
        # No cache hit means no history scan was needed to discover this was stale.
        channel.history.assert_not_called()
        channel.send.assert_awaited_once()
        assert account_monitor._accounts_message is new_message


class TestAccountCardDrawnOnChange:
    """The numbers are read every minute; the image is drawn when they move."""

    @pytest.fixture
    def card(self, account_monitor, mocker):
        message = MagicMock()
        message.edit = AsyncMock()
        channel = MagicMock()
        channel.send = AsyncMock()
        account_monitor.poliswag.ACCOUNTS_CHANNEL = channel
        account_monitor._accounts_message = message
        account_monitor.build_account_card = AsyncMock(return_value=b"PNG")
        self.pools = [{"key": "scanning", "accounts": {"ready": 205}, "device": None}]
        account_monitor.get_pool_stats = AsyncMock(side_effect=lambda: self.pools)
        self.clock = mocker.patch("modules.account_monitor.time.monotonic")
        self.clock.return_value = 1000.0
        return account_monitor

    async def test_the_first_update_of_a_process_always_draws(self, card):
        await card.update_channel_accounts_stats()
        card.build_account_card.assert_awaited_once_with(self.pools)
        card._accounts_message.edit.assert_awaited_once()

    async def test_the_same_numbers_a_minute_later_draw_nothing(self, card):
        await card.update_channel_accounts_stats()
        self.clock.return_value += 60
        await card.update_channel_accounts_stats()
        assert card.get_pool_stats.await_count == 2
        card.build_account_card.assert_awaited_once()
        card._accounts_message.edit.assert_awaited_once()

    async def test_a_changed_number_draws_at_once(self, card):
        await card.update_channel_accounts_stats()
        self.clock.return_value += 60
        self.pools = [{"key": "scanning", "accounts": {"ready": 204}, "device": None}]
        await card.update_channel_accounts_stats()
        assert card.build_account_card.await_count == 2
        assert card._accounts_message.edit.await_count == 2

    async def test_a_device_going_offline_draws_at_once(self, card):
        self.pools = [
            {"key": "scanning", "accounts": None, "device": {"connected": True}}
        ]
        await card.update_channel_accounts_stats()
        self.clock.return_value += 60
        self.pools = [
            {"key": "scanning", "accounts": None, "device": {"connected": False}}
        ]
        await card.update_channel_accounts_stats()
        assert card.build_account_card.await_count == 2

    async def test_unchanged_numbers_are_redrawn_after_the_refresh_time(self, card):
        # The area footer (spawns per hour) is not part of the comparison.
        await card.update_channel_accounts_stats()
        self.clock.return_value += CARD_REFRESH_SECONDS - 1
        await card.update_channel_accounts_stats()
        card.build_account_card.assert_awaited_once()
        self.clock.return_value += 1
        await card.update_channel_accounts_stats()
        assert card.build_account_card.await_count == 2

    async def test_a_failed_render_is_tried_again_next_minute(self, card):
        card.build_account_card = AsyncMock(side_effect=[None, b"PNG"])
        await card.update_channel_accounts_stats()
        card._accounts_message.edit.assert_not_awaited()
        self.clock.return_value += 60
        await card.update_channel_accounts_stats()
        card._accounts_message.edit.assert_awaited_once()

    async def test_a_failed_upload_is_tried_again_next_minute(self, card):
        card._accounts_message.edit = AsyncMock(
            side_effect=[
                discord.HTTPException(MagicMock(status=503, reason=""), "busy"),
                None,
            ]
        )
        await card.update_channel_accounts_stats()
        self.clock.return_value += 60
        await card.update_channel_accounts_stats()
        assert card._accounts_message.edit.await_count == 2
        assert card.build_account_card.await_count == 2

    async def test_a_deleted_message_is_sent_again_and_remembered(self, card):
        card._accounts_message.edit = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404, reason=""), "gone")
        )
        await card.update_channel_accounts_stats()
        card.poliswag.ACCOUNTS_CHANNEL.send.assert_awaited_once()
        self.clock.return_value += 60
        await card.update_channel_accounts_stats()
        # Sent once; the same numbers a minute later draw nothing.
        card.poliswag.ACCOUNTS_CHANNEL.send.assert_awaited_once()
        card.build_account_card.assert_awaited_once()

    async def test_the_command_still_fetches_its_own_numbers(self, account_monitor):
        account_monitor.get_pool_stats = AsyncMock(return_value=[])
        account_monitor.poliswag.image_generator.generate_image_from_account_stats = (
            AsyncMock(return_value=b"PNG")
        )
        assert await account_monitor.build_account_card() == b"PNG"
        account_monitor.get_pool_stats.assert_awaited_once()


class TestAccountCardFailureLevels:
    """A failed card update is a WARNING; ten in a row are one ERROR."""

    @pytest.fixture
    def card(self, account_monitor):
        channel = MagicMock()
        channel.history = MagicMock(return_value=_AsyncChannelHistory([]))
        channel.send = AsyncMock()
        account_monitor.poliswag.ACCOUNTS_CHANNEL = channel
        account_monitor.build_account_card = AsyncMock(return_value=b"PNG")
        return account_monitor

    @staticmethod
    def _levels(account_monitor):
        return [
            (c.args[1], c.args[0])
            for c in account_monitor.poliswag.utility.log_to_file.call_args_list
        ]

    async def test_one_failed_upload_is_a_warning_naming_the_exception(self, card):
        card.poliswag.ACCOUNTS_CHANNEL.send.side_effect = ConnectionResetError(
            "Connection reset by peer"
        )
        await card.update_channel_accounts_stats()
        assert self._levels(card) == [
            (
                "WARNING",
                "Error in update_channel_accounts_stats: "
                "ConnectionResetError: Connection reset by peer",
            )
        ]

    async def test_ten_in_a_row_log_exactly_one_error(self, card):
        card.poliswag.ACCOUNTS_CHANNEL.send.side_effect = ConnectionResetError("x")
        for _ in range(ACCOUNT_CARD_ERROR_AFTER + 5):
            await card.update_channel_accounts_stats()
        levels = [level for level, _ in self._levels(card)]
        assert levels.count("ERROR") == 1
        assert levels.index("ERROR") == ACCOUNT_CARD_ERROR_AFTER - 1
        assert levels.count("WARNING") == ACCOUNT_CARD_ERROR_AFTER - 1 + 5
        [message] = [m for lvl, m in self._levels(card) if lvl == "ERROR"]
        assert f"{ACCOUNT_CARD_ERROR_AFTER} times in a row" in message
        assert "ConnectionResetError" in message

    async def test_a_success_in_between_resets_the_streak(self, card):
        before = ACCOUNT_CARD_ERROR_AFTER - 1
        outcomes = [ConnectionResetError("x")] * before
        outcomes += [MagicMock()]
        outcomes += [ConnectionResetError("x")] * before
        card.poliswag.ACCOUNTS_CHANNEL.send.side_effect = outcomes
        for _ in outcomes:
            # Resend every time so each update goes through send().
            card._accounts_message = None
            await card.update_channel_accounts_stats()
        levels = [level for level, _ in self._levels(card)]
        assert "ERROR" not in levels
        assert levels.count("WARNING") == 2 * before

    async def test_missing_images_count_without_the_old_bare_line(self, card):
        card.build_account_card = AsyncMock(return_value=None)
        for _ in range(ACCOUNT_CARD_ERROR_AFTER):
            await card.update_channel_accounts_stats()
        logged = self._levels(card)
        assert logged == [
            (
                "ERROR",
                f"Account card update failed {ACCOUNT_CARD_ERROR_AFTER} times "
                "in a row",
            )
        ]
        assert not any(m == "Error generating account image" for _, m in logged)

    async def test_deleted_message_resends_and_is_not_a_failure(self, card):
        card._card_failures.fail()
        stale = MagicMock()
        stale.edit = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404, reason=""), "gone")
        )
        card._accounts_message = stale
        new_message = MagicMock()
        card.poliswag.ACCOUNTS_CHANNEL.send = AsyncMock(return_value=new_message)
        await card.update_channel_accounts_stats()
        card.poliswag.ACCOUNTS_CHANNEL.send.assert_awaited_once()
        assert card._accounts_message is new_message
        assert card._card_failures.count == 0
        card.poliswag.utility.log_to_file.assert_not_called()


class TestWorkerCounts:
    """_worker_counts summarises the Rotom worker pool for the card chip."""

    def test_no_devices_is_zeroed(self):
        assert _worker_counts([]) == {"total": 0, "in_use": 0}

    def test_sums_across_connected_devices(self):
        devices = [
            {"is_connected": True, "worker_count": 16, "worker_in_use_count": 14},
            {"is_connected": True, "worker_count": 4, "worker_in_use_count": 1},
        ]
        assert _worker_counts(devices) == {"total": 20, "in_use": 15}

    def test_disconnected_devices_are_excluded(self):
        # Rotom keeps reporting a dropped device's last-known worker_count;
        # counting it would show a full pool for a device that is offline.
        devices = [
            {"is_connected": True, "worker_count": 16, "worker_in_use_count": 14},
            {"is_connected": False, "worker_count": 99, "worker_in_use_count": 99},
        ]
        assert _worker_counts(devices) == {"total": 16, "in_use": 14}

    def test_legacy_rotom_payload_yields_zero(self):
        # The legacy Node Rotom shape has no worker fields at all -- zero
        # totals are what make the template hide the chip.
        assert _worker_counts([{"isAlive": True}]) == {"total": 0, "in_use": 0}

    def test_null_worker_fields_are_treated_as_zero(self):
        assert _worker_counts(
            [{"is_connected": True, "worker_count": None, "worker_in_use_count": None}]
        ) == {"total": 0, "in_use": 0}


class TestGetDeviceSnapshot:
    """get_device_snapshot returns connectivity and workers from one fetch."""

    async def test_single_fetch_serves_both_fields(self, account_monitor, mocker):
        fetch = _mock_fetch(
            mocker,
            {
                "devices": [
                    {"is_connected": True, "worker_count": 16, "worker_in_use_count": 9}
                ]
            },
        )
        snapshot = await account_monitor.get_device_snapshot()
        assert snapshot["connected"] is True
        assert snapshot["workers"] == {"total": 16, "in_use": 9}
        # One Rotom call per tick, not one per field.
        fetch.assert_awaited_once()

    async def test_none_payload_is_safe(self, account_monitor, mocker):
        _mock_fetch(mocker, None)
        snapshot = await account_monitor.get_device_snapshot()
        assert snapshot == {"connected": False, "workers": {"total": 0, "in_use": 0}}


class TestStatusBody:
    """The message body carries a live last-update timestamp."""

    def test_body_uses_discord_timestamp_markup(self, mocker):
        mocker.patch("modules.account_monitor.time.time", return_value=1788853091.7)
        body = AccountMonitor._build_status_body()
        # Absolute for the date the user asked for, relative so a stalled
        # card keeps visibly ageing between ticks.
        assert "<t:1788853091:f>" in body
        assert "<t:1788853091:R>" in body

    def test_body_advances_between_ticks(self, mocker):
        t = mocker.patch("modules.account_monitor.time.time", return_value=100.0)
        first = AccountMonitor._build_status_body()
        t.return_value = 200.0
        assert AccountMonitor._build_status_body() != first


class TestStatusBodyIsPublished:
    """Both the edit and the send paths must carry the body + workers."""

    async def _run(self, account_monitor, mocker, existing_messages=()):
        channel = MagicMock()
        channel.history = MagicMock(
            return_value=_AsyncChannelHistory(list(existing_messages))
        )
        channel.send = AsyncMock()
        account_monitor.poliswag.ACCOUNTS_CHANNEL = channel
        mocker.patch(
            "modules.account_monitor.fetch_data",
            new=AsyncMock(
                side_effect=[
                    {"good": 5},
                    {
                        "devices": [
                            {
                                "is_connected": True,
                                "worker_count": 16,
                                "worker_in_use_count": 14,
                            }
                        ]
                    },
                ]
            ),
        )
        account_monitor.poliswag.image_generator.generate_image_from_account_stats = (
            AsyncMock(return_value=b"PNG")
        )
        await account_monitor.update_channel_accounts_stats()
        return channel

    async def test_send_path_includes_body(self, account_monitor, mocker):
        channel = await self._run(account_monitor, mocker)
        assert "<t:" in channel.send.call_args.kwargs["content"]

    async def test_edit_path_includes_body(self, account_monitor, mocker):
        # Authored by the bot so the history scan adopts it as the message
        # to edit rather than sending a new one.
        existing = MagicMock()
        existing.edit = AsyncMock()
        existing.author = account_monitor.poliswag.user
        channel = await self._run(account_monitor, mocker, [existing])
        channel.send.assert_not_called()
        assert "<t:" in existing.edit.call_args.kwargs["content"]

    async def test_worker_counts_reach_the_image_generator(
        self, account_monitor, mocker
    ):
        await self._run(account_monitor, mocker)
        gen = account_monitor.poliswag.image_generator.generate_image_from_account_stats
        pools = gen.call_args.args[0]
        assert [p["key"] for p in pools] == ["scanning"]
        assert pools[0]["device"]["workers"] == {"total": 16, "in_use": 14}


# Live prod numbers from 2026-11-05: the buckets that partition `total`, plus
# `missing_token`, which overlaps them and must never be summed.
PROD_STATS = {
    "total": 312,
    "banned": 5,
    "invalid": 0,
    "auth_banned": 0,
    "suspended": 0,
    "warned": 0,
    "disabled": 0,
    "missing_token": 0,
    "cooldown": 57,
    "good": 199,
    "in_use": 45,
    "provider_disabled": 0,
    "zero_last_released": 6,
}


class TestSummarizeAccounts:
    def test_prod_numbers_partition_the_total(self):
        s = summarize_accounts(PROD_STATS)
        assert (s["in_use"], s["ready"], s["cooldown"], s["out"]) == (45, 205, 57, 5)
        assert s["in_use"] + s["ready"] + s["cooldown"] + s["out"] == s["total"] == 312
        assert s["partition_sum"] == 312

    def test_never_used_counts_as_ready_not_out(self):
        s = summarize_accounts({"total": 6, "zero_last_released": 6})
        assert s["ready"] == 6 and s["never_used"] == 6 and s["out"] == 0

    def test_missing_token_is_not_summed(self):
        # The levelling pool on 2026-11-05: 9 invalid accounts were also
        # missing a token; counting both made 139 out of 125.
        stats = {
            "total": 125,
            "invalid": 9,
            "missing_token": 14,
            "cooldown": 48,
            "good": 66,
            "in_use": 2,
        }
        s = summarize_accounts(stats)
        assert s["out"] == 9 and s["out_detail"] == {"invalid": 9}
        assert s["in_use"] + s["ready"] + s["cooldown"] + s["out"] == 125
        assert s["missing_token"] == 14

    def test_out_detail_lists_only_nonzero_buckets(self):
        s = summarize_accounts({"banned": 2, "invalid": 0, "warned": 1})
        assert s["out_detail"] == {"banned": 2, "warned": 1}

    def test_total_falls_back_to_partition_sum(self):
        s = summarize_accounts({"good": 3, "in_use": 2})
        assert s["total"] == 5

    def test_none_payload_is_all_zero(self):
        s = summarize_accounts(None)
        assert s["total"] == 0 and s["out_detail"] == {}


class TestGetAccountStatsBreakdown:
    async def test_disabled_excludes_never_used_and_missing_token(
        self, account_monitor, mocker
    ):
        _mock_fetch(mocker, PROD_STATS | {"missing_token": 9})
        result = await account_monitor.get_account_stats()
        assert result["disabled"] == 5
        assert result["ready"] == 205 and result["never_used"] == 6
        assert result["total"] == 312


def _device_payload(name, connected=True, total=16, in_use=14):
    return {
        "devices": [
            {
                "id": name,
                "origin": f"PoliSnatch/{name}",
                "is_connected": connected,
                "worker_count": total,
                "worker_in_use_count": in_use,
            }
        ]
    }


def _route_fetch(mocker, responses):
    """fetch_data stand-in answering per endpoint key (missing key -> None)."""

    async def fake(key, log_fn=None, **kwargs):
        value = responses.get(key)
        if isinstance(value, Exception):
            raise value
        return value

    mock = AsyncMock(side_effect=fake)
    mocker.patch("modules.account_monitor.fetch_data", new=mock)
    return mock


class TestGetPoolStats:
    async def test_level_pool_hidden_when_not_configured(self, account_monitor, mocker):
        mocker.patch.dict(
            Config.ENDPOINTS,
            {"level_account_status": None, "level_device_status": None},
        )
        fetch = _route_fetch(
            mocker,
            {
                "account_status": PROD_STATS,
                "device_status": _device_payload("PocoF2Pro"),
            },
        )
        pools = await account_monitor.get_pool_stats()
        assert [p["key"] for p in pools] == ["scanning"]
        assert {c.args[0] for c in fetch.call_args_list} == {
            "account_status",
            "device_status",
        }
        assert pools[0]["accounts"]["ready"] == 205
        assert pools[0]["device"]["names"] == ["PocoF2Pro"]

    async def test_both_pools_when_configured(self, account_monitor, mocker):
        mocker.patch.dict(
            Config.ENDPOINTS,
            {"level_account_status": "http://l/a", "level_device_status": "http://l/d"},
        )
        _route_fetch(
            mocker,
            {
                "account_status": PROD_STATS,
                "device_status": _device_payload("PocoF2Pro", total=50, in_use=45),
                "level_account_status": {"total": 17, "in_use": 16, "cooldown": 1},
                "level_device_status": _device_payload(
                    "RedmiNote4", total=16, in_use=16
                ),
            },
        )
        pools = await account_monitor.get_pool_stats()
        assert [p["key"] for p in pools] == ["scanning", "levelling"]
        level = pools[1]
        assert level["accounts"]["in_use"] == 16 and level["accounts"]["total"] == 17
        assert level["device"]["workers"] == {"total": 16, "in_use": 16}

    async def test_failing_level_endpoint_keeps_scanning_pool(
        self, account_monitor, mocker
    ):
        mocker.patch.dict(
            Config.ENDPOINTS,
            {"level_account_status": "http://l/a", "level_device_status": "http://l/d"},
        )
        _route_fetch(
            mocker,
            {
                "account_status": PROD_STATS,
                "device_status": _device_payload("PocoF2Pro"),
            },
        )
        pools = await account_monitor.get_pool_stats()
        assert pools[0]["accounts"]["total"] == 312
        assert pools[1]["accounts"] is None and pools[1]["device"] is None

    async def test_partition_mismatch_logged_once_per_change(
        self, account_monitor, mocker
    ):
        mocker.patch.dict(Config.ENDPOINTS, {"level_account_status": None})
        stats = {"total": 10, "good": 8}
        _route_fetch(
            mocker, {"account_status": stats, "device_status": {"devices": []}}
        )
        log = account_monitor.poliswag.utility.log_to_file
        await account_monitor.get_pool_stats()
        await account_monitor.get_pool_stats()
        warnings = [c for c in log.call_args_list if "buckets sum to" in c.args[0]]
        assert len(warnings) == 1
        stats["good"] = 7
        await account_monitor.get_pool_stats()
        warnings = [c for c in log.call_args_list if "buckets sum to" in c.args[0]]
        assert len(warnings) == 2


class TestDeviceName:
    def test_prefers_id(self):
        assert (
            _device_name({"id": "PocoF2Pro", "origin": "PoliSnatch/X"}) == "PocoF2Pro"
        )

    def test_strips_origin_prefix(self):
        assert _device_name({"origin": "PoliSnatch/RedmiNote4"}) == "RedmiNote4"

    def test_legacy_device_id(self):
        assert _device_name({"deviceId": "PoGoLeiria"}) == "PoGoLeiria"


class TestBuildAccountCard:
    async def test_passes_pools_and_area_performance(self, account_monitor, mocker):
        pools = [{"key": "scanning"}]
        mocker.patch.object(
            account_monitor, "get_pool_stats", AsyncMock(return_value=pools)
        )
        area = {"Leiria": {"total": 10, "iv": 9}}
        account_monitor.poliswag.scanner_status.get_iv_verification_by_area = AsyncMock(
            return_value=area
        )
        gen = AsyncMock(return_value=b"PNG")
        account_monitor.poliswag.image_generator.generate_image_from_account_stats = gen
        assert await account_monitor.build_account_card() == b"PNG"
        gen.assert_awaited_once_with(pools, area)
