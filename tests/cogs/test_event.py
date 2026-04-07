"""Tests for cogs.event.EventExclusion.

Same shape as test_tracker.py — the cog builds an EventStore at __init__
time so we patch that constructor and replace ``cog.event_store`` with a
MagicMock. ``build_excluded_list_embed`` is patched at the import site.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.event import EventExclusion


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.ADMIN_USERS_IDS = ["42"]
    poliswag.db = MagicMock()
    with patch("cogs.event.EventStore"):
        c = EventExclusion(poliswag)
    c.event_store = MagicMock()
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


class TestExcludeEvent:
    async def test_already_excluded_short_circuits(self, cog):
        ctx = make_ctx()
        cog.event_store.is_excluded.return_value = True
        await EventExclusion.exclude_event.callback(cog, ctx, event_type="Raid")
        cog.event_store.add_excluded.assert_not_called()
        ctx.send.assert_awaited_once()
        assert "já está" in ctx.send.call_args.args[0]

    async def test_new_excludes_and_sends_confirmation_and_list(self, cog):
        ctx = make_ctx()
        cog.event_store.is_excluded.return_value = False
        with patch(
            "cogs.event.build_excluded_list_embed", new=AsyncMock(return_value="E")
        ):
            await EventExclusion.exclude_event.callback(cog, ctx, event_type="Raid")
        cog.event_store.add_excluded.assert_called_once_with("raid")
        assert ctx.send.await_count == 2


class TestIncludeEvent:
    async def test_nothing_to_remove(self, cog):
        ctx = make_ctx()
        cog.event_store.remove_excluded.return_value = 0
        await EventExclusion.include_event.callback(cog, ctx, event_type="Raid")
        ctx.send.assert_awaited_once()
        assert "não estava" in ctx.send.call_args.args[0]

    async def test_removes_and_sends_confirmation_and_list(self, cog):
        ctx = make_ctx()
        cog.event_store.remove_excluded.return_value = 1
        with patch(
            "cogs.event.build_excluded_list_embed", new=AsyncMock(return_value="E")
        ):
            await EventExclusion.include_event.callback(cog, ctx, event_type="Raid")
        assert ctx.send.await_count == 2


class TestExcludeClearAllEvents:
    async def test_clears_and_reports_count(self, cog):
        ctx = make_ctx()
        cog.event_store.clear_excluded.return_value = 5
        await EventExclusion.exclude_clear_all_events.callback(cog, ctx)
        cog.event_store.clear_excluded.assert_called_once()
        ctx.send.assert_awaited_once()


class TestExcludedList:
    async def test_sends_embed(self, cog):
        ctx = make_ctx()
        with patch(
            "cogs.event.build_excluded_list_embed", new=AsyncMock(return_value="E")
        ):
            await EventExclusion.excluded_list.callback(cog, ctx)
        ctx.send.assert_awaited_once_with(embed="E")


class TestEventTypes:
    async def test_empty_list_sends_notice(self, cog):
        ctx = make_ctx()
        cog.event_store.get_all_event_types.return_value = []
        await EventExclusion.event_types.callback(cog, ctx)
        ctx.send.assert_awaited_once()
        assert "Não foram encontrados" in ctx.send.call_args.args[0]

    async def test_populated_list_sends_embed(self, cog):
        ctx = make_ctx()
        cog.event_store.get_all_event_types.return_value = [
            {"event_type": "Raid"},
            {"event_type": "Community Day"},
        ]
        await EventExclusion.event_types.callback(cog, ctx)
        ctx.send.assert_awaited_once()
        _, kwargs = ctx.send.call_args
        assert "embed" in kwargs


class TestLifecycle:
    async def test_cog_load_prints(self, cog, capsys):
        await cog.cog_load()
        assert "EventExclusion loaded" in capsys.readouterr().out

    async def test_cog_unload_prints(self, cog, capsys):
        await cog.cog_unload()
        assert "EventExclusion unloaded" in capsys.readouterr().out
