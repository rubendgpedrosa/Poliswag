"""100IV opt-in counts for !stats, by hundo_alerts' own rules."""

from modules.trade_stats import summarize_hundo


def _row(**over):
    row = {
        "collecting": "hundo",
        "left_at": None,
        "hundo_dms": 1,
        "hundo_areas": "leiria,marinha",
        "hundo_settings_revision": 2,
        "hundo_confirmed_revision": 2,
        "hundo_dm_refused_revision": 0,
        "hundo_confirmed_setting": 3,
        "hundo_health_revision": 2,
        "hundo_health_state": "ready",
    }
    row.update(over)
    return row


def test_a_change_undone_before_its_dm_stays_active():
    # Same settings as confirmed again: hundo_alerts.settled_back.
    assert summarize_hundo([_row(hundo_settings_revision=3)])["active"] == 1


def test_counts_active_players_per_area():
    out = summarize_hundo(
        [_row(), _row(hundo_areas="leiria", hundo_confirmed_setting=1)]
    )
    assert (out["active"], out["leiria"], out["marinha"]) == (2, 2, 1)


def test_unconfirmed_is_waiting_and_a_refused_dm_is_dms_closed():
    out = summarize_hundo(
        [
            # Switched to Leiria only; the DM confirming it hasn't gone out.
            _row(hundo_settings_revision=3, hundo_areas="leiria"),
            _row(
                hundo_settings_revision=3,
                hundo_areas="leiria",
                hundo_dm_refused_revision=3,
            ),
        ]
    )
    assert out["active"] == 0
    assert (out["waiting"], out["dms_closed"]) == (1, 1)


def test_left_or_not_collecting_counts_nowhere():
    out = summarize_hundo([_row(left_at="2026-09-01"), _row(collecting="shiny")])
    assert (out["active"], out["waiting"], out["dms_closed"]) == (0, 0, 0)


def test_only_current_revision_health_counts_as_a_problem():
    out = summarize_hundo(
        [
            _row(hundo_health_state="stopped"),
            _row(hundo_health_state="error", hundo_health_revision=1),  # stale
        ]
    )
    assert out["active"] == 2 and out["unhealthy"] == 1
