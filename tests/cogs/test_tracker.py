"""Tests for cogs.tracker.Tracker.

Instantiation builds a TrackerStore from poliswag.db; we replace the
``cog.tracker_store`` attribute after construction with a MagicMock.
``build_tracked_list_embed`` is patched at the import site inside
cogs.tracker.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.tracker import Tracker


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.ADMIN_USERS_IDS = ["42"]
    poliswag.db = MagicMock()
    poliswag.quest_search.check_tracked = AsyncMock()
    with patch("cogs.tracker.TrackerStore"):
        c = Tracker(poliswag)
    c.tracker_store = MagicMock()
    return c


def make_ctx(author_id="42"):
    ctx = MagicMock()
    ctx.author.id = author_id
    ctx.author.name = "tester"
    ctx.send = AsyncMock()
    return ctx


class TestCogCheck:
    def test_admin_passes(self, cog):
        assert cog.cog_check(make_ctx()) is True

    def test_non_admin_fails(self, cog):
        assert cog.cog_check(make_ctx(author_id="nope")) is False


class TestTrack:
    async def test_already_tracked_short_circuits(self, cog):
        ctx = make_ctx()
        cog.tracker_store.exists.return_value = True
        await Tracker.track.callback(cog, ctx, search_string="Pikachu")
        cog.tracker_store.add.assert_not_called()
        ctx.send.assert_awaited_once()
        assert "já está" in ctx.send.call_args.args[0]

    async def test_new_track_adds_and_sends_two_embeds(self, cog):
        ctx = make_ctx()
        cog.tracker_store.exists.return_value = False
        with patch(
            "cogs.tracker.build_tracked_list_embed", new=AsyncMock(return_value="EMBED")
        ):
            await Tracker.track.callback(cog, ctx, search_string="Pikachu")
        cog.tracker_store.add.assert_called_once_with("pikachu", "tester")
        assert ctx.send.await_count == 2


class TestUntrack:
    async def test_not_tracked_sends_notice(self, cog):
        ctx = make_ctx()
        cog.tracker_store.remove.return_value = 0
        await Tracker.untrack.callback(cog, ctx, search_string="Pikachu")
        ctx.send.assert_awaited_once()
        assert "não está" in ctx.send.call_args.args[0]

    async def test_removes_and_sends_confirmation_and_list(self, cog):
        ctx = make_ctx()
        cog.tracker_store.remove.return_value = 1
        with patch(
            "cogs.tracker.build_tracked_list_embed", new=AsyncMock(return_value="E")
        ):
            await Tracker.untrack.callback(cog, ctx, search_string="Pikachu")
        assert ctx.send.await_count == 2


class TestUntrackAll:
    async def test_clears_and_reports_count(self, cog):
        ctx = make_ctx()
        cog.tracker_store.clear.return_value = 7
        await Tracker.untrack_all.callback(cog, ctx)
        cog.tracker_store.clear.assert_called_once()
        ctx.send.assert_awaited_once()


class TestTrackList:
    async def test_sends_embed(self, cog):
        ctx = make_ctx()
        with patch(
            "cogs.tracker.build_tracked_list_embed", new=AsyncMock(return_value="E")
        ):
            await Tracker.track_list.callback(cog, ctx)
        ctx.send.assert_awaited_once_with(embed="E")


class TestCheckTrackedByCmd:
    async def test_delegates_to_quest_search(self, cog):
        ctx = make_ctx()
        await Tracker.check_tracked_by_cmd.callback(cog, ctx)
        cog.poliswag.quest_search.check_tracked.assert_awaited_once_with(ctx)


class TestLifecycle:
    async def test_cog_load_prints(self, cog, capsys):
        await cog.cog_load()
        assert "Tracker loaded" in capsys.readouterr().out

    async def test_cog_unload_prints(self, cog, capsys):
        await cog.cog_unload()
        assert "Tracker unloaded" in capsys.readouterr().out
