"""Tests for modules.scheduler: budgets, intervals and the heartbeat's time."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.scheduler import DEFAULT_BUDGET, DUE_SLACK, Step, StepRunner


class Clock:
    """A monotonic clock and a wall clock the test moves by hand."""

    def __init__(self):
        self.now = 1000.0
        self.wall_now = 1_800_000_000.0

    def monotonic(self):
        return self.now

    def wall(self):
        return self.wall_now

    def advance(self, seconds):
        self.now += seconds
        self.wall_now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def log():
    return MagicMock()


@pytest.fixture
def runner(log, clock):
    return StepRunner(log, monotonic=clock.monotonic, wall=clock.wall)


def _levels(log):
    return [call.args[1] for call in log.call_args_list]


class TestRunStep:
    async def test_runs_the_step_and_records_it(self, runner, clock, log):
        async def work():
            clock.advance(2.5)

        step = Step("work", work)
        await runner.run_step(step)
        assert step.runs == 1 and step.failures == 0
        assert step.last_duration == pytest.approx(2.5)
        assert step.longest == pytest.approx(2.5)
        assert step.last_started == pytest.approx(1_800_000_000.0)
        assert not step.running and not step.overdue
        log.assert_not_called()

    def test_default_budget_is_one_pass(self):
        assert Step("x", AsyncMock()).budget == DEFAULT_BUDGET == 60

    async def test_a_failing_step_is_logged_and_swallowed(self, runner, log, capsys):
        step = Step("boom", AsyncMock(side_effect=RuntimeError("boom")))
        await runner.run_step(step)
        assert step.failures == 1
        assert step.last_error == "RuntimeError: boom"
        assert _levels(log) == ["CRASH"]
        assert "CRASH" in capsys.readouterr().out

    async def test_a_later_success_clears_the_error(self, runner):
        run = AsyncMock(side_effect=[RuntimeError("boom"), None])
        step = Step("flaky", run)
        await runner.run_step(step)
        await runner.run_step(step)
        assert step.failures == 1 and step.last_error == ""

    async def test_the_step_keeps_its_longest_run(self, runner, clock):
        durations = iter([5.0, 1.0])

        async def work():
            clock.advance(next(durations))

        step = Step("work", work)
        await runner.run_step(step)
        await runner.run_step(step)
        assert step.last_duration == pytest.approx(1.0)
        assert step.longest == pytest.approx(5.0)


class TestBudget:
    async def test_past_its_budget_the_loop_moves_on_and_says_so(self, runner, log):
        release = asyncio.Event()

        async def stuck():
            await release.wait()

        step = Step("stuck", stuck, budget=0.01)
        await runner.run_step(step)
        assert step.running and step.overdue and step.overruns == 1
        message, level = log.call_args.args
        assert level == "ERROR"
        assert "stuck" in message and "still running" in message
        release.set()
        await step._task

    async def test_the_run_is_not_cancelled(self, runner):
        release = asyncio.Event()
        finished = []

        async def slow():
            await release.wait()
            finished.append(True)

        step = Step("slow", slow, budget=0.01)
        await runner.run_step(step)
        release.set()
        await step._task
        # Cancelling half-way through a send is how a notice goes out twice.
        assert finished == [True]

    async def test_it_is_not_started_again_while_it_runs(self, runner, log):
        release = asyncio.Event()
        starts = []

        async def stuck():
            starts.append(True)
            await release.wait()

        step = Step("stuck", stuck, budget=0.01)
        await runner.run_step(step)
        await runner.run_step(step)
        await runner.run_step(step)
        assert starts == [True] and step.runs == 1
        # Told once, when the budget ran out; not again on every pass.
        assert _levels(log) == ["ERROR"]
        release.set()
        await step._task

    async def test_when_it_ends_that_is_logged_and_it_runs_again(self, runner, log):
        release = asyncio.Event()

        async def stuck():
            await release.wait()

        step = Step("stuck", stuck, budget=0.01)
        await runner.run_step(step)
        release.set()
        await step._task
        assert not step.overdue
        assert _levels(log) == ["ERROR", "WARNING"]
        assert "ended after" in log.call_args.args[0]
        await runner.run_step(step)
        assert step.runs == 2

    async def test_a_step_that_fails_after_its_budget_is_still_recorded(
        self, runner, log, capsys
    ):
        release = asyncio.Event()

        async def late_failure():
            await release.wait()
            raise RuntimeError("late")

        step = Step("late", late_failure, budget=0.01)
        await runner.run_step(step)
        release.set()
        await step._task
        assert step.failures == 1 and not step.overdue
        assert _levels(log) == ["ERROR", "CRASH", "WARNING"]
        capsys.readouterr()


class TestEvery:
    async def test_runs_at_once_then_waits_its_interval(self, runner, clock):
        run = AsyncMock()
        step = Step("chore", run, every=300)
        await runner.run_step(step)
        for _ in range(4):
            clock.advance(60)
            await runner.run_step(step)
        assert run.await_count == 1
        clock.advance(60)
        await runner.run_step(step)
        assert run.await_count == 2

    async def test_a_pass_a_moment_early_still_counts(self, runner, clock):
        run = AsyncMock()
        step = Step("chore", run, every=300)
        await runner.run_step(step)
        clock.advance(300 - DUE_SLACK + 0.1)
        await runner.run_step(step)
        assert run.await_count == 2

    async def test_a_step_without_an_interval_runs_every_pass(self, runner):
        run = AsyncMock()
        step = Step("each", run)
        for _ in range(3):
            await runner.run_step(step)
        assert run.await_count == 3

    async def test_a_failed_run_waits_for_the_next_interval_too(self, runner, clock):
        run = AsyncMock(side_effect=RuntimeError("boom"))
        step = Step("chore", run, every=300)
        await runner.run_step(step)
        clock.advance(60)
        await runner.run_step(step)
        assert run.await_count == 1


class TestRunPass:
    async def test_offers_every_step_in_order(self, runner):
        order = []

        def make(name):
            async def run():
                order.append(name)

            return Step(name, run)

        runner.add_loop("health", [make("a"), make("b"), make("c")])
        await runner.run_pass("health")
        assert order == ["a", "b", "c"]

    async def test_one_failing_step_does_not_stop_the_rest(self, runner, capsys):
        after = AsyncMock()
        runner.add_loop(
            "health",
            [
                Step("boom", AsyncMock(side_effect=RuntimeError("boom"))),
                Step("b", after),
            ],
        )
        await runner.run_pass("health")
        after.assert_awaited_once()
        capsys.readouterr()


class TestProgress:
    async def test_is_the_oldest_loop_pass(self, runner, clock):
        runner.add_loop("health", [Step("a", AsyncMock())])
        runner.add_loop("alerts", [Step("b", AsyncMock())])
        clock.advance(60)
        await runner.run_pass("health")
        # alerts has not passed since it was added, a minute ago.
        assert runner.progress_at() == pytest.approx(clock.wall() - 60)
        await runner.run_pass("alerts")
        assert runner.progress_at() == pytest.approx(clock.wall())

    async def test_a_loop_that_stopped_passing_holds_it_back(self, runner, clock):
        runner.add_loop("health", [Step("a", AsyncMock())])
        runner.add_loop("alerts", [Step("b", AsyncMock())])
        await runner.run_pass("alerts")
        stopped = clock.wall()
        for _ in range(12):
            clock.advance(60)
            await runner.run_pass("health")
        assert runner.progress_at() == pytest.approx(stopped)

    async def test_a_step_hanging_past_its_budget_holds_it_back(self, runner, clock):
        release = asyncio.Event()

        async def hang():
            await release.wait()

        runner.add_loop("health", [Step("hang", hang, budget=0.01)])
        await runner.run_pass("health")
        started = clock.wall()
        for _ in range(12):
            clock.advance(60)
            await runner.run_pass("health")
        # The loop keeps passing, but the hung step's start is what counts:
        # ten minutes of this and the watchdog restarts the bot.
        assert runner.progress_at() == pytest.approx(started)
        release.set()
        await runner.loops["health"][0]._task
        await runner.run_pass("health")
        assert runner.progress_at() == pytest.approx(clock.wall())

    def test_with_no_loops_it_is_now(self, runner, clock):
        assert runner.progress_at() == pytest.approx(clock.wall())


class TestCancelRunning:
    async def test_cancels_what_was_left_in_the_background(self, runner):
        async def hang():
            await asyncio.Event().wait()

        step = Step("hang", hang, budget=0.01)
        runner.add_loop("health", [step, Step("idle", AsyncMock())])
        await runner.run_pass("health")
        runner.cancel_running()
        with pytest.raises(asyncio.CancelledError):
            await step._task
        assert not step.running and not step.overdue
