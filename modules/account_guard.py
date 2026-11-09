"""Account guard: spot mass login failures and fake-ban bursts in the scanner logs.

Each scheduler tick reads the new lines of the scanner containers through the
Docker socket and keeps the last few minutes of the events that matter:

- ``problem``: Dragonite's "ACCOUNT PROBLEM" (e.g. "Initial Route Check returns
  RPC4 - SHADOW BANNED"), with the worker that raised it;
- ``authfail``: Dragonite's "Error during authentication" and lork's
  "Authentication failed".

Three rules, chosen by replaying 2026-11-02..09 from Loki (no false trips):

- worker: one worker flags WORKER_PROBLEMS accounts within WORKER_WINDOW. On
  2026-11-05 20:36 Leiria_26 flagged one account every ~2.5 s as shadow banned;
  the bans were fake (a broken worker blames each account it is given).
- fleet: FLEET_PROBLEMS flags from FLEET_WORKERS different workers within
  FLEET_WINDOW (the same evening, spreading over the area).
- auth: AUTH_FAILURES failed logins on one stack within AUTH_WINDOW (2026-11-04,
  Imperva/OAuth failures on every route). Normal days peak at 7 per 15 min.

MODE "alert" (the only mode so far) DMs the owner what it *would* pause and
pauses nothing. Each (rule, stack, key) alerts at most once per COOLDOWN.
Production scanning only (owner, 2026-11-09): the levelling stack
(dragonite-level, lork-level) is left out. Production only: the dev stack has
no scanner containers.
"""

import asyncio
import datetime
import re
import time
from collections import deque

from modules.config import Config
from modules.logging_mixin import LoggingMixin
from modules.owner_alert import notify_owner

MODE = "alert"

WORKER_PROBLEMS, WORKER_WINDOW = 3, 300
FLEET_PROBLEMS, FLEET_WORKERS, FLEET_WINDOW = 5, 3, 600
AUTH_FAILURES, AUTH_WINDOW = 20, 900
COOLDOWN = 3600
KEEP = max(WORKER_WINDOW, FLEET_WINDOW, AUTH_WINDOW)

# container -> stack (the levelling stack is deliberately not watched)
CONTAINERS = {
    "dragonite": "main",
    "lork": "main",
}

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_WORKER = re.compile(r"\[([A-Za-z]+_\d+)\]")
_PROBLEM_TEXT = re.compile(r"ACCOUNT PROBLEM:?\s*(.*)")


def parse_line(container, line):
    """Return (epoch, kind, worker, detail) for a timestamped docker log line, or None."""
    stamp, _, text = line.partition(" ")
    text = _ANSI.sub("", text)
    if container == "dragonite":
        if "ACCOUNT PROBLEM" in text:
            kind = "problem"
        elif "Error during authentication" in text:
            kind = "authfail"
        else:
            return None
    elif "Authentication failed" in text:
        kind = "authfail"
    else:
        return None
    try:
        # 2026-11-09T10:41:53.123456789Z -> keep microseconds
        when = datetime.datetime.strptime(
            stamp[:26].rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f"
        ).replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    worker = _WORKER.search(text)
    detail = _PROBLEM_TEXT.search(text)
    return (
        when.timestamp(),
        kind,
        worker.group(1) if worker else "?",
        (detail.group(1) if detail else text)[:120],
    )


def area_of(worker):
    """Leiria_26 -> Leiria."""
    return worker.rsplit("_", 1)[0] if "_" in worker else worker


def evaluate(events, now):
    """Rules over events [(t, stack, kind, worker, detail)]; returns trips.

    A trip is (rule, stack, key, count, detail): key is the worker for the
    worker rule and "" otherwise.
    """
    trips = []
    for stack in set(CONTAINERS.values()):
        problems = [
            e
            for e in events
            if e[1] == stack and e[2] == "problem" and now - e[0] <= KEEP
        ]
        per_worker = {}
        for e in problems:
            if now - e[0] <= WORKER_WINDOW:
                per_worker.setdefault(e[3], []).append(e)
        for worker, evs in sorted(per_worker.items()):
            if len(evs) >= WORKER_PROBLEMS:
                trips.append(("worker", stack, worker, len(evs), evs[-1][4]))
        fleet = [e for e in problems if now - e[0] <= FLEET_WINDOW]
        if len(fleet) >= FLEET_PROBLEMS and len({e[3] for e in fleet}) >= FLEET_WORKERS:
            trips.append(("fleet", stack, "", len(fleet), fleet[-1][4]))
        fails = [
            e
            for e in events
            if e[1] == stack and e[2] == "authfail" and now - e[0] <= AUTH_WINDOW
        ]
        if len(fails) >= AUTH_FAILURES:
            trips.append(("auth", stack, "", len(fails), fails[-1][4]))
    return trips


_STACK_PT = {"main": "scanner principal"}


def describe(trip):
    """(title, text) in PT-PT for an owner DM."""
    rule, stack, key, count, detail = trip
    where = _STACK_PT[stack]
    if rule == "worker":
        area = area_of(key)
        title = f"🛡️ Possíveis bans falsos: {key}"
        text = (
            f"O worker **{key}** ({where}) marcou {count} contas como problema em "
            f"{WORKER_WINDOW // 60} min. Quando é um só worker, normalmente é o "
            f"worker que está avariado e as contas estão boas.\n"
            f"Último: `{detail}`\n\n"
            f"**Pausaria:** a área {area}.\n"
            f"Depois: rever as contas marcadas e fazer `reset-banned` às falsas."
        )
    elif rule == "fleet":
        title = f"🛡️ Vaga de contas marcadas ({where})"
        text = (
            f"{count} contas marcadas por vários workers em {FLEET_WINDOW // 60} min.\n"
            f"Último: `{detail}`\n\n"
            f"**Pausaria:** todas as áreas do {where}."
        )
    else:
        title = f"🛡️ Muitas autenticações falhadas ({where})"
        text = (
            f"{count} logins falhados em {AUTH_WINDOW // 60} min "
            f"(num dia normal, no máximo 7).\n"
            f"Último: `{detail}`\n\n"
            f"**Pausaria:** os logins do {where} (todas as áreas)."
        )
    if MODE == "alert":
        text += "\n\n_Modo de alerta: nada foi pausado._"
    return title, text


class AccountGuard(LoggingMixin):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self._events = deque()
        self._cursor = None  # epoch the next read starts from (whole seconds)
        self._last_event = {}  # container -> epoch of the newest event kept
        self._last_alert = {}  # (rule, stack, key) -> epoch
        self._started = None

    def _read_new_lines(self, since, until):
        """Blocking: timestamped lines per container in [since, until]."""
        import docker

        client = docker.from_env()
        out = {}
        for name in CONTAINERS:
            try:
                raw = client.containers.get(name).logs(
                    since=int(since), until=int(until) + 1, timestamps=True
                )
            except docker.errors.NotFound:
                continue
            out[name] = raw.decode("utf-8", "replace").splitlines()
        return out

    def ingest(self, lines_by_container, now):
        """Keep new events; the read window overlaps by a second, so drop repeats."""
        for name, lines in lines_by_container.items():
            last = self._last_event.get(name, 0)
            for line in lines:
                parsed = parse_line(name, line)
                if parsed is None or parsed[0] <= last:
                    continue
                t, kind, worker, detail = parsed
                self._events.append((t, CONTAINERS[name], kind, worker, detail))
                self._last_event[name] = max(self._last_event.get(name, 0), t)
        while self._events and now - self._events[0][0] > KEEP:
            self._events.popleft()

    def due_trips(self, now):
        """Trips that fired on events seen since the guard started, past cooldown."""
        due = []
        for trip in evaluate(self._events, now):
            rule, stack, key = trip[:3]
            if now - self._last_alert.get((rule, stack, key), -1e12) < COOLDOWN:
                continue
            newest = max(
                e[0]
                for e in self._events
                if e[1] == stack
                and e[2] == ("authfail" if rule == "auth" else "problem")
            )
            if self._started is not None and newest < self._started:
                continue  # an incident from before a restart: already old news
            self._last_alert[(rule, stack, key)] = now
            due.append(trip)
        return due

    async def tick(self):
        if not Config.IS_PRODUCTION:
            return
        now = time.time()
        if self._started is None:
            # Warm up with the last KEEP seconds; alerts only for newer events.
            self._started = now
            self._cursor = now - KEEP
        lines = await asyncio.to_thread(self._read_new_lines, self._cursor, now)
        self._cursor = int(now)
        self.ingest(lines, now)
        for trip in self.due_trips(now):
            title, text = describe(trip)
            self._log(
                f"Account guard ({MODE}): {trip[0]} rule on {trip[1]} "
                f"{trip[2] or ''} — {trip[3]} events",
                "WARNING",
            )
            await notify_owner(self.poliswag, text, title=title, tag="account_guard")
