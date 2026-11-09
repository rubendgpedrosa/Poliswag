import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules import account_guard as ag
from modules.account_guard import AccountGuard, area_of, describe, evaluate, parse_line

T0 = datetime.datetime(2026, 11, 5, 20, 36, 30, tzinfo=datetime.timezone.utc)


def stamp(seconds):
    t = T0 + datetime.timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "123Z"


def problem_line(seconds, worker="Leiria_26"):
    return (
        f"{stamp(seconds)} WARN 2026-11-05 20:36:32 [{worker}] ACCOUNT PROBLEM: "
        "Initial Route Check returns RPC4 - SHADOW BANNED"
    )


def authfail_line(seconds, worker="Leiria_01"):
    return (
        f"{stamp(seconds)} WARN 2026-11-04 19:20:00 [{worker}] AUTHQUEUE: User x "
        'Error during authentication Post "https://access.pokemon.com/oauth2/token"'
    )


def lork_line(seconds):
    return (
        f"{stamp(seconds)} \x1b[2m19:20:00\x1b[0m | \x1b[31mERROR\x1b[0m | Lork | "
        "Authentication failed error_type=imperva"
    )


def at(seconds):
    return (T0 + datetime.timedelta(seconds=seconds)).timestamp()


class TestParseLine:
    def test_shadow_ban_problem(self):
        t, kind, worker, detail = parse_line("dragonite", problem_line(0))
        assert kind == "problem"
        assert worker == "Leiria_26"
        assert detail.startswith("Initial Route Check returns RPC4")
        assert t == pytest.approx(at(0))

    def test_dragonite_auth_error(self):
        assert parse_line("dragonite-level", authfail_line(0))[1] == "authfail"

    def test_lork_failure_with_colour_codes(self):
        assert parse_line("lork", lork_line(0))[1] == "authfail"

    def test_other_lines_are_ignored(self):
        line = f"{stamp(0)} INFO [Leiria_26] PTCAUTH: Performing refresh"
        assert parse_line("dragonite", line) is None
        assert parse_line("lork", f"{stamp(0)} INFO Asset cache refreshed") is None

    def test_bad_timestamp_is_ignored(self):
        assert parse_line("dragonite", "garbage ACCOUNT PROBLEM: x") is None


def ev(seconds, stack="main", kind="problem", worker="Leiria_26"):
    return (at(seconds), stack, kind, worker, "detail")


class TestEvaluate:
    def test_one_worker_three_flags_in_five_minutes(self):
        trips = evaluate([ev(0), ev(2.5), ev(5)], at(5))
        assert [t[:4] for t in trips] == [("worker", "main", "Leiria_26", 3)]

    def test_two_flags_are_not_enough(self):
        assert evaluate([ev(0), ev(2.5)], at(3)) == []

    def test_flags_spread_over_more_than_five_minutes(self):
        assert evaluate([ev(0), ev(200), ev(400)], at(400)) == []

    def test_fleet_needs_three_workers(self):
        events = [ev(i * 30, worker=f"Leiria_{i % 3}") for i in range(5)]
        rules = {t[0] for t in evaluate(events, at(120))}
        assert "fleet" in rules

    def test_fleet_with_two_workers_does_not_trip(self):
        events = [ev(i * 100, worker=f"Leiria_{i % 2}") for i in range(5)]
        assert all(t[0] != "fleet" for t in evaluate(events, at(400)))

    def test_twenty_auth_failures_in_fifteen_minutes(self):
        events = [ev(i * 40, kind="authfail") for i in range(20)]
        assert [t[:4] for t in evaluate(events, at(760))] == [("auth", "main", "", 20)]

    def test_nineteen_auth_failures_do_not_trip(self):
        events = [ev(i * 40, kind="authfail") for i in range(19)]
        assert evaluate(events, at(720)) == []

    def test_stacks_are_counted_apart(self):
        events = [ev(i, kind="authfail", stack="main") for i in range(10)] + [
            ev(i, kind="authfail", stack="level") for i in range(10)
        ]
        assert evaluate(events, at(10)) == []


class TestDescribe:
    def test_worker_alert_names_the_area_and_says_nothing_was_paused(self):
        title, text = describe(("worker", "main", "Leiria_26", 3, "RPC4"))
        assert "Leiria_26" in title
        assert "a área Leiria" in text
        assert "nada foi pausado" in text

    def test_levelling_pause_is_the_mode(self):
        _, text = describe(("worker", "level", "LevelUp_05", 3, "x"))
        assert "modo LevelUp" in text
        _, text = describe(("auth", "level", "", 25, "x"))
        assert "LevelUp a 0" in text

    def test_area_of(self):
        assert area_of("MarinhaGrande_05") == "MarinhaGrande"
        assert area_of("?") == "?"


class TestGuard:
    def make(self):
        guard = AccountGuard(poliswag=MagicMock())
        guard._started = at(0)
        return guard

    def test_ingest_drops_repeated_lines(self):
        guard = self.make()
        lines = {"dragonite": [problem_line(1), problem_line(2)]}
        guard.ingest(lines, at(3))
        guard.ingest(lines, at(4))  # the next read overlaps by a second
        assert len(guard._events) == 2

    def test_replay_of_2026_11_05_alerts_once_at_the_third_flag(self):
        guard = self.make()
        fired = []
        for i in range(10):  # one flag every 2.5 s, like Leiria_26
            now = at(i * 2.5)
            guard.ingest({"dragonite": [problem_line(i * 2.5)]}, now)
            fired += [(i, t[0]) for t in guard.due_trips(now)]
        assert fired == [(2, "worker")]

    def test_cooldown_then_alerts_again(self, monkeypatch):
        guard = self.make()
        guard.ingest({"dragonite": [problem_line(s) for s in (1, 2, 3)]}, at(3))
        assert len(guard.due_trips(at(3))) == 1
        late = ag.COOLDOWN + 10
        guard.ingest(
            {"dragonite": [problem_line(late + s) for s in (1, 2, 3)]}, at(late + 3)
        )
        assert [t[0] for t in guard.due_trips(at(late + 3))] == ["worker"]

    def test_incident_from_before_a_restart_is_not_reported(self):
        guard = self.make()
        guard._started = at(100)
        guard.ingest({"dragonite": [problem_line(s) for s in (1, 2, 3)]}, at(100))
        assert guard.due_trips(at(100)) == []


class TestTick:
    async def test_dev_does_nothing(self, monkeypatch):
        monkeypatch.setattr(ag.Config, "IS_PRODUCTION", False)
        guard = AccountGuard(poliswag=MagicMock())
        guard._read_new_lines = MagicMock()
        await guard.tick()
        guard._read_new_lines.assert_not_called()

    async def test_production_dms_the_owner(self, monkeypatch):
        monkeypatch.setattr(ag.Config, "IS_PRODUCTION", True)
        notify = AsyncMock(return_value=True)
        monkeypatch.setattr(ag, "notify_owner", notify)
        monkeypatch.setattr(ag.time, "time", lambda: at(10))
        guard = AccountGuard(poliswag=MagicMock())
        guard._started = at(0)
        guard._cursor = int(at(0))
        guard._read_new_lines = MagicMock(
            return_value={"dragonite": [problem_line(s) for s in (5, 6, 7)]}
        )
        await guard.tick()
        notify.assert_awaited_once()
        assert notify.await_args.kwargs["tag"] == "account_guard"
        guard._read_new_lines.assert_called_once_with(int(at(0)), at(10))
