"""The three audiences (members, mods, MY_ID) and the commands they gate."""

from unittest.mock import MagicMock

import pytest

from cogs.accounts import Accounts
from cogs.quests import Quests
from cogs.scheduled import Scheduled
from modules.config import Config
from modules.permissions import is_mod, is_owner


def _ctx(author_id, admin_ids=("111",)):
    ctx = MagicMock()
    ctx.author.id = author_id
    ctx.bot.ADMIN_USERS_IDS = list(admin_ids)
    return ctx


@pytest.fixture(autouse=True)
def _my_id(mocker):
    mocker.patch.object(Config, "MY_ID", 999)


class TestPredicates:
    def test_owner_is_only_my_id(self):
        assert is_owner(_ctx(999)) is True
        assert is_owner(_ctx("999")) is True
        assert is_owner(_ctx("111")) is False

    def test_mods_are_admin_ids_plus_owner(self):
        assert is_mod(_ctx("111")) is True
        assert is_mod(_ctx(999)) is True
        assert is_mod(_ctx(555)) is False

    def test_no_admin_list_means_only_owner_is_mod(self):
        ctx = _ctx(555)
        ctx.bot.ADMIN_USERS_IDS = None
        assert is_mod(ctx) is False


class TestGatedCommands:
    def test_accounts_is_owner_only_not_mods(self):
        cog = Accounts(MagicMock())
        assert cog.cog_check(_ctx(999)) is True
        assert cog.cog_check(_ctx("111")) is False
        assert cog.cog_check(_ctx(555)) is False

    @pytest.mark.parametrize(
        "cog_cls, name",
        [
            (Quests, "scan"),
            (Quests, "exportquests"),
            (Scheduled, "weeklydigest"),
            (Scheduled, "testevent"),
        ],
    )
    def test_body_gated_commands_also_carry_the_mods_check(self, cog_cls, name):
        command = next(c for c in cog_cls.__cog_commands__ if c.name == name)
        assert is_mod in command.checks

    def test_member_commands_carry_no_gate(self):
        command = next(c for c in Quests.__cog_commands__ if c.name == "questleiria")
        assert is_mod not in command.checks and is_owner not in command.checks
