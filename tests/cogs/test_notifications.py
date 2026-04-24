"""Tests for cogs.notifications.Notifications.

The cog wraps poliswag.poracle (a PoracleClient) and uses
poliswag.quest_search for pokemon name resolution. Both are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.notifications import Notifications
from modules.poracle_client import PoracleError


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.ADMIN_USERS_IDS = ["42"]
    poliswag.poracle = MagicMock()
    poliswag.poracle._request = AsyncMock()
    poliswag.poracle.list_pokemon_tracking = AsyncMock()
    poliswag.poracle.add_pokemon_tracking = AsyncMock()
    poliswag.poracle.delete_pokemon_tracking_uid = AsyncMock()
    poliswag.poracle.create_channel = AsyncMock()
    poliswag.poracle.get_human = AsyncMock()
    poliswag.poracle.start = AsyncMock()
    poliswag.poracle.reload = AsyncMock()
    poliswag.poracle.test_pokemon = AsyncMock()
    poliswag.quest_search.pokemon_name_map = {"25": "pikachu", "6": "charizard"}
    poliswag.quest_search.get_pokemon_id_by_pokemon_name_map = MagicMock(
        side_effect=lambda q: [
            pid
            for pid, name in poliswag.quest_search.pokemon_name_map.items()
            if q.lower() in name
        ]
    )
    with patch("cogs.notifications.DatabaseConnector"):
        c = Notifications(poliswag)
    c.poracle_db = MagicMock()
    return c


def make_ctx(author_id="42"):
    ctx = MagicMock()
    ctx.author.id = author_id
    ctx.send = AsyncMock()
    return ctx


def make_channel(channel_id=111, name="leiria-100iv"):
    channel = MagicMock()
    channel.id = channel_id
    channel.name = name
    channel.mention = f"<#{channel_id}>"
    return channel


class TestCogCheck:
    def test_admin_passes(self, cog):
        assert cog.cog_check(make_ctx()) is True

    def test_non_admin_fails(self, cog):
        assert cog.cog_check(make_ctx(author_id="nope")) is False


class TestChannels:
    async def test_lists_channels_from_db(self, cog):
        cog.poracle_db.get_data_from_database.return_value = [
            {"id": "111", "name": "leiria-100iv", "enabled": 1},
            {"id": "222", "name": "alertas-level5", "enabled": 0},
        ]
        ctx = make_ctx()
        await Notifications.channels_cmd.callback(cog, ctx)
        embed = ctx.send.call_args.kwargs["embed"]
        assert "leiria-100iv" in embed.description
        assert "alertas-level5" in embed.description
        assert "🟢" in embed.description
        assert "🔴" in embed.description

    async def test_empty_list_short_circuits(self, cog):
        cog.poracle_db.get_data_from_database.return_value = []
        ctx = make_ctx()
        await Notifications.channels_cmd.callback(cog, ctx)
        ctx.send.assert_awaited_once()
        assert "Sem canais" in ctx.send.call_args.args[0]

    async def test_error_reported(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = RuntimeError("oops")
        ctx = make_ctx()
        await Notifications.channels_cmd.callback(cog, ctx)
        assert "Erro" in ctx.send.call_args.args[0]


class TestList:
    async def test_empty_rules_short_circuits(self, cog):
        cog.poliswag.poracle.list_pokemon_tracking.return_value = []
        ctx = make_ctx()
        channel = make_channel()
        await Notifications.list_cmd.callback(cog, ctx, channel)
        assert "Nenhum" in ctx.send.call_args.args[0]

    async def test_renders_rules_with_names(self, cog):
        cog.poliswag.poracle.list_pokemon_tracking.return_value = [
            {"uid": "a1", "pokemon_id": 25, "min_iv": 90, "min_cp": 0},
            {"uid": "b2", "pokemon_id": 6, "min_iv": 95, "min_cp": 2500},
        ]
        ctx = make_ctx()
        channel = make_channel()
        await Notifications.list_cmd.callback(cog, ctx, channel)
        embed = ctx.send.call_args.kwargs["embed"]
        assert "Pikachu" in embed.description
        assert "Charizard" in embed.description
        assert "IV≥90" in embed.description


class TestAdd:
    async def test_rejects_ambiguous_name(self, cog):
        cog.poliswag.quest_search.get_pokemon_id_by_pokemon_name_map.side_effect = (
            lambda q: ["25", "26"]
        )
        ctx = make_ctx()
        channel = make_channel()
        await Notifications.add_cmd.callback(cog, ctx, channel, "char")
        cog.poliswag.poracle.add_pokemon_tracking.assert_not_awaited()
        assert "único" in ctx.send.call_args.args[0]

    async def test_exact_name_adds_and_reloads(self, cog):
        ctx = make_ctx()
        channel = make_channel()
        await Notifications.add_cmd.callback(cog, ctx, channel, "pikachu", 90, 0)
        cog.poliswag.poracle.add_pokemon_tracking.assert_awaited_once_with(
            channel.id, {"pokemon_id": 25, "min_iv": 90, "min_cp": 0}
        )
        cog.poliswag.poracle.reload.assert_awaited_once()
        assert "✔" in ctx.send.call_args.args[0]

    async def test_api_error_reported(self, cog):
        cog.poliswag.poracle.add_pokemon_tracking.side_effect = PoracleError("500")
        ctx = make_ctx()
        channel = make_channel()
        await Notifications.add_cmd.callback(cog, ctx, channel, "pikachu")
        cog.poliswag.poracle.reload.assert_not_awaited()
        assert "Erro" in ctx.send.call_args.args[0]


class TestRemove:
    async def test_happy_path(self, cog):
        ctx = make_ctx()
        channel = make_channel()
        await Notifications.remove_cmd.callback(cog, ctx, channel, "abc")
        cog.poliswag.poracle.delete_pokemon_tracking_uid.assert_awaited_once_with(
            channel.id, "abc"
        )
        cog.poliswag.poracle.reload.assert_awaited_once()

    async def test_error_skips_reload(self, cog):
        cog.poliswag.poracle.delete_pokemon_tracking_uid.side_effect = PoracleError("x")
        ctx = make_ctx()
        channel = make_channel()
        await Notifications.remove_cmd.callback(cog, ctx, channel, "abc")
        cog.poliswag.poracle.reload.assert_not_awaited()


class TestRegister:
    async def test_already_registered(self, cog):
        cog.poliswag.poracle.get_human.return_value = {"id": "111"}
        ctx = make_ctx()
        channel = make_channel()
        await Notifications.register_cmd.callback(cog, ctx, channel)
        cog.poliswag.poracle.create_channel.assert_not_awaited()
        assert "já está" in ctx.send.call_args.args[0]

    async def test_creates_and_starts(self, cog):
        cog.poliswag.poracle.get_human.return_value = None
        ctx = make_ctx()
        channel = make_channel()
        await Notifications.register_cmd.callback(cog, ctx, channel)
        cog.poliswag.poracle.create_channel.assert_awaited_once_with(
            channel.id, channel.name
        )
        cog.poliswag.poracle.start.assert_awaited_once_with(channel.id)


class TestReload:
    async def test_happy_path(self, cog):
        ctx = make_ctx()
        await Notifications.reload_cmd.callback(cog, ctx)
        cog.poliswag.poracle.reload.assert_awaited_once()
        assert "✔" in ctx.send.call_args.args[0]

    async def test_error_reported(self, cog):
        cog.poliswag.poracle.reload.side_effect = PoracleError("oops")
        ctx = make_ctx()
        await Notifications.reload_cmd.callback(cog, ctx)
        assert "Erro" in ctx.send.call_args.args[0]


class TestTest:
    async def test_sends_test_with_channel_target(self, cog):
        ctx = make_ctx()
        channel = make_channel()
        await Notifications.test_cmd.callback(cog, ctx, channel)
        cog.poliswag.poracle.test_pokemon.assert_awaited_once()
        _, kwargs = cog.poliswag.poracle.test_pokemon.call_args
        # Called with positional args; inspect args
        args = cog.poliswag.poracle.test_pokemon.call_args.args
        webhook, target = args
        assert webhook["pokemon_id"] == 25
        assert target["id"] == str(channel.id)
        assert target["type"] == "discord:channel"
