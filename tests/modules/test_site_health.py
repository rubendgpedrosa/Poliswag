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


class TestMapCheck:
    """The map page loads without its DB; the check has to ask it for data."""

    async def _run(self, monkeypatch, map_stop):
        import modules.site_health as sh

        seen = []

        async def fake_probe(url):
            seen.append(url)
            return None

        monkeypatch.setattr(sh, "probe", fake_probe)
        health = SiteHealth(checks=(), map_stop=map_stop, host="10.0.0.1")
        return await health.probe_all(), seen

    async def test_asks_the_map_for_a_current_pokestop(self, monkeypatch):
        async def stop():
            return "abc.16"

        results, seen = await self._run(monkeypatch, stop)
        assert seen == ["http://10.0.0.1:1082/api/pokestop/abc.16"]
        assert results == {"Mapa": None}

    async def test_no_pokestop_to_ask_for_is_a_failure(self, monkeypatch):
        async def stop():
            return None

        results, seen = await self._run(monkeypatch, stop)
        assert seen == []
        assert results["Mapa"]

    async def test_unreadable_scanner_db_is_a_failure(self, monkeypatch):
        async def stop():
            raise RuntimeError("db down")

        results, _ = await self._run(monkeypatch, stop)
        assert results["Mapa"]
