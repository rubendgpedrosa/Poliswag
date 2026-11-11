"""Tests for modules.event_notices.EventNotices.

The event posts the scheduler sends: "Novos eventos", "Eventos que terminaram"
and Monday's calendar. Moved here from tests/cogs/test_scheduled.py with the
code; the scheduling around them (_check_events, _check_weekly_digest) is still
tested there.

* EventNotices gets a stub poliswag (event manager, stats, renderer, channel).
* Patch ``datetime`` at the module level with a MagicMock when a test needs a
  deterministic "now".
"""

import datetime as real_datetime
import json
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from modules.event_notices import (
    EventNotices,
    _duration,
    _event_extras,
    _event_heading,
    _type_label,
)
from modules.event_stats import Summary
from modules.stat_card import StatCard

SUMMARY = Summary(
    headline="🐾 **5** spawns · Gible",
    areas=(("📍 Leiria", "3 spawns\n💯 1 100IV"), ("📍 Marinha Grande", "sem dados")),
    note="Totais diários de 07/04 · incluem atividade fora do horário do evento.",
)

# --- fixtures -----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _images_as_is(monkeypatch):
    async def image_as_is(url):
        return url

    # Cards check their picture over HTTP; tests/modules/test_event_images.py
    # covers that. Here every image is taken as it is.
    monkeypatch.setattr("modules.event_notices.usable_image", image_as_is)


def _make_poliswag():
    poliswag = MagicMock()
    poliswag.CONVIVIO_CHANNEL = MagicMock()
    poliswag.CONVIVIO_CHANNEL.send = AsyncMock()
    poliswag.utility.log_to_file = MagicMock()
    poliswag.event_manager.mark_event_notified = AsyncMock()
    poliswag.event_manager.get_event_type_key = MagicMock(return_value="community-day")
    poliswag.event_manager.get_event_emoji = MagicMock(return_value="🌟")
    poliswag.event_manager.get_event_link = MagicMock(return_value="http://link")
    poliswag.event_manager.format_end_time = MagicMock(return_value="Termina em 1h")
    poliswag.event_manager.event_colors = {"community-day": 0xFFCC00}
    poliswag.event_manager.get_weekly_events = AsyncMock(return_value=[])
    poliswag.event_stats.get_summary = AsyncMock(return_value=None)
    # No renderer in tests unless one asks: events use the text cards.
    poliswag.image_generator.generate_stat_board = AsyncMock(return_value=None)
    poliswag.image_generator.generate_week_board = AsyncMock(return_value=None)
    return poliswag


@pytest.fixture
def notices():
    return EventNotices(_make_poliswag())


# --- send_change_notifications ------------------------------------------------


class TestSendEventChangeNotifications:
    async def test_ended_then_started(self, notices):
        channel = MagicMock()
        channel.send = AsyncMock()
        event = {
            "event_type": "Community Day",
            "name": "Test",
            "end": "2026-04-07 20:00:00",
            "image": None,
        }
        changed = {"ended": [event], "started": [event]}
        notices.poliswag.event_stats.get_summary = AsyncMock(return_value=SUMMARY)
        await notices.send_change_notifications(channel, changed)
        # One message per batch: the header with its cards as embeds.
        calls = channel.send.await_args_list
        assert len(calls) == 2
        assert calls[0].args == ("**Eventos que terminaram**",)
        assert len(calls[0].kwargs["embeds"]) == 1
        assert calls[1].args == ("**Novos eventos**",)
        assert len(calls[1].kwargs["embeds"]) == 1

    async def test_ended_without_stats_gets_a_card_like_new_events(self, notices):
        channel = MagicMock()
        channel.send = AsyncMock()
        gbl = {
            "event_type": "go-battle-league",
            "name": "Great League",
            "end": "2026-04-07 20:00:00",
            "image": "https://example.test/gbl.jpg",
        }
        cd = {
            "event_type": "community-day",
            "name": "Gible Community Day",
            "end": "2026-04-07 20:00:00",
            "image": None,
        }
        notices.poliswag.event_stats.get_summary = AsyncMock(
            side_effect=lambda event: SUMMARY if event is cd else None
        )
        notices.poliswag.event_manager.format_end_time = MagicMock(
            side_effect=lambda end, verb="Termina": f"{verb} às 20:00"
        )
        await notices.send_change_notifications(
            channel, {"ended": [gbl, cd], "started": []}
        )
        calls = channel.send.await_args_list
        assert len(calls) == 1
        # The same shape as "Novos eventos": the header, then a card per event.
        assert calls[0].args == ("**Eventos que terminaram**",)
        plain, card = calls[0].kwargs["embeds"]
        assert "Great League" in plain.title
        assert plain.description == "Terminou às 20:00"
        assert plain.thumbnail.url == "https://example.test/gbl.jpg"
        assert card.description == SUMMARY.headline
        assert "Gible" in card.title

    async def test_more_than_ten_ended_cards_continue_in_another_message(self, notices):
        channel = MagicMock()
        channel.send = AsyncMock()
        events = [
            {"event_type": "research", "name": f"R{i}", "end": "2026-04-07 20:00:00"}
            for i in range(12)
        ]
        notices.poliswag.event_stats.get_summary = AsyncMock(return_value=None)
        await notices.send_change_notifications(
            channel, {"ended": events, "started": []}
        )
        calls = channel.send.await_args_list
        assert [len(c.kwargs["embeds"]) for c in calls] == [10, 2]
        assert calls[0].args == ("**Eventos que terminaram**",)
        assert calls[1].args == ()

    # "Super Mega Raid Day" and "Staraptor Super Mega Raid Day": one event
    # under two names, one card.
    async def test_identical_stats_are_one_card(self, notices):
        channel = MagicMock()
        channel.send = AsyncMock()
        a = {
            "event_type": "raid-day",
            "name": "Super Mega Raid Day",
            "end": "2026-04-07 17:00:00",
        }
        b = {**a, "name": "Staraptor Super Mega Raid Day"}
        notices.poliswag.event_stats.get_summary = AsyncMock(return_value=SUMMARY)
        await notices.send_change_notifications(
            channel, {"ended": [a, b], "started": []}
        )
        [call] = channel.send.await_args_list
        assert len(call.kwargs["embeds"]) == 1

    async def test_more_than_ten_cards_continue_in_another_message(self, notices):
        channel = MagicMock()
        channel.send = AsyncMock()
        events = [
            {"event_type": "event", "name": f"E{i}", "end": "2026-04-07 20:00:00"}
            for i in range(12)
        ]
        await notices.send_change_notifications(
            channel, {"ended": [], "started": events}
        )
        calls = channel.send.await_args_list
        assert [len(c.kwargs["embeds"]) for c in calls] == [10, 2]
        assert calls[0].args == ("**Novos eventos**",)
        assert calls[1].args == ()

    async def test_empty_lists_send_nothing(self, notices):
        channel = MagicMock()
        channel.send = AsyncMock()
        await notices.send_change_notifications(channel, {"ended": [], "started": []})
        channel.send.assert_not_called()


# --- the event boards and build_event_embed ----------------------------------


class TestEndedBoard:
    EVENT = {
        "event_type": "raid-battles",
        "name": "Mega Sableye in Mega Raids",
        "end": "2026-11-08 22:00:00",
        "image": "http://img",
        "extra_data": '{"heading": "Raid Battles"}',
    }
    CARD = StatCard(kicker="Raids Mega", hero=297, hero_unit="raids", subject="Sableye")

    async def test_one_image_per_event_like_the_embeds(self, notices):
        channel = MagicMock()
        channel.send = AsyncMock()
        gbl = {**self.EVENT, "name": "Great League", "extra_data": None}
        notices.poliswag.event_stats.get_summary = AsyncMock(
            side_effect=lambda e: (
                Summary("h", (), "n", self.CARD) if e is self.EVENT else None
            )
        )
        notices.poliswag.event_manager.format_end_time = MagicMock(
            return_value="Terminou às 22:00"
        )
        notices.poliswag.image_generator.generate_stat_board = AsyncMock(
            return_value=b"png"
        )
        await notices.send_change_notifications(
            channel, {"ended": [self.EVENT, gbl], "started": []}, acknowledge=True
        )
        first, second = channel.send.await_args_list
        # The post's title on the first message, as before; no embeds.
        assert first.args == ("**Eventos que terminaram**",)
        assert second.args == (None,)
        assert [c.kwargs["file"].filename for c in (first, second)] == [
            "evento-terminado-1.png",
            "evento-terminado-2.png",
        ]
        assert all("embeds" not in c.kwargs for c in (first, second))
        renders = notices.poliswag.image_generator.generate_stat_board.await_args_list
        # Each image is one bare panel: no board heading.
        assert [c.args[1] for c in renders] == ["", ""]
        [stats], [plain] = (c.args[0] for c in renders)
        # The game image and name on every panel; stats where there are some.
        assert (stats.title, stats.image, stats.hero) == (
            self.EVENT["name"],
            "http://img",
            297,
        )
        assert (plain.title, plain.hero, plain.kicker, plain.ended) == (
            "Great League",
            None,
            "Evento",
            "Terminou às 22:00",
        )
        assert notices.poliswag.event_manager.mark_event_notified.await_count == 2

    async def test_a_failed_render_falls_back_to_the_text_cards(self, notices):
        channel = MagicMock()
        channel.send = AsyncMock()
        notices.poliswag.event_stats.get_summary = AsyncMock(
            return_value=Summary(
                SUMMARY.headline, SUMMARY.areas, SUMMARY.note, self.CARD
            )
        )
        notices.poliswag.image_generator.generate_stat_board = AsyncMock(
            return_value=None
        )
        await notices.send_change_notifications(
            channel, {"ended": [self.EVENT], "started": []}
        )
        [call] = channel.send.await_args_list
        assert call.args == ("**Eventos que terminaram**",)
        [embed] = call.kwargs["embeds"]
        assert embed.description == SUMMARY.headline

    async def test_one_failed_render_sends_the_whole_post_as_text(self, notices):
        channel = MagicMock()
        channel.send = AsyncMock()
        events = [{**self.EVENT, "name": f"E{i}"} for i in range(3)]
        notices.poliswag.image_generator.generate_stat_board = AsyncMock(
            side_effect=[b"png", None, b"png"]
        )
        await notices.send_change_notifications(
            channel, {"ended": events, "started": []}
        )
        [call] = channel.send.await_args_list
        assert call.args == ("**Eventos que terminaram**",)
        assert len(call.kwargs["embeds"]) == 3

    def test_heading_comes_from_scrapedduck(self):
        assert _event_heading(self.EVENT) == "Raid Battles"
        assert _event_heading({"extra_data": "not json"}) == "Evento"


class TestStartedBoard:
    CD = {
        "event_type": "community-day",
        "name": "Pawniard Community Day",
        "start": "2026-11-15 14:00:00",
        "end": "2026-11-15 17:00:00",
        "image": "http://cd.jpg",
        "extra_data": json.dumps(
            {
                "heading": "Community Day",
                "extraData": {
                    "communityday": {
                        "spawns": [{"name": "Pawniard", "image": "http://624.png"}],
                        "shinies": [{"name": "Pawniard", "image": "http://624s.png"}],
                        "bonuses": [{"text": "3-hour Incense"}, {"text": ""}],
                    }
                },
            }
        ),
    }
    RAIDS = {
        "event_type": "raid-battles",
        "name": "Mega Metagross in Mega Raids",
        "start": "2026-11-24 10:00:00",
        "end": "2026-12-01 22:00:00",
        "image": None,
        "extra_data": json.dumps(
            {
                "heading": "Raid Battles",
                "extraData": {
                    "raidbattles": {
                        "bosses": [
                            {
                                "name": "Mega Metagross",
                                "image": "http://m.png",
                                "canBeShiny": True,
                            }
                        ]
                    }
                },
            }
        ),
    }

    async def test_a_one_day_event_shows_its_hours(self, notices):
        panel = await notices._started_panel(self.CD)
        assert panel.hero is None and panel.kicker == "Community Day"
        assert panel.chips == (("Horário", "14:00 – 17:00"), ("Duração", "3 h"))
        assert panel.pokemon == (("Pawniard", "http://624.png", True),)
        assert panel.bonuses == ("3-hour Incense",)
        assert panel.image == "http://cd.jpg"

    async def test_a_longer_event_shows_until_when(self, notices):
        panel = await notices._started_panel(self.RAIDS)
        assert panel.chips == (("Até", "Ter 01/12 · 22:00"), ("Duração", "8 dias"))
        assert panel.pokemon == (("Mega Metagross", "http://m.png", True),)
        assert panel.image == ""

    async def test_new_events_one_image_each(self, notices):
        channel = MagicMock()
        channel.send = AsyncMock()
        notices.poliswag.image_generator.generate_stat_board = AsyncMock(
            return_value=b"png"
        )
        await notices.send_change_notifications(
            channel, {"ended": [], "started": [self.CD, self.RAIDS]}, acknowledge=True
        )
        first, second = channel.send.await_args_list
        assert first.args == ("**Novos eventos**",)
        assert [c.kwargs["file"].filename for c in (first, second)] == [
            "novo-evento-1.png",
            "novo-evento-2.png",
        ]
        acked = notices.poliswag.event_manager.mark_event_notified.await_args_list
        assert [c.kwargs["is_end"] for c in acked] == [False, False]

    @pytest.mark.parametrize(
        "minutes,text",
        [
            (60, "1 h"),
            (210, "3 h 30 min"),
            (45, "45 min"),
            (24 * 60, "1 dia"),
            (7 * 24 * 60, "7 dias"),
        ],
    )
    def test_duration(self, minutes, text):
        assert _duration(real_datetime.timedelta(minutes=minutes)) == text

    def test_extras_without_scrapedduck_data(self):
        assert _event_extras({"extra_data": None}) == ((), ())


class TestWeekBoard:
    async def test_the_week_is_one_image(self, notices):
        channel = MagicMock()
        channel.send = AsyncMock()
        notices.poliswag.event_manager.get_weekly_events = AsyncMock(
            return_value=[
                {
                    "name": "Raid Hour",
                    "event_type": "raid-hour",
                    "start": "2099-01-01 18:00:00",
                    "end": "2099-01-01 19:00:00",
                    "image": "http://rh.jpg",
                },
                {
                    "name": "Great League",
                    "event_type": "go-battle-league",
                    "start": "2099-01-01 18:00:00",
                    "end": "2099-01-08 19:00:00",
                },
            ]
        )
        notices.poliswag.image_generator.generate_week_board = AsyncMock(
            return_value=b"png"
        )
        assert await notices.send_weekly_digest(channel=channel) is True
        [call] = channel.send.await_args_list
        assert call.kwargs["file"].filename == "eventos-da-semana.png"
        events, _now = (
            notices.poliswag.image_generator.generate_week_board.await_args.args
        )
        # GO Battle League stays out, as in the text calendar.
        assert [(e.title, e.kicker, e.image) for e in events] == [
            ("Raid Hour", "Raid Hour", "http://rh.jpg")
        ]

    @pytest.mark.parametrize(
        "event_type,label",
        [
            ("raid-hour", "Raid Hour"),
            ("pokemon-spotlight-hour", "Spotlight Hour"),
            ("go-pass", "GO Pass"),
            ("season", "Temporada"),
            ("", "Evento"),
        ],
    )
    def test_type_labels(self, event_type, label):
        assert _type_label(event_type) == label


class TestBuildEventEmbed:
    async def test_ended_event_lays_the_stats_out_in_columns(self, notices):
        event = {
            "event_type": "Community Day",
            "name": "Test",
            "end": "2026-04-07 20:00:00",
            "image": None,
        }
        embed = await notices.build_event_embed(event, is_ended=True, summary=SUMMARY)
        assert isinstance(embed, discord.Embed)
        assert "Test" in embed.title
        # The headline on top, one column per area side by side, the caveat
        # small at the bottom: one line per area was too dense to read.
        assert embed.description == SUMMARY.headline
        assert [(f.name, f.value, f.inline) for f in embed.fields] == [
            ("📍 Leiria", "3 spawns\n💯 1 100IV", True),
            ("📍 Marinha Grande", "sem dados", True),
        ]
        assert embed.footer.text == SUMMARY.note
        notices.poliswag.event_manager.format_end_time.assert_not_called()

    async def test_ended_event_without_stats_says_when_it_ended(self, notices):
        event = {
            "event_type": "Community Day",
            "name": "Test",
            "end": "2026-04-07 20:00:00",
            "image": None,
        }
        notices.poliswag.event_manager.format_end_time = MagicMock(
            return_value="Terminou às 20:00"
        )
        embed = await notices.build_event_embed(event, is_ended=True)
        assert embed.description == "Terminou às 20:00"
        assert notices.poliswag.event_manager.format_end_time.call_args.kwargs == {
            "verb": "Terminou"
        }

    async def test_started_event_with_image(self, notices):
        event = {
            "event_type": "Community Day",
            "name": "Test",
            "end": "2026-04-07 20:00:00",
            "image": "http://img",
        }
        embed = await notices.build_event_embed(event, is_ended=False)
        assert embed.thumbnail.url == "http://img"
        notices.poliswag.event_manager.format_end_time.assert_called_once_with(
            real_datetime.datetime(2026, 4, 7, 20, 0)
        )
        notices.poliswag.event_stats.get_summary.assert_not_called()


# --- send_weekly_digest -------------------------------------------------------


class TestSendWeeklyDigest:
    async def test_no_events_returns_false(self, notices):
        notices.poliswag.event_manager.get_weekly_events.return_value = []
        assert (await notices.send_weekly_digest()) is False

    async def test_filters_battle_and_league(self, notices):
        notices.poliswag.event_manager.get_weekly_events.return_value = [
            {
                "event_type": "Go Battle League",
                "name": "GBL",
                "start": "2026-04-07 00:00:00",
                "end": "2026-04-07 23:59:00",
            },
            {
                "event_type": "PvP League",
                "name": "PVP",
                "start": "2026-04-07 00:00:00",
                "end": "2026-04-07 23:59:00",
            },
        ]
        assert (await notices.send_weekly_digest()) is False
        notices.poliswag.CONVIVIO_CHANNEL.send.assert_not_called()

    async def test_ongoing_and_upcoming_send_embed(self, notices):
        now = real_datetime.datetime(2026, 4, 7, 12, 0)
        past = now - real_datetime.timedelta(hours=1)
        future = now + real_datetime.timedelta(days=1, hours=2)
        future_end = future + real_datetime.timedelta(hours=3)
        notices.poliswag.event_manager.get_weekly_events.return_value = [
            {
                "event_type": "Community Day",
                "name": "Ongoing",
                "start": past.strftime("%Y-%m-%d %H:%M:%S"),
                "end": (now + real_datetime.timedelta(hours=2)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            },
            {
                "event_type": "Spotlight Hour",
                "name": "Upcoming",
                "start": future.strftime("%Y-%m-%d %H:%M:%S"),
                "end": future_end.strftime("%Y-%m-%d %H:%M:%S"),
            },
        ]
        with patch("modules.event_notices.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.datetime.strptime = real_datetime.datetime.strptime
            mock_dt.timedelta = real_datetime.timedelta
            result = await notices.send_weekly_digest()
        assert result is True
        notices.poliswag.CONVIVIO_CHANNEL.send.assert_awaited_once()

    async def test_today_upcoming_uses_hoje_label(self, notices):
        now = real_datetime.datetime(2026, 4, 7, 9, 0)
        future_today = now + real_datetime.timedelta(hours=6)
        notices.poliswag.event_manager.get_weekly_events.return_value = [
            {
                "event_type": "Raid Hour",
                "name": "Tonight",
                "start": future_today.strftime("%Y-%m-%d %H:%M:%S"),
                "end": (future_today + real_datetime.timedelta(hours=1)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        ]
        with patch("modules.event_notices.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.datetime.strptime = real_datetime.datetime.strptime
            mock_dt.timedelta = real_datetime.timedelta
            await notices.send_weekly_digest()
        embed = notices.poliswag.CONVIVIO_CHANNEL.send.call_args.kwargs["embed"]
        assert "**Hoje " in embed.description

    async def test_explicit_channel_overrides_default(self, notices):
        now = real_datetime.datetime(2026, 4, 7, 12, 0)
        notices.poliswag.event_manager.get_weekly_events.return_value = [
            {
                "event_type": "Community Day",
                "name": "X",
                "start": (now - real_datetime.timedelta(hours=1)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "end": (now + real_datetime.timedelta(hours=1)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        ]
        override = MagicMock()
        override.send = AsyncMock()
        with patch("modules.event_notices.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.datetime.strptime = real_datetime.datetime.strptime
            mock_dt.timedelta = real_datetime.timedelta
            await notices.send_weekly_digest(channel=override)
        override.send.assert_awaited_once()
        notices.poliswag.CONVIVIO_CHANNEL.send.assert_not_called()

    async def test_multi_day_event_end_uses_date_prefix(self, notices):
        now = real_datetime.datetime(2026, 4, 7, 12, 0)
        start = now + real_datetime.timedelta(days=1)
        end = start + real_datetime.timedelta(days=2)
        notices.poliswag.event_manager.get_weekly_events.return_value = [
            {
                "event_type": "Community Day",
                "name": "MultiDay",
                "start": start.strftime("%Y-%m-%d %H:%M:%S"),
                "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            }
        ]
        with patch("modules.event_notices.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.datetime.strptime = real_datetime.datetime.strptime
            mock_dt.timedelta = real_datetime.timedelta
            await notices.send_weekly_digest()
        embed = notices.poliswag.CONVIVIO_CHANNEL.send.call_args.kwargs["embed"]
        # Multi-day end formatted as "dd/mm HH:MM" not just "HH:MM"
        assert end.strftime("%d/%m") in embed.description


class TestEventDeliveryFailures:
    @staticmethod
    def events(count=12):
        return [
            {
                "name": f"Event {i}",
                "event_type": "event",
                "start": "2026-09-26 10:00:00",
                "end": "2026-09-26 18:00:00",
            }
            for i in range(count)
        ]

    async def test_failed_first_send_leaves_all_events_retryable(self, notices):
        channel = MagicMock(
            send=AsyncMock(side_effect=RuntimeError("Discord unavailable"))
        )
        with pytest.raises(RuntimeError):
            await notices.send_change_notifications(
                channel, {"started": self.events(), "ended": []}, acknowledge=True
            )
        notices.poliswag.event_manager.mark_event_notified.assert_not_awaited()

    async def test_second_batch_failure_only_acknowledges_first_ten(self, notices):
        channel = MagicMock(
            send=AsyncMock(side_effect=[None, RuntimeError("Discord unavailable")])
        )
        events = self.events()
        with pytest.raises(RuntimeError):
            await notices.send_change_notifications(
                channel, {"started": events, "ended": []}, acknowledge=True
            )
        calls = notices.poliswag.event_manager.mark_event_notified.await_args_list
        assert [c.args[0] for c in calls] == events[:10]
        assert all(c.kwargs == {"is_end": False} for c in calls)

    async def test_plain_ended_messages_split_without_losing_events(self, notices):
        events = self.events(20)
        channel = MagicMock(send=AsyncMock())
        await notices.send_change_notifications(
            channel, {"started": [], "ended": events}, acknowledge=True
        )
        # A card per event, ten per message: every one delivered exactly once.
        embeds = [e for c in channel.send.await_args_list for e in c.kwargs["embeds"]]
        assert len(embeds) == len(events)
        names = [e.title.partition(" ")[2] for e in embeds]  # "<emoji> <name>"
        assert sorted(names) == sorted(ev["name"] for ev in events)
        assert [
            c.args[0]
            for c in notices.poliswag.event_manager.mark_event_notified.await_args_list
        ] == events

    async def test_duplicate_summary_acknowledges_both_only_after_delivery(
        self, notices
    ):
        events = self.events(2)
        notices.poliswag.event_stats.get_summary.return_value = SUMMARY
        channel = MagicMock(send=AsyncMock())
        await notices.send_change_notifications(
            channel, {"started": [], "ended": events}, acknowledge=True
        )
        assert len(channel.send.await_args.kwargs["embeds"]) == 1
        assert [
            c.args[0]
            for c in notices.poliswag.event_manager.mark_event_notified.await_args_list
        ] == events

    async def test_preview_never_acknowledges(self, notices):
        channel = MagicMock(send=AsyncMock())
        await notices.send_change_notifications(
            channel, {"started": self.events(), "ended": []}
        )
        notices.poliswag.event_manager.mark_event_notified.assert_not_awaited()


async def test_event_batches_respect_total_embed_characters(notices):
    events = TestEventDeliveryFailures.events(3)
    embeds = [discord.Embed(title=e["name"], description="x" * 3100) for e in events]
    channel = MagicMock(send=AsyncMock())
    await notices._send_event_batches(
        channel,
        "**Novos eventos**",
        embeds,
        [[e] for e in events],
        [],
        is_end=False,
        acknowledge=True,
    )
    assert len(channel.send.await_args_list) == 3
    assert all(
        sum(len(e) for e in c.kwargs["embeds"]) <= 6000
        for c in channel.send.await_args_list
    )
    assert all(
        c.kwargs["allowed_mentions"].everyone is False
        for c in channel.send.await_args_list
    )
    assert [
        c.args[0]
        for c in notices.poliswag.event_manager.mark_event_notified.await_args_list
    ] == events


async def test_long_event_name_fits_discord_title(notices):
    event = {**TestEventDeliveryFailures.events(1)[0], "name": "x" * 255}
    embed = await notices.build_event_embed(event)
    assert len(embed.title) == 256


class TestWeeklyDigestLimits:
    def _many_events(self, count):
        base = real_datetime.datetime(2026, 4, 7, 12, 0)
        events = []
        for i in range(count):
            start = base + real_datetime.timedelta(hours=i)
            end = start + real_datetime.timedelta(hours=3)
            events.append(
                {
                    "event_type": "Community Day",
                    "name": f"Event-{i:03d}-" + "x" * 80,
                    "start": start.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": end.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        return events

    async def test_oversized_calendar_splits_preserving_all_entries(self, notices):
        now = real_datetime.datetime(2026, 4, 7, 12, 0)
        events = self._many_events(80)
        notices.poliswag.event_manager.get_weekly_events = AsyncMock(
            return_value=events
        )
        with patch("modules.event_notices.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = now
            mock_dt.datetime.strptime = real_datetime.datetime.strptime
            mock_dt.timedelta = real_datetime.timedelta
            result = await notices.send_weekly_digest()
        assert result is True
        calls = notices.poliswag.CONVIVIO_CHANNEL.send.await_args_list
        assert len(calls) > 1
        for call in calls:
            embed = call.kwargs["embed"]
            assert len(embed.description) <= 4096
            assert len(embed) <= 6000
            assert call.kwargs["allowed_mentions"].everyone is False
        combined = "\n".join(c.kwargs["embed"].description for c in calls)
        for i in range(80):
            assert f"Event-{i:03d}-" in combined
