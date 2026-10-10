"""The linker's decisions, with the phone, Rotom, the lobby and the database
all faked: which guards block a run, what each failure does to the queue,
and when the linker switches itself off."""

from types import SimpleNamespace

import pytest

from scripts.shiny_linker.linker import Linker
from scripts.shiny_linker.lobby import CodeError, RunError
from scripts.shiny_linker.proto import integer

ID_A = "0123456789abcdef"
ID_B = "fedcba9876543210"


class HookError(RuntimeError):
    pass


class FakeStore:
    def __init__(self):
        self.enabled = 1
        self.minutes_since_run = None
        self.alerted_version = None
        self.rows = [{"discord_id": 1, "friend_code": "111111111111", "attempts": 0}]
        self.gym = ("gym1", 39.74, -8.80)
        self.accounts = [("acct1", "token1")]
        self.linked, self.failed, self.bumped = [], [], []
        self.breaker = None
        self.started = 0

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

    def account(self, usernames, in_use):
        for name, token in self.accounts:
            if name in usernames and name not in in_use:
                return name, token
        return None

    def start_run(self):
        self.started += 1

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
        world = self

        class Session:
            def __init__(self, lat, lon):
                world.events.append("session")
                self.host = b""

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
                pass

            def search(self, code):
                found = world.search[code]
                if isinstance(found, Exception):
                    raise found
                return found

            def open(self):
                world.events.append("open")
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
            notify=self.notified.append,
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
            (lambda w: setattr(w.store, "minutes_since_run", 59), "intervalo"),
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

    def test_an_hour_since_the_last_run_is_enough(self, world):
        world.store.minutes_since_run = 60
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


class TestValidate:
    def test_passes_when_the_known_account_returns_its_known_id(self, world):
        world.search["999999999999"] = ("Scanner", "E:b")
        world.version = "0.433.0"  # the point of validating
        world.store.rows = []
        world.store.minutes_since_run = 1
        outcome = world.linker().run(validate=True)
        assert outcome.startswith("validação OK")
        assert world.store.linked == [] and world.store.started == 0

    def test_fails_when_another_id_comes_back(self, world):
        world.search["999999999999"] = ("Scanner", "E:a")
        outcome = world.linker().run(validate=True)
        assert outcome.startswith("validação FALHOU")
        assert world.store.bumped == [] and world.store.failed == []

    def test_still_needs_the_linker_switched_on(self, world):
        world.store.enabled = 0
        assert "ligador ativo" in world.linker().run(validate=True)
