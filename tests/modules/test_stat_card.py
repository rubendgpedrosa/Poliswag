"""Tests for modules.stat_card -- the generic stat card's view model."""

import re
from pathlib import Path

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
            **kw,
        }
    )


class TestBuildView:
    def test_bars_are_relative_to_the_biggest_area(self):
        view = sc.build_view(_card())
        bars = [row["bar"] for row in view["rows"]]
        assert bars[0] == view["bar_track"]
        assert bars[1] == round(view["bar_track"] / 4)
        assert bars[2] == 0
        assert view["rows"][2]["value"] is None

    def test_figures_use_pt_grouping(self):
        assert sc.build_view(_card())["hero"] == "1 250"

    def test_extras_shrink_the_bar_track(self):
        plain = sc.build_view(_card())["bar_track"]
        rows = (
            sc.StatRow("Leiria", "Leiria", 5, "spawns", (("100IV", 1), ("0IV", 0))),
        )
        assert (
            sc.build_view(_card(rows=rows))["bar_track"] == plain - 2 * sc.ROW_EXTRA_W
        )

    def test_height_counts_each_optional_section(self):
        base = sc.card_height(_card())
        assert sc.card_height(_card(chips=(("100IV", "3"),))) == base + sc.CHIPS_H
        assert sc.card_height(_card(note="")) == base - sc.NOTE_H
        assert sc.card_height(_card(rows=())) == base - sc.DIVIDER_H - 3 * sc.ROW_H

    def test_template_css_matches_the_python_geometry(self):
        css = (Path(__file__).parents[2] / "templates" / "stat-card.html").read_text()
        assert re.search(r"\.row \{ height: %dpx" % sc.ROW_H, css)
        assert (
            "padding: %dpx %dpx %dpx %dpx"
            % (
                sc.INNER_PAD_TOP,
                sc.INNER_PAD_X,
                sc.INNER_PAD_BOTTOM,
                sc.INNER_PAD_X,
            )
            in css
        )
        assert "width: %dpx;\n        display: flex" % sc.ROW_LABEL_W in css
