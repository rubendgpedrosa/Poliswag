"""Tests for modules.stat_card -- the stat board's view model."""

from pathlib import Path

import pytest

from modules import stat_card as sc


def _card(**kw):
    rows = (
        sc.StatRow("Leiria", "Leiria", 200, "raids"),
        sc.StatRow("MarinhaGrande", "Marinha Grande", 50, "raids"),
        sc.StatRow("Batalha", "Batalha", None, "raids"),
    )
    return sc.StatCard(
        **{
            "kicker": "Raids Mega",
            "hero": 1250,
            "hero_unit": "raids",
            "subject": "Sableye",
            "rows": rows,
            "note": "n",
            "title": "Mega Sableye in Mega Raids",
            **kw,
        }
    )


def _panel(card):
    return sc.build_board_view([card], "h")["rows"][0]["panels"][0]


class TestPanel:
    def test_bars_are_relative_to_the_biggest_area(self):
        panel = _panel(_card())
        bars = [row["bar"] for row in panel["rows"]]
        assert bars[0] == panel["bar_track"]
        assert bars[1] == round(panel["bar_track"] / 4)
        assert bars[2] == 0
        assert [row["lead"] for row in panel["rows"]] == [True, False, False]
        assert panel["rows"][2]["value"] is None

    def test_figures_use_pt_grouping(self):
        assert _panel(_card())["hero"] == "1 250"

    def test_subject_already_in_the_title_is_not_repeated(self):
        assert _panel(_card())["subject"] == ""
        assert _panel(_card(title="Raid Hour"))["subject"] == "Sableye"

    def test_extras_shrink_the_bar_track(self):
        plain = _panel(_card())["bar_track"]
        rows = (
            sc.StatRow("Leiria", "Leiria", 5, "spawns", (("100IV", 1), ("0IV", 0))),
        )
        assert _panel(_card(rows=rows))["bar_track"] == plain - 2 * sc.ROW_EXTRA_W

    def test_height_counts_each_optional_section(self):
        base = sc.panel_height(_card())
        assert sc.panel_height(_card(chips=(("100IV", "3"),))) == base + sc.CHIPS_H
        assert sc.panel_height(_card(note="")) == base - sc.NOTE_H
        assert sc.panel_height(_card(rows=())) == base - sc.DIVIDER_H - 3 * sc.ROW_H

    def test_a_panel_without_stats_is_banner_and_end_time(self):
        card = sc.StatCard(
            kicker="Wild Area", hero=None, title="W", ended="Terminou às 09:00"
        )
        panel = _panel(card)
        assert panel["stats"] is False and panel["ended"] == "Terminou às 09:00"
        assert sc.panel_height(card) == (
            2 * sc.BORDER
            + sc.BANNER_H
            + sc.BODY_PAD_TOP
            + sc.ENDED_H
            + sc.BODY_PAD_BOTTOM
        )


class TestBoard:
    @pytest.mark.parametrize(
        "count,cols", [(1, 1), (2, 2), (3, 3), (4, 2), (5, 3), (6, 3)]
    )
    def test_stays_landscape(self, count, cols):
        assert sc.columns_for(count) == cols

    def test_size_follows_the_rows(self):
        tall, short = _card(), sc.StatCard(kicker="k", hero=None, title="t")
        view = sc.build_board_view([tall, short, short], "h")
        assert [len(r["panels"]) for r in view["rows"]] == [3]
        assert view["rows"][0]["height"] == sc.panel_height(tall)
        assert (
            view["width"]
            == 2 * sc.BOARD_PAD + 3 * sc.PANEL_W + 2 * sc.PANEL_GAP + 2 * sc.BORDER
        )
        assert view["height"] == (
            2 * sc.BOARD_PAD
            + 2 * sc.BORDER
            + sc.HEAD_H
            + sc.HEAD_GAP
            + sc.panel_height(tall)
        )

    def test_chunks_of_six(self):
        assert [len(c) for c in sc.chunks(range(13))] == [6, 6, 1]

    def test_needs_a_panel(self):
        with pytest.raises(ValueError):
            sc.build_board_view([], "h")

    def test_template_css_matches_the_python_geometry(self):
        css = (Path(__file__).parents[2] / "templates" / "stat-card.html").read_text()
        assert "height: %dpx;\n        background-color" % sc.BANNER_H in css
        assert ".row { height: %dpx" % sc.ROW_H in css
        assert ".row-label {\n        width: %dpx;" % sc.ROW_LABEL_W in css
        assert ".row-value { width: %dpx;" % sc.ROW_VALUE_W in css
        assert (
            "width: %dpx;\n        overflow: hidden;\n        border-radius: 20px"
            % sc.PANEL_W
            in css
        )
        assert (
            "padding: %dpx %dpx %dpx %dpx"
            % (sc.BODY_PAD_TOP, sc.BODY_PAD_X, sc.BODY_PAD_BOTTOM, sc.BODY_PAD_X)
            in css
        )
