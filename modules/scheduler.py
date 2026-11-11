"""The scheduler's steps: each on its own interval, none able to hold up the rest.

A step is a coroutine function the scheduler calls again and again;
cogs/scheduled.py groups the steps into loops. The runner here gives every
call a budget: how long its loop waits for it. A step that outlasts the budget
is not cancelled -- cancelling half-way through a Discord send or a database
write is how a notice goes out twice -- it carries on in the background, the
loop moves to its next step, and the step is not started again until that run
has ended.

The runner also keeps what `!ticks` shows (how long each step took, failures,
steps still running) and the time the host watchdog's heartbeat carries.
"""

import asyncio
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

# How long a loop waits for a step unless the step says otherwise: one pass.
DEFAULT_BUDGET = 60
# A step due "every 300 s" on a 60 s loop is offered at 0, 60, ... 300 s, give
# or take the loop's jitter. Without this slack a pass that lands at 299.9 s
# would skip it, and the step would run every six minutes instead of five.
DUE_SLACK = 5


@dataclass
class Step:
    """One job of the scheduler and what its last runs looked like.

    `every` is the least number of seconds between two starts (0: every
    pass). `budget` is how long the loop waits for a run before going on
    without it.
    """

    name: str
    run: Callable[[], Awaitable[None]]
    every: float = 0
    budget: float = DEFAULT_BUDGET
    runs: int = 0
    failures: int = 0
    overruns: int = 0
    # Wall-clock start of the latest run, and how long finished runs took.
    last_started: float | None = None
    last_duration: float | None = None
    longest: float = 0
    last_error: str = ""
    # True from the moment a run outlasts its budget until that run ends.
    overdue: bool = False
    _task: asyncio.Task | None = field(default=None, repr=False)
    _next_due: float = field(default=0, repr=False)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()


class StepRunner:
    def __init__(self, log, *, monotonic=time.monotonic, wall=time.time):
        self._log = log
        self._monotonic = monotonic
        self._wall = wall
        self.loops: dict[str, list[Step]] = {}
        # Loop -> wall-clock time its latest pass ended (its creation before
        # the first one, so a loop that never starts shows as stalled).
        self._passed: dict[str, float] = {}

    def add_loop(self, name: str, steps: list[Step]) -> None:
        self.loops[name] = list(steps)
        self._passed[name] = self._wall()

    async def run_pass(self, name: str) -> None:
        """Offer every step of a loop its turn, in order."""
        for step in self.loops[name]:
            await self.run_step(step)
        self._passed[name] = self._wall()

    async def run_step(self, step: Step) -> None:
        """Start the step if it is due and wait for it, at most its budget."""
        if step.running:
            # Still busy from an earlier pass. That was logged when its
            # budget ran out; two runs of one step never overlap.
            return
        now = self._monotonic()
        if step.every and now < step._next_due:
            return
        step._next_due = now + step.every - DUE_SLACK
        step._task = asyncio.create_task(
            self._execute(step), name=f"scheduler:{step.name}"
        )
        done, _pending = await asyncio.wait({step._task}, timeout=step.budget)
        if not done:
            step.overruns += 1
            step.overdue = True
            self._log(
                f"Scheduler step {step.name} still running after "
                f"{step.budget:.0f}s; its loop goes on without it and starts "
                "it again only when this run ends",
                "ERROR",
            )

    async def _execute(self, step: Step) -> None:
        started = self._monotonic()
        step.last_started = self._wall()
        step.runs += 1
        step.last_error = ""
        try:
            await step.run()
        except Exception as error:
            # One step's bug or bad day never stops the others, least of all
            # the workers step, which feeds the map's self-healing.
            step.failures += 1
            step.last_error = f"{type(error).__name__}: {error}"[:200]
            print("CRASH ---", error)
            traceback.print_exc()
            self._log(f"{error}\n{traceback.format_exc()}", "CRASH")
        finally:
            elapsed = self._monotonic() - started
            step.last_duration = elapsed
            step.longest = max(step.longest, elapsed)
            if step.overdue:
                step.overdue = False
                self._log(
                    f"Scheduler step {step.name} ended after {elapsed:.0f}s",
                    "WARNING",
                )

    def progress_at(self) -> float:
        """Wall-clock time of the oldest sign of life, for the heartbeat.

        The oldest of every loop's latest finished pass and the start of any
        run still going past its budget: a loop that stopped passing, or a
        step that hangs for good, makes this stand still, and the host
        watchdog restarts the bot when it is ten minutes old.
        """
        times = list(self._passed.values())
        times += [
            step.last_started
            for steps in self.loops.values()
            for step in steps
            if step.overdue and step.last_started is not None
        ]
        return min(times) if times else self._wall()

    def cancel_running(self) -> None:
        """Stop runs left in the background (the cog is being unloaded)."""
        for steps in self.loops.values():
            for step in steps:
                if step.running:
                    step._task.cancel()
