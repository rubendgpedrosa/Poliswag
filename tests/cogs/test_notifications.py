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
    # Dedupe lookup defaults to "no existing rule" so each add_cmd test can
    # opt into the duplicate branch explicitly.
    c._rule_exists = MagicMock(return_value=False)
    return c


def make_ctx(author_id="42"):
    ctx = MagicMock()
    ctx.author.id = author_id
    ctx.send = AsyncMock()
    return ctx


def reply_text(ctx) -> str:
    """Grab the rendered embed text (description + title) from the latest ctx.send."""
    embed = ctx.send.call_args.kwargs.get("embed")
    if embed is None:
        return ctx.send.call_args.args[0] if ctx.send.call_args.args else ""
    parts = []
    if embed.title:
        parts.append(embed.title)
    if embed.description:
        parts.append(embed.description)
    for field in getattr(embed, "_fields", []) or []:
        parts.append(field.get("name", ""))
        parts.append(field.get("value", ""))
    return "\n".join(parts)


class TestCogCheck:
    def test_admin_passes(self, cog):
        assert cog.cog_check(make_ctx()) is True

    def test_non_admin_fails(self, cog):
        assert cog.cog_check(make_ctx(author_id="nope")) is False


class TestGroupParent:
    async def test_bare_notify_lists_subcommands(self, cog):
        ctx = make_ctx()
        await Notifications.notify.callback(cog, ctx)
        msg = reply_text(ctx)
        for sub in ("channels", "list", "add", "remove", "enable", "disable", "test"):
            assert sub in msg
        # reload is gone
        assert "reload" not in msg


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
        assert "Sem canais" in reply_text(ctx)

    async def test_error_reported(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = RuntimeError("oops")
        ctx = make_ctx()
        await Notifications.channels_cmd.callback(cog, ctx)
        assert "Erro" in reply_text(ctx)


class TestList:
    async def test_no_match_shows_help(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [[], []]
        ctx = make_ctx()
        await Notifications.list_cmd.callback(cog, ctx, "bogus")
        assert "Não encontrei" in reply_text(ctx)

    async def test_no_arg_merges_leiria_marinha_channels(self, cog):
        cog.poracle_db.get_data_from_database.return_value = [
            {"id": "111", "name": "leiria-raros", "enabled": 1},
            {"id": "222", "name": "marinha-raros", "enabled": 1},
        ]
        cog.poliswag.poracle.list_pokemon_tracking.side_effect = [
            [{"uid": 1, "pokemon_id": 6, "min_iv": 0, "max_iv": 100, "min_cp": 0}],
            [{"uid": 7, "pokemon_id": 6, "min_iv": 0, "max_iv": 100, "min_cp": 0}],
        ]
        ctx = make_ctx()
        await Notifications.list_cmd.callback(cog, ctx)
        desc = ctx.send.call_args.kwargs["embed"].description
        assert "todos os canais" in ctx.send.call_args.kwargs["embed"].title
        # leiria and marinha are merged under one "raros" group header
        assert "**raros**" in desc
        assert "<#111>" in desc
        assert "<#222>" in desc
        # Charizard appears only once (deduplicated)
        assert desc.count("Charizard") == 1
        # Merged view uses _render_rule_summary (no per-channel UID tags)
        assert "`1`" not in desc
        assert "`7`" not in desc

    async def test_no_arg_empty_channel_shows_placeholder(self, cog):
        cog.poracle_db.get_data_from_database.return_value = [
            {"id": "111", "name": "alertas-level5", "enabled": 1},
        ]
        cog.poliswag.poracle.list_pokemon_tracking.return_value = []
        ctx = make_ctx()
        await Notifications.list_cmd.callback(cog, ctx)
        desc = ctx.send.call_args.kwargs["embed"].description
        assert "alertas-level5" in desc
        assert "nenhum pokémon" in desc.lower()

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

    async def test_multi_target_merged_dedupe(self, cog):
        # Both channels have an identical rule for Charizard — should appear once,
        # grouped under a "raros" header with both channel mentions.
        cog.poracle_db.get_data_from_database.side_effect = [
            [],
            [
                {"id": "111", "name": "leiria-raros", "enabled": 1},
                {"id": "222", "name": "marinha-raros", "enabled": 1},
            ],
        ]
        cog.poliswag.poracle.list_pokemon_tracking.side_effect = [
            [{"uid": 1, "pokemon_id": 6, "min_iv": 0, "max_iv": 100, "min_cp": 0}],
            [{"uid": 7, "pokemon_id": 6, "min_iv": 0, "max_iv": 100, "min_cp": 0}],
        ]
        ctx = make_ctx()
        await Notifications.list_cmd.callback(cog, ctx, "raros")
        desc = ctx.send.call_args.kwargs["embed"].description
        # One Charizard line (deduplicated), no per-channel uid tags
        assert desc.count("Charizard") == 1
        assert "**raros**" in desc
        assert "<#111>" in desc
        assert "<#222>" in desc
        assert "`1`" not in desc
        assert "`7`" not in desc

    async def test_multi_target_different_filters_not_merged(self, cog):
        # leiria and marinha have different IV thresholds for the same pokemon —
        # both rules should appear (different dedup keys).
        cog.poracle_db.get_data_from_database.side_effect = [
            [],
            [
                {"id": "111", "name": "leiria-raros", "enabled": 1},
                {"id": "222", "name": "marinha-raros", "enabled": 1},
            ],
        ]
        cog.poliswag.poracle.list_pokemon_tracking.side_effect = [
            [{"uid": 1, "pokemon_id": 6, "min_iv": 90, "max_iv": 100, "min_cp": 0}],
            [{"uid": 7, "pokemon_id": 6, "min_iv": 0, "max_iv": 100, "min_cp": 0}],
        ]
        ctx = make_ctx()
        await Notifications.list_cmd.callback(cog, ctx, "raros")
        desc = ctx.send.call_args.kwargs["embed"].description
        assert "**raros**" in desc
        # Two distinct rules (different min_iv) → Charizard appears twice
        assert desc.count("Charizard") == 2

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

    async def test_ambiguous_pokemon_skipped_as_unresolved(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [{"id": "111", "name": "leiria-raros", "enabled": 1}]
        ]
        cog.poliswag.quest_search.get_pokemon_id_by_pokemon_name_map.side_effect = (
            lambda q: ["25", "26"]
        )
        ctx = make_ctx()
        await Notifications.add_cmd.callback(cog, ctx, "111", "char")
        cog.poliswag.poracle.add_pokemon_tracking.assert_not_awaited()
        assert "Nenhum" in reply_text(ctx) or "único" in reply_text(ctx)

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
        msg = reply_text(ctx)
        assert "#leiria-raros" in msg
        assert "#marinha-raros" in msg

    async def test_comma_separated_names_fan_out_per_channel(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [],
            [
                {"id": "111", "name": "leiria-raros", "enabled": 1},
                {"id": "222", "name": "marinha-raros", "enabled": 1},
            ],
        ]
        ctx = make_ctx()
        await Notifications.add_cmd.callback(cog, ctx, "raros", "pikachu, charizard")
        # 2 pokemon × 2 channels = 4 calls
        assert cog.poliswag.poracle.add_pokemon_tracking.await_count == 4
        # One reload total, not per-pokemon
        cog.poliswag.poracle.reload.assert_awaited_once()
        msg = reply_text(ctx)
        assert "Pikachu" in msg
        assert "Charizard" in msg

    async def test_mixed_valid_and_invalid_names(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [{"id": "111", "name": "leiria-raros", "enabled": 1}]
        ]
        # Only pikachu resolves; "bogusmon" doesn't match anything.
        ctx = make_ctx()
        await Notifications.add_cmd.callback(cog, ctx, "111", "pikachu, bogusmon")
        assert cog.poliswag.poracle.add_pokemon_tracking.await_count == 1
        cog.poliswag.poracle.reload.assert_awaited_once()
        msg = reply_text(ctx)
        assert "✔" in msg
        assert "Pikachu" in msg
        assert "✖" in msg
        assert "bogusmon" in msg

    async def test_dedupes_existing_rule(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [],
            [
                {"id": "111", "name": "leiria-raros", "enabled": 1},
                {"id": "222", "name": "marinha-raros", "enabled": 1},
            ],
        ]
        # leiria already has the rule, marinha doesn't.
        cog._rule_exists = MagicMock(side_effect=[True, False])
        ctx = make_ctx()
        await Notifications.add_cmd.callback(cog, ctx, "raros", "pikachu")
        # Only one insert went through — for marinha.
        assert cog.poliswag.poracle.add_pokemon_tracking.await_count == 1
        added_call = cog.poliswag.poracle.add_pokemon_tracking.call_args
        assert added_call.args[0] == "222"
        msg = reply_text(ctx)
        assert "✔" in msg
        assert "já existe em #leiria-raros" in msg

    async def test_all_skipped_no_reload(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [{"id": "111", "name": "leiria-raros", "enabled": 1}]
        ]
        cog._rule_exists = MagicMock(return_value=True)
        ctx = make_ctx()
        await Notifications.add_cmd.callback(cog, ctx, "111", "pikachu")
        cog.poliswag.poracle.add_pokemon_tracking.assert_not_awaited()
        cog.poliswag.poracle.reload.assert_not_awaited()
        assert "já existe" in reply_text(ctx)

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
        msg = reply_text(ctx)
        assert "✔" in msg
        assert "✖" in msg
        assert "marinha-raros" in msg


class TestRemove:
    async def test_no_matching_ref(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [[], []]
        ctx = make_ctx()
        await Notifications.remove_cmd.callback(cog, ctx, "bogus", "172")
        cog.poliswag.poracle.delete_pokemon_tracking_uid.assert_not_awaited()
        assert "Não encontrei" in reply_text(ctx)

    async def test_unknown_uid_tells_user(self, cog):
        # Ref resolves fine; UID lookup returns nothing.
        cog.poracle_db.get_data_from_database.side_effect = [
            [{"id": "111", "name": "leiria-raros", "enabled": 1}],
            [],
        ]
        ctx = make_ctx()
        await Notifications.remove_cmd.callback(cog, ctx, "raros", "999")
        cog.poliswag.poracle.delete_pokemon_tracking_uid.assert_not_awaited()
        assert "Não encontrei nenhuma regra com uid" in reply_text(ctx)

    async def test_uid_path_deletes_regardless_of_ref(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [{"id": "111", "name": "leiria-raros", "enabled": 1}],
            [{"id": "222"}],  # owner lookup
        ]
        ctx = make_ctx()
        await Notifications.remove_cmd.callback(cog, ctx, "raros", "17")
        cog.poliswag.poracle.delete_pokemon_tracking_uid.assert_awaited_once_with(
            "222", "17"
        )
        cog.poliswag.poracle.reload.assert_awaited_once()

    async def test_name_path_fan_out_deletes_matching_rules(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [],  # exact-name miss
            [  # category fan-out
                {"id": "111", "name": "leiria-raros", "enabled": 1},
                {"id": "222", "name": "marinha-raros", "enabled": 1},
            ],
            [{"uid": 50}, {"uid": 51}],  # leiria matches
            [{"uid": 60}],  # marinha matches
        ]
        ctx = make_ctx()
        await Notifications.remove_cmd.callback(cog, ctx, "raros", "pikachu")
        assert cog.poliswag.poracle.delete_pokemon_tracking_uid.await_count == 3
        cog.poliswag.poracle.reload.assert_awaited_once()
        msg = reply_text(ctx)
        assert "Pikachu" in msg
        assert "leiria-raros" in msg
        assert "marinha-raros" in msg

    async def test_name_path_comma_separated(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [{"id": "111", "name": "leiria-raros", "enabled": 1}],
            [{"uid": 50}],  # pikachu hit
            [{"uid": 60}],  # charizard hit
        ]
        ctx = make_ctx()
        await Notifications.remove_cmd.callback(
            cog, ctx, "leiria-raros", "pikachu,charizard"
        )
        assert cog.poliswag.poracle.delete_pokemon_tracking_uid.await_count == 2
        cog.poliswag.poracle.reload.assert_awaited_once()
        msg = reply_text(ctx)
        assert "Pikachu" in msg
        assert "Charizard" in msg

    async def test_name_path_no_matches_reports_gracefully(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [{"id": "111", "name": "leiria-raros", "enabled": 1}],
            [],  # no monsters matching pokemon_id
        ]
        ctx = make_ctx()
        await Notifications.remove_cmd.callback(cog, ctx, "leiria-raros", "pikachu")
        cog.poliswag.poracle.delete_pokemon_tracking_uid.assert_not_awaited()
        cog.poliswag.poracle.reload.assert_not_awaited()
        assert "Nenhuma regra" in reply_text(ctx)

    async def test_ambiguous_pokemon_rejected(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [
            [{"id": "111", "name": "leiria-raros", "enabled": 1}],
        ]
        cog.poliswag.quest_search.get_pokemon_id_by_pokemon_name_map.side_effect = (
            lambda q: ["25", "26"]
        )
        ctx = make_ctx()
        await Notifications.remove_cmd.callback(cog, ctx, "leiria-raros", "char")
        cog.poliswag.poracle.delete_pokemon_tracking_uid.assert_not_awaited()
        assert "único" in reply_text(ctx)


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
        await Notifications.test_cmd.callback(cog, ctx, "raros", "pikachu")
        assert cog.poliswag.poracle.test_pokemon.await_count == 2
        targets = [
            call.args[1]["id"]
            for call in cog.poliswag.poracle.test_pokemon.call_args_list
        ]
        assert set(targets) == {"111", "222"}
        # Pokemon id resolved from the supplied name, not hardcoded Pikachu=25 by accident.
        webhook_payload = cog.poliswag.poracle.test_pokemon.call_args_list[0].args[0]
        assert webhook_payload["pokemon_id"] == 25

    async def test_dm_uses_user_id_and_user_type(self, cog):
        ctx = make_ctx()
        ctx.author.id = 98846248865398784
        ctx.author.name = "faynn"
        await Notifications.test_cmd.callback(cog, ctx, "dm", "charizard")
        cog.poliswag.poracle.test_pokemon.assert_awaited_once()
        webhook, target = cog.poliswag.poracle.test_pokemon.call_args.args
        assert target["id"] == "98846248865398784"
        assert target["type"] == "discord:user"
        assert webhook["pokemon_id"] == 6

    async def test_no_match_shows_help(self, cog):
        cog.poracle_db.get_data_from_database.side_effect = [[], []]
        ctx = make_ctx()
        await Notifications.test_cmd.callback(cog, ctx, "bogus", "pikachu")
        cog.poliswag.poracle.test_pokemon.assert_not_awaited()

    async def test_unknown_pokemon_rejected(self, cog):
        ctx = make_ctx()
        await Notifications.test_cmd.callback(cog, ctx, "dm", "bogusmon")
        cog.poliswag.poracle.test_pokemon.assert_not_awaited()
        assert "único" in reply_text(ctx)
