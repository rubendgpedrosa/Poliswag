"""Tests for modules.page_view_report — pure rendering, no DB, no Discord.

Every table is read in a Discord fenced block on a phone, which does not wrap,
so the hard 56-character budget is asserted over deliberately hostile values.
"""

from datetime import date
from decimal import Decimal

import pytest

from modules.page_view_report import (
    MAX_LINE,
    Period,
    build_sections,
    render_table,
)

PERIOD = Period(days=7, since=date(2026, 9, 13), until=date(2026, 9, 19))


def stats(**overrides):
    base = {
        "totals": [
            {
                "views": 109,
                "loads": Decimal(67),
                "sessions": 67,
                "pwa_sessions": 2,
                "days": 5,
            }
        ],
        "daily": [
            {
                "d": date(2026, 9, 15),
                "loads": Decimal(3),
                "views": 5,
                "sessions": 3,
                "visitors": 3,
            },
            {
                "d": date(2026, 9, 16),
                "loads": Decimal(23),
                "views": 41,
                "sessions": 23,
                "visitors": 16,
            },
            {
                "d": date(2026, 9, 17),
                "loads": Decimal(12),
                "views": 22,
                "sessions": 12,
                "visitors": 10,
            },
            {
                "d": date(2026, 9, 18),
                "loads": Decimal(26),
                "views": 37,
                "sessions": 26,
                "visitors": 15,
            },
            {
                "d": date(2026, 9, 19),
                "loads": Decimal(3),
                "views": 4,
                "sessions": 3,
                "visitors": 3,
            },
        ],
        "hourly": [{"h": 12, "views": 14}, {"h": 13, "views": 7}],
        "views": [
            {"view": "map", "loads": Decimal(26), "switches": Decimal(30), "views": 56},
            {"view": "home", "loads": Decimal(41), "switches": Decimal(3), "views": 44},
        ],
        "devices": [
            {
                "device": "mobile",
                "os": "Android",
                "browser": "Samsung Internet",
                "sessions": 7,
                "views": 9,
            },
        ],
        "screens": [{"bucket": "380-429", "mn": 384, "sessions": 48}],
        "langs": [{"lang": "pt-PT", "sessions": 45}],
        "countries": [{"country": "PT", "sessions": 67}],
        "referrers": [{"referrer": "(direto)", "sessions": 67}],
        "paths": [{"path": "/mapa", "views": 50, "sessions": 49}],
    }
    base.update(overrides)
    return base


def sections_by_title(report):
    return {section.title: section for section in report.sections}


def kpis_by_label(report):
    return dict(report.kpis)


class TestKpis:
    def test_reports_the_period_totals(self):
        kpis = kpis_by_label(build_sections(stats(), PERIOD))
        assert kpis["Loads"] == "67"
        assert kpis["Views"] == "109"
        assert kpis["Sessões"] == "67"

    def test_views_per_session_is_rounded_to_two_places(self):
        assert kpis_by_label(build_sections(stats(), PERIOD))["Views/sessão"] == "1.63"

    def test_pwa_share_counts_sessions_not_views(self):
        assert (
            kpis_by_label(build_sections(stats(), PERIOD))["PWA instalada"]
            == "2 de 67 (3%)"
        )

    def test_daily_uniques_average_over_days_that_have_rows(self):
        # 3+16+10+15+3 = 47 over 5 days, not over the period's 7.
        assert (
            kpis_by_label(build_sections(stats(), PERIOD))["Únicos/dia"]
            == "9.4 média · 16 máx"
        )

    def test_an_empty_period_divides_by_nothing(self):
        empty = {k: [] for k in stats()}
        empty["totals"] = [
            {"views": 0, "loads": 0, "sessions": 0, "pwa_sessions": 0, "days": 0}
        ]
        kpis = kpis_by_label(build_sections(empty, PERIOD))
        assert kpis["Views/sessão"] == "—"
        assert kpis["Únicos/dia"] == "—"


class TestDailyTable:
    def test_lists_a_row_per_day_for_a_short_period(self):
        table = render_table(
            sections_by_title(build_sections(stats(), PERIOD))["Tráfego diário"]
        )
        assert "2026-09-15" in table
        assert len(table.splitlines()) == 6  # header + 5 days

    def test_rolls_up_to_weeks_past_a_fortnight(self):
        days = [
            {
                "d": date(2026, 4, 1) + __import__("datetime").timedelta(days=i),
                "loads": Decimal(2),
                "views": 4,
                "sessions": 2,
                "visitors": 2,
            }
            for i in range(30)
        ]
        report = build_sections(
            stats(daily=days),
            Period(days=30, since=date(2026, 4, 1), until=date(2026, 4, 30)),
        )
        table = render_table(sections_by_title(report)["Tráfego diário"])
        rows = table.splitlines()[1:]
        assert 4 <= len(rows) <= 20
        # 30 days x 4 views, summed across the week rows, must be conserved.
        assert sum(int(r.split()[2]) for r in rows) == 120

    def test_caps_a_very_long_period_at_two_years_of_months(self):
        import datetime as dt

        days = [
            {
                "d": date(2023, 1, 1) + dt.timedelta(days=i),
                "loads": Decimal(1),
                "views": 1,
                "sessions": 1,
                "visitors": 1,
            }
            for i in range(1000)
        ]
        report = build_sections(
            stats(daily=days),
            Period(days=None, since=date(1970, 1, 1), until=date(2025, 9, 27)),
        )
        rows = render_table(sections_by_title(report)["Tráfego diário"]).splitlines()[
            1:
        ]
        assert len(rows) <= 24


class TestSparkline:
    def test_is_always_a_full_day_wide(self):
        report = build_sections(stats(), PERIOD)
        spark = render_table(
            sections_by_title(report)["Atividade por hora (UTC)"]
        ).splitlines()[1]
        assert len(spark) == 24

    def test_quiet_hours_are_dots_not_zero_height_blocks(self):
        report = build_sections(stats(), PERIOD)
        spark = render_table(
            sections_by_title(report)["Atividade por hora (UTC)"]
        ).splitlines()[1]
        assert spark.count("·") == 22  # only 12h and 13h saw traffic

    def test_a_period_with_no_traffic_has_no_sparkline_at_all(self):
        # Every row carries an hour, so empty hourly means an empty period.
        report = build_sections(stats(hourly=[]), PERIOD)
        assert "Atividade por hora (UTC)" not in sections_by_title(report)

    def test_the_busiest_hour_is_a_full_block(self):
        report = build_sections(stats(), PERIOD)
        spark = render_table(
            sections_by_title(report)["Atividade por hora (UTC)"]
        ).splitlines()[1]
        assert spark[12] == "█"
        assert spark[0] == "·"


class TestWidthBudget:
    @pytest.fixture
    def hostile(self):
        return build_sections(
            stats(
                paths=[
                    {"path": "/mapa/pokemon/" + "9" * 240, "views": 1, "sessions": 1}
                ],
                referrers=[{"referrer": "r" * 100, "sessions": 3}],
                devices=[
                    {
                        "device": "desktop",
                        "os": "Windows",
                        "browser": "Samsung Internet",
                        "sessions": 13,
                        "views": 21,
                    }
                ],
                langs=[{"lang": "x" * 16, "sessions": 2}],
            ),
            PERIOD,
        )

    def test_no_rendered_line_can_scroll_a_phone(self, hostile):
        for section in hostile.sections:
            for line in render_table(section).splitlines():
                assert len(line) <= MAX_LINE, f"{section.title}: {len(line)} chars"

    def test_a_long_value_is_ellipsised_not_allowed_to_set_the_width(self, hostile):
        table = render_table(sections_by_title(hostile)["Caminhos"])
        assert "…" in table

    def test_every_line_of_a_table_is_the_same_length(self, hostile):
        for section in hostile.sections:
            lengths = {len(line) for line in render_table(section).splitlines()}
            assert len(lengths) == 1, f"{section.title} is ragged: {lengths}"


from modules.page_view_report import (  # noqa: E402
    FOOTER,
    build_dm_embed,
    fits,
    render_text_report,
)


def optional_stats():
    return stats(
        os_versions=[{"os": "Android", "os_version": "15", "sessions": 41}],
        browser_versions=[
            {"browser": "Chrome", "browser_version": "140", "sessions": 52}
        ],
        models=[{"val": "SM-S918B", "sessions": 7}],
        arch=[{"val": "x86", "sessions": 13}],
        bitness=[{"val": "64", "sessions": 13}],
        cpu_cores=[{"val": 8, "sessions": 38}],
        device_memory=[{"val": Decimal("4.00"), "sessions": 38}],
    )


class TestEmbed:
    def test_the_six_scalars_are_inline_in_the_documented_order(self):
        embed = build_dm_embed(build_sections(stats(), PERIOD))
        inline = [f.name for f in embed.fields if f.inline]
        assert inline == [
            "Loads",
            "Views",
            "Sessões",
            "Views/sessão",
            "Únicos/dia",
            "PWA instalada",
        ]

    def test_every_table_is_a_block_field_in_a_fence(self):
        embed = build_dm_embed(build_sections(stats(), PERIOD))
        blocks = [f for f in embed.fields if not f.inline]
        assert blocks
        for field in blocks:
            assert field.value.startswith("```")
            assert field.value.endswith("```")

    def test_thirteen_fields_before_the_alter_and_seventeen_after(self):
        assert len(build_dm_embed(build_sections(stats(), PERIOD)).fields) == 13
        assert (
            len(build_dm_embed(build_sections(optional_stats(), PERIOD)).fields) == 17
        )

    def test_says_so_plainly_when_nothing_happened(self):
        empty = {k: [] for k in stats()}
        empty["totals"] = [
            {"views": 0, "loads": 0, "sessions": 0, "pwa_sessions": 0, "days": 0}
        ]
        embed = build_dm_embed(build_sections(empty, PERIOD))
        assert embed.description == "Sem dados no período."
        assert len(embed.fields) == 0

    def test_carries_the_rotation_caveat_in_the_footer(self):
        embed = build_dm_embed(build_sections(stats(), PERIOD))
        assert embed.footer.text == FOOTER

    def test_an_optional_section_says_how_much_of_the_period_it_covers(self):
        embed = build_dm_embed(build_sections(optional_stats(), PERIOD))
        os_field = [f for f in embed.fields if f.name.startswith("Versões de OS")][0]
        assert "cobertura 41/67 sessões (61%)" in os_field.value


class TestLimits:
    def test_a_full_report_stays_inside_every_discord_limit(self):
        import datetime as dt

        days = [
            {
                "d": date(2024, 1, 1) + dt.timedelta(days=i),
                "loads": Decimal(40),
                "views": 90,
                "sessions": 40,
                "visitors": 30,
            }
            for i in range(365)
        ]
        big = optional_stats()
        big["daily"] = days
        big["devices"] = [
            {
                "device": "desktop",
                "os": "Windows",
                "browser": "Samsung Internet",
                "sessions": 99,
                "views": 199,
            }
        ] * 10
        big["models"] = [{"val": f"SM-{i:04d}XYZ", "sessions": 9} for i in range(8)]
        report = build_sections(
            big, Period(days=None, since=date(2024, 1, 1), until=date(2024, 12, 30))
        )
        embed = build_dm_embed(report)
        assert embed is not None
        assert len(embed.fields) <= 25
        assert all(len(f.value) <= 1024 for f in embed.fields)
        assert len(embed) <= 6000

    def test_an_oversized_report_is_refused_rather_than_truncated(self):
        report = build_sections(stats(), PERIOD)
        fat = report._replace(sections=report.sections * 6)
        assert fits(fat) is False
        assert build_dm_embed(fat) is None

    def test_the_text_fallback_carries_the_same_sections_uncapped(self):
        report = build_sections(optional_stats(), PERIOD)
        text = render_text_report(report)
        for section in report.sections:
            assert section.title in text
        assert report.description in text
