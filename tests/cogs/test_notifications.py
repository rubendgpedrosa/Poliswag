"""Tests for cogs.notifications.Notifications.

Commands target a "ref" string (channel id/mention/name or a category suffix
like ``raros`` that fan-outs across leiria/marinha channels). The cog uses
``self.poracle_db`` for DB reads and ``self.poliswag.poracle`` for mutations.
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
    poliswag.poracle.list_pokemon_tracking = AsyncMock()
    poliswag.poracle.add_pokemon_tracking = AsyncMock()
    poliswag.poracle.delete_pokemon_tracking_uid = AsyncMock()
    poliswag.poracle.create_channel = AsyncMock()
    poliswag.poracle.get_human = AsyncMock()
    poliswag.poracle.start = AsyncMock()
    poliswag.poracle.stop = AsyncMock()
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


class TestCogCheck:
    def test_admin_passes(self, cog):
        assert cog.cog_check(make_ctx()) is True

    def test_non_admin_fails(self, cog):
        assert cog.cog_check(make_ctx(author_id="nope")) is False


class TestResolveTargets:
    def test_mention_strips_and_queries_by_id(self, cog):
        cog.poracle_db.get_data_from_database.return_value = [
            {"id": "111", "name": "leiria-100iv", "enabled": 1}
        ]
        out = cog._resolve_targets("<#111>")
        assert out == [{"id": "111", "name": "leiria-100iv", "enabled": 1}]
        query, kwargs = (
            cog.poracle_db.get_data_from_database.call_args.args[0],
            cog.poracle_db.get_data_from_database.call_args.kwargs,
        )
        assert "id = %s" in query
        assert kwargs["params"] == ("111",)

    def test_numeric_ref_is_id_match(self, cog):
        cog.poracle_db.get_data_from_database.return_value = []
        cog._resolve_targets("111")
        assert cog.poracle_db.get_data_from_database.call_args.kwargs["params"] == (
            "111",
        )

    def test_exact_name_match_short_circuits(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [{"id": "111", "name": "alertas-level5", "enabled": 1}],
        ]
        out = cog._resolve_targets("alertas-level5")
        assert len(out) == 1
        assert out[0]["name"] == "alertas-level5"
        # Only the exact-name query was issued (no LIKE fallback)
        assert cog.poracle_db.get_data_from_database.call_count == 1

    def test_category_suffix_fans_out(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [],  # exact match returns nothing
            [  # LIKE %-raros returns both
                {"id": "111", "name": "leiria-raros", "enabled": 1},
                {"id": "222", "name": "marinha-raros", "enabled": 0},
            ],
        ]
        out = cog._resolve_targets("raros")
        assert {r["name"] for r in out} == {"leiria-raros", "marinha-raros"}
        like_call = cog.poracle_db.get_data_from_database.call_args_list[1]
        assert "LIKE %s" in like_call.args[0]
        assert like_call.kwargs["params"] == ("%-raros",)

    def test_no_match_returns_empty_list(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [[], []]
        assert cog._resolve_targets("bogus") == []


class TestChannels:
    async def test_lists_channels_from_db(self, cog):
        cog.poracle_db.get_data_from_database.return_value = [
            {"id": "111", "name": "leiria-100iv", "enabled": 1},
            {"id": "222", "name": "alertas-level5", "enabled": 0},
        ]
        ctx = make_ctx()
        await Notifications.channels_cmd.callback(cog, ctx)
        embed = ctx.send.call_args.kwargs["embed"]
        assert "<#111>" in embed.description
        assert "<#222>" in embed.description
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
    async def test_no_match_shows_help(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [[], []]
        ctx = make_ctx()
        await Notifications.list_cmd.callback(cog, ctx, "bogus")
        assert "Não encontrei" in ctx.send.call_args.args[0]

    async def test_single_target_with_rules(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [{"id": "111", "name": "leiria-100iv", "enabled": 1}]
        ]
        cog.poliswag.poracle.list_pokemon_tracking.return_value = [
            {
                "uid": "a1",
                "pokemon_id": 25,
                "min_iv": 100,
                "max_iv": 100,
                "min_cp": 0,
                "max_cp": 9000,
            }
        ]
        ctx = make_ctx()
        await Notifications.list_cmd.callback(cog, ctx, "<#111>")
        embed = ctx.send.call_args.kwargs["embed"]
        assert "Pikachu" in embed.description
        assert "IV=100" in embed.description

    async def test_multi_target_fan_out(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [],
            [
                {"id": "111", "name": "leiria-raros", "enabled": 1},
                {"id": "222", "name": "marinha-raros", "enabled": 0},
            ],
        ]
        cog.poliswag.poracle.list_pokemon_tracking.side_effect = [
            [{"uid": "1", "pokemon_id": 6, "min_iv": 0, "max_iv": 100, "min_cp": 0}],
            [],
        ]
        ctx = make_ctx()
        await Notifications.list_cmd.callback(cog, ctx, "raros")
        embed = ctx.send.call_args.kwargs["embed"]
        assert "leiria-raros" in embed.description
        assert "marinha-raros" in embed.description
        assert "Charizard" in embed.description
        assert "nenhum pokémon" in embed.description.lower()

    async def test_wildcard_rendered_as_qualquer(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [{"id": "111", "name": "leiria-raros", "enabled": 1}]
        ]
        cog.poliswag.poracle.list_pokemon_tracking.return_value = [
            {
                "uid": "3",
                "pokemon_id": 0,
                "min_iv": 100,
                "max_iv": 100,
                "min_cp": 0,
                "max_cp": 9000,
            }
        ]
        ctx = make_ctx()
        await Notifications.list_cmd.callback(cog, ctx, "111")
        embed = ctx.send.call_args.kwargs["embed"]
        assert "Qualquer" in embed.description
        assert "IV=100" in embed.description


class TestAdd:
    async def test_no_matching_channel(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [[], []]
        ctx = make_ctx()
        await Notifications.add_cmd.callback(cog, ctx, "bogus", "pikachu")
        cog.poliswag.poracle.add_pokemon_tracking.assert_not_awaited()

    async def test_ambiguous_pokemon_rejected(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [{"id": "111", "name": "leiria-raros", "enabled": 1}]
        ]
        cog.poliswag.quest_search.get_pokemon_id_by_pokemon_name_map.side_effect = (
            lambda q: ["25", "26"]
        )
        ctx = make_ctx()
        await Notifications.add_cmd.callback(cog, ctx, "111", "char")
        cog.poliswag.poracle.add_pokemon_tracking.assert_not_awaited()
        assert "único" in ctx.send.call_args.args[0]

    async def test_fan_out_adds_to_all_and_reloads_once(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [],
            [
                {"id": "111", "name": "leiria-raros", "enabled": 1},
                {"id": "222", "name": "marinha-raros", "enabled": 1},
            ],
        ]
        ctx = make_ctx()
        await Notifications.add_cmd.callback(cog, ctx, "raros", "pikachu")
        assert cog.poliswag.poracle.add_pokemon_tracking.await_count == 2
        cog.poliswag.poracle.reload.assert_awaited_once()
        msg = ctx.send.call_args.args[0]
        assert "#leiria-raros" in msg
        assert "#marinha-raros" in msg

    async def test_per_target_failure_reported_others_succeed(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [],
            [
                {"id": "111", "name": "leiria-raros", "enabled": 1},
                {"id": "222", "name": "marinha-raros", "enabled": 1},
            ],
        ]
        cog.poliswag.poracle.add_pokemon_tracking.side_effect = [
            None,
            PoracleError("boom"),
        ]
        ctx = make_ctx()
        await Notifications.add_cmd.callback(cog, ctx, "raros", "pikachu")
        msg = ctx.send.call_args.args[0]
        assert "✔" in msg
        assert "✖" in msg
        assert "marinha-raros" in msg


class TestRemove:
    async def test_non_numeric_uid_rejected(self, cog):
        ctx = make_ctx()
        await Notifications.remove_cmd.callback(cog, ctx, "abc")
        cog.poliswag.poracle.delete_pokemon_tracking_uid.assert_not_awaited()

    async def test_unknown_uid_tells_user(self, cog):
        cog.poracle_db.get_data_from_database.return_value = []
        ctx = make_ctx()
        await Notifications.remove_cmd.callback(cog, ctx, "999")
        cog.poliswag.poracle.delete_pokemon_tracking_uid.assert_not_awaited()
        assert "Não encontrei" in ctx.send.call_args.args[0]

    async def test_resolves_channel_and_deletes(self, cog):
        cog.poracle_db.get_data_from_database.return_value = [{"id": "111"}]
        ctx = make_ctx()
        await Notifications.remove_cmd.callback(cog, ctx, "17")
        cog.poliswag.poracle.delete_pokemon_tracking_uid.assert_awaited_once_with(
            "111", "17"
        )
        cog.poliswag.poracle.reload.assert_awaited_once()


class TestRegister:
    async def test_already_registered(self, cog):
        cog.poliswag.poracle.get_human.return_value = {"id": "111"}
        ctx = make_ctx()
        channel = MagicMock()
        channel.id = 111
        channel.mention = "<#111>"
        channel.name = "leiria-100iv"
        await Notifications.register_cmd.callback(cog, ctx, channel)
        cog.poliswag.poracle.create_channel.assert_not_awaited()

    async def test_creates_and_starts(self, cog):
        cog.poliswag.poracle.get_human.return_value = None
        ctx = make_ctx()
        channel = MagicMock()
        channel.id = 111
        channel.mention = "<#111>"
        channel.name = "leiria-100iv"
        await Notifications.register_cmd.callback(cog, ctx, channel)
        cog.poliswag.poracle.create_channel.assert_awaited_once_with(
            111, "leiria-100iv"
        )
        cog.poliswag.poracle.start.assert_awaited_once_with(111)


class TestEnableDisable:
    async def test_enable_fans_out(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [],
            [
                {"id": "111", "name": "leiria-raros", "enabled": 0},
                {"id": "222", "name": "marinha-raros", "enabled": 0},
            ],
        ]
        ctx = make_ctx()
        await Notifications.enable_cmd.callback(cog, ctx, "raros")
        assert cog.poliswag.poracle.start.await_count == 2
        cog.poliswag.poracle.stop.assert_not_awaited()

    async def test_disable_fans_out(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [{"id": "111", "name": "alertas-level5", "enabled": 1}],
        ]
        ctx = make_ctx()
        await Notifications.disable_cmd.callback(cog, ctx, "alertas-level5")
        cog.poliswag.poracle.stop.assert_awaited_once_with("111")

    async def test_no_match_shows_help(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [[], []]
        ctx = make_ctx()
        await Notifications.enable_cmd.callback(cog, ctx, "bogus")
        cog.poliswag.poracle.start.assert_not_awaited()


class TestReload:
    async def test_happy_path(self, cog):
        ctx = make_ctx()
        await Notifications.reload_cmd.callback(cog, ctx)
        cog.poliswag.poracle.reload.assert_awaited_once()

    async def test_error_reported(self, cog):
        cog.poliswag.poracle.reload.side_effect = PoracleError("oops")
        ctx = make_ctx()
        await Notifications.reload_cmd.callback(cog, ctx)
        assert "Erro" in ctx.send.call_args.args[0]


class TestTest:
    async def test_fan_out_sends_to_all(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [],
            [
                {"id": "111", "name": "leiria-raros", "enabled": 1},
                {"id": "222", "name": "marinha-raros", "enabled": 1},
            ],
        ]
        ctx = make_ctx()
        await Notifications.test_cmd.callback(cog, ctx, "raros")
        assert cog.poliswag.poracle.test_pokemon.await_count == 2
        targets = [
            call.args[1]["id"]
            for call in cog.poliswag.poracle.test_pokemon.call_args_list
        ]
        assert set(targets) == {"111", "222"}

    async def test_no_match_shows_help(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [[], []]
        ctx = make_ctx()
        await Notifications.test_cmd.callback(cog, ctx, "bogus")
        cog.poliswag.poracle.test_pokemon.assert_not_awaited()
