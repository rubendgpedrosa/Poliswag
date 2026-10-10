"""Approval drives channel provisioning; Discord and SQL are faked."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from modules.config import Config
from modules.shiny_channels import (
    ARCHIVE_CATEGORY,
    CHANNEL_CATEGORY,
    ShinyChannels,
    map_url,
)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(Config, "MY_ID", 1)
    owner, member = MagicMock(id=1), MagicMock(id=2)
    member.name = "trainer"
    guild = MagicMock(id=99)
    guild.get_member.side_effect = lambda user_id: {1: owner, 2: member}.get(user_id)
    guild.categories, guild.text_channels = [], []
    guild.get_channel.side_effect = lambda channel_id: next(
        (c for c in guild.text_channels if c.id == channel_id), None
    )

    async def category(name, **kwargs):
        value = MagicMock(id=10 + len(guild.categories))
        value.name = name
        guild.categories.append(value)
        return value

    guild.create_category = AsyncMock(side_effect=category)
    message = MagicMock(id=55, pinned=False, pin=AsyncMock())

    async def create(name, **kwargs):
        value = MagicMock(id=77, send=AsyncMock(return_value=message))
        value.name, value.topic = name, kwargs["topic"]
        value.category, value.overwrites = kwargs["category"], kwargs["overwrites"]
        value.pins = AsyncMock(return_value=[])
        value.fetch_message = AsyncMock(return_value=message)
        value.overwrites_for.side_effect = lambda who: value.overwrites.get(who)

        async def edit(**changes):
            for key, new_value in changes.items():
                setattr(value, key, new_value)

        value.edit = AsyncMock(side_effect=edit)
        guild.text_channels.append(value)
        return value

    guild.create_text_channel = AsyncMock(side_effect=create)
    row = dict(
        discord_id=2,
        trainer_name="Trainer",
        status="linked",
        player_id="abc",
        channel_id=None,
        channel_archived=0,
        channel_intro_message_id=None,
        shiny_access=1,
        shiny_map=1,
        left_at=None,
    )
    store = MagicMock()
    store.channel_players = AsyncMock(side_effect=lambda *_: [dict(row)])

    async def set_channel(_id, channel_id):
        row.update(
            channel_id=channel_id, channel_archived=1, channel_intro_message_id=None
        )

    async def intro(_id, message_id):
        row["channel_intro_message_id"] = message_id

    async def archive(_id, archived):
        row["channel_archived"] = int(archived)

    store.set_channel = AsyncMock(side_effect=set_channel)
    store.set_channel_intro = AsyncMock(side_effect=intro)
    store.set_channel_archived = AsyncMock(side_effect=archive)
    bot = MagicMock(guilds=[guild])
    manager = ShinyChannels(bot, store)
    return manager, store, guild, row, member, owner, message


async def test_approval_creates_read_only_channel_and_pins_named_map(setup):
    manager, store, guild, row, member, owner, message = setup
    await manager.tick()
    channel = guild.text_channels[0]
    assert channel.overwrites[guild.default_role].view_channel is False
    assert channel.overwrites[member].view_channel is True
    for permission in (
        "send_messages",
        "add_reactions",
        "create_public_threads",
        "create_private_threads",
        "send_messages_in_threads",
    ):
        assert getattr(channel.overwrites[member], permission) is False
    assert channel.overwrites[owner].send_messages is True
    assert channel.send.call_args.kwargs["view"].children[0].url == map_url(row)
    message.pin.assert_awaited_once()
    assert row["channel_archived"] == 0
    assert row["channel_intro_message_id"] == 55
    await manager.tick()
    guild.create_text_channel.assert_awaited_once()
    channel.send.assert_awaited_once()


@pytest.mark.parametrize(
    "change",
    [
        dict(shiny_access=0),
        dict(shiny_map=0),
        dict(status="pending"),
        dict(status="failed"),
        dict(left_at="today"),
    ],
)
async def test_held_or_unready_accounts_get_no_channel(setup, change):
    manager, store, guild, row, *_ = setup
    row.update(change)
    await manager.tick()
    guild.create_category.assert_not_awaited()
    guild.create_text_channel.assert_not_awaited()
    store.set_channel.assert_not_awaited()


@pytest.mark.parametrize(
    "change", [dict(shiny_access=0), dict(shiny_map=0), dict(left_at="today")]
)
async def test_disabling_hides_existing_channel_and_reapproval_restores_it(
    setup, change
):
    manager, store, guild, row, member, owner, message = setup
    await manager.tick()
    channel = guild.text_channels[0]
    row.update(change)
    await manager.tick()
    assert row["channel_archived"] == 1
    assert channel.category.name == ARCHIVE_CATEGORY
    assert channel.overwrites[member].view_channel is False
    assert channel.overwrites[owner].send_messages is True
    row.update(shiny_access=1, shiny_map=1, left_at=None)
    message.pinned = True
    await manager.tick()
    assert row["channel_archived"] == 0
    assert channel.category.name == CHANNEL_CATEGORY
    assert channel.overwrites[member].view_channel is True
    guild.create_text_channel.assert_awaited_once()
    channel.send.assert_awaited_once()


async def test_a_failed_pin_is_retried_without_a_second_introduction(setup):
    manager, store, guild, row, *_, message = setup
    message.pin.side_effect = RuntimeError("Discord unavailable")
    await manager.tick()
    assert row["channel_archived"] == 1
    assert row["channel_intro_message_id"] == 55
    message.pin.side_effect = None
    await manager.tick()
    assert row["channel_archived"] == 0
    guild.text_channels[0].send.assert_awaited_once()
    assert message.pin.await_count == 2


async def test_legacy_channel_adopts_existing_pin_without_posting_again(setup):
    manager, store, guild, row, member, _, message = setup
    await manager.tick()
    channel = guild.text_channels[0]
    row["channel_intro_message_id"] = None
    button = MagicMock(url=map_url(row))
    message.components = [MagicMock(children=[button])]
    message.author.id = guild.me.id
    message.pinned = True
    channel.pins.return_value = [message]
    channel.send.reset_mock()
    await manager.tick()
    channel.send.assert_not_awaited()
    assert row["channel_intro_message_id"] == 55


async def test_retry_recovers_a_channel_after_database_write_failed(setup):
    manager, store, guild, row, *_ = setup
    save = store.set_channel.side_effect
    store.set_channel.side_effect = RuntimeError("database unavailable")
    await manager.tick()
    assert row["channel_id"] is None
    store.set_channel.side_effect = save
    await manager.tick()
    assert row["channel_id"] == 77
    guild.create_text_channel.assert_awaited_once()


async def test_simultaneous_ticks_do_not_duplicate_channels(setup):
    manager, store, guild, row, *_ = setup
    await asyncio.gather(manager.ensure(guild, 2), manager.ensure(guild, 2))
    guild.create_text_channel.assert_awaited_once()
    guild.text_channels[0].send.assert_awaited_once()


async def test_permission_failure_leaves_setup_pending_and_retries(setup):
    manager, store, guild, row, *_ = setup
    create = guild.create_text_channel.side_effect
    guild.create_text_channel.side_effect = discord.Forbidden(
        MagicMock(status=403), "Missing permissions"
    )
    await manager.tick()
    assert row["channel_id"] is None
    store.set_channel.assert_not_awaited()
    manager.poliswag.utility.log_to_file.assert_called_once()
    guild.create_text_channel.side_effect = create
    await manager.tick()
    assert row["channel_id"] == 77


async def test_revocation_while_pinning_keeps_the_channel_archived(setup):
    manager, store, guild, row, member, _, message = setup

    async def revoke():
        row["shiny_access"] = 0

    message.pin.side_effect = revoke
    await manager.tick()
    assert row["channel_archived"] == 1
    assert guild.text_channels[0].overwrites[member].view_channel is False
