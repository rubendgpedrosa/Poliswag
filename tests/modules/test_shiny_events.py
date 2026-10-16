from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.config import Config
from modules.shiny_events import ShinyEventDigests, event_predictions, species_filter

PLAYER = "123456789012345678901"
NOW = 1791629000


def spawn(identifier="1", **changes):
    row = {
        "id": identifier,
        "pokemon_id": 570,
        "form_id": 0,
        "lat": 39.74,
        "lon": -8.80,
        "expire_timestamp": NOW + 600,
        "expire_timestamp_verified": True,
        "seen_type": "encounter",
        "iv": 98,
        "name": "Zorua",
        "rate": 25,
    }
    row.update(changes)
    return row


def recipient(**changes):
    row = {
        "discord_id": 111,
        "player_id": PLAYER,
        "alert_leiria": True,
        "alert_marinha": False,
        "event_pokemon_ids": None,
    }
    row.update(changes)
    return row


def test_real_java_fixture_matches_event_threshold_not_base():
    # The independent Java fixture gives nextInt(512)=9 for this pair, so its
    # first draw lies in [9/512,10/512): event 1/25, but not base 1/512.
    predictions = event_predictions([spawn()], recipient(), NOW)
    assert len(predictions) == 1 and predictions[0][1] == 25
    assert 9 / 512 <= predictions[0][2] < 10 / 512


@pytest.mark.parametrize(
    "draw,expected",
    [
        (0, 0),
        # Draws are 24-bit fractions: the grid step either side of 1/512.
        (32767 / 2**24, 0),
        (32768 / 2**24, 1),
        (0.039999, 1),
        (0.04, 0),
        (0.5, 0),
    ],
)
def test_event_only_and_strict_odds_boundary(mocker, draw, expected):
    mocker.patch("modules.shiny_events.shiny_draw", return_value=draw)
    assert len(event_predictions([spawn()], recipient(), NOW)) == expected


@pytest.mark.parametrize(
    "changes",
    [
        {"lon": -8.93},
        {"seen_type": "lure"},
        {"seen_type": "tappable"},
        {"expire_timestamp": NOW + 119},
        {"rate": 0},
        {"rate": 512},
        {"id": "bad"},
    ],
)
def test_unsupported_area_type_expiry_or_rate_is_excluded(changes):
    assert event_predictions([spawn(**changes)], recipient(), NOW) == []


def test_species_filters_and_unknown_expiry():
    assert event_predictions(
        [spawn(expire_timestamp=None)], recipient(event_pokemon_ids="[570]"), NOW
    )
    assert not event_predictions([spawn()], recipient(event_pokemon_ids="[25]"), NOW)
    assert not event_predictions([spawn()], recipient(event_pokemon_ids="corrupt"), NOW)
    assert species_filter(None) is None and species_filter("[]") is None


def test_digest_caps_unique_spawns(mocker):
    mocker.patch("modules.shiny_events.shiny_draw", return_value=0.01)
    rows = [spawn(str(value)) for value in range(25)]
    predictions = event_predictions([rows[0], *rows], recipient(), NOW)
    assert len(predictions) == 10
    assert len({item[0]["id"] for item in predictions}) == 10


@pytest.fixture
def service(mocker):
    mocker.patch.object(Config, "SHINY_EVENT_DIGESTS_ENABLED", True)
    mocker.patch.object(Config, "SHINY_ALERT_USER_IDS", [111])
    mocker.patch("modules.shiny_events.time.time", return_value=NOW)
    bot = MagicMock()
    bot.db.get_data_from_database = AsyncMock(return_value=[spawn()])
    bot.get_user.return_value.send = AsyncMock()
    events = ShinyEventDigests(bot)
    events.store = MagicMock(
        event_recipients=AsyncMock(return_value=[recipient()]),
        claim_event_digest=AsyncMock(return_value=True),
        record_event_alert=AsyncMock(return_value=True),
        mark_event_delivery=AsyncMock(),
    )
    return events


async def test_grouped_delivery_records_actual_outcome(service):
    await service.tick()
    service.store.event_recipients.assert_awaited_once_with([111])
    service.store.claim_event_digest.assert_awaited_once_with(111)
    service.store.record_event_alert.assert_awaited_once()
    sent = service.poliswag.get_user.return_value.send.call_args.kwargs
    assert "1/25" in sent["embed"].description
    assert "google.com/maps" in sent["embed"].description
    assert "mapa" in sent["embed"].footer.text
    assert "pogoleiria.pt/shiny" not in sent["embed"].description
    service.store.mark_event_delivery.assert_awaited_once_with(111, ["1"], True)


async def test_disabled_feature_performs_no_reads_or_sends(service, mocker):
    mocker.patch.object(Config, "SHINY_EVENT_DIGESTS_ENABLED", False)
    await service.tick()
    service.store.event_recipients.assert_not_awaited()
    service.poliswag.db.get_data_from_database.assert_not_awaited()
    service.poliswag.get_user.return_value.send.assert_not_awaited()


async def test_digest_claim_lost_to_another_sender_sends_nothing(service):
    service.store.claim_event_digest.return_value = False
    await service.tick()
    service.store.record_event_alert.assert_not_awaited()
    service.poliswag.get_user.return_value.send.assert_not_awaited()


async def test_previously_recorded_spawns_are_not_sent_again(service):
    service.store.record_event_alert.return_value = False
    await service.tick()
    service.poliswag.get_user.return_value.send.assert_not_awaited()
    service.store.mark_event_delivery.assert_not_awaited()


async def test_failed_delivery_recorded_without_retrying_forever(service):
    service.poliswag.get_user.return_value.send.side_effect = discord.Forbidden(
        MagicMock(status=403), "closed"
    )
    await service.tick()
    service.store.mark_event_delivery.assert_awaited_once_with(111, ["1"], False)


async def test_empty_prediction_set_does_not_consume_cooldown(service):
    service.poliswag.db.get_data_from_database.return_value = []
    await service.tick()
    service.store.claim_event_digest.assert_not_awaited()


async def test_only_unexpired_rates_and_unalerted_spawns_are_selected(service):
    await service.tick()
    sql = service.poliswag.db.get_data_from_database.call_args.args[0]
    assert "r.ends_at > UTC_TIMESTAMP()" in sql
    assert "a.encounter_id IS NULL" in sql
    assert service.poliswag.db.get_data_from_database.call_args.kwargs["params"] == (
        111,
        NOW + 120,
    )
