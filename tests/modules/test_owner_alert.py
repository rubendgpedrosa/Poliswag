"""Tests for modules.owner_alert: HA first, a DM when HA can't take it."""

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import discord
import pytest

from modules import owner_alert

URL = "http://ha.local/api/webhook/poliswag_owner"


def _poliswag(send=None):
    user = MagicMock()
    user.send = send or AsyncMock()
    poliswag = MagicMock()
    poliswag.get_user.return_value = user
    return poliswag, user


def _session(mocker, *, status=200, raise_exc=None):
    response = MagicMock(status=status)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.post = (
        MagicMock(side_effect=raise_exc) if raise_exc else MagicMock(return_value=cm)
    )
    mocker.patch("modules.owner_alert.get_session", return_value=session)
    return session


@pytest.fixture
def production(mocker):
    mocker.patch("modules.owner_alert.Config.IS_PRODUCTION", True)
    mocker.patch("modules.owner_alert.Config.MY_ID", 123)
    mocker.patch.dict("modules.owner_alert.Config.ENDPOINTS", {"owner_alert": URL})


def test_plain_strips_discord_markdown():
    assert (
        owner_alert.plain("⚠️ **Site** não responde: `x`") == "⚠️ Site não responde: x"
    )


@pytest.mark.usefixtures("production")
async def test_goes_to_home_assistant_without_a_dm(mocker):
    session = _session(mocker)
    poliswag, user = _poliswag()
    assert await owner_alert.notify_owner(poliswag, "**down**", title="T", tag="t")
    session.post.assert_called_once()
    assert session.post.call_args.kwargs["json"] == {
        "title": "T",
        "message": "down",
        "tag": "t",
    }
    user.send.assert_not_called()


@pytest.mark.usefixtures("production")
async def test_falls_back_to_dm_when_ha_errors(mocker):
    _session(mocker, status=500)
    poliswag, user = _poliswag()
    assert await owner_alert.notify_owner(poliswag, "**down**", title="T", tag="t")
    user.send.assert_awaited_once_with("**down**")


@pytest.mark.usefixtures("production")
async def test_falls_back_to_dm_when_ha_unreachable(mocker):
    _session(mocker, raise_exc=aiohttp.ClientConnectionError())
    poliswag, user = _poliswag()
    assert await owner_alert.notify_owner(poliswag, "down", title="T", tag="t")
    user.send.assert_awaited_once()


async def test_dm_outside_production(mocker):
    mocker.patch("modules.owner_alert.Config.IS_PRODUCTION", False)
    mocker.patch("modules.owner_alert.Config.MY_ID", 123)
    session = _session(mocker)
    poliswag, user = _poliswag()
    assert await owner_alert.notify_owner(poliswag, "down", title="T", tag="t")
    session.post.assert_not_called()
    user.send.assert_awaited_once()


@pytest.mark.usefixtures("production")
async def test_false_when_nothing_delivered(mocker):
    _session(mocker, status=500)
    poliswag, _ = _poliswag(
        send=AsyncMock(side_effect=discord.HTTPException(MagicMock(status=403), "no"))
    )
    assert not await owner_alert.notify_owner(poliswag, "down", title="T", tag="t")
