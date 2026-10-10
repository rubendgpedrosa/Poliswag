"""Shiny alerts: the link announcements, the spawn alerts, the DM itself.
The database is faked; the roll is the real one, fed vectors that the
reference JavaScript rolls to 0 (see tests/modules/test_shiny_roll.py)."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

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
