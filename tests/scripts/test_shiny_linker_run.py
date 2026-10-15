"""The linker's decisions, with the phone, Rotom, the lobby and the database
all faked: which guards block a run, what each failure does to the queue,
and when the linker switches itself off."""

import subprocess
from types import SimpleNamespace

import pytest

from scripts.shiny_linker.linker import Linker
from scripts.shiny_linker.lobby import CodeError, RunError
from scripts.shiny_linker.proto import integer
from scripts.shiny_linker.remote import NoRaid

ID_A = "0123456789abcdef"
ID_B = "fedcba9876543210"


class HookError(RuntimeError):
    pass


class NoWorker(RuntimeError):
    pass


class FakeStore:
    def __init__(self):
        self.enabled = 1
        self.minutes_since_run = None
        self.alerted_version = None
        self.rows = [{"discord_id": 1, "friend_code": "111111111111", "attempts": 0}]
        self.gym = ("gym1", 39.74, -8.80)
        self.accounts = [("acct1", "token1")]
        self.tired = set()  # accounts Dragonite used in the last two hours
        self.rested_asked = []
        self.linked, self.failed, self.bumped = [], [], []
        self.breaker = None
        self.started = 0
        self.processing_started, self.processing_finished = [], []

    def state(self):
        return {
            "enabled": self.enabled,
            "minutes_since_run": self.minutes_since_run,
            "alerted_version": self.alerted_version,
        }

    def pending(self, limit):
        return self.rows[:limit]

    def raid_gym(self):
        return self.gym

    def accounts_free(self, usernames, in_use, rested=0):
        self.rested_asked.append(rested)
        return [
            (name, token)
            for name, token in self.accounts
            if name in usernames
            and name not in in_use
            and not (rested and name in self.tired)
        ]

    def start_run(self):
        self.started += 1

    def start_processing(self, discord_ids):
        self.processing_started.extend(discord_ids)

    def finish_processing(self, discord_ids):
        self.processing_finished.extend(discord_ids)

    def mark_linked(self, discord_id, trainer_name, player_id):
        self.linked.append((discord_id, trainer_name, player_id))

    def mark_failed(self, discord_id, reason):
        self.failed.append((discord_id, reason))

    def bump_attempts(self, discord_ids, limit, reason):
        self.bumped.append((list(discord_ids), limit))

    def trip_breaker(self, reason):
        self.breaker = reason

    def set_alerted_version(self, version):
        self.alerted_version = version


class World:
    """Everything outside the linker, with knobs for what goes wrong."""

    def __init__(self):
        self.store = FakeStore()
        self.idle = 4
        self.busy = 0  # scanning controllers a worker could be borrowed from
        self.no_worker_times = 0  # how many session attempts find no worker
        self.in_use = set()
        self.pid = 100
        self.pid_after = 100
        self.mapping = 200
        self.version = "0.431.0"
        self.attach_error = None
        self.detached = True
        self.login_error = None
        self.search = {"111111111111": ("Rita99", "E:a"), "222222222222": ("Zé", "E:b")}
        self.invite = {"E:a": ID_A, "E:b": ID_B}
        self.open_error = None
        self.notified = []
        self.events = []
        self.now = 0.0  # seconds on the linker's clock
        self.lobby_seconds = 0  # how long each lobby takes
        self.remote_gym = ("gymCA", 36.98, -122.03)  # what the map there shows
        self.sessions = []  # every session opened, to read where it stands
        self.lobby_gyms = []
        world = self

        class Session:
            def __init__(self, lat, lon):
                if world.no_worker_times > 0:
                    world.no_worker_times -= 1
                    raise NoWorker("rotom gave no idle worker")
                world.events.append("session")
                self.host = b""
                self.lat, self.lon = lat, lon
                self.opened_at = (lat, lon)
                world.sessions.append(self)

            def login(self, account, token):
                if world.login_error:
                    raise world.login_error
                world.events.append(f"login {account}")

            def rpc(self, method, payload=b""):
                return integer(1, 1)

            def close(self):
                world.events.append("close")

        class Hook:
            def attach(self, pid):
                world.events.append("attach")
                if world.attach_error:
                    raise world.attach_error

            def detach(self, send):
                world.events.append("detach")
                send(b"detach")
                return world.detached

        class Lobby:
            def __init__(self, session, hook, gym):
                world.lobby_gyms.append(gym)

            def search(self, code):
                found = world.search[code]
                if isinstance(found, Exception):
                    raise found
                return found

            def open(self):
                world.events.append("open")
                world.now += world.lobby_seconds
                if world.open_error:
                    raise world.open_error

            def invite(self, seen):
                found = world.invite[seen]
                if isinstance(found, Exception):
                    raise found
                return found

            def leave(self):
                world.events.append("leave")

        calls = {"pid": 0}

        def game_pid():
            calls["pid"] += 1
            return world.pid if calls["pid"] == 1 else world.pid_after

        self.rotom = SimpleNamespace(
            status=lambda: {"in_use": world.in_use},
            idle_workers=lambda state: world.idle,
            accounts_in_use=lambda state: set(state["in_use"]),
            to_borrow=lambda state: {"id": "Leiria_14"} if world.busy else None,
            release_one=lambda state: world.events.append("release")
            or bool(world.busy),
            NoWorker=NoWorker,
            Session=Session,
        )
        self.phone = SimpleNamespace(
            game_pid=game_pid,
            mapping_pid=lambda: world.mapping,
            game_version=lambda: world.version,
            Hook=Hook,
            HookError=HookError,
        )
        self.lobby_cls = Lobby

    def find_raid(self, session):
        self.events.append("map")
        if isinstance(self.remote_gym, Exception):
            raise self.remote_gym
        return self.remote_gym

    def linker(self, **env):
        settings = {
            "SHINY_LINKER_ACCOUNTS": "acct1,acct2",
            "SHINY_LINKER_GAME_VERSIONS": "0.431.0",
            "SHINY_LINKER_SELFTEST_CODE": "999999999999",
            "SHINY_LINKER_SELFTEST_PLAYER_ID": ID_B,
        }
        settings.update(env)
        return Linker(
            self.store,
            settings,
            rotom=self.rotom,
            phone=self.phone,
            lobby_cls=self.lobby_cls,
            find_raid=self.find_raid,
            notify=self.notified.append,
            sleep=lambda _seconds: None,
            clock=lambda: self.now,
            shuffle=lambda accounts: None,  # tests keep the listed order
        )


@pytest.fixture
def world():
    return World()


class TestGuards:
    def test_a_clean_world_links_the_pending_code(self, world):
        outcome = world.linker().run()
        assert world.store.linked == [(1, "Rita99", ID_A)]
        assert world.store.started == 1
        assert "1 ligado" in outcome
        # Logged in before the hook went in, and left before it came out.
        assert world.events == [
            "session",
            "login acct1",
            "attach",
            "open",
            "leave",
            "detach",
            "close",
        ]

    @pytest.mark.parametrize(
        "break_it, guard",
        [
            (lambda w: setattr(w.store, "enabled", 0), "ligador ativo"),
            (lambda w: setattr(w.store, "rows", []), "códigos pendentes"),
            (lambda w: setattr(w.store, "minutes_since_run", 4), "intervalo"),
            (lambda w: setattr(w.store, "gym", None), "raid ativa"),
            (lambda w: setattr(w, "idle", 0), "worker livre"),
            (lambda w: setattr(w, "in_use", {"acct1", "acct2"}), "conta livre"),
            (lambda w: setattr(w, "pid", None), "jogo a correr"),
            (lambda w: setattr(w, "mapping", None), "Aegis a mapear"),
            (lambda w: setattr(w, "version", "0.433.0"), "versão validada"),
        ],
    )
    def test_each_guard_alone_blocks_the_run(self, world, break_it, guard):
        break_it(world)
        outcome = world.linker().run()
        assert guard in outcome
        assert world.events == [] and world.store.started == 0
        assert world.store.linked == [] and world.store.breaker is None

    def test_five_minutes_since_the_last_run_is_enough(self, world):
        world.store.minutes_since_run = 5
        world.linker().run()
        assert world.store.linked

    def test_the_second_account_is_used_when_the_first_is_scanning(self, world):
        world.in_use = {"acct1"}
        world.store.accounts = [("acct1", "t1"), ("acct2", "t2")]
        world.linker().run()
        assert "login acct2" in world.events

    def test_an_unvalidated_version_alerts_the_owner_once(self, world):
        world.version = "0.433.0"
        world.linker().run()
        world.linker().run()
        assert len(world.notified) == 1 and "0.433.0" in world.notified[0]

    def test_check_evaluates_every_guard_and_touches_nothing(self, world):
        world.store.rows = []
        world.idle = 0
        checks, _ = world.linker().survey()
        failed = [name for name, ok, _ in checks if not ok]
        assert failed == ["códigos pendentes", "worker livre"]
        assert world.events == []


class TestBorrowedWorker:
    def test_an_idle_worker_is_used_without_touching_the_scanner(self, world):
        world.busy = 16
        world.linker().run()
        assert "release" not in world.events and world.store.linked

    def test_with_none_idle_one_scanning_worker_is_borrowed(self, world):
        world.idle, world.busy, world.no_worker_times = 0, 16, 1
        world.linker().run()
        assert world.events[:3] == ["release", "session", "login acct1"]
        assert world.store.linked == [(1, "Rita99", ID_A)]

    def test_the_guard_says_a_worker_will_be_borrowed(self, world):
        world.idle, world.busy = 0, 16
        checks, _ = world.linker().survey()
        (worker,) = [c for c in checks if c[0] == "worker livre"]
        assert worker[1] is True and "empresta" in worker[2]

    def test_losing_the_race_for_the_freed_worker_costs_the_codes_nothing(self, world):
        world.idle, world.busy, world.no_worker_times = 0, 16, 10_000
        outcome = world.linker().run()
        assert world.events.count("release") == 3  # then it gives up until next time
        assert "worker" in outcome
        assert world.store.bumped == [] and world.store.breaker is None
        assert "attach" not in world.events


class TestFailures:
    def test_an_unknown_code_fails_and_the_rest_are_linked(self, world):
        world.store.rows.append(
            {"discord_id": 2, "friend_code": "222222222222", "attempts": 0}
        )
        world.search["111111111111"] = CodeError("código não encontrado")
        world.linker().run()
        assert world.store.failed == [(1, "código não encontrado")]
        assert world.store.linked == [(2, "Zé", ID_B)]

    def test_no_lobby_is_opened_when_no_code_resolved(self, world):
        world.search["111111111111"] = CodeError("código não encontrado")
        world.linker().run()
        assert "open" not in world.events

    def test_a_refused_invite_fails_only_that_code(self, world):
        world.invite["E:a"] = CodeError("convite recusado pelo jogo (10)")
        world.linker().run()
        assert world.store.failed == [(1, "convite recusado pelo jogo (10)")]
        assert world.store.breaker is None

    def test_a_run_error_leaves_codes_pending_with_one_more_attempt(self, world):
        world.open_error = RunError("entrada no lobby privado falhou (9)")
        outcome = world.linker().run()
        assert world.store.bumped == [([1], 5)]
        assert world.store.linked == [] and world.store.failed == []
        assert world.store.breaker is None
        assert "falhou (9)" in outcome
        assert world.events[-3:] == ["leave", "detach", "close"]

    def test_a_failed_login_never_touches_the_game(self, world):
        world.login_error = RuntimeError("login did not reach a token (5)")
        world.linker().run()
        assert "attach" not in world.events and "detach" not in world.events
        assert world.store.bumped == [([1], 5)]
        assert world.store.breaker is None

    def test_an_account_that_started_scanning_is_not_logged_in(self, world):
        states = iter([set(), {"acct1"}])
        world.rotom.status = lambda: {"in_use": next(states)}
        world.linker().run()
        assert "login acct1" not in world.events and "attach" not in world.events
        assert world.store.breaker is None

    def test_a_hook_error_switches_the_linker_off_and_tells_the_owner(self, world):
        world.attach_error = HookError("hook setup failed")
        world.linker().run()
        assert world.store.breaker == "hook setup failed"
        assert len(world.notified) == 1 and "desligado" in world.notified[0]

    def test_a_hook_that_did_not_leave_switches_the_linker_off(self, world):
        world.detached = False
        world.linker().run()
        assert "hook" in world.store.breaker
        assert world.store.linked == [(1, "Rita99", ID_A)]  # the work is kept

    def test_a_game_restart_during_the_run_switches_the_linker_off(self, world):
        world.pid_after = 999
        world.linker().run()
        assert "reiniciou" in world.store.breaker

    def test_an_unavailable_pid_does_not_claim_the_game_restarted(self, world):
        world.pid_after = None
        outcome = world.linker().run()
        assert "confirmar o estado do jogo" in world.store.breaker
        assert "reiniciou" not in outcome
        assert world.store.linked == [(1, "Rita99", ID_A)]

    def test_a_failed_login_and_unavailable_pid_do_not_trip_the_breaker(self, world):
        world.login_error = RuntimeError("login unavailable")
        world.pid_after = None
        world.linker().run()
        assert world.store.breaker is None
        assert "attach" not in world.events

    def test_a_hook_error_survives_an_unavailable_pid(self, world):
        world.attach_error = HookError("adb failed (exit 1): device offline")
        world.pid_after = None
        world.linker().run()
        assert world.store.breaker == "adb failed (exit 1): device offline"

    def test_subprocess_failure_logs_safe_details_and_stack(self, world, caplog):
        world.login_error = subprocess.CalledProcessError(
            1, ["adb", "push", "secret-token"], stderr=b"error: device offline"
        )
        outcome = world.linker().run()
        assert "adb failed (exit 1): error: device offline" in outcome
        assert "CalledProcessError" in caplog.text
        assert "test_shiny_linker_run.py" in caplog.text
        assert "secret-token" not in caplog.text

    def test_unknown_failure_does_not_log_private_exception_text(self, world, caplog):
        world.login_error = RuntimeError("secret-token rpc-payload")
        world.linker().run()
        assert "RuntimeError" in caplog.text
        assert "secret-token" not in caplog.text


def queue(world, count):
    """`count` pending players whose codes all resolve."""
    world.store.rows = []
    for n in range(1, count + 1):
        code = f"{n:012d}"
        world.store.rows.append({"discord_id": n, "friend_code": code, "attempts": 0})
        world.search[code] = (f"T{n}", f"E:{n}")
        world.invite[f"E:{n}"] = f"{n:016x}"


class TestWholeQueue:
    """One run empties the queue on one worker: a lobby of ten at a time,
    another account for each lobby, the hook attached once."""

    def test_twelve_codes_take_two_lobbies_and_two_accounts(self, world):
        queue(world, 12)
        world.store.accounts = [("acct1", "t1"), ("acct2", "t2")]
        outcome = world.linker().run()
        assert len(world.store.linked) == 12 and "12 ligado" in outcome
        assert world.events == [
            "session",
            "login acct1",
            "attach",
            "open",
            "leave",
            "login acct2",  # same session: the worker is never handed back
            "open",
            "leave",
            "detach",
            "close",
        ]

    def test_the_accounts_come_round_again(self, world):
        queue(world, 25)
        world.store.accounts = [("acct1", "t1"), ("acct2", "t2")]
        world.linker().run()
        logins = [e for e in world.events if e.startswith("login")]
        assert logins == ["login acct1", "login acct2", "login acct1"]
        assert world.events.count("session") == 1

    def test_a_single_account_just_opens_the_next_lobby(self, world):
        queue(world, 12)
        world.linker(SHINY_LINKER_ACCOUNTS="acct1").run()
        assert [e for e in world.events if e.startswith("login")] == ["login acct1"]
        assert world.events.count("open") == 2 and len(world.store.linked) == 12

    def test_an_account_that_started_scanning_is_passed_over(self, world):
        queue(world, 12)
        world.store.accounts = [("acct1", "t1"), ("acct2", "t2"), ("acct3", "t3")]
        # Free at the guard and at the first login; acct2 is scanning by the
        # time the second lobby is due.
        states = iter([set(), set(), {"acct1", "acct2"}])
        world.rotom.status = lambda: {"in_use": next(states)}
        world.linker(SHINY_LINKER_ACCOUNTS="acct1,acct2,acct3").run()
        logins = [e for e in world.events if e.startswith("login")]
        assert logins == ["login acct1", "login acct3"]

    def test_which_account_goes_first_is_shuffled(self, world):
        world.store.accounts = [("acct1", "t1"), ("acct2", "t2")]
        linker = world.linker()
        linker.shuffle = lambda accounts: accounts.reverse()
        linker.run()
        assert "login acct2" in world.events and "login acct1" not in world.events

    def test_what_does_not_fit_the_time_waits_for_the_next_run(self, world):
        queue(world, 25)
        world.lobby_seconds = 200  # two lobbies and the time is up
        outcome = world.linker().run()
        assert len(world.store.linked) == 20
        assert world.store.bumped == []  # the five left over are not penalised
        assert "5 por resolver" in outcome
        assert world.events[-2:] == ["detach", "close"]

    def test_a_run_takes_no_more_than_fifty_codes(self, world):
        queue(world, 60)
        world.linker().run()
        assert len(world.store.linked) == 50

    def test_an_error_in_a_later_lobby_only_costs_that_lobby(self, world):
        queue(world, 12)
        world.invite["E:11"] = RunError("sem resposta do lobby")
        outcome = world.linker().run()
        assert len(world.store.linked) == 10
        assert world.store.bumped == [([11, 12], 5)]
        assert "sem resposta do lobby" in outcome
        assert world.events[-3:] == ["leave", "detach", "close"]


class TestValidate:
    def test_passes_when_the_known_account_returns_its_known_id(self, world):
        world.search["999999999999"] = ("Scanner", "E:b")
        world.version = "0.433.0"  # the point of validating
        world.store.rows = []
        world.store.minutes_since_run = 1
        outcome = world.linker().run(validate=True)
        assert outcome.startswith("validação OK")
        assert world.store.linked == [] and world.store.started == 0

    def test_it_resolves_the_account_twice_to_prove_the_account_switch(self, world):
        world.search["999999999999"] = ("Scanner", "E:b")
        world.store.accounts = [("acct1", "t1"), ("acct2", "t2")]
        outcome = world.linker().run(validate=True)
        assert outcome.startswith("validação OK")
        assert [e for e in world.events if e.startswith("login")] == [
            "login acct1",
            "login acct2",
        ]
        assert world.events.count("open") == 2 and world.events.count("attach") == 1

    def test_fails_when_only_the_first_lobby_worked(self, world):
        world.search["999999999999"] = ("Scanner", "E:b")
        answers = iter([ID_B, RunError("sem resposta do lobby")])

        class Twice(world.lobby_cls):
            def invite(self, seen):
                answer = next(answers)
                if isinstance(answer, Exception):
                    raise answer
                return answer

        world.lobby_cls = Twice
        outcome = world.linker().run(validate=True)
        assert outcome.startswith("validação FALHOU")

    def test_fails_when_another_id_comes_back(self, world):
        world.search["999999999999"] = ("Scanner", "E:a")
        outcome = world.linker().run(validate=True)
        assert outcome.startswith("validação FALHOU")
        assert world.store.bumped == [] and world.store.failed == []

    def test_still_needs_the_linker_switched_on(self, world):
        world.store.enabled = 0
        assert "ligador ativo" in world.linker().run(validate=True)


SITE = "36.9741,-122.0258"


class TestRemoteSite:
    """With no raid in the scanned areas, a run goes to the configured point
    elsewhere: it looks at the map there before the hook goes in."""

    def test_a_local_raid_keeps_the_run_at_home(self, world):
        world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run()
        assert "map" not in world.events
        assert world.sessions[0].opened_at == (39.74, -8.80)
        assert world.store.rested_asked == [0]
        assert world.lobby_gyms == [("gym1", 39.74, -8.80)]

    def test_without_a_local_raid_it_finds_one_at_the_site(self, world):
        world.store.gym = None
        outcome = world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run()
        assert world.store.linked == [(1, "Rita99", ID_A)] and "1 ligado" in outcome
        # Logged in there and looked at the map before the hook went in.
        assert world.events == [
            "session",
            "login acct1",
            "map",
            "attach",
            "open",
            "leave",
            "detach",
            "close",
        ]
        (session,) = world.sessions
        assert session.opened_at == (36.9741, -122.0258)
        assert (session.lat, session.lon) == (36.98, -122.03)  # at the gym
        assert world.lobby_gyms == [("gymCA", 36.98, -122.03)]

    def test_the_guard_says_where_it_will_look(self, world):
        world.store.gym = None
        checks, _ = world.linker(SHINY_LINKER_REMOTE_SITE=SITE).survey()
        (raid,) = [c for c in checks if c[0] == "raid ativa"]
        assert raid[1] is True and "nenhuma local" in raid[2]
        assert world.events == []

    def test_an_unreadable_site_is_no_site(self, world):
        world.store.gym = None
        outcome = world.linker(SHINY_LINKER_REMOTE_SITE="santa cruz").run()
        assert "raid ativa" in outcome and world.events == []

    def test_only_accounts_dragonite_left_alone_go_there(self, world):
        world.store.gym = None
        world.store.accounts = [("acct1", "t1"), ("acct2", "t2")]
        world.store.tired = {"acct1"}
        world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run()
        assert world.store.rested_asked == [7200]
        assert "login acct2" in world.events and "login acct1" not in world.events

    def test_with_no_rested_account_nothing_is_touched(self, world):
        world.store.gym = None
        world.store.tired = {"acct1"}
        outcome = world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run()
        assert "conta livre" in outcome
        assert world.events == [] and world.store.started == 0

    def test_no_raid_there_ends_before_the_hook_and_costs_the_codes_nothing(
        self, world
    ):
        world.store.gym = None
        world.remote_gym = NoRaid("sem raid no local remoto (7 ginásios)")
        outcome = world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run()
        assert world.events == ["session", "login acct1", "map", "close"]
        assert world.store.bumped == [] and world.store.breaker is None
        assert "sem raid no local remoto (7 ginásios)" in outcome
        assert world.store.started == 1  # the interval still applies

    def test_a_map_that_cannot_be_read_counts_as_a_failed_run(self, world):
        world.store.gym = None
        world.remote_gym = RunError("mapa remoto sem ginásios")
        outcome = world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run()
        assert "attach" not in world.events
        assert world.store.bumped == [([1], 5)]
        assert "mapa remoto sem ginásios" in outcome

    def test_later_lobbies_stay_at_the_gym_found(self, world):
        world.store.gym = None
        queue(world, 12)
        world.store.accounts = [("acct1", "t1"), ("acct2", "t2")]
        world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run()
        assert len(world.store.linked) == 12
        assert world.events.count("map") == 1 and world.events.count("session") == 1
        assert world.lobby_gyms == [("gymCA", 36.98, -122.03)] * 2

    def test_asked_to_it_skips_a_local_raid(self, world):
        world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run(remote=True)
        assert "map" in world.events
        assert world.lobby_gyms == [("gymCA", 36.98, -122.03)]

    def test_asked_to_without_a_site_it_does_nothing(self, world):
        outcome = world.linker().run(remote=True)
        assert "raid ativa" in outcome and "sem local remoto" in outcome
        assert world.events == []

    def test_validation_can_prove_the_remote_path(self, world):
        world.search["999999999999"] = ("Scanner", "E:b")
        world.store.accounts = [("acct1", "t1"), ("acct2", "t2")]
        outcome = world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run(
            validate=True, remote=True
        )
        assert outcome.startswith("validação OK")
        assert world.events.count("map") == 1 and world.events.count("open") == 2
        assert world.store.started == 0


class TestProcessingFeedback:
    def test_processing_begins_before_search_and_finishes_after_linking(self, world):
        base = world.lobby_cls

        class ObservedLobby(base):
            def search(self, code):
                assert world.store.processing_started == [1]
                assert world.store.processing_finished == []
                return super().search(code)

        world.lobby_cls = ObservedLobby
        outcome = world.linker().run()
        assert "1 ligado" in outcome
        assert world.store.processing_finished == [1]

    def test_failed_processing_clears_feedback_for_retry(self, world):
        world.open_error = RunError("raid unavailable")
        world.linker().run()
        assert world.store.processing_started == [1]
        assert world.store.processing_finished == [1]
        assert world.store.bumped

    def test_waiting_for_infrastructure_is_not_reported_as_processing(self, world):
        world.login_error = RunError("login failed")
        world.linker().run()
        assert world.store.processing_started == []
        assert world.store.processing_finished == []

    def test_validation_does_not_publish_player_progress(self, world):
        world.search["999999999999"] = ("SelfTest", "E:b")
        world.linker().run(validate=True)
        assert world.store.processing_started == []
        assert world.store.processing_finished == []
