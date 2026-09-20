from datetime import date, datetime, timedelta
import json

from modules.page_view_report import build_snapshot_embed, render_export


def stats(**patch):
    out = {
        "since": datetime(2026, 9, 14),
        "as_of": datetime(2026, 9, 20, 8),
        "schema_v2": True,
        "totals": [
            {
                "sessions": 10,
                "views": 15,
                "loads": 10,
                "entries": 10,
                "pwa_sessions": 2,
                "days": 1,
            }
        ],
        "daily": [{"d": date(2026, 9, 20), "sessions": 10, "views": 15, "visitors": 7}],
        "views": [
            {"view": "map", "sessions": 10, "views": 15},
            {"view": "quests", "sessions": 6, "views": 9},
        ],
        "health": [
            {"first_seen": datetime(2026, 1, 1), "last_seen": datetime(2026, 9, 20, 8)}
        ],
    }
    out.update(patch)
    return out


def description(embed):
    return embed.description or ""


def test_snapshot_reports_todays_visitors_and_the_tools_behind_them():
    embed = build_snapshot_embed(stats())
    assert "~7 visitantes" in description(embed)
    assert "Mapa 15" in description(embed)
    assert "Quests 9" in description(embed)


def test_snapshot_counts_no_visitors_when_today_has_no_row():
    # The daily row is keyed by date; a collection whose newest row predates
    # as_of must read as zero today, not as yesterday's figure.
    data = stats(
        daily=[{"d": date(2026, 9, 19), "sessions": 4, "views": 6, "visitors": 5}]
    )
    assert "~0 visitantes" in description(build_snapshot_embed(data))


def test_snapshot_links_the_report_only_when_a_link_exists():
    with_link = build_snapshot_embed(
        stats(), None, "https://pogoleiria.pt/webstats/abc"
    )
    assert "https://pogoleiria.pt/webstats/abc" in description(with_link)
    # An existing token cannot be read back out of its hash, so the embed has
    # to hold together with no link at all.
    assert "http" not in description(build_snapshot_embed(stats()))


def test_snapshot_does_not_promise_the_link_expires():
    # It used to say "válido durante 24 horas", which stopped being true when
    # the report became a permanent address.
    embed = build_snapshot_embed(stats(), None, "https://pogoleiria.pt/webstats/abc")
    assert "24 horas" not in (embed.footer.text or "")


def test_snapshot_adds_trades_only_when_there_are_trade_figures():
    embed = build_snapshot_embed(stats(), {"entries": 12, "users": 3})
    assert "12 Pokémon" in description(embed)
    assert "3 utilizadores" in description(embed)
    # "Trades" also names a tool on the openings line, so the absence to
    # assert is the trade-entries line itself.
    assert "Novas entradas" not in description(build_snapshot_embed(stats(), None))


def test_export_keeps_every_queried_day():
    days = [
        {
            "d": date(2025, 1, 1) + timedelta(days=i),
            "sessions": 1,
            "views": 1,
            "visitors": 1,
        }
        for i in range(365)
    ]
    assert len(json.loads(render_export(stats(daily=days)))["daily"]) == 365


def test_export_drops_raw_paths_ids_and_unexpected_future_fields():
    data = stats(
        paths=[{"path": "/trades/entrar/SECRET"}], visitor="SECRET", load_id="SECRET"
    )
    data["daily"][0]["extra_sensitive_field"] = "SECRET"
    data["referrers"] = [{"referrer": "/trades/entrar/SECRET", "sessions": 1}]
    assert "SECRET" not in render_export(data)
    out = json.loads(render_export(data))
    assert out["metric_version"] == 2
    assert "not activity sessions" in out["definitions"]["sessions"]


def test_export_serialises_dates_and_decimals_rather_than_failing():
    from decimal import Decimal

    data = stats()
    data["daily"][0]["views"] = Decimal("15")
    out = json.loads(render_export(data))
    assert out["daily"][0]["d"] == "2025-01-01" or out["daily"][0]["d"].startswith(
        "2026"
    )
    assert out["daily"][0]["views"] == 15
