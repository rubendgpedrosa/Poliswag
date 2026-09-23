"""The announcer's message and recording paths.

The module had no tests at all, which is how a KeyError reached production in
the one branch that only fires for a busy list: more than _DETAIL_LIMIT rows
for a single person. Every test here covers a line that was uncovered.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from modules.config import Config
from modules.trade_announcer import TradeAnnouncer, _DETAIL_LIMIT


def row(**patch):
    """A row shaped exactly like _read_batch's SELECT produces one."""
    out = {
        "holder_id": 1,
        "wanter_id": 2,
        "pokemon_id": 25,
        "form_id": 0,
        "category": "normal",
        "have_created_at": None,
        "want_created_at": None,
        "actor_id": 1,
        "holder_username": "holder",
        "holder_display_name": "Rui",
        "wanter_username": "wanter",
        "wanter_display_name": "Ana",
        "pokemon_name": "Pikachu",
        "form_name": None,
    }
    out.update(patch)
    return out


def announcer():
    return TradeAnnouncer(MagicMock())


class FakeCursor:
    """Returns a queued result per execute(), in order."""

    def __init__(self, results):
        self._results = list(results)
        self._current = None
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._current = self._results.pop(0) if self._results else None

    def fetchone(self):
        return self._current

    def fetchall(self):
        return self._current or []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_db(results):
    db = MagicMock()
    cursor = FakeCursor(results)
    db.cursor.return_value = cursor
    db._cursor = cursor
    return db


class TestMessageNamesTheActor:
    """The collapsed embed has to say whose lists changed.

    `actor` is whichever side posted last, so the name lives under
    holder_display_name or wanter_display_name -- never a bare display_name.
    """

    def test_collapses_past_the_detail_limit(self):
        rows = [row(wanter_id=100 + i) for i in range(_DETAIL_LIMIT + 1)]
        _content, embed, _mentions, collapsed = announcer()._message("1", rows)
        assert collapsed
        assert embed is not None

    def test_names_the_holder_when_the_holder_posted_last(self):
        rows = [
            row(actor_id=1, holder_id=1, wanter_id=100 + i)
            for i in range(_DETAIL_LIMIT + 1)
        ]
        _content, embed, _mentions, _collapsed = announcer()._message("1", rows)
        assert "Rui" in embed.description

    def test_names_the_wanter_when_the_wanter_posted_last(self):
        rows = [
            row(actor_id=2, holder_id=1, wanter_id=2, pokemon_id=25 + i)
            for i in range(_DETAIL_LIMIT + 1)
        ]
        _content, embed, _mentions, _collapsed = announcer()._message("2", rows)
        assert "Ana" in embed.description

    def test_counts_the_rows_it_collapsed(self):
        rows = [row(wanter_id=100 + i) for i in range(_DETAIL_LIMIT + 1)]
        _content, embed, _mentions, _collapsed = announcer()._message("1", rows)
        assert str(_DETAIL_LIMIT + 1) in embed.description


class TestMessageReadsLikeTheSite:
    """One pair of lists meeting, said the way the site says it."""

    def test_the_title_names_the_actor_rather_than_a_raw_mention(self):
        # Discord doesn't render <@id> in an embed title: it showed the number.
        _content, embed, _mentions, _collapsed = announcer()._message("1", [row()])
        assert "Rui" in embed.title
        assert "<@" not in embed.title

    def test_a_line_says_which_kind_of_pokemon(self):
        _content, embed, _mentions, _collapsed = announcer()._message(
            "1", [row(category="shiny")]
        )
        assert "Pikachu · Shiny" in embed.description

    def test_a_plain_pokemon_has_no_label(self):
        _content, embed, _mentions, _collapsed = announcer()._message(
            "1", [row(category="normal")]
        )
        assert "Pikachu —" in embed.description

    def test_one_holder_tem(self):
        # The wanter posted last, so the line names who has it: one person.
        _content, embed, _mentions, _collapsed = announcer()._message(
            "2", [row(actor_id=2)]
        )
        assert "— tem: <@1>" in embed.description


class TestMessageLinksTheConfiguredChannel:
    """The channel comes from config, not from a snowflake pasted in source."""

    def test_uses_the_configured_channel_id(self):
        with patch.object(Config, "TRADES_CHANNEL_ID", 424242):
            content, _embed, _mentions, _collapsed = announcer()._message("1", [row()])
        assert "<#424242>" in content


class TestAFailedSendDoesNotReAnnounce:
    """A send that throws mid-batch must not re-announce what already went out.

    Nothing was recorded until the whole batch had been sent, so one failing
    group meant every group before it was announced again on the next tick,
    and again every 60s after that.
    """

    def test_groups_sent_before_the_failure_are_recorded(self):
        ta = announcer()
        first = row(actor_id=1, holder_id=1, wanter_id=10)
        second = row(actor_id=2, holder_id=2, wanter_id=20)

        recorded = []
        ta._read_batch = lambda: {
            "cutoff": "2026-09-21 10:00:00",
            "initialise": False,
            "connections": [first, second],
        }
        ta._record_notices = lambda rows: recorded.extend(rows)
        ta._advance_watermark = lambda cutoff: None

        channel = MagicMock()
        sent = []

        async def send(**kwargs):
            sent.append(kwargs)
            if len(sent) == 2:
                raise RuntimeError("discord is down")

        channel.send = send
        ta.poliswag.get_channel.return_value = channel

        with patch.object(Config, "TRADES_CHANNEL_ID", 1):
            with pytest.raises(RuntimeError):
                asyncio.run(ta.tick())

        assert first in recorded, "the group that was sent must be recorded"
        assert second not in recorded, "the group that failed must not be"

    def test_the_watermark_stays_put_when_a_send_fails(self):
        """Advancing it would skip the group that never got announced."""
        ta = announcer()
        ta._read_batch = lambda: {
            "cutoff": "2026-09-21 10:00:00",
            "initialise": False,
            "connections": [row(actor_id=1), row(actor_id=2)],
        }
        ta._record_notices = lambda rows: None
        advanced = []
        ta._advance_watermark = lambda cutoff: advanced.append(cutoff)

        channel = MagicMock()

        async def send(**kwargs):
            raise RuntimeError("discord is down")

        channel.send = send
        ta.poliswag.get_channel.return_value = channel

        with patch.object(Config, "TRADES_CHANNEL_ID", 1):
            with pytest.raises(RuntimeError):
                asyncio.run(ta.tick())

        assert advanced == []


class TestReadBatchSurvivesAnEmptySettingsRow:
    """poliswag.poliswag holding no row is a TypeError, not a crash-loop."""

    def test_no_settings_row_initialises_instead_of_raising(self):
        ta = announcer()
        # cutoff row, then nothing for the watermark SELECT
        ta._connect = lambda: fake_db([{"cutoff": "2026-09-21 10:00:00"}, None])
        batch = ta._read_batch()
        assert batch["initialise"]


class TestConnectionsCannotHangTheScheduler:
    """A read with no timeout stalls the 60s loop, and _check_workers with it."""

    def test_connect_sets_timeouts(self):
        with patch("pymysql.connect") as connect:
            announcer()._connect()
        kwargs = connect.call_args.kwargs
        assert kwargs["connect_timeout"]
        assert kwargs["read_timeout"]
        assert kwargs["write_timeout"]
