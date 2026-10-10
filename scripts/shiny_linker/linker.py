"""One linker run: decide whether to run at all, then drive a scanner
account through a private lobby for up to ten pending codes.

The scanner comes first. A run only starts when every guard holds, uses a
worker and an account nobody else is using, logs in before the game is
touched, and switches the whole linker off (and tells the owner) if the hook
or the game misbehaves. It never restarts anything.
"""

import subprocess
import sys
from pathlib import Path

from . import phone as _phone
from . import rotom as _rotom
from .lobby import CodeError, Lobby, RunError
from .proto import fields, one

MAX_CODES = 10
MAX_ATTEMPTS = 5
MIN_MINUTES_BETWEEN_RUNS = 60
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
        notify=notify_owner,
    ):
        self.store, self.rotom, self.phone = store, rotom, phone
        self.lobby_cls, self.notify = lobby_cls, notify
        self.accounts = _listed(env.get("SHINY_LINKER_ACCOUNTS"))
        self.versions = _listed(env.get("SHINY_LINKER_GAME_VERSIONS"))
        self.selftest_code = env.get("SHINY_LINKER_SELFTEST_CODE", "")
        self.selftest_id = env.get("SHINY_LINKER_SELFTEST_PLAYER_ID", "")
        self.session = None

    # --- guards -----------------------------------------------------------

    def survey(self, validate=False, stop_early=False):
        """([(guard, ok, detail)], context for the run). Changes nothing."""
        checks, ctx = [], {}
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
        ctx["targets"] = [
            {"discord_id": None, "friend_code": self.selftest_code, "attempts": 0}
        ]
        return self.selftest_code and self.selftest_id, ""

    def _pending(self, ctx):
        ctx["targets"] = self.store.pending(MAX_CODES)
        return ctx["targets"], f"{len(ctx['targets'])} em fila"

    def _interval(self, ctx):
        minutes = (ctx.get("state") or self.store.state())["minutes_since_run"]
        ok = minutes is None or minutes >= MIN_MINUTES_BETWEEN_RUNS
        return ok, "nunca correu" if minutes is None else f"há {minutes} min"

    def _raid(self, ctx):
        ctx["gym"] = self.store.raid_gym()
        return ctx["gym"], ""

    def _worker(self, ctx):
        try:
            ctx["rotom"] = self.rotom.status()
        except Exception:
            return False, "rotom não responde"
        idle = self.rotom.idle_workers(ctx["rotom"])
        return idle > 0, f"{idle} livres"

    def _account(self, ctx):
        in_use = self.rotom.accounts_in_use(ctx["rotom"]) if "rotom" in ctx else set()
        ctx["account"] = self.store.account(self.accounts, in_use)
        return ctx["account"], ""

    def _game(self, ctx):
        ctx["pid"] = self.phone.game_pid()
        return ctx["pid"], ""

    def _mapping(self, ctx):
        return self.phone.mapping_pid(), ""

    def _version(self, ctx):
        ctx["version"] = self.phone.game_version()
        return ctx["version"] in self.versions, ctx["version"] or "desconhecida"

    # --- the run ----------------------------------------------------------

    def run(self, validate=False):
        checks, ctx = self.survey(validate, stop_early=True)
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

    def _login(self, gym, account, token):
        # Re-checked here: minutes may have passed since the guard looked.
        if account in self.rotom.accounts_in_use(self.rotom.status()):
            raise RunError("a conta entrou em uso pelo scanner")
        self.session = self.rotom.Session(gym[1], gym[2])
        self.session.login(account, token)
        if one(fields(self.session.rpc(2)), 1) != 1:
            raise RunError("GET_PLAYER não respondeu com sucesso")

    def _link(self, ctx, validate):
        gym, (account, token), pid = ctx["gym"], ctx["account"], ctx["pid"]
        unsettled = list(ctx["targets"])
        linked = failed = 0
        breaker = error = None
        hook = self.phone.Hook()
        lobby = None
        attach_attempted = False

        def send_detach(payload):
            try:
                self.session.rpc(2, payload)
            except Exception:
                # The session died mid-run; the hook can only be asked to
                # leave through a logged-in one.
                self._close()
                self._login(gym, account, token)
                self.session.rpc(2, payload)

        try:
            self._login(gym, account, token)
            attach_attempted = True
            hook.attach(pid)
            lobby = self.lobby_cls(self.session, hook, gym)
            found = []
            for row in list(unsettled):
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
                    self.store.mark_linked(row["discord_id"], trainer_name, player_id)
                linked += 1
        except self.phone.HookError as problem:
            breaker = str(problem)
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

        if unsettled and not validate:
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
            if linked == 1 and not error and not breaker:
                return "validação OK: a conta de teste devolveu o ID conhecido"
            return f"validação FALHOU: {breaker or error or 'a conta de teste não foi resolvida'}"
        summary = (
            f"{linked} ligado(s), {failed} falhado(s), {len(unsettled)} por resolver"
        )
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
