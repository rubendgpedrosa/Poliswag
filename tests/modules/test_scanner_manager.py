"""Tests for modules.scanner_manager.ScannerManager.

Most methods take the scanner_container_name from env in __init__ and then
delegate to db + utility on poliswag. We construct via __new__ and attach
mocks directly.
"""

from unittest.mock import MagicMock

import pytest

from modules.scanner_manager import ScannerManager


@pytest.fixture
def sm():
    s = ScannerManager.__new__(ScannerManager)
    s.poliswag = MagicMock()
    s.poliswag.utility.time_now = MagicMock(return_value="2024-01-02T00:00:00")
    s.SCANNER_CONTAINER_NAME = None
    return s


class TestUpdateLastScannedDate:
    def test_issues_update_query_with_param(self, sm):
        sm.update_last_scanned_date("2024-01-02T00:00:00")
        sm.poliswag.db.execute_query_to_database.assert_called_once()
        args, kwargs = sm.poliswag.db.execute_query_to_database.call_args
        assert "UPDATE poliswag SET last_scanned_date" in args[0]
        assert kwargs["params"] == ("2024-01-02T00:00:00",)
        sm.poliswag.utility.log_to_file.assert_called_once()


class TestUpdateQuestScanningState:
    def test_default_state_is_one_and_logs_finished(self, sm):
        sm.update_quest_scanning_state()
        args, kwargs = sm.poliswag.db.execute_query_to_database.call_args
        assert kwargs["params"] == (1,)
        log_msg = sm.poliswag.utility.log_to_file.call_args.args[0]
        assert "Finished" in log_msg

    def test_state_zero_logs_started(self, sm):
        sm.update_quest_scanning_state(0)
        args, kwargs = sm.poliswag.db.execute_query_to_database.call_args
        assert kwargs["params"] == (0,)
        log_msg = sm.poliswag.utility.log_to_file.call_args.args[0]
        assert "Started" in log_msg


class TestStartPokestopScan:
    def test_sets_date_then_flips_state_to_zero(self, sm):
        sm.start_pokestop_scan()
        # First call set the scanned date, second flipped scanned flag to 0.
        calls = sm.poliswag.db.execute_query_to_database.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["params"] == ("2024-01-02T00:00:00",)
        assert calls[1].kwargs["params"] == (0,)


class TestIsDayChange:
    def test_returns_true_when_db_returns_rows(self, sm):
        sm.poliswag.db.get_data_from_database.return_value = [
            {"last_scanned_date": "x"}
        ]
        assert sm.is_day_change() is True
        # Day-change path should also kick off a new scan → 2 update calls.
        assert sm.poliswag.db.execute_query_to_database.call_count == 2

    def test_returns_false_when_no_rows(self, sm):
        sm.poliswag.db.get_data_from_database.return_value = []
        assert sm.is_day_change() is False
        sm.poliswag.db.execute_query_to_database.assert_not_called()

    def test_query_is_parametrized_with_time_now(self, sm):
        sm.poliswag.db.get_data_from_database.return_value = []
        sm.is_day_change()
        args, kwargs = sm.poliswag.db.get_data_from_database.call_args
        assert "last_scanned_date < %s" in args[0]
        assert kwargs["params"] == ("2024-01-02T00:00:00",)


class TestChangeScannerStatus:
    def test_raises_when_env_var_missing(self, sm):
        sm.SCANNER_CONTAINER_NAME = None
        with pytest.raises(ValueError, match="SCANNER_CONTAINER_NAME"):
            sm.change_scanner_status("start")

    def test_raises_when_env_var_empty_string(self, sm):
        sm.SCANNER_CONTAINER_NAME = ""
        with pytest.raises(ValueError, match="SCANNER_CONTAINER_NAME"):
            sm.change_scanner_status("start")

    def test_start_calls_container_start(self, sm, mocker):
        sm.SCANNER_CONTAINER_NAME = "scanner"
        container = MagicMock()
        client = MagicMock()
        client.containers.get.return_value = container
        mocker.patch("modules.scanner_manager.docker.from_env", return_value=client)
        sm.change_scanner_status("start")
        container.start.assert_called_once()
        client.close.assert_called_once()

    def test_stop_calls_container_stop(self, sm, mocker):
        sm.SCANNER_CONTAINER_NAME = "scanner"
        container = MagicMock()
        client = MagicMock()
        client.containers.get.return_value = container
        mocker.patch("modules.scanner_manager.docker.from_env", return_value=client)
        sm.change_scanner_status("stop")
        container.stop.assert_called_once()
        client.close.assert_called_once()

    def test_invalid_action_raises(self, sm, mocker):
        sm.SCANNER_CONTAINER_NAME = "scanner"
        client = MagicMock()
        client.containers.get.return_value = MagicMock()
        mocker.patch("modules.scanner_manager.docker.from_env", return_value=client)
        with pytest.raises(ValueError, match="Invalid action"):
            sm.change_scanner_status("restart")
        client.close.assert_called_once()

    def test_container_not_found_raised_as_exception(self, sm, mocker):
        import docker as docker_mod

        sm.SCANNER_CONTAINER_NAME = "scanner"
        client = MagicMock()
        client.containers.get.side_effect = docker_mod.errors.NotFound("nope")
        mocker.patch("modules.scanner_manager.docker.from_env", return_value=client)
        with pytest.raises(Exception, match="not found"):
            sm.change_scanner_status("start")
        client.close.assert_called_once()

    def test_api_error_raised_as_exception(self, sm, mocker):
        import docker as docker_mod

        sm.SCANNER_CONTAINER_NAME = "scanner"
        client = MagicMock()
        client.containers.get.side_effect = docker_mod.errors.APIError("boom")
        mocker.patch("modules.scanner_manager.docker.from_env", return_value=client)
        with pytest.raises(Exception, match="Docker API error"):
            sm.change_scanner_status("start")
        client.close.assert_called_once()
