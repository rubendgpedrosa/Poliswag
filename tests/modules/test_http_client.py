"""Tests for modules.http_client.fetch_data.

fetch_data has two branches:

1. DEV mode (Config.IS_PRODUCTION is False) + endpoint not in {all_down, events}:
   read a JSON file from the mock_data directory.
2. Otherwise: perform a real aiohttp request against Config.ENDPOINTS[key].

Both branches have several failure modes. We cover the mock-file branch directly
via pyfakefs-style file mocks, and the HTTP branch via an async-context-manager
mock injected in place of aiohttp.ClientSession.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp

from modules import http_client


def _async_cm(inner):
    """Wrap *inner* in a MagicMock that behaves as an async context manager."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=inner)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _install_aiohttp_mock(
    mocker,
    *,
    json_data=None,
    text_data="",
    content_type="application/json",
    raise_exc=None,
):
    """Replace aiohttp.ClientSession in the http_client module with a mock chain.

    Returns the response mock so tests can make additional assertions on it.
    """
    response = MagicMock()
    response.headers = {"Content-Type": content_type}
    if raise_exc is not None:
        response.raise_for_status = MagicMock(side_effect=raise_exc)
    else:
        response.raise_for_status = MagicMock()
    response.json = AsyncMock(return_value=json_data)
    response.text = AsyncMock(return_value=text_data)

    session = MagicMock()
    session.request = MagicMock(return_value=_async_cm(response))

    mocker.patch(
        "modules.http_client.aiohttp.ClientSession",
        return_value=_async_cm(session),
    )
    return response


class TestDevMockFileBranch:
    """When IS_PRODUCTION is False and endpoint is mapped, read from mock_data/."""

    def test_returns_parsed_json_from_mock_file(self, mocker, tmp_path):
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", False)
        mocker.patch.object(http_client.Config, "MOCK_DATA_DIR", str(tmp_path))
        (tmp_path / "scanner_status.json").write_text(json.dumps({"areas": ["mocked"]}))

        import asyncio

        result = asyncio.run(http_client.fetch_data("scanner_status"))
        assert result == {"areas": ["mocked"]}

    async def test_returns_none_when_mock_file_missing(self, mocker, tmp_path):
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", False)
        mocker.patch.object(http_client.Config, "MOCK_DATA_DIR", str(tmp_path))
        # No file written — open() will raise FileNotFoundError.

        log = MagicMock()
        result = await http_client.fetch_data("device_status", log_fn=log)
        assert result is None
        log.assert_called_once()
        assert "Error loading mock data" in log.call_args.args[0]

    async def test_returns_none_when_mock_file_has_invalid_json(self, mocker, tmp_path):
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", False)
        mocker.patch.object(http_client.Config, "MOCK_DATA_DIR", str(tmp_path))
        (tmp_path / "account_status.json").write_text("{not valid json")

        result = await http_client.fetch_data("account_status")
        assert result is None

    async def test_unknown_endpoint_key_uses_default_file(self, mocker, tmp_path):
        # mock_file_map does not contain this key, so it falls through to
        # "default.json" which does not exist.
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", False)
        mocker.patch.object(http_client.Config, "MOCK_DATA_DIR", str(tmp_path))

        result = await http_client.fetch_data("unknown_key")
        assert result is None


class TestDevBranchExceptions:
    """all_down and events bypass the mock_file branch even in DEV mode."""

    async def test_all_down_in_dev_goes_to_http_branch(self, mocker):
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", False)
        mocker.patch.object(http_client.Config, "ENDPOINTS", {"all_down": None})

        log = MagicMock()
        result = await http_client.fetch_data("all_down", log_fn=log)
        # No URL configured → logs and returns None from the HTTP branch.
        assert result is None
        log.assert_called_once()
        assert "No URL defined" in log.call_args.args[0]

    async def test_events_in_dev_goes_to_http_branch(self, mocker):
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", False)
        mocker.patch.object(http_client.Config, "ENDPOINTS", {"events": None})

        result = await http_client.fetch_data("events")
        assert result is None


class TestHttpBranchHappyPaths:
    async def test_production_json_response_is_parsed(self, mocker):
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", True)
        mocker.patch.object(
            http_client.Config,
            "ENDPOINTS",
            {"scanner_status": "https://example.invalid/status"},
        )
        _install_aiohttp_mock(
            mocker,
            json_data={"areas": ["live"]},
            content_type="application/json",
        )
        result = await http_client.fetch_data("scanner_status")
        assert result == {"areas": ["live"]}

    async def test_text_response_parseable_as_json(self, mocker):
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", True)
        mocker.patch.object(
            http_client.Config,
            "ENDPOINTS",
            {"scanner_status": "https://example.invalid/status"},
        )
        _install_aiohttp_mock(
            mocker,
            text_data='{"areas": ["text-json"]}',
            content_type="text/plain",
        )
        result = await http_client.fetch_data("scanner_status")
        assert result == {"areas": ["text-json"]}

    async def test_events_endpoint_returns_raw_text_when_not_json(self, mocker):
        # The `events` endpoint is allowed to return non-JSON text verbatim.
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", True)
        mocker.patch.object(
            http_client.Config,
            "ENDPOINTS",
            {"events": "https://example.invalid/events"},
        )
        _install_aiohttp_mock(
            mocker,
            text_data="not json just text",
            content_type="text/plain",
        )
        result = await http_client.fetch_data("events")
        assert result == "not json just text"


class TestHttpBranchFailures:
    async def test_missing_endpoint_url_returns_none(self, mocker):
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", True)
        mocker.patch.object(http_client.Config, "ENDPOINTS", {})
        log = MagicMock()
        result = await http_client.fetch_data("scanner_status", log_fn=log)
        assert result is None
        assert "No URL defined" in log.call_args.args[0]

    async def test_empty_text_response_returns_none(self, mocker):
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", True)
        mocker.patch.object(
            http_client.Config,
            "ENDPOINTS",
            {"scanner_status": "https://example.invalid/status"},
        )
        _install_aiohttp_mock(
            mocker,
            text_data="   ",  # whitespace-only is considered empty
            content_type="text/plain",
        )
        log = MagicMock()
        result = await http_client.fetch_data("scanner_status", log_fn=log)
        assert result is None
        assert "Empty response" in log.call_args.args[0]

    async def test_invalid_json_text_for_non_events_returns_none(self, mocker):
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", True)
        mocker.patch.object(
            http_client.Config,
            "ENDPOINTS",
            {"scanner_status": "https://example.invalid/status"},
        )
        _install_aiohttp_mock(
            mocker,
            text_data="<html>not json</html>",
            content_type="text/html",
        )
        log = MagicMock()
        result = await http_client.fetch_data("scanner_status", log_fn=log)
        assert result is None
        assert "Error decoding JSON" in log.call_args.args[0]

    async def test_client_response_error_returns_none(self, mocker):
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", True)
        mocker.patch.object(
            http_client.Config,
            "ENDPOINTS",
            {"scanner_status": "https://example.invalid/status"},
        )
        fake_req_info = MagicMock()
        fake_history = ()
        err = aiohttp.ClientResponseError(
            request_info=fake_req_info,
            history=fake_history,
            status=503,
            message="Service Unavailable",
        )
        _install_aiohttp_mock(mocker, raise_exc=err)
        log = MagicMock()
        result = await http_client.fetch_data("scanner_status", log_fn=log)
        assert result is None
        assert "HTTP error" in log.call_args.args[0]

    async def test_generic_exception_returns_none(self, mocker):
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", True)
        mocker.patch.object(
            http_client.Config,
            "ENDPOINTS",
            {"scanner_status": "https://example.invalid/status"},
        )
        _install_aiohttp_mock(mocker, raise_exc=RuntimeError("boom"))
        log = MagicMock()
        result = await http_client.fetch_data("scanner_status", log_fn=log)
        assert result is None
        assert "Error fetching data" in log.call_args.args[0]


class TestLogFnOptional:
    async def test_missing_log_fn_is_safe(self, mocker):
        # When log_fn is None the internal _log helper should silently no-op.
        mocker.patch.object(http_client.Config, "IS_PRODUCTION", True)
        mocker.patch.object(http_client.Config, "ENDPOINTS", {})
        # Must not raise.
        assert await http_client.fetch_data("scanner_status") is None
