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
    async def test_get_human_swallows_not_found(self, client):
        _install_session(client, _response(status=404, text_data="nope"))
        out = await client.get_human(123)
        assert out is None

    async def test_create_channel_sends_expected_payload(self, client):
        session = _install_session(client, _response(json_data={"id": "123"}))
        await client.create_channel(123, "leiria-100iv")
        _, kwargs = session.request.call_args
        assert kwargs["json"] == {
            "id": "123",
            "name": "leiria-100iv",
            "type": "discord:channel",
        }

    async def test_set_areas_posts_array(self, client):
        session = _install_session(client, _response(status=204, content_length=0))
        await client.set_areas(123, ["leiria"])
        _, kwargs = session.request.call_args
        assert kwargs["json"] == ["leiria"]


class TestPokemonTracking:
    async def test_list_unwraps_poracle_envelope(self, client):
        _install_session(
            client,
            _response(json_data={"pokemon": [{"uid": 1}], "status": "ok"}),
        )
        out = await client.list_pokemon_tracking(123)
        assert out == [{"uid": 1}]

    async def test_list_returns_bare_list(self, client):
        _install_session(client, _response(json_data=[{"uid": 1}]))
        out = await client.list_pokemon_tracking(123)
        assert out == [{"uid": 1}]

    async def test_list_envelope_without_pokemon_key_empty(self, client):
        _install_session(client, _response(json_data={"unexpected": True}))
        out = await client.list_pokemon_tracking(123)
        assert out == []

    async def test_add_wraps_single_rule_in_array(self, client):
        session = _install_session(client, _response(json_data={"ok": True}))
        await client.add_pokemon_tracking(123, {"pokemon_id": 25, "min_iv": 90})
        _, kwargs = session.request.call_args
        assert kwargs["json"] == [{"pokemon_id": 25, "min_iv": 90}]

    async def test_add_preserves_array(self, client):
        session = _install_session(client, _response(json_data=[]))
        rules = [{"pokemon_id": 1}, {"pokemon_id": 2}]
        await client.add_pokemon_tracking(123, rules)
        _, kwargs = session.request.call_args
        assert kwargs["json"] == rules

    async def test_delete_hits_by_uid_path(self, client):
        session = _install_session(client, _response(status=204, content_length=0))
        await client.delete_pokemon_tracking_uid(123, "abc")
        args, _ = session.request.call_args
        assert args[0] == "DELETE"
        assert args[1] == "http://poracle.test:3030/api/tracking/pokemon/123/byUid/abc"


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
