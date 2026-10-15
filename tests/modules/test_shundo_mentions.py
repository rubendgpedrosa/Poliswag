"""Only a recipient's personal channel pings for that player's Shundo."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.config import Config
from modules.poracle_client import PoracleError
from modules.shiny_alerts import ShinyAlerts, poracle_template


def spawn(**overrides):
    return {
        "id": "1234",
        "pokemon_id": 25,
        "form_id": 0,
        "lat": 39.74,
        "lon": -8.8,
        "expire_timestamp": 2_000_000_000,
        "expire_timestamp_verified": True,
        "first_seen_timestamp": 1_000_000_000,
        "seen_type": "encounter",
        "iv": 100,
        "atk_iv": 15,
        "def_iv": 15,
        "sta_iv": 15,
        "cp": 812,
        "level": 27,
        "predicted_shiny": True,
        **overrides,
    }


def recipient(channel_id=101, discord_id=1):
    return {
        "discord_id": discord_id,
        "channel_id": channel_id,
        "player_id": str(discord_id),
        "alert_leiria": True,
        "alert_marinha": True,
        "shiny_notify_background": True,
        "shiny_notify_all": True,
    }


@pytest.fixture
def alerts(monkeypatch):
    monkeypatch.setattr(Config, "SHINY_ALERT_USER_IDS", [1])
    bot = MagicMock()
    bot.db.get_data_from_database = AsyncMock(return_value=[])
    bot.poracle.test_pokemon = AsyncMock()
    bot.get_user.return_value.send = AsyncMock()
    bot.get_channel.return_value.send = AsyncMock()
    alerts = ShinyAlerts(bot)
    alerts.store = MagicMock(
        notifications_enabled=AsyncMock(return_value=True),
        background_notifications_enabled=AsyncMock(return_value=True),
        record_alert=AsyncMock(return_value=True),
    )
    return alerts


@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("predicted_shiny", [True, "legacy"])
def test_only_channel_shundos_select_ping_templates(background, predicted_shiny):
    data = spawn(predicted_background=background)
    if predicted_shiny == "legacy":
        del data["predicted_shiny"]
    ordinary = (
        "shiny-background"
        if background and predicted_shiny is True
        else ("background" if background else "shiny")
    )
    assert poracle_template(data) == ordinary
    expected = "shiny-background-here" if background else "shiny-here"
    assert poracle_template(data, channel=True) == expected


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"def_iv": 14}, "shiny"),
        ({"atk_iv": None, "def_iv": None, "sta_iv": None}, "shiny"),
        ({"predicted_shiny": False, "predicted_background": True}, "background"),
        ({"def_iv": 14, "predicted_background": True}, "shiny-background"),
    ],
)
def test_other_predictions_never_select_ping_template(overrides, expected):
    assert poracle_template(spawn(**overrides), channel=True) == expected


@pytest.mark.parametrize("background", [False, True])
async def test_poracle_ping_is_part_of_the_personal_channel_alert(alerts, background):
    await alerts._alert(recipient(), spawn(predicted_background=background))
    _, target = alerts.poliswag.poracle.test_pokemon.call_args.args
    assert target["type"] == "discord:channel" and target["id"] == "101"
    assert target["template"] == (
        "shiny-background-here" if background else "shiny-here"
    )
    alerts.poliswag.get_channel.return_value.send.assert_not_awaited()
    alerts.poliswag.get_user.return_value.send.assert_not_awaited()


@pytest.mark.parametrize("background", [False, True])
async def test_poracle_dm_keeps_ordinary_template(alerts, background):
    await alerts._alert(
        recipient(channel_id=None), spawn(predicted_background=background)
    )
    _, target = alerts.poliswag.poracle.test_pokemon.call_args.args
    assert target["type"] == "discord:user"
    assert target["template"] == ("shiny-background" if background else "shiny")


@pytest.mark.parametrize(
    "overrides, ping",
    [
        ({}, True),
        ({"predicted_background": True}, True),
        ({"def_iv": 14}, False),
        ({"atk_iv": None, "def_iv": None, "sta_iv": None, "iv": None}, False),
        ({"predicted_shiny": False, "predicted_background": True}, False),
    ],
)
async def test_fallback_mentions_only_channel_shundos(alerts, overrides, ping):
    alerts.poliswag.poracle.test_pokemon.side_effect = PoracleError("down")
    await alerts._alert(recipient(), spawn(**overrides))
    send = alerts.poliswag.get_channel.return_value.send
    send.assert_awaited_once()
    message = send.call_args.kwargs
    assert message.get("content") == ("@here" if ping else None)
    assert message["allowed_mentions"].everyone is ping
    assert message["allowed_mentions"].users is False
    assert message["allowed_mentions"].roles is False
    assert message["allowed_mentions"].replied_user is False
    assert "embed" in message
    alerts.poliswag.get_user.return_value.send.assert_not_awaited()


@pytest.mark.parametrize("missing_channel", [False, True])
async def test_fallback_dm_never_pings_even_when_channel_is_missing(
    alerts, missing_channel
):
    alerts.poliswag.poracle.test_pokemon.side_effect = PoracleError("down")
    alerts.poliswag.get_channel.return_value = None
    await alerts._alert(recipient(channel_id=101 if missing_channel else None), spawn())
    send = alerts.poliswag.get_user.return_value.send
    send.assert_awaited_once()
    assert "content" not in send.call_args.kwargs
    assert send.call_args.kwargs["allowed_mentions"].everyone is False


async def test_same_spawn_only_pings_for_the_player_with_the_shiny_prediction(
    alerts, monkeypatch
):
    alerts.store.watermark = AsyncMock(return_value=1_000_000_000)
    alerts.store.set_watermark = AsyncMock()
    alerts.store.recipients = AsyncMock(return_value=[recipient(), recipient(202, 2)])
    alerts.store.shiny_eligible = AsyncMock(return_value={(25, 0, 0)})
    alerts.store.background_rules = AsyncMock(return_value=[{}])
    alerts.poliswag.db.get_data_from_database.return_value = [spawn()]
    monkeypatch.setattr(
        "modules.shiny_alerts.background_rule_for",
        lambda *_: {"probability": 1, "label": "test", "ends_at": 2_000_000_000},
    )
    monkeypatch.setattr(
        "modules.shiny_alerts.personal_draws",
        lambda encounter_id, player_id: (0 if player_id == "1" else 0.5, 0),
    )
    await alerts._alert_spawns()
    targets = [
        call.args[1] for call in alerts.poliswag.poracle.test_pokemon.await_args_list
    ]
    assert [(target["id"], target["template"]) for target in targets] == [
        ("101", "shiny-background-here"),
        ("202", "background"),
    ]
    assert all(target["type"] == "discord:channel" for target in targets)


@pytest.mark.parametrize("kind", ["shiny", "shiny-background"])
def test_ping_templates_have_fixed_content_and_existing_alert_structure(kind):
    root = Path(__file__).resolve().parents[2] / "templates" / "poracle"
    ping = json.loads((root / f"{kind}-here-monster.json").read_text())
    assert ping["id"] == f"{kind}-here"
    template = ping["template"]
    assert template["content"] == "@here"
    # Poracle joins arrays of strings during rendering: an allowed_mentions
    # parse list would become an invalid string. Its regular bot POST permits
    # the fixed @here content by default; there is no variable mention text.
    assert "allowed_mentions" not in template
    assert template["embed"]["color"] == "F2CD5C"
    assert "✨" in template["embed"]["title"]
    assert template["embed"]["thumbnail"]["url"] == "{{{map 'shiny-sprites' imgUrl}}}"
    assert template["embed"]["image"]["url"] == "{{{staticMap}}}"
    buttons = template["components"][0]["components"]
    assert [(button["label"], button["url"]) for button in buttons] == [
        ("Google", "{{{googleMapUrl}}}"),
        ("Apple", "{{{appleMapUrl}}}"),
    ]
