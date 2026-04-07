"""Tests for cogs.quests.Quests.

Covers the admin-gated exportquests/scan commands and the questleiria/
questmarinha search command's full branching. ``fetch_data`` in ``rescancmd``
is imported locally inside the function, so we patch it at the module where it
is *looked up* (``modules.http_client``).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.quests import Quests


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.ADMIN_USERS_IDS = ["42"]
    poliswag.quest_exporter.export = AsyncMock()
    return Quests(poliswag)


def make_ctx(author_id="42", invoked_with="questleiria"):
    ctx = MagicMock()
    ctx.author.id = author_id
    ctx.author.mention = f"<@{author_id}>"
    ctx.invoked_with = invoked_with
    ctx.send = AsyncMock()
    return ctx


# --- exportquestscmd ----------------------------------------------------------


class TestExportQuestsCmd:
    async def test_non_admin_returns_silently(self, cog):
        ctx = make_ctx(author_id="nope")
        await Quests.exportquestscmd.callback(cog, ctx)
        ctx.send.assert_not_called()
        cog.poliswag.quest_exporter.export.assert_not_called()

    async def test_admin_success_edits_message(self, cog):
        ctx = make_ctx()
        msg = MagicMock()
        msg.edit = AsyncMock()
        ctx.send = AsyncMock(return_value=msg)
        await Quests.exportquestscmd.callback(cog, ctx)
        cog.poliswag.quest_exporter.export.assert_awaited_once()
        msg.edit.assert_awaited_once()
        assert "sucesso" in msg.edit.call_args.kwargs["content"]

    async def test_admin_failure_edits_with_error(self, cog):
        ctx = make_ctx()
        msg = MagicMock()
        msg.edit = AsyncMock()
        ctx.send = AsyncMock(return_value=msg)
        cog.poliswag.quest_exporter.export = AsyncMock(side_effect=RuntimeError("x"))
        await Quests.exportquestscmd.callback(cog, ctx)
        msg.edit.assert_awaited_once()
        assert "Erro" in msg.edit.call_args.kwargs["content"]


# --- rescancmd ----------------------------------------------------------------


class TestRescanCmd:
    async def test_non_admin_returns_silently(self, cog):
        ctx = make_ctx(author_id="nope")
        await Quests.rescancmd.callback(cog, ctx)
        ctx.send.assert_not_called()

    async def test_success_updates_state(self, cog):
        ctx = make_ctx()
        with patch(
            "modules.http_client.fetch_data", new=AsyncMock(return_value={"ok": 1})
        ):
            await Quests.rescancmd.callback(cog, ctx)
        ctx.send.assert_awaited_once_with("Scan de quests iniciado!")
        cog.poliswag.scanner_manager.update_quest_scanning_state.assert_called_once_with(
            0
        )

    async def test_failure_sends_error(self, cog):
        ctx = make_ctx()
        with patch("modules.http_client.fetch_data", new=AsyncMock(return_value=None)):
            await Quests.rescancmd.callback(cog, ctx)
        ctx.send.assert_awaited_once_with("Erro ao iniciar o scan de quests!")
        cog.poliswag.scanner_manager.update_quest_scanning_state.assert_not_called()


# --- questcmd -----------------------------------------------------------------


class TestQuestCmd:
    async def test_empty_search_prompts_user(self, cog):
        ctx = make_ctx()
        await Quests.questcmd.callback(cog, ctx, search="   ")
        ctx.send.assert_awaited_once()
        assert "necessário" in ctx.send.call_args.args[0]

    async def test_no_results_leiria(self, cog):
        ctx = make_ctx(invoked_with="questleiria")
        cog.poliswag.quest_search.find_quest_by_search_keyword.return_value = []
        await Quests.questcmd.callback(cog, ctx, search="pikachu")
        ctx.send.assert_awaited_once()
        assert "em Leiria" in ctx.send.call_args.args[0]

    async def test_no_results_marinha(self, cog):
        ctx = make_ctx(invoked_with="questmarinha")
        cog.poliswag.quest_search.find_quest_by_search_keyword.return_value = []
        await Quests.questcmd.callback(cog, ctx, search="pikachu")
        assert "na Marinha" in ctx.send.call_args.args[0]

    async def test_happy_path_sends_embeds_and_deletes_processing(self, cog):
        ctx = make_ctx(invoked_with="questleiria")
        processing = MagicMock()
        processing.delete = AsyncMock()
        ctx.send = AsyncMock(return_value=processing)

        cog.poliswag.quest_search.find_quest_by_search_keyword.return_value = [
            {"stop": 1}
        ]
        cog.poliswag.quest_search.group_pokestops_by_reward.return_value = {
            "bulbasaur": {
                "title": "Catch Bulbasaur",
                "reward_text": "Bulbasaur",
                "pokestops": [{"id": "a"}, {"id": "b"}],
            }
        }
        # Two geographic groups → two embed sends.
        cog.poliswag.quest_search.group_pokestops_geographically.return_value = [
            [{"id": "a"}],
            [{"id": "b"}],
        ]
        cog.poliswag.quest_search.create_quest_embed.return_value = MagicMock(
            set_image=MagicMock()
        )
        cog.poliswag.image_generator.generate_static_map_for_group_of_quests.return_value = (
            "http://map"
        )

        await Quests.questcmd.callback(cog, ctx, search="bulbasaur")

        # Initial processing send + two embed sends = 3 sends total.
        assert ctx.send.await_count == 3
        processing.delete.assert_awaited_once()
        assert cog.poliswag.quest_search.create_quest_embed.call_count == 2

    async def test_happy_path_without_map_url_skips_set_image(self, cog):
        ctx = make_ctx(invoked_with="questleiria")
        processing = MagicMock()
        processing.delete = AsyncMock()
        ctx.send = AsyncMock(return_value=processing)
        cog.poliswag.quest_search.find_quest_by_search_keyword.return_value = [
            {"stop": 1}
        ]
        cog.poliswag.quest_search.group_pokestops_by_reward.return_value = {
            "x": {"title": "T", "reward_text": "R", "pokestops": [{}]}
        }
        cog.poliswag.quest_search.group_pokestops_geographically.return_value = [[{}]]
        embed = MagicMock()
        cog.poliswag.quest_search.create_quest_embed.return_value = embed
        cog.poliswag.image_generator.generate_static_map_for_group_of_quests.return_value = (
            None
        )

        await Quests.questcmd.callback(cog, ctx, search="thing")

        embed.set_image.assert_not_called()


class TestLifecycle:
    async def test_cog_load_prints(self, cog, capsys):
        await cog.cog_load()
        assert "Quests loaded" in capsys.readouterr().out

    async def test_cog_unload_prints(self, cog, capsys):
        await cog.cog_unload()
        assert "Quests unloaded" in capsys.readouterr().out
