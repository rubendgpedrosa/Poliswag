"""One linker run: decide whether to run at all, then work through the
pending codes on one worker: a private lobby of up to ten codes at a time,
a different scanner account for each lobby.

The scanner comes first. A run only starts when every guard holds, uses an
account nobody else is using, logs in before the game is touched, and
switches the whole linker off (and tells the owner) if the hook or the game
misbehaves. It never restarts anything. When every worker is scanning it
borrows one for the length of the run (owner's decision, 2026-10-10): one
scanning controller is disconnected and Dragonite takes the worker back the
moment the linker's session closes. It is always that one worker: the next
account logs in on the same session, and the hook goes into the game once
per run however many lobbies follow.

With no raid in the scanned areas and a remote site configured, the run
happens there instead (remote.py): an account Dragonite has left alone for
two hours logs in at the site and looks at the map once; the hook only goes
in when that shows a raid.
"""

import random
import subprocess
import sys
import time
from pathlib import Path

from . import phone as _phone
from . import remote as _remote
from . import rotom as _rotom
from .lobby import CodeError, Lobby, RunError
from .proto import fields, one

# A lobby starts its battle two minutes after it opens, which is about what
# ten invites take.
LOBBY_CODES = 10
RUN_CODES = 50
# No new lobby is opened this long after the hook went in; the hook removes
# itself at ten minutes (phone.LIFETIME_MS). Codes left over wait for the
# next run and lose nothing.
RUN_SECONDS = 360
MAX_ATTEMPTS = 5
# Under the timer's ten minutes, so every timer run may link; it only
# holds back a run started by hand right after another.
MIN_MINUTES_BETWEEN_RUNS = 5
# With every worker scanning, the linker disconnects one scanning controller
# and takes the worker it frees. Dragonite reconnects at once and may win it
# back, so that is tried this many times before the run gives up.
BORROW_ATTEMPTS = 3
# Quarter-second tries for the freed worker after each disconnect.
WORKER_TRIES = 20
VERSION_GUARD = "versão validada"
_NOTIFY = Path(__file__).resolve().parent.parent / "notify_owner.py"


def notify_owner(message):
    subprocess.run(
        [sys.executable, str(_NOTIFY), message, "Shiny linker"], timeout=60, check=False
    )


def _listed(value):
    return [part.strip() for part in (value or "").split(",") if part.strip()]


class Linker:
    def __init__(
        self,
        store,
        env,
        rotom=_rotom,
        phone=_phone,
        lobby_cls=Lobby,
        find_raid=_remote.find_raid,
        notify=notify_owner,
        sleep=time.sleep,
        clock=time.monotonic,
        shuffle=random.shuffle,
    ):
        self.store, self.rotom, self.phone = store, rotom, phone
        self.lobby_cls, self.notify, self.sleep = lobby_cls, notify, sleep
        self.clock, self.shuffle = clock, shuffle
        self.find_raid = find_raid
        # Where to look for a raid when the scanned areas have none.
        self.remote = _remote.site(env.get("SHINY_LINKER_REMOTE_SITE"))
        self.accounts = _listed(env.get("SHINY_LINKER_ACCOUNTS"))
        self.versions = _listed(env.get("SHINY_LINKER_GAME_VERSIONS"))
        self.selftest_code = env.get("SHINY_LINKER_SELFTEST_CODE", "")
        self.selftest_id = env.get("SHINY_LINKER_SELFTEST_PLAYER_ID", "")
        self.session = None

    # --- guards -----------------------------------------------------------

    def survey(self, validate=False, stop_early=False, remote=False):
        """([(guard, ok, detail)], context for the run). Changes nothing.
        `remote` skips the local raid and goes straight to the remote site."""
        checks, ctx = [], {"skip_local": remote}
        for name, probe in self._guards(validate):
            ok, detail = probe(ctx)
            checks.append((name, bool(ok), detail))
            if stop_early and not ok:
                break
        return checks, ctx

    def _guards(self, validate):
        guards = [("ligador ativo", self._enabled)]
        if validate:
            guards.append(("conta de teste configurada", self._selftest))
        else:
            guards += [
                ("códigos pendentes", self._pending),
                ("intervalo desde a última ligação", self._interval),
            ]
        guards += [
            ("raid ativa", self._raid),
            ("worker livre", self._worker),
            ("conta livre", self._account),
            ("jogo a correr", self._game),
            ("Aegis a mapear", self._mapping),
        ]
        if not validate:
            guards.append((VERSION_GUARD, self._version))
        return guards

    def _enabled(self, ctx):
        ctx["state"] = self.store.state()
        return ctx["state"]["enabled"], ""

    def _selftest(self, ctx):
        # Twice, a lobby each, so the switch to another account is proven on
        # this game version too.
        ctx["targets"] = [
            {"discord_id": None, "friend_code": self.selftest_code, "attempts": 0}
            for _ in range(2)
        ]
        return self.selftest_code and self.selftest_id, ""

    def _pending(self, ctx):
        ctx["targets"] = self.store.pending(RUN_CODES)
        return ctx["targets"], f"{len(ctx['targets'])} em fila"

    def _interval(self, ctx):
        minutes = (ctx.get("state") or self.store.state())["minutes_since_run"]
        ok = minutes is None or minutes >= MIN_MINUTES_BETWEEN_RUNS
        return ok, "nunca correu" if minutes is None else f"há {minutes} min"

    def _raid(self, ctx):
        ctx["gym"] = None if ctx["skip_local"] else self.store.raid_gym()
        ctx["remote"] = not ctx["gym"] and self.remote is not None
        if ctx["remote"]:
            where = "procura no local remoto"
            return True, where if ctx["skip_local"] else f"nenhuma local; {where}"
        return ctx["gym"], "sem local remoto" if ctx["skip_local"] else ""

    def _worker(self, ctx):
        try:
            ctx["rotom"] = self.rotom.status()
        except Exception:
            return False, "rotom não responde"
        idle = self.rotom.idle_workers(ctx["rotom"])
        if idle > 0:
            return True, f"{idle} livres"
        if self.rotom.to_borrow(ctx["rotom"]) is not None:
            return True, "0 livres; empresta um ao scanner durante a ligação"
        return False, "0 livres"

    def _account(self, ctx):
        in_use = self.rotom.accounts_in_use(ctx["rotom"]) if "rotom" in ctx else set()
        # An account that jumps to the remote site must not have been
        # scanning here a moment ago.
        rested = _remote.REST_SECONDS if ctx.get("remote") else 0
        ctx["accounts"] = self.store.accounts_free(self.accounts, in_use, rested)
        # Not always the same one first: the lobbies are spread over the list.
        self.shuffle(ctx["accounts"])
        detail = f"{len(ctx['accounts'])} livres"
        return ctx["accounts"], detail + (
            f", paradas há {rested // 3600} h" if rested else ""
        )

    def _game(self, ctx):
        ctx["pid"] = self.phone.game_pid()
        return ctx["pid"], ""

    def _mapping(self, ctx):
        return self.phone.mapping_pid(), ""

    def _version(self, ctx):
        ctx["version"] = self.phone.game_version()
        return ctx["version"] in self.versions, ctx["version"] or "desconhecida"

    # --- the run ----------------------------------------------------------

    def run(self, validate=False, remote=False):
        checks, ctx = self.survey(validate, stop_early=True, remote=remote)
        name, ok, detail = checks[-1]
        if not ok:
            if name == VERSION_GUARD and ctx.get("version"):
                self._alert_new_version(ctx)
            return f"sem ligação: {name}" + (f" ({detail})" if detail else "")
        if not validate:
            self.store.start_run()
        return self._link(ctx, validate)

    def _alert_new_version(self, ctx):
        version = ctx["version"]
        if ctx["state"]["alerted_version"] == version:
            return
        self.store.set_alerted_version(version)
        self.notify(
            f"O jogo no telemóvel passou para {version}. As ligações de shiny "
            "ficam paradas até correres `shiny-linker --validate` e juntares a "
            "versão a SHINY_LINKER_GAME_VERSIONS."
        )

    def _login(self, place, account, token):
        # Re-checked here: minutes may have passed since the guard looked.
        if account in self.rotom.accounts_in_use(self.rotom.status()):
            raise RunError("a conta entrou em uso pelo scanner")
        self.session = self._take_worker(*place)
        self.session.login(account, token)
        if one(fields(self.session.rpc(2)), 1) != 1:
            raise RunError("GET_PLAYER não respondeu com sucesso")

    def _take_worker(self, lat, lon):
        """A session on an idle worker, or on one borrowed from the scanner."""
        try:
            return self.rotom.Session(lat, lon)
        except self.rotom.NoWorker:
            pass
        for _ in range(BORROW_ATTEMPTS):
            if not self.rotom.release_one(self.rotom.status()):
                break
            for _ in range(WORKER_TRIES):
                try:
                    return self.rotom.Session(lat, lon)
                except self.rotom.NoWorker:
                    self.sleep(0.25)
        raise self.rotom.NoWorker("no worker, idle or borrowed")

    def _next_account(self, accounts, turn, current):
        """The account for lobby number `turn`: the next one down the list
        that the scanner has not taken since, logged in on the session the
        run already holds. With nothing else free, the current one stays."""
        in_use = self.rotom.accounts_in_use(self.rotom.status())
        for step in range(len(accounts)):
            candidate = accounts[(turn + step) % len(accounts)]
            if candidate == current:
                return current
            if candidate[0] in in_use:
                continue
            self.session.login(*candidate)
            if one(fields(self.session.rpc(2)), 1) != 1:
                raise RunError("GET_PLAYER não respondeu com sucesso")
            return candidate
        return current

    def _link(self, ctx, validate):
        gym, accounts, pid = ctx["gym"], ctx["accounts"], ctx["pid"]
        # Where the account stands: at the gym, or at the remote site until
        # a gym is found there.
        place = self.remote if ctx["remote"] else gym[1:]
        targets = list(ctx["targets"])
        size = 1 if validate else LOBBY_CODES
        lobbies = [targets[i : i + size] for i in range(0, len(targets), size)]
        unsettled = []  # codes of the lobbies that were started
        processing = []
        linked = failed = 0
        breaker = error = None
        penalise = True
        hook = self.phone.Hook()
        lobby = None
        attach_attempted = False
        current = accounts[0]

        def send_detach(payload):
            try:
                self.session.rpc(2, payload)
            except Exception:
                # The session died mid-run; the hook can only be asked to
                # leave through a logged-in one.
                self._close()
                self._login(place, *current)
                self.session.rpc(2, payload)

        try:
            for turn, codes in enumerate(lobbies):
                if turn == 0:
                    unsettled.extend(codes)
                    self._login(place, *current)
                    if ctx["remote"]:
                        gym = self.find_raid(self.session)
                        place = gym[1:]
                        self.session.lat, self.session.lon = place
                    attach_attempted = True
                    hook.attach(pid)
                    started = self.clock()
                else:
                    if self.clock() - started > RUN_SECONDS:
                        break
                    unsettled.extend(codes)
                    current = self._next_account(accounts, turn, current)
                if not validate:
                    discord_ids = [row["discord_id"] for row in codes]
                    self.store.start_processing(discord_ids)
                    processing.extend(discord_ids)
                lobby = self.lobby_cls(self.session, hook, gym)
                found = []
                for row in codes:
                    try:
                        found.append((row, *lobby.search(row["friend_code"])))
                    except CodeError as reason:
                        unsettled.remove(row)
                        failed += 1
                        if not validate:
                            self.store.mark_failed(row["discord_id"], str(reason))
                if found:
                    lobby.open()
                for row, trainer_name, seen_id in found:
                    try:
                        player_id = lobby.invite(seen_id)
                    except CodeError as reason:
                        unsettled.remove(row)
                        failed += 1
                        if not validate:
                            self.store.mark_failed(row["discord_id"], str(reason))
                        continue
                    unsettled.remove(row)
                    if validate:
                        if player_id != self.selftest_id:
                            raise RunError("a conta de teste devolveu outro ID")
                    else:
                        self.store.mark_linked(
                            row["discord_id"], trainer_name, player_id
                        )
                    linked += 1
                lobby.leave()
                lobby = None
        except self.phone.HookError as problem:
            breaker = str(problem)
        except self.rotom.NoWorker:
            # Not the codes' fault: they keep their attempts for next time.
            error, penalise = "sem worker para a ligação", False
        except _remote.NoRaid as problem:
            # Gyms there, none with a raid to use: the codes lose nothing.
            error, penalise = str(problem), False
        except Exception as problem:
            error = (
                str(problem)
                if isinstance(problem, RunError)
                else type(problem).__name__
            )
        finally:
            if lobby is not None:
                try:
                    lobby.leave()
                except Exception:
                    pass
            detached = True
            if attach_attempted:
                try:
                    detached = hook.detach(send_detach)
                except Exception:
                    detached = False
            self._close()
            if not detached:
                breaker = breaker or "o hook não saiu do jogo"
            if self.phone.game_pid() != pid:
                breaker = "o jogo reiniciou durante a ligação"
            if processing:
                self.store.finish_processing(processing)

        if unsettled and not validate and penalise:
            self.store.bump_attempts(
                [row["discord_id"] for row in unsettled],
                MAX_ATTEMPTS,
                "sem resposta do jogo",
            )
        if breaker:
            self.store.trip_breaker(breaker)
            self.notify(
                f"Ligador de shiny desligado: {breaker}. Vê o estado do scanner; "
                "para o voltar a ligar: UPDATE poliswag SET shiny_linker_enabled = 1."
            )
        if validate:
            if linked == len(targets) and not error and not breaker:
                return (
                    "validação OK: a conta de teste devolveu o ID conhecido "
                    f"em {len(lobbies)} lobbies"
                )
            return f"validação FALHOU: {breaker or error or 'a conta de teste não foi resolvida'}"
        waiting = len(targets) - linked - failed
        summary = f"{linked} ligado(s), {failed} falhado(s), {waiting} por resolver"
        return (
            summary
            + (f"; erro: {error}" if error else "")
            + (f"; DESLIGADO: {breaker}" if breaker else "")
        )

    def _close(self):
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
