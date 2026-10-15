from types import SimpleNamespace
from datetime import datetime, timezone

import discord
from modules.shiny_access_requests import request_payload
from unittest.mock import AsyncMock, Mock

import pytest

from cogs import shiny_access_requests as cog_module

BUTTON = "shiny-access:accept:123:20261015120000123456"


@pytest.fixture
def setup(mocker):
    mocker.patch.object(cog_module.Config, "MY_ID", 1)
    mocker.patch.object(cog_module.Config, "MOD_CHANNEL_ID", 321)
    bot = SimpleNamespace(
        ADMIN_USERS_IDS=["2"], utility=Mock(), user=SimpleNamespace(id=999)
    )
    cog = cog_module.ShinyAccessRequests(bot)
    interaction = SimpleNamespace(
        data={"custom_id": BUTTON},
        user=SimpleNamespace(id=2, display_name="Moderator Nick", name="mod.account"),
        guild=object(),
        channel_id=321,
        response=AsyncMock(),
        followup=AsyncMock(),
        message=SimpleNamespace(
            embeds=[], edit=AsyncMock(), author=SimpleNamespace(id=999)
        ),
    )
    decide = mocker.patch.object(cog_module, "decide_request", return_value=True)
    return cog, interaction, decide


@pytest.mark.parametrize(
    "user,channel,guild", [(3, 321, True), (2, 322, True), (1, 321, False)]
)
async def test_denies_unauthorized_users_channels_and_dms(setup, user, channel, guild):
    cog, interaction, decide = setup
    interaction.user.id = user
    interaction.channel_id = channel
    interaction.guild = object() if guild else None
    await cog.on_interaction(interaction)
    decide.assert_not_called()
    interaction.response.send_message.assert_awaited_once()
    interaction.message.edit.assert_not_awaited()


@pytest.mark.parametrize("user", [1, 2])
async def test_owner_and_mod_can_decide_and_remove_buttons(setup, user):
    cog, interaction, decide = setup
    interaction.user.id = user
    await cog.on_interaction(interaction)
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    decide.assert_called_once_with("123", "20261015120000123456", True)
    assert interaction.message.edit.call_args.kwargs["view"] is None
    embed = interaction.message.edit.call_args.kwargs["embed"]
    assert embed.fields[0].name == "Aceite por"
    assert embed.fields[0].value == "Moderator Nick (@mod.account)"
    assert embed.fields[1].name == "Aceite em"


async def test_stale_button_leaves_message_unchanged(setup):
    cog, interaction, decide = setup
    decide.return_value = False
    await cog.on_interaction(interaction)
    interaction.message.edit.assert_not_awaited()
    assert "já foi resolvido" in interaction.followup.send.call_args.args[0]


async def test_database_failure_leaves_buttons_for_retry(setup):
    cog, interaction, decide = setup
    decide.side_effect = RuntimeError("database unavailable")
    await cog.on_interaction(interaction)
    interaction.message.edit.assert_not_awaited()
    assert "Tenta novamente" in interaction.followup.send.call_args.args[0]


async def test_denies_forged_message_author(setup):
    cog, interaction, decide = setup
    interaction.message.author.id = 888
    await cog.on_interaction(interaction)
    decide.assert_not_called()
    interaction.response.send_message.assert_awaited_once()


async def test_acceptance_retains_request_and_records_commit_time(setup, mocker):
    cog, interaction, decide = setup
    payload = request_payload(
        {
            "discord_id": "123",
            "name": "Trainer",
            "display_name": "Server Nick",
            "username": "account",
            "avatar_url": "https://site/avatar.webp",
            "shiny_access_requested_at": "2026-10-15 12:00:00.123456",
        },
        "https://site",
    )
    original = discord.Embed.from_dict(payload["embeds"][0])
    interaction.message.embeds = [original]
    decided = datetime(2026, 10, 15, 14, tzinfo=timezone.utc)

    def clock():
        decide.assert_called_once()
        return decided

    mocker.patch.object(cog_module.discord.utils, "utcnow", side_effect=clock)
    await cog.on_interaction(interaction)
    result = interaction.message.edit.call_args.kwargs["embed"]
    assert result.to_dict()["fields"][:3] == original.to_dict()["fields"]
    assert result.thumbnail.url == original.thumbnail.url
    assert result.timestamp == original.timestamp
    assert result.fields[-1].value == f"<t:{int(decided.timestamp())}:F>"
    assert (
        interaction.message.edit.call_args.kwargs["allowed_mentions"].everyone is False
    )
