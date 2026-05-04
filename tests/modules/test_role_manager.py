"""Tests for modules.role_manager.RoleManager."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.role_manager import RoleManager, _NOTIF_ROLES


@pytest.fixture
def rm():
    return RoleManager()


def _make_role(name):
    r = MagicMock()
    r.name = name
    return r


def _guild_get(roles_by_name):
    """Return a discord.utils.get side-effect that resolves names from a dict."""

    def _get(_iterable, **kwargs):
        return roles_by_name.get(kwargs.get("name"))

    return _get


class TestRemoveTeamRoles:
    async def test_no_op_for_non_team_role(self, rm):
        user = MagicMock()
        user.remove_roles = AsyncMock()
        await rm.remove_team_roles("AlertasLeiria", user)
        user.remove_roles.assert_not_called()

    async def test_removes_other_two_team_roles(self, rm, mocker):
        mystic = _make_role("Mystic")
        valor = _make_role("Valor")
        mocker.patch(
            "modules.role_manager.discord.utils.get",
            side_effect=_guild_get({"Mystic": mystic, "Valor": valor}),
        )
        user = MagicMock()
        user.roles = [mystic, valor]
        user.remove_roles = AsyncMock()

        await rm.remove_team_roles("Instinct", user)

        assert user.remove_roles.call_count == 2
        removed = [c.args[0] for c in user.remove_roles.call_args_list]
        assert mystic in removed and valor in removed

    async def test_skips_roles_user_does_not_have(self, rm, mocker):
        mystic = _make_role("Mystic")
        valor = _make_role("Valor")
        mocker.patch(
            "modules.role_manager.discord.utils.get",
            side_effect=_guild_get({"Mystic": mystic, "Valor": valor}),
        )
        user = MagicMock()
        user.roles = []
        user.remove_roles = AsyncMock()

        await rm.remove_team_roles("Instinct", user)
        user.remove_roles.assert_not_called()


class TestToggleRole:
    async def test_removes_notif_role_when_user_already_has_it(self, rm, mocker):
        alerta = _make_role("AlertasLeiria")
        mocker.patch("modules.role_manager.discord.utils.get", return_value=alerta)
        user = MagicMock()
        user.roles = [alerta]
        user.remove_roles = AsyncMock()
        user.add_roles = AsyncMock()

        await rm.toggle_role("AlertasLeiria", user)

        user.remove_roles.assert_called_once_with(alerta, atomic=True)
        user.add_roles.assert_not_called()

    async def test_does_nothing_when_role_not_found_in_guild(self, rm, mocker):
        mocker.patch("modules.role_manager.discord.utils.get", return_value=None)
        user = MagicMock()
        user.remove_roles = AsyncMock()
        user.add_roles = AsyncMock()

        await rm.toggle_role("AlertasLeiria", user)

        user.remove_roles.assert_not_called()
        user.add_roles.assert_not_called()

    async def test_adds_team_role_and_removes_other_teams(self, rm, mocker):
        instinct = _make_role("Instinct")
        mystic = _make_role("Mystic")
        valor = _make_role("Valor")
        mocker.patch(
            "modules.role_manager.discord.utils.get",
            side_effect=_guild_get(
                {"Instinct": instinct, "Mystic": mystic, "Valor": valor}
            ),
        )
        user = MagicMock()
        user.roles = [mystic, valor, MagicMock(), MagicMock()]
        user.remove_roles = AsyncMock()
        user.add_roles = AsyncMock()

        await rm.toggle_role("Instinct", user)

        removed = [c.args[0] for c in user.remove_roles.call_args_list]
        assert mystic in removed and valor in removed
        user.add_roles.assert_any_call(instinct, atomic=True)

    async def test_team_role_not_removed_when_user_already_has_it(self, rm, mocker):
        # Clicking a team role the user already owns is a no-op — teams are sticky.
        instinct = _make_role("Instinct")
        mocker.patch("modules.role_manager.discord.utils.get", return_value=instinct)
        user = MagicMock()
        user.roles = [instinct]
        user.remove_roles = AsyncMock()
        user.add_roles = AsyncMock()

        await rm.toggle_role("Instinct", user)

        user.remove_roles.assert_not_called()
        user.add_roles.assert_not_called()


class TestAddDefaultNotifRoles:
    async def test_grants_all_notif_roles_skipping_missing(self, rm, mocker):
        roles_by_name = {name: _make_role(name) for name in _NOTIF_ROLES}
        # Simulate one role missing from the guild.
        del roles_by_name["Remote"]
        mocker.patch(
            "modules.role_manager.discord.utils.get",
            side_effect=_guild_get(roles_by_name),
        )
        user = MagicMock()
        user.add_roles = AsyncMock()

        await rm._add_default_notif_roles(user)

        assert user.add_roles.await_count == len(_NOTIF_ROLES) - 1
        granted = [c.args[0] for c in user.add_roles.call_args_list]
        assert roles_by_name["AlertasLeiria"] in granted
        assert roles_by_name["AlertasMarinha"] in granted


class TestFirstTimeUserFlow:
    async def test_new_user_gets_all_notif_roles_then_requested_role(self, rm, mocker):
        instinct = _make_role("Instinct")
        notif_roles = {name: _make_role(name) for name in _NOTIF_ROLES}
        everyone = MagicMock()

        mocker.patch(
            "modules.role_manager.discord.utils.get",
            side_effect=_guild_get({"Instinct": instinct, **notif_roles}),
        )
        user = MagicMock()
        user.roles = [everyone]  # only @everyone → first-time user
        user.remove_roles = AsyncMock()
        user.add_roles = AsyncMock()

        await rm.toggle_role("Instinct", user)

        # 5 notif roles + 1 team role = 6 add_roles calls
        assert user.add_roles.await_count == len(_NOTIF_ROLES) + 1

    async def test_existing_user_does_not_get_notif_roles_again(self, rm, mocker):
        instinct = _make_role("Instinct")
        some_other_role = MagicMock()
        mocker.patch("modules.role_manager.discord.utils.get", return_value=instinct)
        user = MagicMock()
        # Two roles means not a first-time user.
        user.roles = [some_other_role, MagicMock()]
        user.remove_roles = AsyncMock()
        user.add_roles = AsyncMock()

        await rm.toggle_role("Instinct", user)

        # Only the team role itself should be added, no notif bulk-add.
        assert user.add_roles.await_count == 1


class TestResponseUserRoleSelection:
    async def test_delegates_to_toggle_role_and_defers(self, rm, mocker):
        interaction = MagicMock()
        interaction.data = {"custom_id": "AlertasLeiria"}
        interaction.user = MagicMock()
        interaction.response.defer = AsyncMock()
        mocker.patch.object(rm, "toggle_role", new=AsyncMock())

        await rm.response_user_role_selection(interaction)

        rm.toggle_role.assert_called_once_with("AlertasLeiria", interaction.user)
        interaction.response.defer.assert_called_once()


class TestAddButtonEvent:
    async def test_assigns_callback(self, rm):
        button = MagicMock()
        await rm.add_button_event(button)
        assert button.callback == rm.response_user_role_selection
