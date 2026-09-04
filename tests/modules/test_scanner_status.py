import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.scanner_status import ScannerStatus


@pytest.fixture
def scanner_status():
    """A ScannerStatus instance with a mocked poliswag dependency.

    The _log helper routes through poliswag.utility.log_to_file, which the
    MagicMock swallows silently — letting us exercise error paths without
    touching the real logger. stack_recovery.observe is awaited from
    rename_voice_channels, so it needs to be a real AsyncMock.
    """
    poliswag = MagicMock()
    poliswag.stack_recovery.observe = AsyncMock()
    poliswag.db = AsyncMock()
    poliswag.quest_search.db = AsyncMock()
    return ScannerStatus(poliswag=poliswag)


class TestGetStatusMessage:
    """Pure-logic tests for the combined status-indicator state machine.

    Leiria and Marinha share one device/stack, so all four inputs (down count
    + expected count per area, both read live from Dragonite on every tick —
    see TestGetWorkersWithIssues) feed one indicator. Production values are
    currently 7 (LeiriaBigger) + 1 (MarinhaGrande) = 8 total, but nothing here
    hardcodes that — it's just what's passed in.

    Three states only: 🟢 the map is being scanned (however few accounts are
    left), 🔴 every worker down with the device still connected, ❌ device
    offline or no data at all.
    """

    def test_missing_input_renders_as_cross(self, scanner_status):
        # Any of the four being None — Dragonite unreachable, or an area
        # absent from its response — still means the map isn't being scanned,
        # so it displays the same ❌ as a confirmed device-offline red. This
        # is a display choice only: rename_voice_channels keeps this out of
        # all_red so a data outage never triggers the recovery ladder (see
        # TestRenameVoiceChannels.test_dragonite_unreachable_renders_as_cross).
        assert scanner_status.get_status_message(None, 0, 7, 1) == "STATUS: ❌"
        assert scanner_status.get_status_message(0, None, 7, 1) == "STATUS: ❌"
        assert scanner_status.get_status_message(0, 0, None, 1) == "STATUS: ❌"
        assert scanner_status.get_status_message(0, 0, 7, None) == "STATUS: ❌"
        assert scanner_status.get_status_message(None, None, None, None) == "STATUS: ❌"

    def test_zero_down_is_green(self, scanner_status):
        assert scanner_status.get_status_message(0, 0, 7, 1) == "STATUS: 🟢"

    def test_partially_down_is_still_green(self, scanner_status):
        # A thinned account pool still scans the map, so it stays 🟢 — the
        # whole point of dropping the 🟡/🟠 tiers is that these swings no
        # longer rename the status channel.
        assert scanner_status.get_status_message(1, 0, 7, 1) == "STATUS: 🟢"
        assert scanner_status.get_status_message(3, 1, 7, 1) == "STATUS: 🟢"
        assert scanner_status.get_status_message(6, 1, 7, 1) == "STATUS: 🟢"

    def test_only_last_area_alive_is_still_green(self, scanner_status):
        # Every Leiria worker down but Marinha still scanning → 🟢.
        assert scanner_status.get_status_message(7, 0, 7, 1) == "STATUS: 🟢"

    def test_all_down_is_red(self, scanner_status):
        # 8/8 down — nothing is scanning anywhere → 🔴
        assert scanner_status.get_status_message(7, 1, 7, 1) == "STATUS: 🔴"

    def test_more_than_expected_is_still_red(self, scanner_status):
        # 11/8 — the down count can overshoot expected; still red, no overflow
        assert scanner_status.get_status_message(9, 2, 7, 1) == "STATUS: 🔴"

    def test_zero_expected_workers_is_green(self, scanner_status):
        # Nothing expected means nothing is down — never report red off a
        # 0/0 reading (that would hand StackRecovery a false red episode).
        assert scanner_status.get_status_message(0, 0, 0, 0) == "STATUS: 🟢"

    # --- device_connected: splits the red state into 🔴 (accounts) / ❌ (device) ---

    def test_red_with_device_down_becomes_cross(self, scanner_status):
        assert (
            scanner_status.get_status_message(7, 1, 7, 1, device_connected=False)
            == "STATUS: ❌"
        )

    def test_red_with_device_up_stays_red(self, scanner_status):
        assert (
            scanner_status.get_status_message(7, 1, 7, 1, device_connected=True)
            == "STATUS: 🔴"
        )

    def test_green_ignores_device_flag(self, scanner_status):
        # Only a real red reading is ambiguous; a map that is still scanning
        # never turns into ❌ just because the device flag happens to read
        # False (in practice rename_voice_channels doesn't even look it up).
        assert (
            scanner_status.get_status_message(0, 0, 7, 1, device_connected=False)
            == "STATUS: 🟢"
        )
        assert (
            scanner_status.get_status_message(1, 0, 7, 1, device_connected=False)
            == "STATUS: 🟢"
        )

    def test_missing_input_becomes_cross_regardless_of_device_flag(
        self, scanner_status
    ):
        # Missing data short-circuits to ❌ before the device flag is even
        # consulted — there's no percentage to compute, so device_connected
        # is irrelevant here (unlike the real-red case above).
        assert (
            scanner_status.get_status_message(None, 0, 7, 1, device_connected=False)
            == "STATUS: ❌"
        )
        assert (
            scanner_status.get_status_message(None, 0, 7, 1, device_connected=True)
            == "STATUS: ❌"
        )


class TestShouldUpdateChannel:
    """The single gate in front of every Discord call in the rename path.

    Pure change detection against the last name we wrote — no time-based
    re-assert, so a steady status never spends the channel's rename budget
    (two per 10 minutes) no matter how long it holds.
    """

    def test_nothing_written_yet_triggers_update(self, scanner_status):
        # Nothing written yet (fresh boot) → always update
        assert scanner_status.channelStatusName is None
        assert scanner_status.should_update_channel("STATUS: 🟢") is True

    def test_same_status_skips_update(self, scanner_status):
        scanner_status.channelStatusName = "STATUS: 🟢"
        assert scanner_status.should_update_channel("STATUS: 🟢") is False

    def test_different_status_triggers_update(self, scanner_status):
        scanner_status.channelStatusName = "STATUS: 🟢"
        assert scanner_status.should_update_channel("STATUS: 🔴") is True

    def test_unchanged_status_never_goes_stale(self, scanner_status, mocker):
        """A status that holds for hours is still not rewritten.

        The old implementation re-asserted the name once an hour; that write
        could only ever set the name to what it already was, while consuming
        a rename slot. Guard against it coming back.
        """
        scanner_status.channelStatusName = "STATUS: 🟢"
        mocker.patch("modules.scanner_status.time.time", return_value=10_000_000)
        assert scanner_status.should_update_channel("STATUS: 🟢") is False


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


_NO_WORKER_DATA = {
    "downDevicesLeiria": None,
    "downDevicesMarinha": None,
    "expectedWorkersLeiria": None,
    "expectedWorkersMarinha": None,
}


class TestGetWorkersWithIssues:
    """Parses the scanner_status HTTP payload into per-region down counters
    and the expected_workers Dragonite reported alongside them (the live
    source of truth rename_voice_channels uses instead of a hardcoded total).
    """

    async def test_none_response_returns_none_counters(self, scanner_status, mocker):
        # This is what a fully unreachable Dragonite looks like — fetch_data
        # returns None on any connection/HTTP failure (see http_client.py).
        _make_fetch_mock(mocker, None)
        result = await scanner_status.get_workers_with_issues()
        assert result == _NO_WORKER_DATA

    async def test_response_missing_areas_returns_none_counters(
        self, scanner_status, mocker
    ):
        _make_fetch_mock(mocker, {"unrelated": "payload"})
        result = await scanner_status.get_workers_with_issues()
        assert result == _NO_WORKER_DATA

    async def test_all_leiria_workers_healthy(self, scanner_status, mocker):
        now = time.time()
        _make_fetch_mock(
            mocker,
            {
                "areas": [
                    _area(
                        "LeiriaBigger",
                        [_worker(now) for _ in range(7)],
                    )
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        # 7 expected (fallback default, no override in payload), 7 up → 0 down
        assert result["downDevicesLeiria"] == 0
        assert result["expectedWorkersLeiria"] == 7
        assert result["downDevicesMarinha"] is None
        assert result["expectedWorkersMarinha"] is None

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
                        [_worker(stale) for _ in range(7)],
                    )
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        assert result["downDevicesLeiria"] == 7
        assert result["expectedWorkersLeiria"] == 7

    async def test_leiria_partial_down_count(self, scanner_status, mocker):
        now = time.time()
        stale = now - 10_000
        _make_fetch_mock(
            mocker,
            {
                "areas": [
                    _area(
                        "LeiriaBigger",
                        [_worker(now) for _ in range(5)]
                        + [_worker(stale) for _ in range(2)],
                    )
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        # 7 expected, 5 up → 2 down
        assert result["downDevicesLeiria"] == 2
        assert result["expectedWorkersLeiria"] == 7

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
                        [_worker(now, connection_status="Idle")]
                        + [
                            _worker(now, connection_status="Executing Worker")
                            for _ in range(6)
                        ],
                    )
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        # 6 executing + 1 idle → 1 down
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
                        [_worker(None)] + [_worker(now) for _ in range(6)],
                    )
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        assert result["downDevicesLeiria"] == 1

    async def test_expected_workers_override_from_payload(self, scanner_status, mocker):
        now = time.time()
        # Override says 6 expected even though the fallback default is 7
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
        assert result["expectedWorkersLeiria"] == 6

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
        assert result["expectedWorkersMarinha"] == 1
        assert result["downDevicesLeiria"] is None
        assert result["expectedWorkersLeiria"] is None

    async def test_both_areas_populated(self, scanner_status, mocker):
        now = time.time()
        stale = now - 10_000
        _make_fetch_mock(
            mocker,
            {
                "areas": [
                    _area(
                        "LeiriaBigger",
                        [_worker(now) for _ in range(5)]
                        + [_worker(stale) for _ in range(2)],
                    ),
                    _area("MarinhaGrande", [_worker(stale)]),
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        assert result == {
            "downDevicesLeiria": 2,
            "downDevicesMarinha": 1,
            "expectedWorkersLeiria": 7,
            "expectedWorkersMarinha": 1,
        }

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
                    _area("LeiriaBigger", [_worker(now) for _ in range(7)]),
                ]
            },
        )
        result = await scanner_status.get_workers_with_issues()
        # Unknown area contributes nothing; Leiria is parsed normally.
        assert result == {
            "downDevicesLeiria": 0,
            "downDevicesMarinha": None,
            "expectedWorkersLeiria": 7,
            "expectedWorkersMarinha": None,
        }

    async def test_unknown_area_never_leaks_into_known_counters(
        self, scanner_status, mocker
    ):
        # Even when the payload supplies an expected_workers override for an
        # unknown area (so the area IS processed rather than skipped), its
        # downDevices value must not leak into Leiria/MarinhaGrande.
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
        assert result == _NO_WORKER_DATA


class TestGetFullStatusDevices:
    """get_full_status parses the Rotom /api/status `devices` array.

    Covers both the RotomNG (snake_case) payload and the legacy Node Rotom
    (camelCase) payload, so the bot survives the rotom→rotom-ng cutover and a
    rollback. Only the device branch is exercised here; worker/account branches
    are mocked out.
    """

    def _prime(self, scanner_status, mocker, device_payload, *, now=1_000_000):
        # First fetch_data call is device_status, second is scanner_status.
        mocker.patch(
            "modules.scanner_status.fetch_data",
            new=AsyncMock(side_effect=[device_payload, {"areas": []}]),
        )
        mocker.patch("modules.scanner_status.time.time", return_value=now)
        mocker.patch.object(
            scanner_status, "_get_seconds_since_last_pokemon", return_value=0
        )
        scanner_status.poliswag.account_monitor.get_account_stats = AsyncMock(
            return_value={"good": 1}
        )

    async def test_parses_ng_device(self, scanner_status, mocker):
        now = 1_000_000
        self._prime(
            scanner_status,
            mocker,
            {
                "devices": [
                    {
                        "id": "Redmi",
                        "origin": "PokemodAegis-Redmi",
                        "is_connected": True,
                        "last_seen_at_ms": (now - 30) * 1000,
                    }
                ]
            },
            now=now,
        )
        result = await scanner_status.get_full_status()
        assert result["devices"] == [
            {
                "origin": "PokemodAegis-Redmi",
                "is_alive": True,
                "last_msg_seconds_ago": 30,
            }
        ]

    async def test_ng_device_falls_back_to_id_when_no_origin(
        self, scanner_status, mocker
    ):
        now = 1_000_000
        self._prime(
            scanner_status,
            mocker,
            {"devices": [{"id": "Redmi", "is_connected": False}]},
            now=now,
        )
        result = await scanner_status.get_full_status()
        assert result["devices"][0]["origin"] == "Redmi"
        assert result["devices"][0]["is_alive"] is False
        assert result["devices"][0]["last_msg_seconds_ago"] is None

    async def test_parses_legacy_device(self, scanner_status, mocker):
        # Rollback safety: the old Node Rotom camelCase shape must still parse.
        now = 1_000_000
        self._prime(
            scanner_status,
            mocker,
            {
                "devices": [
                    {
                        "deviceId": "Redmi",
                        "origin": "PokemodAegis-Redmi",
                        "isAlive": True,
                        "dateLastMessageReceived": (now - 45) * 1000,
                    }
                ]
            },
            now=now,
        )
        result = await scanner_status.get_full_status()
        assert result["devices"] == [
            {
                "origin": "PokemodAegis-Redmi",
                "is_alive": True,
                "last_msg_seconds_ago": 45,
            }
        ]


class TestGetFullStatusWorkers:
    """get_full_status also parses the Dragonite `workers` array (via the
    scanner_status fetch_data payload). Only the worker branch is exercised
    here; device/account branches are mocked out."""

    def _prime(self, scanner_status, mocker, scanner_payload, *, now=1_000_000):
        # First fetch_data call is device_status, second is scanner_status.
        mocker.patch(
            "modules.scanner_status.fetch_data",
            new=AsyncMock(side_effect=[{"devices": []}, scanner_payload]),
        )
        mocker.patch("modules.scanner_status.time.time", return_value=now)
        mocker.patch.object(
            scanner_status, "_get_seconds_since_last_pokemon", return_value=0
        )
        scanner_status.poliswag.account_monitor.get_account_stats = AsyncMock(
            return_value={"good": 1}
        )

    async def test_parses_workers_across_areas_and_managers(
        self, scanner_status, mocker
    ):
        now = 1_000_000
        self._prime(
            scanner_status,
            mocker,
            {
                "areas": [
                    {
                        "name": "Leiria",
                        "worker_managers": [
                            {
                                "workers": [
                                    {
                                        "worker_id": "leiria-1",
                                        "connection_status": "Executing Worker",
                                        "last_data": now - 10,
                                    }
                                ]
                            }
                        ],
                    }
                ]
            },
            now=now,
        )
        result = await scanner_status.get_full_status()
        assert result["workers"] == [
            {
                "worker_id": "leiria-1",
                "area": "Leiria",
                "status": "Executing Worker",
                "last_data_seconds_ago": 10,
            }
        ]

    async def test_worker_missing_last_data_has_no_age(self, scanner_status, mocker):
        now = 1_000_000
        self._prime(
            scanner_status,
            mocker,
            {
                "areas": [
                    {
                        "name": "Leiria",
                        "worker_managers": [{"workers": [{}]}],
                    }
                ]
            },
            now=now,
        )
        result = await scanner_status.get_full_status()
        assert result["workers"] == [
            {
                "worker_id": "?",
                "area": "Leiria",
                "status": "?",
                "last_data_seconds_ago": None,
            }
        ]


class TestGetFullStatusIvVerification:
    """get_full_status wires in _get_iv_verification_stats under the
    iv_verification key; device/worker/account branches are mocked out."""

    def _prime(self, scanner_status, mocker, *, now=1_000_000):
        mocker.patch(
            "modules.scanner_status.fetch_data",
            new=AsyncMock(side_effect=[{"devices": []}, {"areas": []}]),
        )
        mocker.patch("modules.scanner_status.time.time", return_value=now)
        mocker.patch.object(
            scanner_status, "_get_seconds_since_last_pokemon", return_value=0
        )
        scanner_status.poliswag.account_monitor.get_account_stats = AsyncMock(
            return_value={"good": 1}
        )

    async def test_includes_iv_verification_stats(self, scanner_status, mocker):
        self._prime(scanner_status, mocker)
        mocker.patch.object(
            scanner_status,
            "_get_iv_verification_stats",
            return_value={"total": 100, "iv": 90, "unverified": 10},
        )
        result = await scanner_status.get_full_status()
        assert result["iv_verification"] == {
            "total": 100,
            "iv": 90,
            "unverified": 10,
        }

    async def test_none_when_no_recent_activity(self, scanner_status, mocker):
        self._prime(scanner_status, mocker)
        mocker.patch.object(
            scanner_status, "_get_iv_verification_stats", return_value=None
        )
        result = await scanner_status.get_full_status()
        assert result["iv_verification"] is None


class TestGetVoiceChannel:
    async def test_cache_hit_skips_the_http_fetch(self, scanner_status, mocker):
        # discord.py already holds every visible channel, so the normal path
        # must not spend an HTTP GET on top of the rename's PATCH.
        mocker.patch("modules.scanner_status.Config.VOICE_CHANNEL_ID", 12345)
        expected = MagicMock(name="channel")
        scanner_status.poliswag.get_channel = MagicMock(return_value=expected)
        scanner_status.poliswag.fetch_channel = AsyncMock()
        result = await scanner_status.get_voice_channel()
        assert result is expected
        scanner_status.poliswag.get_channel.assert_called_once_with(12345)
        scanner_status.poliswag.fetch_channel.assert_not_called()

    async def test_cache_miss_falls_back_to_fetch(self, scanner_status, mocker):
        # A tick landing before the gateway cache is populated still works.
        mocker.patch("modules.scanner_status.Config.VOICE_CHANNEL_ID", 12345)
        expected = MagicMock(name="channel")
        scanner_status.poliswag.get_channel = MagicMock(return_value=None)
        scanner_status.poliswag.fetch_channel = AsyncMock(return_value=expected)
        result = await scanner_status.get_voice_channel()
        assert result is expected
        scanner_status.poliswag.fetch_channel.assert_called_once_with(12345)

    async def test_returns_none_and_logs_on_missing_env(self, scanner_status, mocker):
        mocker.patch("modules.scanner_status.Config.VOICE_CHANNEL_ID", 0)
        scanner_status.poliswag.get_channel = MagicMock()
        scanner_status.poliswag.fetch_channel = AsyncMock()
        result = await scanner_status.get_voice_channel()
        assert result is None
        scanner_status.poliswag.get_channel.assert_not_called()
        scanner_status.poliswag.utility.log_to_file.assert_called_once()

    async def test_returns_none_when_fetch_channel_raises(self, scanner_status, mocker):
        mocker.patch("modules.scanner_status.Config.VOICE_CHANNEL_ID", 12345)
        scanner_status.poliswag.get_channel = MagicMock(return_value=None)
        scanner_status.poliswag.fetch_channel = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        result = await scanner_status.get_voice_channel()
        assert result is None
        scanner_status.poliswag.utility.log_to_file.assert_called_once()


class TestGetSecondsSinceLastPokemon:
    async def test_returns_seconds_from_db(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = [
            {"seconds_ago": 42}
        ]
        assert await scanner_status._get_seconds_since_last_pokemon() == 42

    async def test_returns_none_when_table_empty(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = [
            {"seconds_ago": None}
        ]
        assert await scanner_status._get_seconds_since_last_pokemon() is None

    async def test_returns_none_on_db_error(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.side_effect = (
            RuntimeError("db gone")
        )
        assert await scanner_status._get_seconds_since_last_pokemon() is None
        scanner_status.poliswag.utility.log_to_file.assert_called_once()

    async def test_queries_correct_column(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = [
            {"seconds_ago": 5}
        ]
        await scanner_status._get_seconds_since_last_pokemon()
        sql = scanner_status.poliswag.quest_search.db.get_data_from_database.call_args.args[
            0
        ]
        assert "MAX(updated)" in sql
        assert "seconds_ago" in sql
        assert "pokemon" in sql


class TestGetIvVerificationStats:
    async def test_returns_totals_from_db(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = [
            {"total": 100, "iv": 90, "unverified": 10}
        ]
        result = await scanner_status._get_iv_verification_stats()
        assert result == {"total": 100, "iv": 90, "unverified": 10}

    async def test_returns_none_when_no_rows(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = []
        assert await scanner_status._get_iv_verification_stats() is None

    async def test_returns_none_when_total_is_zero(self, scanner_status):
        # No scanning activity in the window -- e.g. right after a restart.
        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = [
            {"total": 0, "iv": 0, "unverified": 0}
        ]
        assert await scanner_status._get_iv_verification_stats() is None

    async def test_returns_none_on_db_error(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.side_effect = (
            RuntimeError("db gone")
        )
        assert await scanner_status._get_iv_verification_stats() is None
        scanner_status.poliswag.utility.log_to_file.assert_called_once()

    async def test_uses_requested_minutes_window(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = [
            {"total": 1, "iv": 1, "unverified": 0}
        ]
        await scanner_status._get_iv_verification_stats(minutes=30)
        call_args = (
            scanner_status.poliswag.quest_search.db.get_data_from_database.call_args
        )
        assert call_args.kwargs["params"] == (30 * 60,)

    async def test_queries_correct_table_and_areas(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = [
            {"total": 1, "iv": 1, "unverified": 0}
        ]
        await scanner_status._get_iv_verification_stats()
        sql = scanner_status.poliswag.quest_search.db.get_data_from_database.call_args.args[
            0
        ]
        assert "pokemon_area_stats" in sql
        assert "Leiria" in sql
        assert "MarinhaGrande" in sql


class TestGetIvVerificationByArea:
    async def test_returns_dict_keyed_by_area(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = [
            {"area": "Leiria", "total": 300, "iv": 297},
            {"area": "MarinhaGrande", "total": 100, "iv": 100},
        ]
        result = await scanner_status.get_iv_verification_by_area()
        assert result == {
            "Leiria": {"total": 300, "iv": 297},
            "MarinhaGrande": {"total": 100, "iv": 100},
        }

    async def test_omits_areas_with_no_activity(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = [
            {"area": "Leiria", "total": 300, "iv": 297},
            {"area": "MarinhaGrande", "total": 0, "iv": 0},
        ]
        result = await scanner_status.get_iv_verification_by_area()
        assert result == {"Leiria": {"total": 300, "iv": 297}}

    async def test_empty_dict_when_no_rows(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = []
        assert await scanner_status.get_iv_verification_by_area() == {}

    async def test_empty_dict_on_db_error(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.side_effect = (
            RuntimeError("db gone")
        )
        assert await scanner_status.get_iv_verification_by_area() == {}
        scanner_status.poliswag.utility.log_to_file.assert_called_once()

    async def test_uses_requested_minutes_window(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = []
        await scanner_status.get_iv_verification_by_area(minutes=30)
        call_args = (
            scanner_status.poliswag.quest_search.db.get_data_from_database.call_args
        )
        assert call_args.kwargs["params"] == (30 * 60,)

    async def test_defaults_to_one_hour_window(self, scanner_status):
        # The account card labels this figure "spawns/h" -- the default
        # window and that label have to agree.
        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = []
        await scanner_status.get_iv_verification_by_area()
        call_args = (
            scanner_status.poliswag.quest_search.db.get_data_from_database.call_args
        )
        assert call_args.kwargs["params"] == (60 * 60,)

    async def test_queries_correct_table_and_groups_by_area(self, scanner_status):
        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = []
        await scanner_status.get_iv_verification_by_area()
        sql = scanner_status.poliswag.quest_search.db.get_data_from_database.call_args.args[
            0
        ]
        assert "pokemon_area_stats" in sql
        assert "GROUP BY area" in sql
        assert "Leiria" in sql
        assert "MarinhaGrande" in sql


class TestTriggerAllDownAction:
    def _setup(self, scanner_status, mocker, *, seconds_ago=30):
        scanner_status.last_all_down_request_time = 0
        scanner_status.poliswag.account_monitor.is_device_connected = AsyncMock(
            return_value=True
        )
        scanner_status.poliswag.account_monitor.get_account_stats = AsyncMock(
            return_value={"good": 5}
        )
        mocker.patch.object(
            scanner_status, "_get_seconds_since_last_pokemon", return_value=seconds_ago
        )

    async def test_sends_dev_log_when_not_production(self, scanner_status, mocker):
        self._setup(scanner_status, mocker)
        mocker.patch("modules.scanner_status.Config.IS_PRODUCTION", False)
        fetch_mock = mocker.patch("modules.scanner_status.fetch_data", new=AsyncMock())
        await scanner_status.trigger_all_down_action()
        fetch_mock.assert_not_called()
        scanner_status.poliswag.utility.log_to_file.assert_called_once()
        msg = scanner_status.poliswag.utility.log_to_file.call_args.args[0]
        assert "[DEV]" in msg
        assert "accounts" in msg

    async def test_posts_to_endpoint_in_production(self, scanner_status, mocker):
        self._setup(scanner_status, mocker, seconds_ago=42)
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
        assert payload["value"]["last_pokemon_seconds_ago"] == 42
        assert (
            payload["value"]["last_pokemon_message"] == "Last pokemon scanned 42s ago"
        )

    async def test_payload_message_when_seconds_unknown(self, scanner_status, mocker):
        self._setup(scanner_status, mocker, seconds_ago=None)
        mocker.patch("modules.scanner_status.Config.IS_PRODUCTION", True)
        fetch_mock = mocker.patch("modules.scanner_status.fetch_data", new=AsyncMock())
        await scanner_status.trigger_all_down_action()
        payload = fetch_mock.call_args.kwargs["data"]
        assert payload["value"]["last_pokemon_seconds_ago"] is None
        assert (
            payload["value"]["last_pokemon_message"] == "Last pokemon scan time unknown"
        )

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
        mocker.patch.object(
            scanner_status, "_get_seconds_since_last_pokemon", return_value=10
        )
        await scanner_status.trigger_all_down_action()
        scanner_status.poliswag.utility.log_to_file.assert_called_once()
        msg = scanner_status.poliswag.utility.log_to_file.call_args.args[0]
        assert "Error sending all-down" in msg


def _ws(leiria_down, marinha_down, leiria_expected=7, marinha_expected=1):
    """Build a get_workers_with_issues()-shaped dict for rename_voice_channels."""
    return {
        "downDevicesLeiria": leiria_down,
        "downDevicesMarinha": marinha_down,
        "expectedWorkersLeiria": leiria_expected,
        "expectedWorkersMarinha": marinha_expected,
    }


class TestRenameVoiceChannels:
    def _patch_fresh(
        self, scanner_status, mocker, *, seconds_ago=1, device_connected=True
    ):
        """Patch trigger + voice channel + pokemon staleness for a clean test."""
        trigger = mocker.patch.object(
            scanner_status, "trigger_all_down_action", new=AsyncMock()
        )
        mocker.patch.object(
            scanner_status, "get_voice_channel", new=AsyncMock(return_value=None)
        )
        mocker.patch.object(
            scanner_status, "_get_seconds_since_last_pokemon", return_value=seconds_ago
        )
        scanner_status.poliswag.account_monitor.is_device_connected = AsyncMock(
            return_value=device_connected
        )
        return trigger

    async def test_stale_pokemon_triggers_all_down_regardless_of_worker_count(
        self, scanner_status, mocker
    ):
        # Workers all down AND pokemon stale → webhook fires.
        trigger = self._patch_fresh(scanner_status, mocker, seconds_ago=660)
        await scanner_status.rename_voice_channels(_ws(7, 1))
        trigger.assert_awaited_once()

    async def test_stale_pokemon_triggers_even_when_workers_appear_healthy(
        self, scanner_status, mocker
    ):
        # Workers look fine (0 down) but pokemon table is 11 minutes stale.
        trigger = self._patch_fresh(scanner_status, mocker, seconds_ago=660)
        await scanner_status.rename_voice_channels(_ws(0, 0))
        trigger.assert_awaited_once()

    async def test_all_workers_down_but_fresh_pokemon_does_not_trigger(
        self, scanner_status, mocker
    ):
        # Workers all down, but pokemon was seen 5 seconds ago — no webhook.
        trigger = self._patch_fresh(scanner_status, mocker, seconds_ago=5)
        await scanner_status.rename_voice_channels(_ws(7, 1))
        trigger.assert_not_called()

    async def test_fresh_pokemon_and_workers_up_does_not_trigger(
        self, scanner_status, mocker
    ):
        trigger = self._patch_fresh(scanner_status, mocker, seconds_ago=1)
        await scanner_status.rename_voice_channels(_ws(0, 0))
        trigger.assert_not_called()

    async def test_none_counters_do_not_trigger_all_down(self, scanner_status, mocker):
        trigger = self._patch_fresh(scanner_status, mocker, seconds_ago=1)
        await scanner_status.rename_voice_channels(_ws(None, None, None, None))
        trigger.assert_not_called()

    async def test_updates_channel_name_when_cache_stale(self, scanner_status, mocker):
        mocker.patch.object(scanner_status, "trigger_all_down_action", new=AsyncMock())
        mocker.patch.object(
            scanner_status, "_get_seconds_since_last_pokemon", return_value=1
        )
        channel = MagicMock()
        channel.edit = AsyncMock()
        mocker.patch.object(
            scanner_status, "get_voice_channel", new=AsyncMock(return_value=channel)
        )
        await scanner_status.rename_voice_channels(_ws(0, 0))
        assert channel.edit.await_count == 1
        assert scanner_status.channelStatusName == "STATUS: 🟢"

    async def test_skips_edit_when_status_unchanged(self, scanner_status, mocker):
        mocker.patch.object(scanner_status, "trigger_all_down_action", new=AsyncMock())
        mocker.patch.object(
            scanner_status, "_get_seconds_since_last_pokemon", return_value=1
        )
        scanner_status.channelStatusName = "STATUS: 🟢"
        get_channel = mocker.patch.object(
            scanner_status, "get_voice_channel", new=AsyncMock()
        )
        await scanner_status.rename_voice_channels(_ws(0, 0))
        get_channel.assert_not_called()

    async def test_device_down_renames_red_to_cross(self, scanner_status, mocker):
        # All workers down AND device offline → ❌, not 🔴.
        mocker.patch.object(scanner_status, "trigger_all_down_action", new=AsyncMock())
        mocker.patch.object(
            scanner_status, "_get_seconds_since_last_pokemon", return_value=1
        )
        scanner_status.poliswag.account_monitor.is_device_connected = AsyncMock(
            return_value=False
        )
        channel = MagicMock()
        channel.edit = AsyncMock()
        mocker.patch.object(
            scanner_status, "get_voice_channel", new=AsyncMock(return_value=channel)
        )
        await scanner_status.rename_voice_channels(_ws(7, 1))
        assert scanner_status.channelStatusName == "STATUS: ❌"

    async def test_device_up_keeps_red_when_all_workers_down(
        self, scanner_status, mocker
    ):
        # All workers down but device connected → account problem → 🔴 stays.
        mocker.patch.object(scanner_status, "trigger_all_down_action", new=AsyncMock())
        mocker.patch.object(
            scanner_status, "_get_seconds_since_last_pokemon", return_value=1
        )
        scanner_status.poliswag.account_monitor.is_device_connected = AsyncMock(
            return_value=True
        )
        channel = MagicMock()
        channel.edit = AsyncMock()
        mocker.patch.object(
            scanner_status, "get_voice_channel", new=AsyncMock(return_value=channel)
        )
        await scanner_status.rename_voice_channels(_ws(7, 1))
        assert scanner_status.channelStatusName == "STATUS: 🔴"

    async def test_device_check_skipped_when_not_red(self, scanner_status, mocker):
        # Healthy counters → the extra device_status fetch never happens.
        self._patch_fresh(scanner_status, mocker, seconds_ago=1)
        is_connected = scanner_status.poliswag.account_monitor.is_device_connected
        await scanner_status.rename_voice_channels(_ws(0, 0))
        is_connected.assert_not_called()

    async def test_all_red_with_device_up_reported_to_stack_recovery(
        self, scanner_status, mocker
    ):
        self._patch_fresh(scanner_status, mocker, seconds_ago=1, device_connected=True)
        await scanner_status.rename_voice_channels(_ws(7, 1))
        scanner_status.poliswag.stack_recovery.observe.assert_awaited_once_with(True)

    async def test_device_down_all_red_still_feeds_stack_recovery(
        self, scanner_status, mocker
    ):
        # ❌ is only a display distinction — the recovery ladder runs either
        # way (containers first, device reboot if red persists).
        self._patch_fresh(scanner_status, mocker, seconds_ago=1, device_connected=False)
        await scanner_status.rename_voice_channels(_ws(7, 1))
        scanner_status.poliswag.stack_recovery.observe.assert_awaited_once_with(True)

    async def test_partial_down_is_not_all_red(self, scanner_status, mocker):
        # 4/8 down — the map is still being scanned → 🟢, no recovery trigger.
        self._patch_fresh(scanner_status, mocker, seconds_ago=1, device_connected=True)
        await scanner_status.rename_voice_channels(_ws(4, 0))
        scanner_status.poliswag.stack_recovery.observe.assert_awaited_once_with(False)

    async def test_partial_down_keeps_channel_green(self, scanner_status, mocker):
        # The status channel is only renamed when the map stops scanning
        # entirely, so a shrinking account pool never touches it.
        mocker.patch.object(scanner_status, "trigger_all_down_action", new=AsyncMock())
        mocker.patch.object(
            scanner_status, "_get_seconds_since_last_pokemon", return_value=1
        )
        channel = MagicMock()
        channel.edit = AsyncMock()
        mocker.patch.object(
            scanner_status, "get_voice_channel", new=AsyncMock(return_value=channel)
        )
        await scanner_status.rename_voice_channels(_ws(0, 0))
        assert scanner_status.channelStatusName == "STATUS: 🟢"
        await scanner_status.rename_voice_channels(_ws(6, 0))
        await scanner_status.rename_voice_channels(_ws(7, 0))
        assert channel.edit.await_count == 1
        assert scanner_status.channelStatusName == "STATUS: 🟢"

    async def test_dragonite_unreachable_renders_as_cross(self, scanner_status, mocker):
        # get_workers_with_issues() returns all-None when the Dragonite fetch
        # fails entirely — that renders as ❌ (it still means the map isn't
        # being scanned), but critically must preserve the recovery ladder's
        # current episode. A forced recreate causes this transient state; it
        # must not re-arm already exhausted attempts.
        mocker.patch.object(scanner_status, "trigger_all_down_action", new=AsyncMock())
        mocker.patch.object(
            scanner_status, "_get_seconds_since_last_pokemon", return_value=1
        )
        is_connected = scanner_status.poliswag.account_monitor.is_device_connected
        channel = MagicMock()
        channel.edit = AsyncMock()
        mocker.patch.object(
            scanner_status, "get_voice_channel", new=AsyncMock(return_value=channel)
        )
        await scanner_status.rename_voice_channels(_ws(None, None, None, None))
        assert scanner_status.channelStatusName == "STATUS: ❌"
        is_connected.assert_not_called()
        scanner_status.poliswag.stack_recovery.observe.assert_awaited_once_with(None)

    async def test_rate_limit_is_logged_not_raised(self, scanner_status, mocker):
        import discord

        mocker.patch.object(scanner_status, "trigger_all_down_action", new=AsyncMock())
        mocker.patch.object(
            scanner_status, "_get_seconds_since_last_pokemon", return_value=1
        )
        channel = MagicMock()
        exc = discord.errors.HTTPException(
            MagicMock(status=429, reason=""), {"message": "rate limited", "code": 429}
        )
        exc.code = 429
        channel.edit = AsyncMock(side_effect=exc)
        mocker.patch.object(
            scanner_status, "get_voice_channel", new=AsyncMock(return_value=channel)
        )
        await scanner_status.rename_voice_channels(_ws(0, 0))
        logged = [
            c.args[0]
            for c in scanner_status.poliswag.utility.log_to_file.call_args_list
        ]
        assert any("Rate limited" in m for m in logged)

    async def test_non_rate_limit_http_error_is_logged_not_raised(
        self, scanner_status, mocker
    ):
        import discord

        mocker.patch.object(scanner_status, "trigger_all_down_action", new=AsyncMock())
        mocker.patch.object(
            scanner_status, "_get_seconds_since_last_pokemon", return_value=1
        )
        channel = MagicMock()
        exc = discord.errors.HTTPException(
            MagicMock(status=500, reason=""), {"message": "server error", "code": 0}
        )
        exc.code = 0
        channel.edit = AsyncMock(side_effect=exc)
        mocker.patch.object(
            scanner_status, "get_voice_channel", new=AsyncMock(return_value=channel)
        )
        await scanner_status.rename_voice_channels(_ws(0, 0))
        logged = [
            c.args[0]
            for c in scanner_status.poliswag.utility.log_to_file.call_args_list
        ]
        assert any("Error updating status channel" in m for m in logged)


def _poliswag_db_handler(*, scanning_ongoing=False, expected=(371, 109)):
    """Side-effect for the poliswag DB mock, routing by the SQL it receives.

    Two distinct reads hit poliswag.db during a completion check: the
    ``scanned = 1`` guard and the expected-totals lookup.
    """

    def _handler(sql, *args, **kwargs):
        if "scanned = 1" in sql:
            return [{"scanned": 1}] if scanning_ongoing else []
        if "quest_expected" in sql:
            return [
                {
                    "quest_expected_leiria": expected[0],
                    "quest_expected_marinha": expected[1],
                }
            ]
        return []

    return _handler


class TestIsQuestScanningComplete:
    """The completion detector is plateau-based: it fires only after the live
    quest count stops growing for ``PLATEAU_TICKS`` checks, is past the floor,
    and the scanner is alive. Tests drive it tick-by-tick."""

    def _prime(
        self,
        scanner_status,
        mocker,
        *,
        expected=(371, 109),
        scanning_ongoing=False,
        alive=True,
    ):
        import datetime as _dt

        mock_datetime = mocker.patch("modules.scanner_status.datetime.datetime")
        mock_datetime.now.return_value = _dt.datetime(2024, 1, 2, 3, 0, 0)
        # Long past the post-reset quiet period by default; tests targeting
        # that guard set this explicitly instead.
        scanner_status._plateau_reset_at = 0
        scanner_status.poliswag.db.get_data_from_database.side_effect = (
            _poliswag_db_handler(scanning_ongoing=scanning_ongoing, expected=expected)
        )
        mocker.patch.object(
            scanner_status, "_is_scanner_alive", new=AsyncMock(return_value=alive)
        )

    async def _tick(self, scanner_status, leiria, marinha):
        """One minute-check with the given per-area live quest counts."""
        scanner_status.poliswag.quest_search.db.get_data_from_database.side_effect = [
            [{"scanned": leiria}],
            [{"scanned": marinha}],
        ]
        return await scanner_status.is_quest_scanning_complete()

    async def _tick_until_plateau(self, scanner_status, leiria, marinha):
        """Hold both counts flat through the full plateau window; return the
        final-tick result and the result one tick before it should fire."""
        result = None
        for _ in range(scanner_status.PLATEAU_TICKS):
            result = await self._tick(scanner_status, leiria, marinha)
        before = result
        after = await self._tick(scanner_status, leiria, marinha)
        return before, after

    def _complete(self, result):
        return bool(result and result["leiriaCompleted"] and result["marinhaCompleted"])

    # --- early-exit guards -------------------------------------------------

    async def test_returns_none_within_quiet_period_after_reset(
        self, scanner_status, mocker
    ):
        # A reset just now (day-change, or a fresh bot startup) must block any
        # plateau signal until PLATEAU_RESET_QUIET_PERIOD has actually elapsed —
        # yesterday's still-valid quest_expiry rows could otherwise be read as
        # today's finished scan.
        mocker.patch("modules.scanner_status.time.time", return_value=1_000.0)
        scanner_status._plateau_reset_at = 1_000.0
        assert await scanner_status.is_quest_scanning_complete() is None

    async def test_proceeds_once_quiet_period_has_elapsed(self, scanner_status, mocker):
        self._prime(scanner_status, mocker, scanning_ongoing=True)
        mocker.patch("modules.scanner_status.time.time", return_value=1_000.0)
        scanner_status._plateau_reset_at = (
            1_000.0 - scanner_status.PLATEAU_RESET_QUIET_PERIOD
        )
        # Past the guard now, so it proceeds to the real "scanning ongoing" check.
        assert await scanner_status.is_quest_scanning_complete() is None
        scanner_status.poliswag.db.get_data_from_database.assert_called_once()

    async def test_returns_none_when_scanning_ongoing(self, scanner_status, mocker):
        self._prime(scanner_status, mocker, scanning_ongoing=True)
        assert await self._tick(scanner_status, 371, 109) is None

    async def test_returns_none_on_exception(self, scanner_status, mocker):
        self._prime(scanner_status, mocker)
        scanner_status.poliswag.quest_search.db.get_data_from_database.side_effect = (
            RuntimeError("db error")
        )
        assert await scanner_status.is_quest_scanning_complete() is None
        scanner_status.poliswag.utility.log_to_file.assert_called_once()

    async def test_returns_none_when_count_valid_quests_yields_no_row(
        self, scanner_status, mocker
    ):
        # An empty result set from the SUM query means _count_valid_quests
        # returns None, which short-circuits the whole check.
        self._prime(scanner_status, mocker)
        scanner_status.poliswag.quest_search.db.get_data_from_database.side_effect = [
            [],
            [{"scanned": 109}],
        ]
        assert await scanner_status.is_quest_scanning_complete() is None

    # --- plateau behaviour -------------------------------------------------

    async def test_completes_after_flat_plateau_above_floor(
        self, scanner_status, mocker
    ):
        self._prime(scanner_status, mocker, expected=(100, 50), alive=True)
        before, after = await self._tick_until_plateau(scanner_status, 100, 50)
        assert self._complete(before) is False  # still inside the window
        assert self._complete(after) is True
        assert after["leiriaPercentage"] == 100
        assert after["marinhaScanned"] == 50

    async def test_never_completes_while_count_is_zero(self, scanner_status, mocker):
        self._prime(scanner_status, mocker, expected=(100, 50))
        _, after = await self._tick_until_plateau(scanner_status, 0, 0)
        assert self._complete(after) is False

    async def test_never_completes_below_floor(self, scanner_status, mocker):
        # 80/100 = 80% in Leiria, under the 90% floor — a stall, not a finish.
        self._prime(scanner_status, mocker, expected=(100, 50))
        _, after = await self._tick_until_plateau(scanner_status, 80, 48)
        assert self._complete(after) is False
        assert after["leiriaCompleted"] is False

    async def test_not_complete_until_both_areas_plateau(self, scanner_status, mocker):
        self._prime(scanner_status, mocker, expected=(100, 50), alive=True)
        # Leiria flat at ceiling, Marinha still climbing → resets each tick.
        result = None
        for marinha in range(40, 40 + scanner_status.PLATEAU_TICKS + 2):
            result = await self._tick(scanner_status, 100, marinha)
        assert self._complete(result) is False
        # Marinha now flattens for a full window → completes.
        _, after = await self._tick_until_plateau(scanner_status, 100, 50)
        assert self._complete(after) is True

    async def test_straggler_just_below_ceiling_still_completes(
        self, scanner_status, mocker
    ):
        # 1-2 stuck stops: 369/371 and 108/109 are both >= 90% floor.
        self._prime(scanner_status, mocker, expected=(371, 109), alive=True)
        _, after = await self._tick_until_plateau(scanner_status, 369, 108)
        assert self._complete(after) is True

    async def test_self_heals_when_plateau_stuck_below_floor(
        self, scanner_status, mocker
    ):
        # Radius reduction: the real ceiling (80) is now permanently below the
        # stale expected's floor (90). The fast path can never fire, so after a
        # longer STUCK_TICKS window the plateau is accepted as the new ceiling.
        self._prime(scanner_status, mocker, expected=(100, 50), alive=True)
        result = None
        for _ in range(scanner_status.STUCK_TICKS + 1):
            result = await self._tick(scanner_status, 80, 48)
        assert self._complete(result) is True

    async def test_stuck_below_floor_still_blocked_by_dark_scanner(
        self, scanner_status, mocker
    ):
        # The self-heal path is still gated by scanner-alive: a long plateau
        # while the scanner is dark (e.g. crashed) must not falsely complete.
        self._prime(scanner_status, mocker, expected=(100, 50), alive=False)
        result = None
        for _ in range(scanner_status.STUCK_TICKS + 1):
            result = await self._tick(scanner_status, 80, 48)
        assert self._complete(result) is False

    async def test_new_stops_cap_percentage_at_100(self, scanner_status, mocker):
        self._prime(scanner_status, mocker, expected=(100, 50))
        result = await self._tick(scanner_status, 110, 55)
        assert result["leiriaPercentage"] == 100
        assert result["marinhaPercentage"] == 100

    # --- scanner-health gate ----------------------------------------------

    async def test_dark_scanner_blocks_completion_at_plateau(
        self, scanner_status, mocker
    ):
        self._prime(scanner_status, mocker, expected=(100, 50), alive=False)
        _, after = await self._tick_until_plateau(scanner_status, 100, 50)
        assert self._complete(after) is False
        # Once workers come back, the held plateau fires immediately.
        scanner_status._is_scanner_alive = AsyncMock(return_value=True)
        result = await self._tick(scanner_status, 100, 50)
        assert self._complete(result) is True


class TestQuestPlateauHelpers:
    """Unit tests for the supporting plateau / expected-total helpers."""

    def test_reset_quest_plateau_clears_state_and_bumps_reset_time(
        self, scanner_status, mocker
    ):
        scanner_status._quest_plateau["leiria"] = {"prev_count": 300, "flat_streak": 5}
        scanner_status._plateau_reset_at = 0
        mocker.patch("modules.scanner_status.time.time", return_value=12_345.0)
        scanner_status.reset_quest_plateau()
        assert scanner_status._quest_plateau["leiria"] == {
            "prev_count": -1,
            "flat_streak": 0,
        }
        assert scanner_status._plateau_reset_at == 12_345.0

    async def test_is_scanner_alive_true_when_one_area_has_workers(
        self, scanner_status, mocker
    ):
        mocker.patch.object(
            scanner_status,
            "get_workers_with_issues",
            new=AsyncMock(return_value=_ws(4, 0)),
        )
        assert await scanner_status._is_scanner_alive() is True

    async def test_is_scanner_alive_false_when_all_dark(self, scanner_status, mocker):
        mocker.patch.object(
            scanner_status,
            "get_workers_with_issues",
            new=AsyncMock(return_value=_ws(7, 1)),
        )
        assert await scanner_status._is_scanner_alive() is False

    async def test_is_scanner_alive_false_when_status_unavailable(
        self, scanner_status, mocker
    ):
        mocker.patch.object(
            scanner_status,
            "get_workers_with_issues",
            new=AsyncMock(return_value=_ws(None, None, None, None)),
        )
        assert await scanner_status._is_scanner_alive() is False

    async def test_count_valid_quests_coerces_decimal_to_int(self, scanner_status):
        # MariaDB SUM(CASE ...) returns a decimal.Decimal; if it leaks through,
        # _coverage_pct yields a Decimal that crashes _build_progress_embed's
        # Decimal + float math. Counts must come back as plain ints.
        from decimal import Decimal

        scanner_status.poliswag.quest_search.db.get_data_from_database.return_value = [
            {"scanned": Decimal("330")}
        ]
        count = await scanner_status._count_valid_quests(
            scanner_status.poliswag.quest_search.db, leiria=True
        )
        assert count == 330
        assert type(count) is int
        # And the derived percentage must be a plain float (addable to a float).
        pct = scanner_status._coverage_pct(count, 371)
        assert isinstance(pct, float)
        assert pct + 50.0  # would raise TypeError if pct were a Decimal

    def test_coverage_pct_is_zero_when_expected_is_not_positive(self, scanner_status):
        assert scanner_status._coverage_pct(10, 0) == 0
        assert scanner_status._coverage_pct(10, -1) == 0

    async def test_expected_totals_fall_back_to_defaults(self, scanner_status):
        scanner_status.poliswag.db.get_data_from_database.return_value = []
        assert await scanner_status._get_expected_totals() == (371, 109)

    async def test_expected_totals_read_from_db(self, scanner_status):
        scanner_status.poliswag.db.get_data_from_database.return_value = [
            {"quest_expected_leiria": 400, "quest_expected_marinha": 120}
        ]
        assert await scanner_status._get_expected_totals() == (400, 120)

    async def test_record_completion_persists_and_resets(self, scanner_status):
        scanner_status._quest_plateau["leiria"] = {"prev_count": 371, "flat_streak": 10}
        await scanner_status.record_quest_scan_completion(371, 109)
        scanner_status.poliswag.db.execute_query_to_database.assert_called_once()
        params = scanner_status.poliswag.db.execute_query_to_database.call_args.kwargs[
            "params"
        ]
        assert params == (371, 109)
        assert scanner_status._quest_plateau["leiria"] == {
            "prev_count": -1,
            "flat_streak": 0,
        }

    async def test_expected_totals_second_read_does_not_hit_db_again(
        self, scanner_status
    ):
        scanner_status.poliswag.db.get_data_from_database.return_value = [
            {"quest_expected_leiria": 400, "quest_expected_marinha": 120}
        ]

        assert await scanner_status._get_expected_totals() == (400, 120)
        assert await scanner_status._get_expected_totals() == (400, 120)

        scanner_status.poliswag.db.get_data_from_database.assert_called_once()

    async def test_empty_rows_are_not_cached(self, scanner_status):
        scanner_status.poliswag.db.get_data_from_database.return_value = []

        assert await scanner_status._get_expected_totals() == (371, 109)
        assert await scanner_status._get_expected_totals() == (371, 109)

        assert scanner_status.poliswag.db.get_data_from_database.call_count == 2

    async def test_record_completion_updates_cache_without_a_read(self, scanner_status):
        await scanner_status.record_quest_scan_completion(400, 120)

        assert await scanner_status._get_expected_totals() == (400, 120)
        scanner_status.poliswag.db.get_data_from_database.assert_not_called()

    async def test_expected_totals_db_error_is_logged_and_falls_back(
        self, scanner_status
    ):
        scanner_status.poliswag.db.get_data_from_database.side_effect = RuntimeError(
            "db down"
        )
        assert await scanner_status._get_expected_totals() == (371, 109)
        scanner_status.poliswag.utility.log_to_file.assert_called_once()

    async def test_record_completion_db_error_is_logged_but_still_resets(
        self, scanner_status
    ):
        scanner_status._quest_plateau["leiria"] = {"prev_count": 371, "flat_streak": 10}
        scanner_status.poliswag.db.execute_query_to_database.side_effect = RuntimeError(
            "db down"
        )
        await scanner_status.record_quest_scan_completion(371, 109)
        scanner_status.poliswag.utility.log_to_file.assert_called_once()
        assert scanner_status._quest_plateau["leiria"] == {
            "prev_count": -1,
            "flat_streak": 0,
        }
