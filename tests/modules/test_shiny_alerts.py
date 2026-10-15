"""Shiny alerts: the link announcements, the spawn alerts, the DM itself.
The database is faked; the roll is the real one, fed vectors that the
reference JavaScript rolls to 0 (see tests/modules/test_shiny_roll.py)."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules import shiny_alerts
from modules.config import Config
from modules.poracle_client import PoracleError
from modules.shiny_alerts import ShinyAlerts, link_message, wants_alert

OWNER = 111
PLAYER = "0123456789abcdef"
ZERO = "10000000000000000168"  # rolls 0 for PLAYER
OTHER = "1"  # rolls 113 for PLAYER


@pytest.fixture(autouse=True)
def only_the_owner(mocker):
    mocker.patch.object(Config, "SHINY_ALERT_USER_IDS", [OWNER])


@pytest.fixture
def alerts():
    bot = MagicMock()
    bot.db.get_data_from_database = AsyncMock(return_value=[])
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user.return_value = user
    # Poracle is "down" by default, so these tests exercise the bot's own
    # embed, the fallback. TestThroughPoracle covers the normal path.
    bot.poracle.test_pokemon = AsyncMock(side_effect=PoracleError("down"))
    a = ShinyAlerts(bot)
    a.channels.tick = AsyncMock()
    a.store = MagicMock(
        unannounced=AsyncMock(return_value=[]),
        mark_announced=AsyncMock(),
        recipients=AsyncMock(return_value=[]),
        watermark=AsyncMock(return_value=None),
        set_watermark=AsyncMock(),
        record_alert=AsyncMock(return_value=True),
        shiny_eligible=AsyncMock(return_value={(25, 0, 0), (570, 2108, 0)}),
        owns_shiny=AsyncMock(return_value=False),
        notifications_enabled=AsyncMock(return_value=True),
        notification_changes=AsyncMock(return_value=[]),
        claim_notification_change=AsyncMock(return_value=True),
        pending_settings=AsyncMock(return_value=[]),
        confirm_settings=AsyncMock(),
    )
    a.user = user
    return a


class TestLinkMessage:
    def test_linked_names_the_trainer(self):
        row = {"status": "linked", "trainer_name": "Rita99", "fail_reason": None}
        assert "**Rita99**" in link_message(row)

    def test_failed_gives_the_reason_and_the_way_back(self):
        row = {
            "status": "failed",
            "trainer_name": None,
            "fail_reason": "código não encontrado",
        }
        text = link_message(row)
        assert "código não encontrado" in text and "!shiny" in text


class TestAnnounceLinks:
    async def test_the_owner_is_told_once(self, alerts):
        alerts.store.unannounced.return_value = [
            {
                "discord_id": OWNER,
                "status": "linked",
                "trainer_name": "A",
                "fail_reason": None,
            }
        ]
        await alerts.tick()
        alerts.user.send.assert_awaited_once()
        alerts.store.mark_announced.assert_awaited_once_with(OWNER)

    async def test_nobody_outside_the_pilot_is_messaged(self, alerts):
        # Only the pilot's users hear from the bot about shinies at all; a
        # link made for anyone else is closed without a DM.
        alerts.store.unannounced.return_value = [
            {
                "discord_id": 5,
                "status": "linked",
                "trainer_name": "B",
                "fail_reason": None,
            },
            {
                "discord_id": 6,
                "status": "failed",
                "trainer_name": None,
                "fail_reason": "x",
            },
        ]
        await alerts.tick()
        alerts.user.send.assert_not_awaited()
        assert [c.args[0] for c in alerts.store.mark_announced.await_args_list] == [
            5,
            6,
        ]

    async def test_closed_dms_are_still_marked(self, alerts):
        alerts.store.unannounced.return_value = [
            {
                "discord_id": OWNER,
                "status": "failed",
                "trainer_name": None,
                "fail_reason": "x",
            }
        ]
        alerts.user.send.side_effect = discord.Forbidden(MagicMock(status=403), "no")
        await alerts.tick()
        alerts.store.mark_announced.assert_awaited_once_with(OWNER)


def spawn(encounter_id=ZERO, **over):
    row = {
        "id": encounter_id,
        "pokemon_id": 25,
        "form_id": 0,
        "lat": 39.7436,
        "lon": -8.8071,
        "expire_timestamp": 1791630000,
        "expire_timestamp_verified": 1,
        "seen_type": "encounter",
        "first_seen_timestamp": 1791629000,
        "iv": 97.8,
        "atk_iv": 15,
        "def_iv": 14,
        "sta_iv": 15,
        "cp": 812,
        "level": 27,
        "costume": 0,
        "gender": 2,
        "move_1": 202,
        "move_2": 88,
        "weight": 12.5,
        "height": 0.7,
        "size": 3,
        "weather": 0,
        "spawn_id": 0xD1F3A2B1C5,
        "pokestop_id": None,
        "is_event": 0,
        "updated": 1791629050,
        "pvp": None,
    }
    row.update(over)
    return row


def recipient(
    leiria=1, marinha=1, channel_id=None, discord_id=OWNER, notify_all=0, notify_hundo=1
):
    return {
        "discord_id": discord_id,
        "discord_name": "faynn",
        "channel_id": channel_id,
        "player_id": PLAYER,
        "alert_leiria": leiria,
        "alert_marinha": marinha,
        "shiny_notify_all": notify_all,
        "shiny_notify_hundo": notify_hundo,
    }


def with_spawns(alerts, spawns, name="Pikachu", seen_shiny=True):
    """Route the three reads by what they select."""

    async def read(sql, params=None, **_):
        if "pokemon_shiny_stats" in sql:
            return [{"seen": 1}] if seen_shiny else []
        if "pokemon_name" in sql:
            return [{"name": name, "form_name": None, "form_id": 0}] if name else []
        return spawns

    alerts.poliswag.db.get_data_from_database = AsyncMock(side_effect=read)
    alerts.store.watermark.return_value = 1791628000
    alerts.store.recipients.return_value = [recipient()]


class TestAlertEmbed:
    def test_says_what_where_and_until_when(self):
        embed = shiny_alerts.alert_embed(spawn(), "Pikachu", seen_shiny=True)
        # Gold: a Shiny's colour on the map, and Poracle's template's.
        assert embed.color.value == 0xF2CD5C
        assert "Pikachu" in embed.title
        assert "https://www.google.com/maps?q=39.7436,-8.8071" in embed.description
        assert "Desaparece às" in embed.description
        assert "já viu este Pokémon shiny" in embed.description

    def test_an_unverified_despawn_is_called_an_estimate(self):
        embed = shiny_alerts.alert_embed(
            spawn(expire_timestamp_verified=0), "Pikachu", True
        )
        assert "estimado" in embed.description

    def test_no_despawn_time_is_said_plainly(self):
        embed = shiny_alerts.alert_embed(spawn(expire_timestamp=None), "Pikachu", True)
        assert "desconhecida" in embed.description

    def test_a_species_never_seen_shiny_is_flagged(self):
        embed = shiny_alerts.alert_embed(spawn(), "Pikachu", seen_shiny=False)
        assert "nunca viu" in embed.description


class TestAlertSpawns:
    async def test_the_first_tick_only_sets_the_watermark(self, alerts):
        alerts.store.recipients.return_value = [recipient()]
        await alerts.tick()
        alerts.store.set_watermark.assert_awaited_once()
        alerts.user.send.assert_not_awaited()

    async def test_nobody_linked_keeps_the_watermark_moving(self, alerts):
        alerts.store.watermark.return_value = 1791628000
        await alerts.tick()
        alerts.store.set_watermark.assert_awaited_once()
        alerts.poliswag.db.get_data_from_database.assert_not_awaited()

    async def test_a_roll_of_zero_is_sent(self, alerts):
        with_spawns(alerts, [spawn(), spawn(OTHER, first_seen_timestamp=1791629100)])
        await alerts.tick()
        alerts.store.record_alert.assert_awaited_once_with(OWNER, ZERO, 25, 0)
        sent = alerts.user.send.call_args.kwargs
        assert "Pikachu" in sent["embed"].title
        assert sent["embed"].title.startswith("✨ ")  # the star is all that marks it
        assert "view" not in sent and sent["embed"].footer.text is None
        alerts.store.set_watermark.assert_awaited_once_with(1791629100)

    async def test_a_spawn_already_alerted_is_not_sent_again(self, alerts):
        with_spawns(alerts, [spawn()])
        alerts.store.record_alert.return_value = False
        await alerts.tick()
        alerts.user.send.assert_not_awaited()

    async def test_a_species_that_is_never_shiny_is_not_sent(self, alerts):
        with_spawns(alerts, [spawn(pokemon_id=931, form_id=2987), spawn(OTHER)])
        alerts.store.shiny_eligible.return_value = {(931, 2986, 0)}
        await alerts.tick()
        alerts.store.record_alert.assert_not_awaited()
        alerts.user.send.assert_not_awaited()

    async def test_tarountula_with_a_zero_roll_and_unknown_availability_is_not_sent(
        self, alerts
    ):
        with_spawns(alerts, [spawn(pokemon_id=917, form_id=3232)])
        await alerts.tick()
        alerts.store.record_alert.assert_not_awaited()
        alerts.user.send.assert_not_awaited()

    async def test_another_form_of_that_species_still_is(self, alerts):
        with_spawns(alerts, [spawn(pokemon_id=931, form_id=2986)])
        alerts.store.shiny_eligible.return_value = {(931, 2986, 0)}
        await alerts.tick()
        alerts.store.record_alert.assert_awaited_once_with(OWNER, ZERO, 931, 2986)

    async def test_no_zero_roll_means_no_dm(self, alerts):
        with_spawns(alerts, [spawn(OTHER)])
        await alerts.tick()
        alerts.store.record_alert.assert_not_awaited()
        alerts.user.send.assert_not_awaited()

    async def test_an_unnamed_pokemon_falls_back_to_its_number(self, alerts):
        with_spawns(alerts, [spawn()], name=None)
        await alerts.tick()
        assert "#25" in alerts.user.send.call_args.kwargs["embed"].title

    async def test_a_malformed_encounter_id_is_skipped(self, alerts):
        with_spawns(alerts, [spawn("not-a-number"), spawn()])
        await alerts.tick()
        alerts.user.send.assert_awaited_once()

    async def test_the_query_leaves_out_lures_and_expiring_spawns(self, alerts):
        with_spawns(alerts, [])
        await alerts.tick()
        (sql,) = [
            c.args[0]
            for c in alerts.poliswag.db.get_data_from_database.call_args_list
            if "first_seen_timestamp" in c.args[0]
        ]
        # As Golbat PR #423: only spawns the scanner has seen itself, never
        # Ditto or anything shown as another species.
        assert "seen_type IN ('wild', 'encounter')" in sql
        assert "nearby" not in sql and "lure" not in sql
        assert "pokemon_id <> 132" in sql and "is_ditto = 0" in sql
        assert "display_pokemon_id" in sql
        watermark, expires_after = (
            alerts.poliswag.db.get_data_from_database.call_args.kwargs["params"]
        )
        # Reads a few minutes back, for spawns that were waiting for their IV.
        assert watermark == 1791628000 - shiny_alerts.LOOK_BACK
        assert isinstance(expires_after, int)


class TestOwnedShinies:
    async def test_owned_shiny_is_neither_recorded_nor_delivered(self, alerts):
        with_spawns(alerts, [spawn()])
        alerts.store.owns_shiny.return_value = True
        await alerts.tick()
        alerts.store.owns_shiny.assert_awaited_once_with(OWNER, 25, 0)
        alerts.store.record_alert.assert_not_awaited()
        alerts.poliswag.poracle.test_pokemon.assert_not_awaited()
        alerts.user.send.assert_not_awaited()

    async def test_missing_shiny_still_gets_delivered(self, alerts):
        with_spawns(alerts, [spawn()])
        await alerts.tick()
        alerts.user.send.assert_awaited_once()

    async def test_each_recipient_uses_their_own_collection(self, alerts):
        with_spawns(alerts, [spawn()])
        alerts.store.recipients.return_value = [recipient(), recipient(discord_id=222)]
        alerts.store.owns_shiny.side_effect = [True, False]
        await alerts.tick()
        alerts.store.record_alert.assert_awaited_once_with(222, ZERO, 25, 0)

    async def test_an_owned_shiny_at_100_is_still_delivered(self, alerts):
        with_spawns(alerts, [spawn(iv=100.0, def_iv=15)])
        alerts.store.owns_shiny.return_value = True
        await alerts.tick()
        alerts.store.record_alert.assert_awaited_once_with(OWNER, ZERO, 25, 0)
        alerts.user.send.assert_awaited_once()

    async def test_but_not_with_that_switch_off(self, alerts):
        with_spawns(alerts, [spawn(iv=100.0, def_iv=15)])
        alerts.store.recipients.return_value = [recipient(notify_hundo=0)]
        alerts.store.owns_shiny.return_value = True
        await alerts.tick()
        alerts.store.record_alert.assert_not_awaited()
        alerts.user.send.assert_not_awaited()

    async def test_todos_delivers_an_owned_shiny_without_reading_the_collection(
        self, alerts
    ):
        with_spawns(alerts, [spawn()])
        alerts.store.recipients.return_value = [recipient(notify_all=1)]
        alerts.store.owns_shiny.return_value = True
        await alerts.tick()
        alerts.store.owns_shiny.assert_not_awaited()
        alerts.user.send.assert_awaited_once()

    async def test_an_owned_shiny_whose_iv_never_came_is_skipped_unrecorded(
        self, alerts
    ):
        import time

        old = int(time.time()) - shiny_alerts.WAIT_FOR_IV - 5
        unknown = spawn(
            iv=None, atk_iv=None, def_iv=None, sta_iv=None, first_seen_timestamp=old
        )
        with_spawns(alerts, [unknown])
        alerts.store.owns_shiny.return_value = True
        await alerts.tick()
        # Not recorded, so an IV that lands inside LOOK_BACK is judged again.
        alerts.store.record_alert.assert_not_awaited()
        alerts.user.send.assert_not_awaited()


HUNDO = {"atk_iv": 15, "def_iv": 15, "sta_iv": 15}
NOT_HUNDO = {"atk_iv": 15, "def_iv": 14, "sta_iv": 15}
UNKNOWN = {"atk_iv": None, "def_iv": None, "sta_iv": None}


@pytest.mark.parametrize(
    "notify_all, notify_hundo, owned, ivs, told",
    [
        # A shiny the player lacks is always told, whatever its IV.
        (0, 1, False, NOT_HUNDO, True),
        (0, 0, False, UNKNOWN, True),
        # Owned, only-missing with the 100% switch on (the default).
        (0, 1, True, HUNDO, True),
        (0, 1, True, NOT_HUNDO, False),
        (0, 1, True, UNKNOWN, False),
        # Owned, only-missing with the switch off.
        (0, 0, True, HUNDO, False),
        # Todos: everything, and the 100% switch changes nothing.
        (1, 0, True, NOT_HUNDO, True),
        (1, 1, True, UNKNOWN, True),
    ],
)
def test_wants_alert(notify_all, notify_hundo, owned, ivs, told):
    who = recipient(notify_all=notify_all, notify_hundo=notify_hundo)
    assert wants_alert(who, spawn(**ivs), owned) is told


def test_a_recipient_row_without_the_columns_gets_the_defaults():
    old_row = {"discord_id": OWNER}
    assert wants_alert(old_row, spawn(**HUNDO), True) is True
    assert wants_alert(old_row, spawn(**NOT_HUNDO), True) is False


class TestConfirmingSettings:
    """The site waits on shiny_applied_revision ("A aguardar pelo Poliswag…")."""

    async def test_a_revision_read_before_the_work_is_confirmed_after_it(self, alerts):
        order = []
        alerts.store.pending_settings.side_effect = lambda: order.append("read") or [
            {"discord_id": 222, "shiny_settings_revision": 4}
        ]
        alerts.channels.tick.side_effect = lambda: order.append("channels")
        alerts.store.confirm_settings.side_effect = lambda *a: order.append("confirm")
        await alerts.tick()
        assert order == ["read", "channels", "confirm"]
        alerts.store.confirm_settings.assert_awaited_once_with(222, 4)

    async def test_a_tick_that_fails_confirms_nothing(self, alerts):
        alerts.store.pending_settings.return_value = [
            {"discord_id": 222, "shiny_settings_revision": 4}
        ]
        alerts.channels.tick.side_effect = RuntimeError("discord down")
        with pytest.raises(RuntimeError):
            await alerts.tick()
        alerts.store.confirm_settings.assert_not_awaited()

    async def test_nobody_waiting_means_no_write(self, alerts):
        await alerts.tick()
        alerts.store.confirm_settings.assert_not_awaited()


class TestAreas:
    """Leiria is east of Config.MARINHA_LON_MAX (-8.9), Marinha Grande west."""

    async def test_a_leiria_player_is_not_sent_a_marinha_spawn(self, alerts):
        with_spawns(alerts, [spawn(lon=-8.93)])
        alerts.store.recipients.return_value = [recipient(marinha=0)]
        await alerts.tick()
        alerts.store.record_alert.assert_not_awaited()
        alerts.user.send.assert_not_awaited()

    async def test_a_leiria_player_still_gets_leiria(self, alerts):
        with_spawns(alerts, [spawn(lon=-8.8071)])
        alerts.store.recipients.return_value = [recipient(marinha=0)]
        await alerts.tick()
        alerts.user.send.assert_awaited_once()

    async def test_a_marinha_player_is_not_sent_a_leiria_spawn(self, alerts):
        with_spawns(alerts, [spawn(lon=-8.8071), spawn(lon=-8.93)])
        alerts.store.recipients.return_value = [recipient(leiria=0)]
        await alerts.tick()
        alerts.user.send.assert_awaited_once()
        assert "-8.93" in alerts.user.send.call_args.kwargs["embed"].description


class TestIvAndName:
    def test_the_alert_leads_with_the_iv(self):
        embed = shiny_alerts.alert_embed(spawn(), "Zorua", True)
        assert "98%" in embed.title and "Zorua" in embed.title
        assert "15/14/15" in embed.description
        assert "CP 812" in embed.description and "nível 27" in embed.description

    def test_an_unknown_iv_is_said_plainly(self):
        embed = shiny_alerts.alert_embed(
            spawn(iv=None, cp=None, level=None), "Zorua", True
        )
        assert "%" not in embed.title
        assert "IV ainda desconhecido" in embed.description

    async def test_a_fresh_sighting_waits_for_the_scanner_to_read_its_iv(self, alerts):
        import time

        with_spawns(
            alerts, [spawn(iv=None, first_seen_timestamp=int(time.time()) - 30)]
        )
        await alerts.tick()
        alerts.store.record_alert.assert_not_awaited()
        alerts.user.send.assert_not_awaited()

    async def test_but_not_for_ever(self, alerts):
        import time

        old = int(time.time()) - shiny_alerts.WAIT_FOR_IV - 5
        with_spawns(alerts, [spawn(iv=None, first_seen_timestamp=old)])
        await alerts.tick()
        alerts.user.send.assert_awaited_once()

    async def test_the_name_falls_back_to_the_species_base_form(self, alerts):
        # Golbat sends Zorua as form 2108 ("normal"); pokemon_name files the
        # normal form under 0. The first alerts went out as "#570".
        with_spawns(alerts, [spawn(pokemon_id=570, form_id=2108)], name="Zorua")
        await alerts.tick()
        sql, params = [
            (c.args[0], c.kwargs["params"])
            for c in alerts.poliswag.db.get_data_from_database.call_args_list
            if "pokemon_name" in c.args[0]
        ][0]
        assert params == (570, 2108) and "form_id IN (%s, 0)" in sql
        assert "Zorua" in alerts.user.send.call_args.kwargs["embed"].title


class TestThroughPoracle:
    """The normal path: Poracle renders and delivers the alert, so it looks
    like every other Poracle alert (map tile, IV, moves, map buttons)."""

    @pytest.fixture
    def poracle(self, alerts):
        alerts.poliswag.poracle.test_pokemon = AsyncMock()
        return alerts.poliswag.poracle.test_pokemon

    async def test_the_spawn_goes_to_poracle_with_the_shiny_template(
        self, alerts, poracle
    ):
        with_spawns(alerts, [spawn()])
        await alerts.tick()
        webhook, target = poracle.call_args.args
        assert target == {
            "id": str(OWNER),
            "name": "faynn",
            "type": "discord:user",
            "language": "en",
            "template": "shiny",
        }
        assert webhook["shiny"] is True
        assert webhook["encounter_id"] == ZERO and webhook["pokemon_id"] == 25
        assert (webhook["latitude"], webhook["longitude"]) == (39.7436, -8.8071)
        assert webhook["disappear_time"] == 1791630000
        assert webhook["disappear_time_verified"] is True
        assert (
            webhook["individual_attack"],
            webhook["individual_defense"],
            webhook["individual_stamina"],
        ) == (15, 14, 15)
        assert webhook["cp"] == 812 and webhook["pokemon_level"] == 27
        assert webhook["spawnpoint_id"] == "d1f3a2b1c5"
        # Poracle sent it: the bot's own embed stays in the drawer.
        alerts.user.send.assert_not_awaited()

    async def test_it_is_still_recorded_first_and_sent_once(self, alerts, poracle):
        with_spawns(alerts, [spawn()])
        await alerts.tick()
        alerts.store.record_alert.assert_awaited_once_with(OWNER, ZERO, 25, 0)
        alerts.store.record_alert.return_value = False
        await alerts.tick()
        poracle.assert_awaited_once()

    async def test_poracle_down_falls_back_to_the_bots_own_embed(self, alerts):
        with_spawns(alerts, [spawn()])
        await alerts.tick()
        assert "Pikachu" in alerts.user.send.call_args.kwargs["embed"].title

    def test_a_stored_pvp_blob_is_passed_on_as_json(self):
        webhook = shiny_alerts.poracle_webhook(spawn(pvp='{"great": [{"rank": 3}]}'))
        assert webhook["pvp"] == {"great": [{"rank": 3}]}
        assert shiny_alerts.poracle_webhook(spawn(pvp="not json"))["pvp"] is None

    def test_the_form_the_database_hands_over_as_a_decimal_still_makes_json(self):
        import json
        from decimal import Decimal

        webhook = shiny_alerts.poracle_webhook(spawn(form_id=Decimal("2108")))
        assert json.loads(json.dumps(webhook))["form"] == 2108


class TestOwnChannel:
    """A player with a channel of their own gets alerts there, not by DM."""

    async def test_poracle_is_pointed_at_the_channel(self, alerts):
        alerts.poliswag.poracle.test_pokemon = AsyncMock()
        with_spawns(alerts, [spawn()])
        alerts.store.recipients.return_value = [recipient(channel_id=4242)]
        await alerts.tick()
        _, target = alerts.poliswag.poracle.test_pokemon.call_args.args
        assert target["id"] == "4242" and target["type"] == "discord:channel"
        assert target["template"] == "shiny"

    async def test_the_fallback_embed_goes_to_the_channel_too(self, alerts):
        channel = MagicMock()
        channel.send = AsyncMock()
        alerts.poliswag.get_channel.return_value = channel
        with_spawns(alerts, [spawn()])
        alerts.store.recipients.return_value = [recipient(channel_id=4242)]
        await alerts.tick()
        alerts.poliswag.get_channel.assert_called_with(4242)
        channel.send.assert_awaited_once()
        alerts.user.send.assert_not_awaited()

    async def test_a_channel_that_is_gone_falls_back_to_the_dm(self, alerts):
        alerts.poliswag.get_channel.return_value = None
        with_spawns(alerts, [spawn()])
        alerts.store.recipients.return_value = [recipient(channel_id=4242)]
        await alerts.tick()
        alerts.user.send.assert_awaited_once()

    async def test_but_never_to_the_dm_of_someone_who_only_has_a_channel(self, alerts):
        # Only the pilot's users are ever messaged directly.
        alerts.poliswag.get_channel.return_value = None
        with_spawns(alerts, [spawn()])
        alerts.store.recipients.return_value = [
            recipient(channel_id=4242, discord_id=555)
        ]
        await alerts.tick()
        alerts.user.send.assert_not_awaited()
        alerts.poliswag.get_user.assert_not_called()


async def test_shiny_ordinary_form_does_not_enable_an_unknown_costume(alerts):
    with_spawns(alerts, [spawn(costume=7)])
    await alerts.tick()
    alerts.user.send.assert_not_awaited()


async def test_exact_confirmed_costume_gets_its_alert(alerts):
    with_spawns(alerts, [spawn(costume=7)])
    alerts.store.shiny_eligible.return_value = {(25, 0, 7)}
    await alerts.tick()
    alerts.user.send.assert_awaited_once()


class TestDiscordNotifications:
    @pytest.mark.parametrize(
        "notify_all,notify_hundo,owned", [(1, 1, False), (1, 1, True), (0, 1, True)]
    )
    def test_disabled_overrides_all_filters(self, notify_all, notify_hundo, owned):
        who = recipient(notify_all=notify_all, notify_hundo=notify_hundo)
        who["shiny_notify_discord"] = 0
        assert wants_alert(who, spawn(atk_iv=15, def_iv=15, sta_iv=15), owned) is False

    async def test_a_switch_off_during_a_tick_stops_delivery(self, alerts):
        with_spawns(alerts, [spawn()])
        alerts.store.notifications_enabled.return_value = False
        await alerts.tick()
        alerts.store.record_alert.assert_not_awaited()
        alerts.poliswag.poracle.test_pokemon.assert_not_awaited()
        alerts.user.send.assert_not_awaited()

    async def test_disabled_recipient_never_reads_collection_or_sends(self, alerts):
        who = {**recipient(), "shiny_notify_discord": 0}
        await alerts._alert(who, spawn())
        alerts.store.owns_shiny.assert_not_awaited()
        alerts.store.record_alert.assert_not_awaited()
        alerts.poliswag.poracle.test_pokemon.assert_not_awaited()
        alerts.user.send.assert_not_awaited()

    async def test_confirms_once_in_the_channel_and_keeps_the_map(self, alerts):
        channel = MagicMock(send=AsyncMock())
        alerts.poliswag.get_channel.return_value = channel
        alerts.store.notification_changes.return_value = [
            {
                "discord_id": OWNER,
                "shiny_map": 1,
                "shiny_notify_discord": 0,
                "channel_id": 99,
                "channel_archived": 0,
            }
        ]
        alerts.store.claim_notification_change.side_effect = [True, False]
        await alerts.tick()
        await alerts.tick()
        channel.send.assert_awaited_once()
        text = channel.send.call_args.kwargs["content"]
        assert "foram desligadas" in text and "mapa de shinies continua ativo" in text
        assert channel.send.call_args.kwargs["allowed_mentions"].everyone is False
        alerts.user.send.assert_not_awaited()

    @pytest.mark.parametrize("channel_id,archived", [(None, 0), (99, 1)])
    async def test_confirms_by_dm_without_an_accessible_channel(
        self, alerts, channel_id, archived
    ):
        alerts.store.notification_changes.return_value = [
            {
                "discord_id": OWNER,
                "shiny_map": 1,
                "shiny_notify_discord": 0,
                "channel_id": channel_id,
                "channel_archived": archived,
            }
        ]
        await alerts.tick()
        alerts.user.send.assert_awaited_once()
        assert "foram desligadas" in alerts.user.send.call_args.kwargs["content"]

    async def test_an_inaccessible_channel_falls_back_to_dm(self, alerts):
        channel = MagicMock(
            send=AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
        )
        alerts.poliswag.get_channel.return_value = channel
        alerts.store.notification_changes.return_value = [
            {
                "discord_id": OWNER,
                "shiny_map": 1,
                "shiny_notify_discord": 0,
                "channel_id": 99,
                "channel_archived": 0,
            }
        ]
        await alerts.tick()
        alerts.user.send.assert_awaited_once()

    async def test_enabling_is_acknowledged_without_a_disable_message(self, alerts):
        alerts.store.notification_changes.return_value = [
            {"discord_id": OWNER, "shiny_notify_discord": 1}
        ]
        await alerts.tick()
        alerts.store.claim_notification_change.assert_awaited_once_with(OWNER, True)
        alerts.user.send.assert_not_awaited()

    async def test_map_off_confirmation_does_not_claim_the_map_is_on(self, alerts):
        alerts.store.notification_changes.return_value = [
            {
                "discord_id": OWNER,
                "shiny_map": 0,
                "shiny_notify_discord": 0,
                "channel_id": None,
            }
        ]
        await alerts.tick()
        assert (
            "mapa de shinies continua ativo"
            not in alerts.user.send.call_args.kwargs["content"]
        )


@pytest.mark.parametrize("shiny", [True, False])
def test_webhook_and_fallback_art_follow_prediction(shiny):
    data = spawn(predicted_shiny=shiny, predicted_background=True)
    assert shiny_alerts.poracle_webhook(data)["shiny"] is shiny
    embed = shiny_alerts.alert_embed(data, "Pikachu", True)
    assert embed.thumbnail.url.endswith("/25_g2_s.png" if shiny else "/25_g2.png")
