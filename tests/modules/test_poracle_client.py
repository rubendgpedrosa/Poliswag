"""Tests for modules.poracle_client.PoracleClient.

PoracleClient is a thin aiohttp wrapper. We patch the session getter so
tests stay offline and assert on the call chain (method, path, headers,
payload) and response handling (JSON, text, 204, errors).
"""

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from modules.poracle_client import PoracleClient, PoracleError


def _async_cm(inner):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=inner)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _response(
    status=200,
    json_data=None,
    text_data="",
    content_type="application/json",
    content_length=1,
):
    resp = MagicMock()
    resp.status = status
    resp.content_length = content_length
    resp.headers = {"Content-Type": content_type}
    resp.json = AsyncMock(return_value=json_data)
    resp.text = AsyncMock(return_value=text_data)
    return resp


@pytest.fixture
def client():
    poliswag = MagicMock()
    poliswag.utility.log_to_file = MagicMock()
    return PoracleClient(poliswag, base_url="http://poracle.test:3030", secret="xyz")


def _install_session(client, response):
    session = MagicMock()
    session.request = MagicMock(return_value=_async_cm(response))
    session.closed = False
    client._session = session
    return session


class TestRequest:
    async def test_parses_json_response(self, client):
        session = _install_session(client, _response(json_data={"ok": True}))
        out = await client._request("GET", "/api/humans/one/1")
        assert out == {"ok": True}
        args, kwargs = session.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://poracle.test:3030/api/humans/one/1"
        assert kwargs["json"] is None

    async def test_no_content_returns_none(self, client):
        _install_session(client, _response(status=204, content_length=0))
        out = await client._request("POST", "/api/humans/1/start")
        assert out is None

    async def test_error_status_raises_and_logs(self, client):
        _install_session(client, _response(status=401, text_data="bad secret"))
        with pytest.raises(PoracleError):
            await client._request("GET", "/api/humans/one/1")
        client.poliswag.utility.log_to_file.assert_called_once()

    async def test_non_json_content_type_returns_raw_text(self, client):
        _install_session(
            client,
            _response(text_data="plain body", content_type="text/plain"),
        )
        out = await client._request("GET", "/api/humans/one/1")
        assert out == "plain body"

    async def test_non_json_empty_text_returns_none(self, client):
        _install_session(
            client,
            _response(text_data="", content_type="text/plain"),
        )
        out = await client._request("GET", "/api/humans/one/1")
        assert out is None

    async def test_client_error_raises_and_logs(self, client):
        session = MagicMock()
        session.closed = False
        # aiohttp.ClientError raised when entering the context manager
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("boom"))
        cm.__aexit__ = AsyncMock(return_value=None)
        session.request = MagicMock(return_value=cm)
        client._session = session

        with pytest.raises(PoracleError):
            await client._request("GET", "/api/humans/one/1")
        client.poliswag.utility.log_to_file.assert_called_once()


class TestHumans:

    @pytest.mark.parametrize("user_id", [123, "123"])
    async def test_create_user_sends_initial_area_in_creation_payload(
        self, client, user_id
    ):
        response = {"human": {"id": "123"}, "status": "ok"}
        session = _install_session(client, _response(json_data=response))
        area = '["leiria", "marinhagrande"]'
        result = await client.create_user(user_id, "Rui", area=area)
        assert result == response
        session.request.assert_called_once()
        args, kwargs = session.request.call_args
        assert args == ("POST", "http://poracle.test:3030/api/humans")
        assert kwargs["json"] == {
            "id": "123",
            "name": "Rui",
            "type": "discord:user",
            "area": area,
        }

    @pytest.mark.parametrize("status", [409, 500])
    async def test_create_user_propagates_failure_without_followup_requests(
        self, client, status
    ):
        session = _install_session(client, _response(status=status, text_data="failed"))
        with pytest.raises(PoracleError, match=str(status)):
            await client.create_user(123, "Rui", area='["leiria"]')
        session.request.assert_called_once()

    async def test_create_user_timeout_is_not_treated_as_success(self, client):
        session = _install_session(client, _response())
        session.request.return_value.__aenter__.side_effect = TimeoutError("timed out")
        with pytest.raises(PoracleError, match="timed out"):
            await client.create_user(123, "Rui", area='["leiria"]')
        session.request.assert_called_once()


class TestMisc:
    async def test_reload_hits_reload_endpoint(self, client):
        session = _install_session(client, _response(status=204, content_length=0))
        await client.reload()
        args, _ = session.request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/api/reload")

    async def test_test_pokemon_shape(self, client):
        session = _install_session(client, _response(json_data={"ok": True}))
        await client.test_pokemon(
            {"pokemon_id": 25},
            {"id": "123", "type": "discord:channel"},
        )
        _, kwargs = session.request.call_args
        assert kwargs["json"]["type"] == "pokemon"
        assert kwargs["json"]["webhook"] == {"pokemon_id": 25}
        assert kwargs["json"]["target"] == {"id": "123", "type": "discord:channel"}


class TestSession:
    async def test_session_built_with_secret_header(self, client):
        session = client._get_session()
        try:
            assert session.headers.get("X-Poracle-Secret") == "xyz"
        finally:
            await client.close()

    async def test_session_built_without_header_when_no_secret(self):
        poliswag = MagicMock()
        client = PoracleClient(poliswag, base_url="http://x", secret="")
        session = client._get_session()
        try:
            assert "X-Poracle-Secret" not in session.headers
        finally:
            await client.close()

    async def test_close_closes_session(self, client):
        session = client._get_session()
        assert not session.closed
        await client.close()
        assert client._session is None


async def test_timeout_without_message_is_actionable(client):
    session = _install_session(client, _response())
    session.request.return_value.__aenter__.side_effect = TimeoutError()
    with pytest.raises(PoracleError, match="TimeoutError"):
        await client.reload()
    assert "TimeoutError" in client.poliswag.utility.log_to_file.call_args.args[0]
