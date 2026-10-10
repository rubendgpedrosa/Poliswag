"""Shiny alerts: the link announcements, the spawn alerts, the DM itself.
The database is faked; the roll is the real one, fed vectors that the
reference JavaScript rolls to 0 (see tests/modules/test_shiny_roll.py)."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules import shiny_alerts
from modules.config import Config
from modules.shiny_alerts import ShinyAlerts, link_message

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
    a = ShinyAlerts(bot)
    a.store = MagicMock(
        unannounced=AsyncMock(return_value=[]),
        mark_announced=AsyncMock(),
        recipients=AsyncMock(return_value=[]),
        watermark=AsyncMock(return_value=None),
        set_watermark=AsyncMock(),
        record_alert=AsyncMock(return_value=True),
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
    }
    row.update(over)
    return row


def recipient(leiria=1, marinha=1):
    return {
        "discord_id": OWNER,
        "player_id": PLAYER,
        "alert_leiria": leiria,
        "alert_marinha": marinha,
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


class TestVerdictView:
    async def test_the_buttons_carry_the_encounter_id(self):
        view = shiny_alerts.verdict_view(ZERO)
        assert [b.custom_id for b in view.children] == [
            f"shiny:s:{ZERO}",
            f"shiny:n:{ZERO}",
        ]
        assert view.timeout is None


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

    async def test_a_roll_of_zero_is_sent_with_buttons(self, alerts):
        with_spawns(alerts, [spawn(), spawn(OTHER, first_seen_timestamp=1791629100)])
        await alerts.tick()
        alerts.store.record_alert.assert_awaited_once_with(OWNER, ZERO, 25, 0)
        sent = alerts.user.send.call_args.kwargs
        assert "Pikachu" in sent["embed"].title
        assert sent["view"].children[0].custom_id == f"shiny:s:{ZERO}"
        alerts.store.set_watermark.assert_awaited_once_with(1791629100)

    async def test_a_spawn_already_alerted_is_not_sent_again(self, alerts):
        with_spawns(alerts, [spawn()])
        alerts.store.record_alert.return_value = False
        await alerts.tick()
        alerts.user.send.assert_not_awaited()

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
