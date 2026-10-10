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
        assert "**Rita99**" in link_message(row, alerts_on=True)

    def test_linked_but_outside_the_pilot_says_alerts_come_later(self):
        row = {"status": "linked", "trainer_name": "Rita99", "fail_reason": None}
        assert "ainda estão em teste" in link_message(row, alerts_on=False)
        assert "ainda estão em teste" not in link_message(row, alerts_on=True)

    def test_failed_gives_the_reason_and_the_way_back(self):
        row = {
            "status": "failed",
            "trainer_name": None,
            "fail_reason": "código não encontrado",
        }
        text = link_message(row, alerts_on=False)
        assert "código não encontrado" in text and "!shiny" in text


class TestAnnounceLinks:
    async def test_each_finished_link_is_told_once(self, alerts):
        alerts.store.unannounced.return_value = [
            {
                "discord_id": 5,
                "status": "linked",
                "trainer_name": "A",
                "fail_reason": None,
            }
        ]
        await alerts.tick()
        alerts.user.send.assert_awaited_once()
        alerts.store.mark_announced.assert_awaited_once_with(5)

    async def test_closed_dms_are_still_marked(self, alerts):
        alerts.store.unannounced.return_value = [
            {
                "discord_id": 5,
                "status": "failed",
                "trainer_name": None,
                "fail_reason": "x",
            }
        ]
        alerts.user.send.side_effect = discord.Forbidden(MagicMock(status=403), "no")
        await alerts.tick()
        alerts.store.mark_announced.assert_awaited_once_with(5)


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
    }
    row.update(over)
    return row


def with_spawns(alerts, spawns, name="Pikachu", seen_shiny=True):
    """Route the three reads by what they select."""

    async def read(sql, params=None, **_):
        if "pokemon_shiny_stats" in sql:
            return [{"seen": 1}] if seen_shiny else []
        if "pokemon_name" in sql:
            return [{"name": name, "form_name": None}] if name else []
        return spawns

    alerts.poliswag.db.get_data_from_database = AsyncMock(side_effect=read)
    alerts.store.watermark.return_value = 1791628000
    alerts.store.recipients.return_value = [{"discord_id": OWNER, "player_id": PLAYER}]


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

    def test_a_nearby_sighting_warns_about_the_location(self):
        embed = shiny_alerts.alert_embed(
            spawn(seen_type="nearby_stop"), "Pikachu", True
        )
        assert "aproximada" in embed.description


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
        alerts.store.recipients.return_value = [
            {"discord_id": OWNER, "player_id": PLAYER}
        ]
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
        assert "'wild', 'encounter', 'nearby_stop'" in sql
        assert "lure" not in sql
        watermark, expires_after = (
            alerts.poliswag.db.get_data_from_database.call_args.kwargs["params"]
        )
        assert watermark == 1791628000 and isinstance(expires_after, int)
