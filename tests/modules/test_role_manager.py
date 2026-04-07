"""Tests for modules.role_manager.RoleManager.

Focuses on build_response_message (pure-logic mapping) plus toggle_role /
remove_team_roles / strip_user_roles using mocked discord.Member/Role shapes.
"""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.role_manager import RoleManager


@pytest.fixture
def rm():
    return RoleManager()


class TestBuildResponseMessage:
    @pytest.mark.parametrize(
        "role",
        ["AlertasLeiria", "AlertasMarinha", "AlertasPvP", "AlertasRaids", "Remote"],
    )
    def test_notification_roles_return_notificacoes(self, rm, role):
        assert rm.build_response_message(role) == f"Notificações de {role.title()}"

    @pytest.mark.parametrize("role", ["Mystic", "Valor", "Instinct"])
    def test_team_roles_return_equipa(self, rm, role):
        assert rm.build_response_message(role) == "Equipa atribuida"

    def test_case_insensitive_matching_on_notifications(self, rm):
        assert (
            rm.build_response_message("ALERTASLEIRIA")
            == "Notificações de Alertasleiria"
        )

    def test_unknown_role_returns_none(self, rm):
        assert rm.build_response_message("RandomRole") is None


class TestRemoveTeamRoles:
    async def test_no_op_for_non_team_role(self, rm):
        user = MagicMock()
        await rm.remove_team_roles("AlertasLeiria", user)
        # user.remove_roles must not be called.
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
        user.roles = []  # has neither
        user.remove_roles = AsyncMock()

        await rm.remove_team_roles("Instinct", user)
        user.remove_roles.assert_not_called()


class TestToggleRole:
    async def test_removes_notification_role_if_user_has_it(self, rm, mocker):
        alerta = MagicMock(name="alerta_role")
        mocker.patch("modules.role_manager.discord.utils.get", return_value=alerta)

        user = MagicMock()
        user.roles = [alerta]  # > 1 role — won't trigger bulk notif add
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
        # User already has the other two team roles + some baseline roles
        # (len > 1 so the bulk-notif-add branch does NOT trigger).
        user.roles = [mystic, valor, MagicMock(), MagicMock()]
        user.remove_roles = AsyncMock()
        user.add_roles = AsyncMock()

        await rm.toggle_role("Instinct", user)
        # Both other teams removed.
        removed = [c.args[0] for c in user.remove_roles.call_args_list]
        assert mystic in removed and valor in removed
        # Instinct added last.
        user.add_roles.assert_any_call(instinct, atomic=True)


class TestStripUserRoles:
    async def test_removes_every_role_on_member(self, rm, mocker):
        member = MagicMock(spec=discord.Member)
        r1, r2 = MagicMock(), MagicMock()
        member.roles = [r1, r2]
        member.remove_roles = AsyncMock()
        await rm.strip_user_roles(member)
        assert member.remove_roles.call_count == 2

    async def test_non_member_is_ignored(self, rm, capsys):
        # A bare object is not a discord.Member → hits the else branch.
        await rm.strip_user_roles(object())
        captured = capsys.readouterr()
        assert "not a member" in captured.out

    async def test_not_found_is_swallowed(self, rm, capsys):
        member = MagicMock(spec=discord.Member)
        role = MagicMock()
        member.roles = [role]

        async def raise_not_found(*_args, **_kwargs):
            raise discord.NotFound(MagicMock(status=404, reason=""), "gone")

        member.remove_roles = raise_not_found
        await rm.strip_user_roles(member)
        captured = capsys.readouterr()
        assert "not found" in captured.out

    async def test_forbidden_is_swallowed(self, rm, capsys):
        member = MagicMock(spec=discord.Member)
        role = MagicMock()
        member.roles = [role]

        async def raise_forbidden(*_args, **_kwargs):
            raise discord.Forbidden(MagicMock(status=403, reason=""), "no perm")

        member.remove_roles = raise_forbidden
        await rm.strip_user_roles(member)
        captured = capsys.readouterr()
        assert "permission" in captured.out


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


class TestPrepareViewRolesLocation:
    async def test_sends_embed_with_seven_buttons(self, rm):
        channel = MagicMock()
        channel.send = AsyncMock()
        await rm.prepare_view_roles_location(channel)
        channel.send.assert_awaited_once()
        kwargs = channel.send.call_args.kwargs
        embed = kwargs["embed"]
        view = kwargs["view"]
        assert "NOTIFICAÇÕES" in embed.title
        # Seven buttons: Leiria, Marinha, RaidsLeiria, RaidsMarinha, Remote, PvP, Raids.
        assert len(view.children) == 7
        custom_ids = {child.custom_id for child in view.children}
        assert custom_ids == {
            "AlertasLeiria",
            "AlertasMarinha",
            "Leiria",
            "Marinha",
            "Remote",
            "AlertasPvP",
            "AlertasRaids",
        }


class TestPrepareViewRolesTeams:
    async def test_sends_embed_with_three_team_buttons(self, rm):
        channel = MagicMock()
        channel.send = AsyncMock()
        await rm.prepare_view_roles_teams(channel)
        channel.send.assert_awaited_once()
        kwargs = channel.send.call_args.kwargs
        embed = kwargs["embed"]
        view = kwargs["view"]
        assert "EQUIPA" in embed.title
        assert len(view.children) == 3
        custom_ids = {child.custom_id for child in view.children}
        assert custom_ids == {"Instinct", "Mystic", "Valor"}


class TestBuildRulesMessage:
    async def test_sends_rules_embed_and_team_selector(self, rm, mocker):
        # Patch prepare_view_roles_teams to observe delegation without
        # re-asserting its details.
        prepare = mocker.patch.object(rm, "prepare_view_roles_teams", new=AsyncMock())
        message = MagicMock()
        message.channel.send = AsyncMock()
        await rm.build_rules_message(message)
        # Rules embed was sent first.
        message.channel.send.assert_awaited_once()
        embed = message.channel.send.call_args.kwargs["embed"]
        assert "REGRAS" in embed.title
        # Delegation to team selector followed.
        prepare.assert_awaited_once_with(message.channel)


class TestToggleRoleFirstTimeBranch:
    async def test_first_time_user_gets_all_notification_roles(self, rm, mocker):
        # User has only the @everyone baseline role (len == 1) → bulk-add branch.
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
        user.roles = [everyone]  # len == 1
        user.remove_roles = AsyncMock()
        user.add_roles = AsyncMock()
        await rm.toggle_role("Instinct", user)
        # All 5 notif roles + instinct itself = 6 add_roles calls.
        assert user.add_roles.await_count == 6
