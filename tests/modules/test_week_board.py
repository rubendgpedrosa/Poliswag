"""Tests for modules.week_board -- the weekly agenda image's view."""

import datetime as dt
from pathlib import Path

from modules import week_board as wb

NOW = dt.datetime(2026, 11, 9, 9, 0)  # a Monday


def _event(title, start, end, kicker="Evento"):
    return wb.WeekEvent(title, kicker, "", start, end)


def test_running_events_then_only_the_days_with_something():
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
    assert [(e["title"], e["when"]) for e in view["ongoing"]] == [
        ("Season", "até Ter 01/12"),
        ("Museum", "até Dom 11/04/2027"),
    ]
    assert [(d["name"], d["date"]) for d in view["days"]] == [
        ("Qua", "11/11"),
        ("Sáb", "14/11"),
    ]
    assert view["days"][0]["events"][0]["when"] == "18:00 – 19:00"
    assert view["days"][1]["events"][0]["when"] == "10:00 · até 15/11"
    assert view["date"] == "09/11 – 15/11"
    assert view["width"] == wb.WIDTH


def test_today_is_marked():
    later = _event("Later", dt.datetime(2026, 11, 9, 18), dt.datetime(2026, 11, 9, 19))
    [today] = wb.build_week_view([later], NOW)["days"]
    assert (today["name"], today["today"]) == ("Hoje", True)


def test_height_adds_up_the_blocks():
    running = _event("R", dt.datetime(2026, 11, 1), dt.datetime(2026, 11, 20))
    a = _event("A", dt.datetime(2026, 11, 10, 10), dt.datetime(2026, 11, 10, 11))
    b = _event("B", dt.datetime(2026, 11, 10, 12), dt.datetime(2026, 11, 10, 13))
    view = wb.build_week_view([running, a, b], NOW)
    assert view["height"] == (
        2 * wb.BOARD_PAD
        + 2 * wb.BORDER
        + wb.PAGE_HEAD_H
        + wb.PAGE_HEAD_GAP
        + wb.SECTION_LABEL_H
        + wb.rows_height(1)
        + wb.SECTION_GAP
        + wb.DAY_HEAD_H
        + wb.rows_height(2)
    )


def test_more_than_six_running_events_are_counted():
    running = [
        _event(f"R{i}", dt.datetime(2026, 11, 1), dt.datetime(2026, 11, 20 + i))
        for i in range(9)
    ]
    view = wb.build_week_view(running, NOW)
    assert len(view["ongoing"]) == 6 and view["ongoing_more"] == 3
    assert view["days"] == []


def test_a_quiet_week_has_no_image():
    assert wb.build_week_view([], NOW) is None


def test_template_css_matches_the_python_geometry():
    templates = Path(__file__).parents[2] / "templates"
    css = (templates / "week-board.html").read_text()
    base = (templates / "_board-base.css").read_text()
    assert "width: %dpx;\n        height: %dpx;" % (wb.CONTENT_W, wb.ROW_H) in css
    assert ".ev.spaced { margin-bottom: %dpx; }" % wb.ROW_GAP in css
    assert ".section-gap { height: %dpx; }" % wb.SECTION_GAP in css
    assert "width: %dpx;\n        height: 72px;" % wb.THUMB_W in css
    assert (
        "height: %dpx;\n        margin-bottom: %dpx;"
        % (wb.PAGE_HEAD_H, wb.PAGE_HEAD_GAP)
        in base
    )
