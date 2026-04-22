import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.scanner_status import ScannerStatus


@pytest.fixture
def scanner_status():
    """A ScannerStatus instance with a mocked poliswag dependency.

    The _log helper routes through poliswag.utility.log_to_file, which the
    MagicMock swallows silently — letting us exercise error paths without
    touching the real logger.
    """
    return ScannerStatus(poliswag=MagicMock())


class TestGetStatusMessage:
    """Pure-logic tests for the status-indicator state machine."""

    def test_none_counter_returns_question_mark(self, scanner_status):
        assert scanner_status.get_status_message(None, "LEIRIA") == "LEIRIA: ❓"
        assert scanner_status.get_status_message(None, "MARINHA") == "MARINHA: ❓"

    # --- MARINHA: binary green/red ---

    def test_marinha_zero_down_is_green(self, scanner_status):
        assert scanner_status.get_status_message(0, "MARINHA") == "MARINHA: 🟢"

    @pytest.mark.parametrize("down_count", [1, 2, 5, 100])
    def test_marinha_any_down_is_red(self, scanner_status, down_count):
        assert scanner_status.get_status_message(down_count, "MARINHA") == "MARINHA: 🔴"

    # --- LEIRIA: four-level threshold (defaultExpectedWorkers = 4) ---

    def test_leiria_zero_down_is_green(self, scanner_status):
        assert scanner_status.get_status_message(0, "LEIRIA") == "LEIRIA: 🟢"

    def test_leiria_one_of_four_is_yellow(self, scanner_status):
        # 1/4 = 0.25 ≤ 0.4 → yellow
        assert scanner_status.get_status_message(1, "LEIRIA") == "LEIRIA: 🟡"

    def test_leiria_two_of_four_is_orange(self, scanner_status):
        # 2/4 = 0.5 ∈ (0.4, 0.8] → orange
        assert scanner_status.get_status_message(2, "LEIRIA") == "LEIRIA: 🟠"

    def test_leiria_three_of_four_is_orange(self, scanner_status):
        # 3/4 = 0.75 ∈ (0.4, 0.8] → orange
        assert scanner_status.get_status_message(3, "LEIRIA") == "LEIRIA: 🟠"

    def test_leiria_four_of_four_is_red(self, scanner_status):
        # 4/4 = 1.0 > 0.8 → red
        assert scanner_status.get_status_message(4, "LEIRIA") == "LEIRIA: 🔴"

    def test_leiria_more_than_expected_is_still_red(self, scanner_status):
        # 10/4 = 2.5 > 0.8 → red; percentage saturates, no overflow
        assert scanner_status.get_status_message(10, "LEIRIA") == "LEIRIA: 🔴"

    # --- Boundary conditions: force exact ratios by adjusting expected workers ---

    def test_boundary_exact_40_percent_is_yellow(self, scanner_status):
        # 2/5 = exactly 0.4 → boundary (≤ 0.4 → yellow)
        scanner_status.defaultExpectedWorkers["LeiriaBigger"] = 5
        assert scanner_status.get_status_message(2, "LEIRIA") == "LEIRIA: 🟡"

    def test_boundary_exact_80_percent_is_orange(self, scanner_status):
        # 4/5 = exactly 0.8 → boundary (≤ 0.8 → orange)
        scanner_status.defaultExpectedWorkers["LeiriaBigger"] = 5
        assert scanner_status.get_status_message(4, "LEIRIA") == "LEIRIA: 🟠"

    def test_unknown_region_falls_into_leiria_branch(self, scanner_status):
        # Any non-MARINHA region is treated by the percentage rule.
        assert scanner_status.get_status_message(0, "SINTRA") == "SINTRA: 🟢"


class TestShouldUpdateChannel:
    """Cache invalidation logic for voice channel renames."""

    def test_empty_cache_triggers_update(self, scanner_status):
        # Default cache has name=None → always update
        assert scanner_status.should_update_channel("leiria", 0) is True

    def test_stale_cache_triggers_update(self, scanner_status, mocker):
        scanner_status.channelCache["leiria"] = {
            "name": "LEIRIA: 🟢",
            "last_update": 0,
        }
        mocker.patch("modules.scanner_status.time.time", return_value=10_000)
        # 10_000 - 0 = 10_000 ≥ UPDATE_THRESHOLD (3600) → stale → update
        assert scanner_status.should_update_channel("leiria", 0) is True

    def test_fresh_cache_same_status_skips_update(self, scanner_status, mocker):
        mocker.patch("modules.scanner_status.time.time", return_value=10_000)
        scanner_status.channelCache["leiria"] = {
            "name": "LEIRIA: 🟢",  # matches get_status_message(0, 'LEIRIA')
            "last_update": 9_500,  # 500s ago, well under UPDATE_THRESHOLD
        }
        assert scanner_status.should_update_channel("leiria", 0) is False

    def test_fresh_cache_different_status_triggers_update(self, scanner_status, mocker):
        mocker.patch("modules.scanner_status.time.time", return_value=10_000)
        scanner_status.channelCache["leiria"] = {
            "name": "LEIRIA: 🟢",  # cached as green
            "last_update": 9_500,
        }
        # counter=4 → red, diverges from cached green → update
        assert scanner_status.should_update_channel("leiria", 4) is True


def _make_fetch_mock(mocker, return_value):
    """Install an AsyncMock replacement for fetch_data inside scanner_status."""
    mock = AsyncMock(return_value=return_value)
    mocker.patch("modules.scanner_status.fetch_data", new=mock)
    return mock


def _worker(last_data, connection_status="Executing Worker"):
    return {"last_data": last_data, "connection_status": connection_status}


def _area(name, workers, expected_workers=None):
    manager = {"workers": workers}
    if expected_workers is not None:
        manager["expected_workers"] = expected_workers
    return {"name": name, "worker_managers": [manager]}


class TestGetWorkersWithIssues:
    """Parses the scanner_status HTTP payload into per-region down counters."""

    async def test_none_response_returns_none_counters(self, scanner_status, mocker):
        _make_fetch_mock(mocker, None)
        result = await scanner_status.get_workers_with_issues()
        assert result == {"downDevicesLeiria": None, "downDevicesMarinha": None}

    async def test_response_missing_areas_returns_none_counters(
        self, scanner_status, mocker
    ):
        _make_fetch_mock(mocker, {"unrelated": "payload"})
        result = await scanner_status.get_workers_with_issues()
        assert result == {"downDevicesLeiria": None, "downDevicesMarinha": None}

    async def test_all_leiria_workers_healthy(self, scanner_status, mocker):
        now = time.time()
        _make_fetch_mock(
            mocker,
            {
                "areas": [
                    _area(
                        "LeiriaBigger",
                        [_worker(now) for _ in range(4)],
                    )
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        # 4 expected, 4 up → 0 down
        assert result["downDevicesLeiria"] == 0
        assert result["downDevicesMarinha"] is None

    async def test_all_leiria_workers_stale_counts_all_down(
        self, scanner_status, mocker
    ):
        stale = time.time() - 10_000  # way past the 600s freshness window
        _make_fetch_mock(
            mocker,
            {
                "areas": [
                    _area(
                        "LeiriaBigger",
                        [_worker(stale) for _ in range(4)],
                    )
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        assert result["downDevicesLeiria"] == 4

    async def test_leiria_partial_down_count(self, scanner_status, mocker):
        now = time.time()
        stale = now - 10_000
        _make_fetch_mock(
            mocker,
            {
                "areas": [
                    _area(
                        "LeiriaBigger",
                        [
                            _worker(now),
                            _worker(now),
                            _worker(stale),
                            _worker(stale),
                        ],
                    )
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        assert result["downDevicesLeiria"] == 2

    async def test_worker_with_wrong_connection_status_counted_as_down(
        self, scanner_status, mocker
    ):
        now = time.time()
        _make_fetch_mock(
            mocker,
            {
                "areas": [
                    _area(
                        "LeiriaBigger",
                        [
                            _worker(now, connection_status="Idle"),
                            _worker(now, connection_status="Executing Worker"),
                            _worker(now, connection_status="Executing Worker"),
                            _worker(now, connection_status="Executing Worker"),
                        ],
                    )
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        # 3 executing + 1 idle → 1 down
        assert result["downDevicesLeiria"] == 1

    async def test_worker_with_no_last_data_counted_as_down(
        self, scanner_status, mocker
    ):
        now = time.time()
        _make_fetch_mock(
            mocker,
            {
                "areas": [
                    _area(
                        "LeiriaBigger",
                        [
                            _worker(None),
                            _worker(now),
                            _worker(now),
                            _worker(now),
                        ],
                    )
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        assert result["downDevicesLeiria"] == 1

    async def test_expected_workers_override_from_payload(self, scanner_status, mocker):
        now = time.time()
        # Override says 6 expected even though default is 4
        _make_fetch_mock(
            mocker,
            {
                "areas": [
                    _area(
                        "LeiriaBigger",
                        [_worker(now) for _ in range(4)],
                        expected_workers=6,
                    )
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        # 6 expected, 4 up → 2 down
        assert result["downDevicesLeiria"] == 2

    async def test_down_counter_never_negative(self, scanner_status, mocker):
        now = time.time()
        # More up workers than the area expects — down should clamp to 0
        _make_fetch_mock(
            mocker,
            {
                "areas": [
                    _area(
                        "LeiriaBigger",
                        [_worker(now) for _ in range(10)],
                    )
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        assert result["downDevicesLeiria"] == 0

    async def test_marinha_workers_parsed_separately(self, scanner_status, mocker):
        now = time.time()
        _make_fetch_mock(
            mocker,
            {
                "areas": [
                    _area("MarinhaGrande", [_worker(now)]),
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        assert result["downDevicesMarinha"] == 0
        assert result["downDevicesLeiria"] is None

    async def test_both_areas_populated(self, scanner_status, mocker):
        now = time.time()
        stale = now - 10_000
        _make_fetch_mock(
            mocker,
            {
                "areas": [
                    _area(
                        "LeiriaBigger",
                        [_worker(now), _worker(now), _worker(stale), _worker(stale)],
                    ),
                    _area("MarinhaGrande", [_worker(stale)]),
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        assert result == {"downDevicesLeiria": 2, "downDevicesMarinha": 1}

    async def test_unknown_area_with_workers_is_skipped(self, scanner_status, mocker):
        # Regression: previously crashed with TypeError because expectedWorkers
        # was None for unknown area names. Now the area is skipped entirely.
        now = time.time()
        _make_fetch_mock(
            mocker,
            {
                "areas": [
                    {
                        "name": "UnknownArea",
                        "worker_managers": [{"workers": [_worker(now)]}],
                    },
                    _area("LeiriaBigger", [_worker(now) for _ in range(4)]),
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        # Unknown area contributes nothing; LeiriaBigger is parsed normally.
        assert result == {"downDevicesLeiria": 0, "downDevicesMarinha": None}

    async def test_unknown_area_never_leaks_into_known_counters(
        self, scanner_status, mocker
    ):
        # Even when the payload supplies an expected_workers override for an
        # unknown area (so the area IS processed rather than skipped), its
        # downDevices value must not leak into LeiriaBigger/MarinhaGrande.
        now = time.time()
        _make_fetch_mock(
            mocker,
            {
                "areas": [
                    _area(
                        "UnknownArea",
                        [_worker(now)],
                        expected_workers=3,
                    )
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        assert result == {"downDevicesLeiria": None, "downDevicesMarinha": None}


class TestGetVoiceChannel:
    async def test_returns_channel_on_success(self, scanner_status, mocker):
        mocker.patch.dict(
            "modules.config.Config.VOICE_CHANNELS",
            {"leiria": 12345},
            clear=True,
        )
        expected = MagicMock(name="channel")
        scanner_status.poliswag.fetch_channel = AsyncMock(return_value=expected)
        result = await scanner_status.get_voice_channel("leiria")
        assert result is expected
        scanner_status.poliswag.fetch_channel.assert_called_once_with(12345)

    async def test_returns_none_and_logs_on_missing_env(self, scanner_status, mocker):
        mocker.patch.dict("modules.config.Config.VOICE_CHANNELS", {}, clear=True)
        scanner_status.poliswag.fetch_channel = AsyncMock()
        result = await scanner_status.get_voice_channel("leiria")
        assert result is None
        scanner_status.poliswag.utility.log_to_file.assert_called_once()

    async def test_returns_none_when_fetch_channel_raises(self, scanner_status, mocker):
        mocker.patch.dict(
            "modules.config.Config.VOICE_CHANNELS",
            {"leiria": 12345},
            clear=True,
        )
        scanner_status.poliswag.fetch_channel = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        result = await scanner_status.get_voice_channel("leiria")
        assert result is None
        scanner_status.poliswag.utility.log_to_file.assert_called_once()


class TestTriggerAllDownAction:
    async def test_sends_dev_log_when_not_production(self, scanner_status, mocker):
        scanner_status.last_all_down_request_time = 0
        scanner_status.poliswag.account_monitor.is_device_connected = AsyncMock(
            return_value=True
        )
        scanner_status.poliswag.account_monitor.get_account_stats = AsyncMock(
            return_value={"good": 5}
        )
        mocker.patch("modules.scanner_status.Config.IS_PRODUCTION", False)
        fetch_mock = mocker.patch("modules.scanner_status.fetch_data", new=AsyncMock())
        await scanner_status.trigger_all_down_action()
        fetch_mock.assert_not_called()
        scanner_status.poliswag.utility.log_to_file.assert_called_once()
        msg = scanner_status.poliswag.utility.log_to_file.call_args.args[0]
        assert "[DEV]" in msg
        assert "accounts" in msg

    async def test_posts_to_endpoint_in_production(self, scanner_status, mocker):
        scanner_status.last_all_down_request_time = 0
        scanner_status.poliswag.account_monitor.is_device_connected = AsyncMock(
            return_value=False
        )
        scanner_status.poliswag.account_monitor.get_account_stats = AsyncMock(
            return_value={"good": 2}
        )
        mocker.patch("modules.scanner_status.Config.IS_PRODUCTION", True)
        fetch_mock = mocker.patch("modules.scanner_status.fetch_data", new=AsyncMock())
        await scanner_status.trigger_all_down_action()
        fetch_mock.assert_awaited_once()
        args, kwargs = fetch_mock.call_args
        assert args[0] == "all_down"
        assert kwargs["method"] == "POST"
        payload = kwargs["data"]
        assert payload["type"] == "map_status"
        assert payload["value"]["accounts"] == 2
        assert payload["value"]["device_status"] is False

    async def test_cooldown_skips_subsequent_triggers(self, scanner_status, mocker):
        scanner_status.last_all_down_request_time = time.time()
        fetch_mock = mocker.patch("modules.scanner_status.fetch_data", new=AsyncMock())
        scanner_status.poliswag.account_monitor.is_device_connected = AsyncMock()
        scanner_status.poliswag.account_monitor.get_account_stats = AsyncMock()
        await scanner_status.trigger_all_down_action()
        fetch_mock.assert_not_called()
        scanner_status.poliswag.account_monitor.is_device_connected.assert_not_called()

    async def test_logs_on_inner_exception(self, scanner_status, mocker):
        scanner_status.last_all_down_request_time = 0
        scanner_status.poliswag.account_monitor.is_device_connected = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        scanner_status.poliswag.account_monitor.get_account_stats = AsyncMock()
        await scanner_status.trigger_all_down_action()
        scanner_status.poliswag.utility.log_to_file.assert_called_once()
        msg = scanner_status.poliswag.utility.log_to_file.call_args.args[0]
        assert "Error sending all-down" in msg


class TestRenameVoiceChannels:
    async def test_both_regions_all_down_triggers_all_down_action(
        self, scanner_status, mocker
    ):
        trigger = mocker.patch.object(
            scanner_status, "trigger_all_down_action", new=AsyncMock()
        )
        mocker.patch.object(
            scanner_status, "get_voice_channel", new=AsyncMock(return_value=None)
        )
        await scanner_status.rename_voice_channels(4, 1)
        trigger.assert_awaited_once()

    async def test_none_counters_do_not_trigger_all_down(self, scanner_status, mocker):
        trigger = mocker.patch.object(
            scanner_status, "trigger_all_down_action", new=AsyncMock()
        )
        mocker.patch.object(
            scanner_status, "get_voice_channel", new=AsyncMock(return_value=None)
        )
        await scanner_status.rename_voice_channels(None, None)
        trigger.assert_not_called()

    async def test_updates_channel_name_when_cache_stale(self, scanner_status, mocker):
        mocker.patch.object(scanner_status, "trigger_all_down_action", new=AsyncMock())
        channel = MagicMock()
        channel.edit = AsyncMock()
        mocker.patch.object(
            scanner_status, "get_voice_channel", new=AsyncMock(return_value=channel)
        )
        await scanner_status.rename_voice_channels(0, 0)
        assert channel.edit.await_count == 2
        assert scanner_status.channelCache["leiria"]["name"] == "LEIRIA: 🟢"
        assert scanner_status.channelCache["marinha"]["name"] == "MARINHA: 🟢"

    async def test_skips_edit_when_status_unchanged(self, scanner_status, mocker):
        mocker.patch.object(scanner_status, "trigger_all_down_action", new=AsyncMock())
        now = time.time()
        scanner_status.channelCache["leiria"] = {
            "name": "LEIRIA: 🟢",
            "last_update": now,
        }
        scanner_status.channelCache["marinha"] = {
            "name": "MARINHA: 🟢",
            "last_update": now,
        }
        get_channel = mocker.patch.object(
            scanner_status, "get_voice_channel", new=AsyncMock()
        )
        await scanner_status.rename_voice_channels(0, 0)
        get_channel.assert_not_called()

    async def test_rate_limit_is_logged_not_raised(self, scanner_status, mocker):
        import discord

        mocker.patch.object(scanner_status, "trigger_all_down_action", new=AsyncMock())
        channel = MagicMock()
        exc = discord.errors.HTTPException(
            MagicMock(status=429, reason=""), {"message": "rate limited", "code": 429}
        )
        exc.code = 429
        channel.edit = AsyncMock(side_effect=exc)
        mocker.patch.object(
            scanner_status, "get_voice_channel", new=AsyncMock(return_value=channel)
        )
        await scanner_status.rename_voice_channels(0, 0)
        logged = [
            c.args[0]
            for c in scanner_status.poliswag.utility.log_to_file.call_args_list
        ]
        assert any("Rate limited" in m for m in logged)


class TestIsQuestScanningComplete:
    async def test_returns_none_near_midnight(self, scanner_status, mocker):
        fake_now = MagicMock()
        fake_now.hour = 0
        fake_now.minute = 1
        mock_dt_module = MagicMock()
        mock_dt_module.datetime.now.return_value = fake_now
        mocker.patch("modules.scanner_status.datetime", new=mock_dt_module)
        assert await scanner_status.is_quest_scanning_complete() is None

    async def test_returns_none_when_scanning_ongoing(self, scanner_status, mocker):
        import datetime as _dt

        mock_datetime = mocker.patch("modules.scanner_status.datetime.datetime")
        mock_datetime.now.return_value = _dt.datetime(2024, 1, 2, 3, 0, 0)
        scanner_status.poliswag.db.get_data_from_database.return_value = [
            {"scanned": 1}
        ]
        assert await scanner_status.is_quest_scanning_complete() is None

    async def test_returns_none_when_fetch_data_empty(self, scanner_status, mocker):
        import datetime as _dt

        mock_datetime = mocker.patch("modules.scanner_status.datetime.datetime")
        mock_datetime.now.return_value = _dt.datetime(2024, 1, 2, 3, 0, 0)
        scanner_status.poliswag.db.get_data_from_database.return_value = []
        mocker.patch(
            "modules.scanner_status.fetch_data",
            new=AsyncMock(side_effect=[None, {"ar_quests": 5, "total": 10}]),
        )
        assert await scanner_status.is_quest_scanning_complete() is None

    async def test_returns_none_when_ar_quests_zero(self, scanner_status, mocker):
        import datetime as _dt

        mock_datetime = mocker.patch("modules.scanner_status.datetime.datetime")
        mock_datetime.now.return_value = _dt.datetime(2024, 1, 2, 3, 0, 0)
        scanner_status.poliswag.db.get_data_from_database.return_value = []
        mocker.patch(
            "modules.scanner_status.fetch_data",
            new=AsyncMock(
                side_effect=[
                    {"ar_quests": 0, "total": 10},
                    {"ar_quests": 5, "total": 10},
                ]
            ),
        )
        assert await scanner_status.is_quest_scanning_complete() is None

    async def test_returns_completion_dict_when_fully_scanned(
        self, scanner_status, mocker
    ):
        import datetime as _dt

        mock_datetime = mocker.patch("modules.scanner_status.datetime.datetime")
        mock_datetime.now.return_value = _dt.datetime(2024, 1, 2, 3, 0, 0)
        scanner_status.poliswag.db.get_data_from_database.return_value = []
        mocker.patch(
            "modules.scanner_status.fetch_data",
            new=AsyncMock(
                side_effect=[
                    {"ar_quests": 100, "total": 100},
                    {"ar_quests": 50, "total": 50},
                ]
            ),
        )
        result = await scanner_status.is_quest_scanning_complete()
        assert result["leiriaCompleted"] is True
        assert result["marinhaCompleted"] is True
        assert result["leiriaPercentage"] == 100
        assert result["marinhaPercentage"] == 100
        assert result["leiriaTotal"] == 100
        assert result["marinhaScanned"] == 50

    async def test_reports_incomplete_when_below_threshold(
        self, scanner_status, mocker
    ):
        import datetime as _dt

        mock_datetime = mocker.patch("modules.scanner_status.datetime.datetime")
        mock_datetime.now.return_value = _dt.datetime(2024, 1, 2, 3, 0, 0)
        scanner_status.poliswag.db.get_data_from_database.return_value = []
        mocker.patch(
            "modules.scanner_status.fetch_data",
            new=AsyncMock(
                side_effect=[
                    {"ar_quests": 50, "total": 100},
                    {"ar_quests": 49, "total": 50},
                ]
            ),
        )
        result = await scanner_status.is_quest_scanning_complete()
        assert result["leiriaCompleted"] is False
        assert result["leiriaPercentage"] == 50

    async def test_returns_none_on_exception(self, scanner_status, mocker):
        import datetime as _dt

        mock_datetime = mocker.patch("modules.scanner_status.datetime.datetime")
        mock_datetime.now.return_value = _dt.datetime(2024, 1, 2, 3, 0, 0)
        scanner_status.poliswag.db.get_data_from_database.return_value = []
        mocker.patch(
            "modules.scanner_status.fetch_data",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        )
        assert await scanner_status.is_quest_scanning_complete() is None
        scanner_status.poliswag.utility.log_to_file.assert_called_once()
