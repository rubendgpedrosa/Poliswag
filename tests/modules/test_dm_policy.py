"""Tests for modules.dm_policy.

A DM is not the server. Every command was written for a channel, so the
public ones lean on the channel's audience and the admin ones check an
author id — which a DM satisfies just as well as the mod channel does.
These pin down who Poliswag still answers once the channel is gone.
"""

from unittest.mock import MagicMock, patch

import pytest

from modules.dm_policy import may_run_in_dm

MY_ID = 98846248865398784
ADMIN_ID = 42
STRANGER_ID = 777


@pytest.fixture(autouse=True)
def _my_id():
    with patch("modules.dm_policy.Config") as config:
        config.MY_ID = MY_ID
        yield config


def make_ctx(author_id=STRANGER_ID, guild=False, cog="Quests", invoked="questleiria"):
    ctx = MagicMock()
    ctx.guild = MagicMock() if guild else None
    ctx.author.id = author_id
    ctx.author.__str__ = lambda self: "alguem#0001"
    ctx.invoked_with = invoked
    ctx.cog = MagicMock() if cog else None
    if cog:
        ctx.cog.qualified_name = cog
    ctx.bot.ADMIN_USERS_IDS = [str(ADMIN_ID), str(MY_ID)]
    ctx.bot.utility.log_to_file = MagicMock()
    return ctx


class TestInAChannel:
    def test_a_stranger_in_a_guild_is_left_alone(self):
        """The policy only has an opinion about DMs; a channel command
        still answers to whatever gate the command itself carries."""
        assert may_run_in_dm(make_ctx(guild=True)) is True


class TestInADm:
    def test_my_id_is_answered(self):
        assert may_run_in_dm(make_ctx(author_id=MY_ID)) is True

    def test_an_admin_is_answered(self):
        assert may_run_in_dm(make_ctx(author_id=ADMIN_ID)) is True

    def test_a_stranger_is_refused(self):
        assert may_run_in_dm(make_ctx()) is False

    def test_an_admin_id_matches_as_a_string(self):
        """ADMIN_USERS_IDS holds strings; ctx.author.id is an int."""
        ctx = make_ctx(author_id=ADMIN_ID)
        ctx.bot.ADMIN_USERS_IDS = ["42"]
        assert may_run_in_dm(ctx) is True


class TestPokedexIsExempt:
    """!trades and !resumo exist to be used in a DM — the help text says
    so — and they carry their own server-membership check."""

    def test_a_stranger_reaches_the_trades_cog(self):
        assert may_run_in_dm(make_ctx(cog="Pokedex", invoked="pokedex")) is True

    def test_only_the_trades_cog_is_exempt(self):
        assert may_run_in_dm(make_ctx(cog="Accounts", invoked="accounts")) is False

    def test_a_command_outside_any_cog_is_not_exempt(self):
        assert may_run_in_dm(make_ctx(cog=None, invoked="help")) is False


class TestLogging:
    def test_a_refusal_names_the_author_and_the_command(self):
        ctx = make_ctx()
        may_run_in_dm(ctx)
        logged = ctx.bot.utility.log_to_file.call_args[0][0]
        assert "[DM]" in logged
        assert str(STRANGER_ID) in logged
        assert "questleiria" in logged

    def test_an_answered_dm_is_not_logged(self):
        """Only the refusals are worth a line; MY_ID's own DMs would
        drown them out."""
        ctx = make_ctx(author_id=MY_ID)
        may_run_in_dm(ctx)
        ctx.bot.utility.log_to_file.assert_not_called()

    def test_a_channel_command_is_not_logged(self):
        ctx = make_ctx(guild=True)
        may_run_in_dm(ctx)
        ctx.bot.utility.log_to_file.assert_not_called()


class TestWiring:
    """The gate has to sit in process_commands, not in a check: a failed
    check raises CheckFailure, and ContainerManagerCog's error handler
    answers that with "não tens autorização" — which tells the stranger
    the command exists. Returning before invoke() says nothing at all."""

    def test_process_commands_consults_the_policy_before_invoking(self):
        import inspect

        import main

        source = inspect.getsource(main.Poliswag.process_commands)
        assert "may_run_in_dm(ctx)" in source
        assert source.index("may_run_in_dm") < source.index("self.invoke(ctx)")


def test_exemption_names_the_real_pokedex_cog():
    # A cog rename must carry this set along, or members' DM logins drop.
    from cogs.pokedex import Pokedex
    from modules.dm_policy import DM_EXEMPT_COGS

    assert Pokedex.__cog_name__ in DM_EXEMPT_COGS
