"""Tests for modules.account_card -- the account card's view model."""

import re
from pathlib import Path

from modules import account_card as ac


def _acc(in_use=0, ready=0, never_used=0, cooldown=0, out_detail=None):
    out_detail = out_detail or {}
    out = sum(out_detail.values())
    return {
        "in_use": in_use,
        "ready": ready,
        "never_used": never_used,
        "cooldown": cooldown,
        "out": out,
        "out_detail": out_detail,
        "total": in_use + ready + cooldown + out,
    }


def _dev(connected=True, total=50, in_use=45, names=("PocoF2Pro",)):
    return {
        "connected": connected,
        "names": list(names),
        "workers": {"total": total, "in_use": in_use},
    }


def _scan(accounts, device):
    return {
        "key": "scanning",
        "title": "Scanning",
        "accounts": accounts,
        "device": device,
    }


class TestBarSegments:
    def test_widths_sum_exactly_to_the_bar(self):
        segs = ac.bar_segments([45, 205, 57, 5])
        gaps = ac.BAR_SEGMENT_GAP * (len(segs) - 1)
        assert sum(px for _, px in segs) + gaps == ac.CONTENT_WIDTH

    def test_zero_values_are_skipped(self):
        segs = ac.bar_segments([0, 10, 0, 0])
        assert segs == [(1, ac.CONTENT_WIDTH)]

    def test_tiny_value_still_visible(self):
        segs = dict(ac.bar_segments([1000, 1, 0, 0]))
        assert segs[1] >= ac.BAR_MIN_SEGMENT

    def test_all_zero_is_empty(self):
        assert ac.bar_segments([0, 0, 0, 0]) == []


class TestStatus:
    def test_ok_when_ready_covers_workers(self):
        view = ac.build_card_view([_scan(_acc(45, 205, 6, 57), _dev())])
        assert (view["status"], view["status_label"]) == ("success", "Healthy")

    def test_critical_when_scanning_device_offline(self):
        view = ac.build_card_view([_scan(_acc(0, 205), _dev(connected=False))])
        assert (view["status"], view["status_label"]) == (
            "destructive",
            "Scanner offline",
        )

    def test_critical_when_nothing_ready(self):
        view = ac.build_card_view([_scan(_acc(45, 0, 0, 57), _dev())])
        assert (view["status"], view["status_label"]) == (
            "destructive",
            "No accounts ready",
        )

    def test_warning_when_ready_below_worker_count(self):
        view = ac.build_card_view([_scan(_acc(45, 10, 0, 57), _dev(total=50))])
        assert (view["status"], view["status_label"]) == ("urgent", "Running low")

    def test_warning_when_stats_unavailable(self):
        view = ac.build_card_view([_scan(None, _dev())])
        assert (view["status"], view["status_label"]) == ("urgent", "Stats unavailable")

    def test_levelling_pool_does_not_drive_status(self):
        level = {
            "key": "levelling",
            "title": "Levelling",
            "accounts": _acc(0, 0),
            "device": _dev(connected=False),
        }
        view = ac.build_card_view([_scan(_acc(45, 205), _dev()), level])
        assert (view["status"], view["status_label"]) == ("success", "Healthy")


class TestPoolView:
    def test_note_lists_never_used_and_out_detail(self):
        view = ac.build_card_view(
            [_scan(_acc(45, 205, 6, 57, {"banned": 5, "auth_banned": 1}), _dev())]
        )
        note = view["pools"][0]["accounts"]["note"]
        assert note == "ready includes 6 never used · out: 5 banned, 1 auth-banned"

    def test_no_note_when_nothing_to_say(self):
        view = ac.build_card_view([_scan(_acc(45, 205), _dev())])
        assert view["pools"][0]["accounts"]["note"] == ""

    def test_device_states(self):
        assert ac._device_view(None) == {
            "state": "off",
            "text": "Rotom unavailable",
            "names": "",
        }
        assert ac._device_view(_dev(connected=False))["text"] == "Device offline"
        assert ac._device_view(_dev())["text"] == "45/50 workers"
        legacy = _dev(total=0, in_use=0)
        assert ac._device_view(legacy)["text"] == "Device online"

    def test_grand_total_skips_unavailable_pools(self):
        level = {
            "key": "levelling",
            "title": "Levelling",
            "accounts": None,
            "device": None,
        }
        view = ac.build_card_view([_scan(_acc(45, 205, 0, 57), _dev()), level])
        assert view["grand_total"] == 307


class TestHeights:
    def test_height_adds_up_the_visible_blocks(self):
        scan = _scan(_acc(45, 205, 6, 57), _dev())
        with_note = ac.build_card_view([scan])["height"]
        scan_plain = _scan(_acc(45, 205, 0, 57), _dev())
        without_note = ac.build_card_view([scan_plain])["height"]
        assert with_note - without_note == ac.POOL_NOTE_H
        footer = {"Leiria": {"total": 10, "iv": 9}}
        assert (
            ac.build_card_view([scan_plain], footer)["height"] - without_note
            == ac.footer_height(1)
            == ac.AREA_DIVIDER_H + ac.AREA_ROW_H
        )

    def test_footer_wraps_after_five_areas(self):
        assert ac.footer_height(0) == 0
        assert ac.footer_height(5) == ac.footer_height(1)
        assert ac.footer_height(6) == (
            ac.AREA_DIVIDER_H + 2 * ac.AREA_ROW_H + ac.AREA_ROW_GAP
        )
        lines = [{"name": str(n), "rate": 90, "spawns": 100} for n in range(6)]
        assert [len(r) for r in ac.area_rows(lines)] == [5, 1]

    def test_area_columns_fill_the_content_width(self):
        used = ac.AREA_COLUMNS * ac.AREA_COL_W + (ac.AREA_COLUMNS - 1) * ac.AREA_COL_GAP
        assert used <= ac.CONTENT_WIDTH
        assert ac.CONTENT_WIDTH - used < ac.AREA_COLUMNS

    def test_image_height_includes_the_board_frame(self):
        view = ac.build_card_view([_scan(_acc(45, 205), _dev())])
        assert view["height"] == view["card_height"] + ac.BOARD_HEAD_H + 2 * (
            ac.BOARD_PAD + ac.BOARD_BORDER
        )

    def test_template_css_uses_the_same_pixel_sizes(self):
        css = Path("templates/accounts.html").read_text(encoding="utf-8")

        def block_height(selector):
            match = re.search(
                re.escape(selector) + r"\s*\{[^}]*?height:\s*(\d+)px", css
            )
            return int(match.group(1))

        assert block_height(".header-row") == ac.HEADER_H
        assert block_height(".pool-title") == ac.POOL_TITLE_H
        assert block_height(".pool-stats.plain") == ac.POOL_STATS_H
        assert block_height(".pool-stats.hero") == ac.POOL_STATS_HERO_H
        assert block_height(".pool-unavailable") == ac.POOL_UNAVAILABLE_H
        assert block_height(".area-row") == ac.AREA_ROW_H
        assert ".area-row.spaced { margin-bottom: 12px; }" in css
        assert ac.AREA_ROW_GAP == 12
        assert "margin-right: 13px" in css and ac.AREA_COL_GAP == 13
        assert "margin: 18px 0 16px" in css and ac.AREA_DIVIDER_H == 18 + 1 + 16
        # The content sits directly on the board: .card-inner's padding is
        # what bounds the text, and CONTENT_WIDTH follows from it.
        pad = re.search(
            r"\.card-inner \{\s*padding: (\d+)px (\d+)px (\d+)px (\d+)px;", css
        )
        top, right, bottom, left = map(int, pad.groups())
        assert (top, bottom, left, right) == (
            ac.INNER_PAD_TOP,
            ac.INNER_PAD_BOTTOM,
            ac.INNER_PAD_X_L,
            ac.INNER_PAD_X_R,
        )
        assert ac.CONTENT_WIDTH == (
            ac.CARD_WIDTH - 2 * ac.BOARD_PAD - 2 * ac.BOARD_BORDER - left - right
        )
        assert block_height(".bar") + 12 == ac.POOL_BAR_H
        assert block_height(".note, .low") + 10 == ac.POOL_NOTE_H == ac.POOL_LOW_H
        assert "margin: 12px 0 14px" in css  # pool divider, POOL_DIVIDER_H
        assert ac.POOL_DIVIDER_H == 12 + 1 + 14


class TestLowNote:
    def test_running_low_note_when_ready_below_workers(self):
        view = ac.build_card_view([_scan(_acc(45, 10, 0, 57), _dev(total=50))])
        acc = view["pools"][0]["accounts"]
        assert acc["low"] == "Running low: 10 ready for 50 workers"

    def test_low_note_adds_its_row_to_the_height(self):
        low = ac.build_card_view([_scan(_acc(45, 10), _dev(total=50))])["height"]
        fine = ac.build_card_view([_scan(_acc(45, 60), _dev(total=50))])["height"]
        assert low - fine == ac.POOL_LOW_H

    def test_no_low_note_for_levelling_pool(self):
        level = {
            "key": "levelling",
            "title": "Levelling",
            "accounts": _acc(16, 0),
            "device": _dev(total=16, in_use=16),
        }
        view = ac.build_card_view([_scan(_acc(45, 205), _dev()), level])
        assert view["pools"][1]["accounts"]["low"] == ""
        assert view["pools"][0]["hero"] and not view["pools"][1]["hero"]


class TestDesignTokens:
    """The card follows the PoGoLeiria design rules (design/DESIGN.md)."""

    css = Path("templates/accounts.html").read_text(encoding="utf-8")

    def test_no_drop_shadows_or_glows(self):
        # No second surface inside the board frame, so not even the `.lit`
        # inset highlight is needed.
        assert not re.findall(r"(?:box|text)-shadow:", self.css)

    def test_font_sizes_only_from_the_scale(self):
        sizes = {int(n) for n in re.findall(r"font-size:\s*(\d+)px", self.css)}
        assert sizes <= {10, 11, 13, 15, 17, 19, 22, 44}

    def test_radii_only_from_the_steps(self):
        radii = {int(n) for n in re.findall(r"border-radius:\s*(\d+)px", self.css)}
        assert radii <= {8, 12, 16, 20, 24, 999}

    def test_no_reward_gold_or_touch_ember(self):
        lowered = self.css.lower()
        assert "#f4cc64" not in lowered


class TestNoEdgeAccent:
    """Owner, 2026-11-05: no coloured strip, border or edge accent on the card."""

    css = Path("templates/accounts.html").read_text(encoding="utf-8")

    def test_no_status_strip(self):
        assert "status-bar" not in self.css

    def test_only_neutral_borders(self):
        borders = set(re.findall(r"border[a-z-]*:\s*([^;]+);", self.css))
        colours = {c for b in borders for c in re.findall(r"#[0-9A-Fa-f]{6}", b)}
        assert colours <= {"#262E3B", "#778190"}  # hairline; hollow offline dot
