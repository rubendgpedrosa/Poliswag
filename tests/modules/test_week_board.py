"""Tests for modules.week_board -- the weekly calendar image's view."""

import datetime as dt
from pathlib import Path

from modules import week_board as wb

NOW = dt.datetime(2026, 11, 9, 9, 0)  # a Monday


def _event(title, start, end, kicker="Evento"):
    return wb.WeekEvent(title, kicker, "", start, end)


def test_running_events_and_the_days_ahead():
    season = _event("Season", dt.datetime(2026, 9, 1), dt.datetime(2026, 12, 1, 10))
    museum = _event("Museum", dt.datetime(2026, 5, 1), dt.datetime(2027, 4, 11, 17))
    hour = _event(
        "Raid Hour", dt.datetime(2026, 11, 11, 18), dt.datetime(2026, 11, 11, 19)
    )
    day = _event(
        "Wild Area", dt.datetime(2026, 11, 14, 10), dt.datetime(2026, 11, 15, 18)
    )
    view = wb.build_week_view([museum, season, hour, day], NOW)
    # Soonest to end first; the year only when it isn't this one.
    assert [(e["title"], e["until"]) for e in view["ongoing"]] == [
        ("Season", "até Ter 01/12"),
        ("Museum", "até Dom 11/04/2027"),
    ]
    assert [d["name"] for d in view["days"]] == [
        "Hoje",
        "Ter",
        "Qua",
        "Qui",
        "Sex",
        "Sáb",
        "Dom",
    ]
    assert view["days"][2]["events"] == [
        {
            "title": "Raid Hour",
            "kicker": "Evento",
            "image": "",
            "hours": "18:00 – 19:00",
        }
    ]
    assert view["days"][5]["events"][0]["hours"] == "10:00 · até 15/11"
    assert view["date"] == "09/11 – 15/11"


def test_height_follows_the_busiest_day():
    a = _event("A", dt.datetime(2026, 11, 10, 10), dt.datetime(2026, 11, 10, 11))
    b = _event("B", dt.datetime(2026, 11, 10, 12), dt.datetime(2026, 11, 10, 13))
    view = wb.build_week_view([a, b], NOW)
    assert view["ongoing"] == []
    assert view["height"] == (
        2 * wb.BOARD_PAD
        + 2 * wb.BORDER
        + wb.PAGE_HEAD_H
        + wb.PAGE_HEAD_GAP
        + wb.SECTION_LABEL_H
        + wb.day_height(2)
    )
    assert wb.day_height(0) == wb.DAY_HEAD_H + wb.EMPTY_H


def test_more_than_eight_running_events_are_counted():
    running = [
        _event(f"R{i}", dt.datetime(2026, 11, 1), dt.datetime(2026, 11, 20 + i))
        for i in range(10)
    ]
    view = wb.build_week_view(running, NOW)
    assert len(view["ongoing"]) == 8 and view["ongoing_more"] == 2
    assert [len(row) for row in view["ongoing_rows"]] == [4, 4]
    assert view["days"] == []  # nothing starts this week: no calendar


def test_a_quiet_week_has_no_image():
    assert wb.build_week_view([], NOW) is None


def test_template_css_matches_the_python_geometry():
    templates = Path(__file__).parents[2] / "templates"
    css = (templates / "week-board.html").read_text()
    base = (templates / "_board-base.css").read_text()
    assert "height: %dpx;\n        margin-right: 16px" % wb.ONGOING_H in css
    assert ".mini {\n        height: %dpx;" % wb.MINI_H in css
    assert ".day { width: %dpx;" % wb.DAY_W in css
    assert ".day-head { height: %dpx; }" % wb.DAY_HEAD_H in css
    assert (
        "height: %dpx;\n        margin-bottom: %dpx;"
        % (wb.PAGE_HEAD_H, wb.PAGE_HEAD_GAP)
        in base
    )
