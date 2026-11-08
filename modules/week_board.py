"""The weekly calendar as one image: data in, a view for templates/week-board.html.

Same design language and frame as the event board (modules/stat_card.py,
templates/_board-base.css), laid out as a calendar: the events already
running as a row of compact cards, then the next seven days as columns, each
new event a mini card with its game image, type, time and name.

The renderer is wkhtmltoimage's old WebKit, so every height is fixed here and
mirrored in the template's CSS: names are clamped to their lines rather than
measured.
"""

import datetime
from dataclasses import dataclass

from modules.locale_pt import PT_DAYS_SHORT
from modules.stat_card import BOARD_PAD, BORDER, RENDER_SCALE

# The site's page header (templates/_board-base.css .page-head).
PAGE_HEAD_H = 77
PAGE_HEAD_GAP = 20

DAYS = 7
DAY_W = 152
DAY_GAP = 12
CONTENT_W = DAYS * DAY_W + (DAYS - 1) * DAY_GAP  # 1136

SECTION_LABEL_H = 26  # 14 label + 12 margin
SECTION_GAP = 24

ONGOING_COLS = 4
ONGOING_GAP = 16
ONGOING_W = (CONTENT_W - (ONGOING_COLS - 1) * ONGOING_GAP) // ONGOING_COLS  # 272
ONGOING_H = 92  # 1 + 10 + 70 thumb + 10 + 1
ONGOING_ROW_GAP = 12
ONGOING_SHOWN = 8  # two rows; the rest are counted in the label

DAY_HEAD_H = 44  # 14 day + 2 + 22 date + 6
MINI_H = 174  # 1 + 72 banner + 10 + 14 kicker + 18 time + 48 name (3 lines) + 10 + 1
MINI_GAP = 10
EMPTY_H = 24


@dataclass(frozen=True)
class WeekEvent:
    title: str
    kicker: str  # ScrapedDuck's heading: "Raid Hour", "Community Day"
    image: str  # URL or data: URI; "" for a plain banner
    start: datetime.datetime
    end: datetime.datetime


def _date(moment, today):
    """dd/mm, with the year only when it isn't this one."""
    return moment.strftime("%d/%m" if moment.year == today.year else "%d/%m/%Y")


def _until(event, today):
    return f"até {PT_DAYS_SHORT[event.end.weekday()]} {_date(event.end, today)}"


def _hours(event, today):
    if event.end.date() == event.start.date():
        return f"{event.start:%H:%M} – {event.end:%H:%M}"
    return f"{event.start:%H:%M} · até {_date(event.end, today)}"


def split(events, now):
    """(running now, soonest to end first; [(date, events starting that day)]
    for the next seven days)."""
    today = now.date()
    ongoing = sorted((e for e in events if e.start <= now), key=lambda e: e.end)
    days = []
    for offset in range(DAYS):
        day = today + datetime.timedelta(days=offset)
        days.append(
            (
                day,
                sorted(
                    (e for e in events if e.start > now and e.start.date() == day),
                    key=lambda e: e.start,
                ),
            )
        )
    return ongoing, days


def day_height(count):
    if not count:
        return DAY_HEAD_H + EMPTY_H
    return DAY_HEAD_H + count * MINI_H + (count - 1) * MINI_GAP


def build_week_view(events, now, brand=""):
    """The template's view, or None when the week has nothing to show."""
    today = now.date()
    ongoing, days = split(events, now)
    upcoming = any(day_events for _day, day_events in days)
    if not ongoing and not upcoming:
        return None

    shown = ongoing[:ONGOING_SHOWN]
    ongoing_rows = -(-len(shown) // ONGOING_COLS)
    height = 2 * BOARD_PAD + 2 * BORDER + PAGE_HEAD_H + PAGE_HEAD_GAP
    if shown:
        height += (
            SECTION_LABEL_H
            + ongoing_rows * ONGOING_H
            + (ongoing_rows - 1) * ONGOING_ROW_GAP
        )
        if upcoming:
            height += SECTION_GAP
    if upcoming:
        height += SECTION_LABEL_H + max(day_height(len(e)) for _d, e in days)

    week_end = today + datetime.timedelta(days=DAYS - 1)
    return {
        "width": CONTENT_W + 2 * BOARD_PAD + 2 * BORDER,
        "height": height,
        "scale": RENDER_SCALE,
        "heading": "Eventos da semana",
        "date": f"{today:%d/%m} – {week_end:%d/%m}",
        "brand": brand,
        "ongoing": [
            {
                "title": e.title,
                "kicker": e.kicker,
                "image": e.image,
                "until": _until(e, today),
            }
            for e in shown
        ],
        "ongoing_more": len(ongoing) - len(shown),
        "ongoing_rows": [
            list(range(i, min(i + ONGOING_COLS, len(shown))))
            for i in range(0, len(shown), ONGOING_COLS)
        ],
        "days": (
            [
                {
                    "name": "Hoje" if day == today else PT_DAYS_SHORT[day.weekday()],
                    "date": f"{day:%d/%m}",
                    "today": day == today,
                    "events": [
                        {
                            "title": e.title,
                            "kicker": e.kicker,
                            "image": e.image,
                            "hours": _hours(e, today),
                        }
                        for e in day_events
                    ],
                }
                for day, day_events in days
            ]
            if upcoming
            else []
        ),
    }
