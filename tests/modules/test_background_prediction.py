"""Synthetic RNG fixtures and eligibility tests, not observed game outcomes."""

import math
from unittest.mock import AsyncMock

import pytest

from modules.background_prediction import background_rule_for
from modules.shiny_roll import is_background_at, personal_draws
from tests.modules import test_shiny_alerts as shiny_fixtures
from tests.modules.test_shiny_alerts import recipient, spawn, with_spawns


@pytest.fixture
def alerts():
    return shiny_fixtures.alerts.__wrapped__()


@pytest.fixture(autouse=True)
def only_the_owner(mocker):
    mocker.patch.object(
        shiny_fixtures.Config, "SHINY_ALERT_USER_IDS", [shiny_fixtures.OWNER]
    )


def rule(**changes):
    return dict(
        rule_id="test",
        label="Test event",
        pokemon_id=25,
        form_id=0,
        costume_id=0,
        encounter_source="wild",
        probability=0.04,
        starts_at=100,
        ends_at=200,
        min_lat=39,
        max_lat=40,
        min_lon=-9,
        max_lon=-8,
        enabled=1,
        **changes,
    )


@pytest.mark.parametrize(
    "player,expected",
    [
        ("0123456789abcdef", (0.22137850522994995, 0.1431974172592163)),
        ("Player-ID:ABC/123", (0.3684830665588379, 0.9563266038894653)),
        (" Trainer-é ", (0.03772324323654175, 0.7365694642066956)),
    ],
)
def test_two_draw_vectors(player, expected):
    assert personal_draws(1, player) == expected


def test_cutoff_uses_the_second_float_lattice():
    assert is_background_at(671088 / 2**24, 0.04)
    assert not is_background_at(671089 / 2**24, 0.04)
    for probability in (0, -1, 1.1, math.nan, math.inf):
        assert not is_background_at(0, probability)


@pytest.mark.parametrize(
    "now,match", [(99, False), (100, True), (199, True), (200, False)]
)
def test_time_window(now, match):
    assert bool(background_rule_for(spawn(), [rule()], now)) == match


@pytest.mark.parametrize(
    "changes",
    [
        {"seen_type": "lure"},
        {"seen_type": "research"},
        {"spawn_id": None},
        {"spawn_id": "0"},
        {"pokestop_id": "lure-stop"},
        {"form_id": 2108},
        {"costume": 1},
        {"lat": 38},
        {"lon": -7},
        {"lat": math.nan},
    ],
)
def test_unverified_source_variant_or_geography_cannot_qualify(changes):
    assert background_rule_for(spawn(**changes), [rule()], 100) is None


def test_geometry_edges_are_inclusive_and_conflicting_rates_fail_closed():
    assert background_rule_for(spawn(lat=39, lon=-9), [rule()], 100)
    conflict = {**rule(), "rule_id": "other", "probability": 0.05}
    assert background_rule_for(spawn(), [rule(), conflict], 100) is None
    assert background_rule_for(spawn(), [{**rule(), "enabled": "0"}], 100) is None


def setup_alerts(alerts, mocker, encounter="66", eligible=False):
    mocker.patch("modules.shiny_alerts.time.time", return_value=150)
    alerts.store.watermark.return_value = 120
    r = {**recipient(), "shiny_notify_background": 1}
    alerts.store.recipients.return_value = [r]
    alerts.store.background_rules = AsyncMock(return_value=[rule()])
    alerts.store.background_notifications_enabled = AsyncMock(return_value=True)
    alerts.store.shiny_eligible.return_value = {(25, 0, 0)} if eligible else set()
    alerts.store.owns_shiny.return_value = True
    s = spawn(encounter, first_seen_timestamp=120)
    with_spawns(alerts, [s])
    alerts.store.watermark.return_value = 120
    alerts.store.recipients.return_value = [r]
    return r, s


async def test_background_only_does_not_require_shiny_release_or_missing_collection(
    alerts, mocker
):
    setup_alerts(alerts, mocker)
    await alerts._alert_spawns()
    # Poracle is asked first, with the template that does not say shiny.
    _, target = alerts.poliswag.poracle.test_pokemon.await_args.args
    assert target["template"] == "background"
    # It is down in this fixture: the bot's own embed carries the same marks.
    embed = alerts.user.send.await_args.kwargs["embed"]
    assert embed.title.startswith("🖼️ Fundo especial") and "Shiny" not in embed.title
    assert "✨" not in embed.title
    assert "experimental" in embed.description
    assert embed.color.value == 0x5AA9E6
    alerts.store.owns_shiny.assert_not_awaited()
    alerts.store.record_alert.assert_awaited_once()


async def test_joint_prediction_is_one_combined_alert(alerts, mocker):
    setup_alerts(alerts, mocker, encounter="12784", eligible=True)
    await alerts._alert_spawns()
    _, target = alerts.poliswag.poracle.test_pokemon.await_args.args
    assert target["template"] == "shiny-background"
    embed = alerts.user.send.await_args.kwargs["embed"]
    assert embed.title.startswith("✨🖼️ Shiny + fundo especial")
    assert embed.color.value == 0xF2CD5C
    alerts.user.send.assert_awaited_once()


async def test_a_background_alert_poracle_takes_is_not_sent_twice(alerts, mocker):
    setup_alerts(alerts, mocker)
    alerts.poliswag.poracle.test_pokemon = AsyncMock()
    await alerts._alert_spawns()
    alerts.poliswag.poracle.test_pokemon.assert_awaited_once()
    alerts.user.send.assert_not_awaited()


def test_the_poracle_templates_are_the_copies_kept_here_and_only_mark_the_difference():
    import json
    from pathlib import Path

    folder = Path(__file__).resolve().parents[2] / "templates" / "poracle"
    shiny = json.loads((folder / "shiny-monster.json").read_text())
    # Gold is a Shiny, with or without a background; sky blue a background alone.
    assert shiny["template"]["embed"]["color"] == "F2CD5C"
    for name, prefix, color in (
        ("background", "🖼️ ", "5AA9E6"),
        ("shiny-background", "✨🖼️ ", "F2CD5C"),
    ):
        template = json.loads((folder / f"{name}-monster.json").read_text())
        assert template["id"] == name
        embed, base = template["template"]["embed"], shiny["template"]["embed"]
        assert embed["title"] == prefix + base["title"].removeprefix("✨ ")
        assert embed["color"] == color
        assert embed["description"].startswith("🖼️ **Fundo especial previsto**")
        assert embed["description"].endswith(base["description"])
        assert template["template"]["components"] == shiny["template"]["components"]


async def test_background_preference_rechecked_before_delivery(alerts, mocker):
    setup_alerts(alerts, mocker)
    alerts.store.background_notifications_enabled.return_value = False
    await alerts._alert_spawns()
    alerts.user.send.assert_not_awaited()
    alerts.store.record_alert.assert_not_awaited()


async def test_master_notifications_and_dedup_still_apply(alerts, mocker):
    r, s = setup_alerts(alerts, mocker)
    alerts.store.notifications_enabled.return_value = False
    await alerts._alert_spawns()
    alerts.user.send.assert_not_awaited()
    alerts.store.notifications_enabled.return_value = True
    alerts.store.record_alert.return_value = False
    await alerts._alert_spawns()
    alerts.user.send.assert_not_awaited()


async def test_no_rules_loaded_without_background_opt_in(alerts, mocker):
    setup_alerts(alerts, mocker)
    alerts.store.recipients.return_value[0]["shiny_notify_background"] = 0
    await alerts._alert_spawns()
    alerts.store.background_rules.assert_not_awaited()
    alerts.user.send.assert_not_awaited()


async def test_event_expiry_rechecked_before_background_delivery(alerts, mocker):
    r, s = setup_alerts(alerts, mocker)
    await alerts._alert(
        r,
        {
            **s,
            "predicted_background": True,
            "predicted_shiny": False,
            "background_ends_at": 150,
        },
    )
    alerts.user.send.assert_not_awaited()
    alerts.store.record_alert.assert_not_awaited()


async def test_disabled_background_joint_falls_back_to_existing_shiny_preferences(
    alerts, mocker
):
    setup_alerts(alerts, mocker, encounter="12784", eligible=True)
    alerts.store.background_notifications_enabled.return_value = False
    alerts.store.owns_shiny.return_value = False
    await alerts._alert_spawns()
    embed = alerts.user.send.await_args.kwargs["embed"]
    assert "Fundo" not in embed.title
    alerts.poliswag.poracle.test_pokemon.assert_awaited_once()
