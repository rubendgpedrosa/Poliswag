"""Tests for modules.role_manager.RoleManager.

Covers toggle_role / remove_team_roles / response_user_role_selection /
add_button_event using mocked discord.Member/Role shapes.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.role_manager import RoleManager


@pytest.fixture
def rm():
    return RoleManager()


class TestRemoveTeamRoles:
    async def test_no_op_for_non_team_role(self, rm):
        user = MagicMock()
        await rm.remove_team_roles("AlertasLeiria", user)
        assert not user.mock_calls or all(
            "remove_roles" not in str(c) for c in user.mock_calls
        )

    async def test_removes_other_two_team_roles(self, rm, mocker):
        mystic = MagicMock(name="mystic_role")
        valor = MagicMock(name="valor_role")

        def get_side_effect(_iterable, **kwargs):
            name = kwargs.get("name")
            return {"Mystic": mystic, "Valor": valor}.get(name)

        mocker.patch(
            "modules.role_manager.discord.utils.get", side_effect=get_side_effect
        )

        user = MagicMock()
        user.roles = [mystic, valor]
        user.remove_roles = AsyncMock()

        await rm.remove_team_roles("Instinct", user)
        assert user.remove_roles.call_count == 2
        called_roles = [c.args[0] for c in user.remove_roles.call_args_list]
        assert mystic in called_roles
        assert valor in called_roles

    async def test_skips_removal_when_user_does_not_have_role(self, rm, mocker):
        mystic = MagicMock()
        valor = MagicMock()

        def get_side_effect(_iterable, **kwargs):
            return {"Mystic": mystic, "Valor": valor}.get(kwargs.get("name"))

        mocker.patch(
            "modules.role_manager.discord.utils.get", side_effect=get_side_effect
        )

        user = MagicMock()
        user.roles = []
        user.remove_roles = AsyncMock()

        await rm.remove_team_roles("Instinct", user)
        user.remove_roles.assert_not_called()


class TestToggleRole:
    async def test_removes_notification_role_if_user_has_it(self, rm, mocker):
        alerta = MagicMock(name="alerta_role")
        mocker.patch("modules.role_manager.discord.utils.get", return_value=alerta)

        user = MagicMock()
        user.roles = [alerta]
        user.remove_roles = AsyncMock()
        user.add_roles = AsyncMock()

        await rm.toggle_role("AlertasLeiria", user)
        user.remove_roles.assert_called_once_with(alerta, atomic=True)
        user.add_roles.assert_not_called()

    async def test_does_nothing_when_role_not_found(self, rm, mocker):
        mocker.patch("modules.role_manager.discord.utils.get", return_value=None)
        user = MagicMock()
        user.remove_roles = AsyncMock()
        user.add_roles = AsyncMock()
        await rm.toggle_role("AlertasLeiria", user)
        user.remove_roles.assert_not_called()
        user.add_roles.assert_not_called()

    async def test_adds_team_role_and_removes_other_teams(self, rm, mocker):
        instinct = MagicMock(name="instinct")
        mystic = MagicMock(name="mystic")
        valor = MagicMock(name="valor")

        def get_side_effect(_iterable, **kwargs):
            return {"Instinct": instinct, "Mystic": mystic, "Valor": valor}.get(
                kwargs.get("name")
            )

        mocker.patch(
            "modules.role_manager.discord.utils.get", side_effect=get_side_effect
        )
        user = MagicMock()
        user.roles = [mystic, valor, MagicMock(), MagicMock()]
        user.remove_roles = AsyncMock()
        user.add_roles = AsyncMock()

        await rm.toggle_role("Instinct", user)
        removed = [c.args[0] for c in user.remove_roles.call_args_list]
        assert mystic in removed and valor in removed
        user.add_roles.assert_any_call(instinct, atomic=True)


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

    async def test_restart_delegates_to_response(self, rm, mocker):
        mocker.patch.object(rm, "response_user_role_selection", new=AsyncMock())
        interaction = MagicMock()
        await rm.restart_response_user_role_selection(interaction)
        rm.response_user_role_selection.assert_called_once_with(interaction)


class TestAddButtonEvent:
    async def test_assigns_callback(self, rm):
        button = MagicMock()
        await rm.add_button_event(button)
        assert button.callback == rm.response_user_role_selection


class TestToggleRoleFirstTimeBranch:
    async def test_first_time_user_gets_all_notification_roles(self, rm, mocker):
        instinct = MagicMock(name="instinct")
        everyone = MagicMock(name="everyone")
        notif_roles = {
            "AlertasLeiria": MagicMock(),
            "AlertasMarinha": MagicMock(),
            "AlertasRaids": MagicMock(),
            "AlertasPvP": MagicMock(),
            "Remote": MagicMock(),
        }

        def get_side_effect(_iterable, **kwargs):
            name = kwargs.get("name")
            if name == "Instinct":
                return instinct
            return notif_roles.get(name)

        mocker.patch(
            "modules.role_manager.discord.utils.get", side_effect=get_side_effect
        )
        user = MagicMock()
        user.roles = [everyone]
        user.remove_roles = AsyncMock()
        user.add_roles = AsyncMock()
        await rm.toggle_role("Instinct", user)
        assert user.add_roles.await_count == 6
