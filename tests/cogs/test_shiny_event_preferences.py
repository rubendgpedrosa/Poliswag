from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.shiny import Shiny
from modules.config import Config


@pytest.fixture
def command(mocker):
    mocker.patch.object(Config, "SHINY_EVENT_DIGESTS_ENABLED", True)
    cog = Shiny(MagicMock())
    cog.store = MagicMock(
        event_preferences=AsyncMock(
            return_value={"event_alerts_enabled": 0, "event_pokemon_ids": None}
        ),
        set_event_preferences=AsyncMock(return_value=True),
    )
    ctx = MagicMock()
    ctx.author.id = 111
    ctx.send = AsyncMock()
    return cog, ctx


@pytest.mark.parametrize(
    "text,enabled,ids",
    [
        ("eventos ligar", True, []),
        ("eventos ligar 570,25,570", True, [25, 570]),
        ("eventos desligar", False, []),
    ],
)
async def test_user_changes_only_their_own_settings(command, text, enabled, ids):
    cog, ctx = command
    await Shiny.shiny.callback(cog, ctx, texto=text)
    cog.store.set_event_preferences.assert_awaited_once_with(111, enabled, ids)


@pytest.mark.parametrize(
    "text",
    [
        "eventos ligar 0",
        "eventos ligar 570,",
        "eventos ligar -2",
        "eventos ligar abc",
        "eventos ligar 25 570",
        "eventos desligar 25",
    ],
)
async def test_invalid_species_or_action_is_rejected(command, text):
    cog, ctx = command
    await Shiny.shiny.callback(cog, ctx, texto=text)
    cog.store.set_event_preferences.assert_not_awaited()


async def test_link_required_and_feature_disabled(command, mocker):
    cog, ctx = command
    cog.store.event_preferences.return_value = None
    await Shiny.shiny.callback(cog, ctx, texto="eventos ligar")
    cog.store.set_event_preferences.assert_not_awaited()
    mocker.patch.object(Config, "SHINY_EVENT_DIGESTS_ENABLED", False)
    cog.store.event_preferences.reset_mock()
    await Shiny.shiny.callback(cog, ctx, texto="eventos ligar")
    cog.store.event_preferences.assert_not_awaited()
