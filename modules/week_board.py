"""The weekly calendar as one image: data in, a view for templates/week-board.html.

Phone first (DESIGN.md: players read these outdoors, on a phone): one narrow
column that Discord shows near full width on a phone, not a wide grid it
shrinks until nothing reads. The site's page header, the events already
running, then each day that has something new, every event a row with its
game image, type, name and time. Days with nothing new are left out.

Same frame and tokens as the event board (templates/_board-base.css). The
renderer is wkhtmltoimage's old WebKit, so every height is fixed here and
mirrored in the template's CSS: names are clamped to two lines, not measured.
"""

import datetime
from dataclasses import dataclass

from modules.locale_pt import PT_DAYS_SHORT
from modules.stat_card import BOARD_PAD, BORDER, RENDER_SCALE

DAYS = 7
CONTENT_W = 432
WIDTH = CONTENT_W + 2 * BOARD_PAD + 2 * BORDER  # 482

# The site's page header (templates/_board-base.css .page-head).
PAGE_HEAD_H = 77
PAGE_HEAD_GAP = 20

SECTION_LABEL_H = 26  # 14 label + 12 margin
DAY_HEAD_H = 26  # 14 label + 12 margin
SECTION_GAP = 22  # between the running list and the first day, and between days

ROW_H = 94  # 1 + 10 + 72 thumb + 10 + 1
ROW_GAP = 8
THUMB_W = 128
ONGOING_SHOWN = 6  # the rest are counted in the label


@dataclass(frozen=True)
class WeekEvent:
    title: str
    kicker: str  # the event's type: "Raid Hour", "Community Day"
    image: str  # URL or data: URI; "" for a plain thumbnail
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
    for the next seven days, only days with something)."""
    today = now.date()
    ongoing = sorted((e for e in events if e.start <= now), key=lambda e: e.end)
    days = []
    for offset in range(DAYS):
        day = today + datetime.timedelta(days=offset)
        starting = sorted(
            (e for e in events if e.start > now and e.start.date() == day),
            key=lambda e: e.start,
        )
        if starting:
            days.append((day, starting))
    return ongoing, days


def rows_height(count):
    return count * ROW_H + (count - 1) * ROW_GAP


def build_week_view(events, now, brand=""):
    """The template's view, or None when the week has nothing to show."""
    today = now.date()
    ongoing, days = split(events, now)
    if not ongoing and not days:
        return None

    shown = ongoing[:ONGOING_SHOWN]
    blocks = []
    if shown:
        blocks.append(SECTION_LABEL_H + rows_height(len(shown)))
    blocks += [DAY_HEAD_H + rows_height(len(day_events)) for _d, day_events in days]
    height = (
        2 * BOARD_PAD
        + 2 * BORDER
        + PAGE_HEAD_H
        + PAGE_HEAD_GAP
        + sum(blocks)
        + SECTION_GAP * (len(blocks) - 1)
    )

    week_end = today + datetime.timedelta(days=DAYS - 1)

    def row(event, when):
        return {
            "title": event.title,
            "kicker": event.kicker,
            "image": event.image,
            "when": when,
        }

    return {
        "width": WIDTH,
        "height": height,
        "scale": RENDER_SCALE,
        "heading": "Eventos da semana",
        "date": f"{today:%d/%m} – {week_end:%d/%m}",
        "brand": brand,
        "ongoing": [row(e, _until(e, today)) for e in shown],
        "ongoing_more": len(ongoing) - len(shown),
        "days": [
            {
                "name": "Hoje" if day == today else PT_DAYS_SHORT[day.weekday()],
                "date": f"{day:%d/%m}",
                "today": day == today,
                "events": [row(e, _hours(e, today)) for e in day_events],
            }
            for day, day_events in days
        ],
    }
