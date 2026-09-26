"""The site-down DM: when it fires, repeats and says the app is back."""

from datetime import datetime, timedelta

from modules.site_health import (
    DOWN_AFTER,
    REPEAT_AFTER,
    CheckState,
    SiteHealth,
    decide,
    message,
)

NOW = datetime(2026, 9, 26, 16, 0)


def fail(state, times, now=NOW, error="HTTP 500"):
    return [decide(state, error, now) for _ in range(times)]


def test_a_restart_blip_is_not_an_outage():
    state = CheckState()
    assert fail(state, DOWN_AFTER - 1) == [None] * (DOWN_AFTER - 1)
    assert decide(state, None, NOW) is None
    assert state.failures == 0


def test_down_after_consecutive_failures_and_only_once():
    state = CheckState()
    actions = fail(state, DOWN_AFTER + 2)
    assert actions.count("down") == 1
    assert actions[DOWN_AFTER - 1] == "down"


def test_reminds_while_still_down():
    state = CheckState()
    fail(state, DOWN_AFTER)
    assert decide(state, "HTTP 500", NOW + REPEAT_AFTER - timedelta(minutes=1)) is None
    assert decide(state, "HTTP 500", NOW + REPEAT_AFTER) == "still_down"


def test_recovery_only_when_an_alert_went_out():
    state = CheckState()
    fail(state, DOWN_AFTER)
    assert decide(state, None, NOW) == "recovered"
    assert decide(state, None, NOW) is None


def test_record_names_each_app():
    health = SiteHealth(checks=(("Quests", "x"), ("Pokédex", "y")))
    for _ in range(DOWN_AFTER):
        actions = health.record({"Quests": None, "Pokédex": "HTTP 500"}, NOW)
    assert [(a, n) for a, n, _ in actions] == [("down", "Pokédex")]


def test_message_says_what_and_why():
    state = CheckState()
    fail(state, DOWN_AFTER, error="HTTP 502")
    text = message("down", "Pokédex", state)
    assert "Pokédex" in text and "HTTP 502" in text and "3 min" in text
