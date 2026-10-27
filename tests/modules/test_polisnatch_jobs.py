"""Tests for modules.polisnatch_jobs: the async client and the poller.

The client tests run against a throwaway aiohttp server bound to 127.0.0.1, so
they exercise the real request/response path (paths, Bearer header, JSON body,
status handling) without touching any real PoliSnatch. The poller tests use a
fake client and a fake store: no network, no database.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from modules.http_client import close_session
from modules.polisnatch_api import JobApiError
from modules.polisnatch_jobs import JobPoller, PoliSnatchJobs


@pytest.fixture(autouse=True)
async def _close_shared_session():
    # modules.http_client caches one session; a session belongs to the loop that
    # created it, and pytest-asyncio gives each test a fresh one.
    yield
    await close_session()


@asynccontextmanager
async def jobs_server(handler):
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield "http://127.0.0.1:%d/v1/jobs" % port
    finally:
        await runner.cleanup()


class TestSubmitLure:
    async def test_nearest_is_explicit_and_the_key_is_a_bearer(self):
        seen = []

        async def handler(request):
            seen.append(
                (
                    request.method,
                    request.path,
                    request.headers["Authorization"],
                    await request.json(),
                )
            )
            return web.json_response(
                {"jobId": "job_0123456789abcdef", "state": "queued"}, status=202
            )

        async with jobs_server(handler) as base:
            client = PoliSnatchJobs(base=base, key="test-key")
            body = await client.submit_lure(
                nearest=True, lure_type=501, requested_by=42
            )

        assert body == {"jobId": "job_0123456789abcdef", "state": "queued"}
        method, path, authorization, payload = seen[0]
        assert (method, path) == ("POST", "/v1/jobs/lure")
        assert authorization == "Bearer test-key"
        assert payload == {"nearest": True, "lureType": 501, "requestedBy": "42"}

    async def test_coordinates_are_sent_when_not_nearest(self):
        seen = []

        async def handler(request):
            seen.append(await request.json())
            return web.json_response({"jobId": "job_1", "state": "queued"}, status=202)

        async with jobs_server(handler) as base:
            client = PoliSnatchJobs(base=base, key="k")
            await client.submit_lure(lat=39.74, lon=-8.80, lure_type=504)

        assert seen[0] == {
            "nearest": False,
            "lureType": 504,
            "lat": 39.74,
            "lon": -8.8,
        }

    async def test_duplicate_is_a_200_answer_not_an_error(self):
        async def handler(request):
            return web.json_response(
                {"jobId": "job_1", "state": "claimed", "duplicate": True}, status=200
            )

        async with jobs_server(handler) as base:
            body = await PoliSnatchJobs(base=base, key="k").submit_lure(
                nearest=True, lure_type=501
            )

        assert body["duplicate"] is True
        assert body["state"] == "claimed"

    async def test_bad_key_is_reported_with_the_server_code(self):
        async def handler(request):
            return web.json_response(
                {
                    "error": "unauthorized",
                    "message": "the jobs submit key is not valid",
                },
                status=401,
            )

        async with jobs_server(handler) as base:
            with pytest.raises(JobApiError) as exc:
                await PoliSnatchJobs(base=base, key="wrong").submit_lure(
                    nearest=True, lure_type=501
                )

        assert (exc.value.status, exc.value.code) == (401, "unauthorized")

    async def test_jobs_disabled_is_reported_with_its_code(self):
        async def handler(request):
            return web.json_response(
                {"error": "jobs_disabled", "message": "jobs are not enabled"},
                status=503,
            )

        async with jobs_server(handler) as base:
            with pytest.raises(JobApiError) as exc:
                await PoliSnatchJobs(base=base, key="k").submit_lure(
                    nearest=True, lure_type=501
                )

        assert (exc.value.status, exc.value.code) == (503, "jobs_disabled")

    async def test_bad_input_is_not_retried_or_rewritten(self):
        async def handler(request):
            return web.json_response(
                {"error": "bad_request", "message": "lat must be a number"},
                status=400,
            )

        async with jobs_server(handler) as base:
            with pytest.raises(JobApiError) as exc:
                await PoliSnatchJobs(base=base, key="k").submit_lure(
                    lat="north", lon=0, lure_type=501
                )

        assert exc.value.status == 400


class TestReads:
    async def test_poll_uses_the_kind_and_the_job_id(self):
        seen = []

        async def handler(request):
            seen.append((request.method, request.path))
            return web.json_response(
                {"jobId": "job_1", "state": "done", "playerId": "player"}
            )

        async with jobs_server(handler) as base:
            body = await PoliSnatchJobs(base=base, key="k").get(
                "link", "job_0123456789abcdef"
            )

        assert body["playerId"] == "player"
        assert seen == [("GET", "/v1/jobs/link/job_0123456789abcdef")]

    async def test_unknown_job_raises_404(self):
        async def handler(request):
            return web.json_response(
                {"error": "not_found", "message": "unknown job"}, status=404
            )

        async with jobs_server(handler) as base:
            with pytest.raises(JobApiError) as exc:
                await PoliSnatchJobs(base=base, key="k").get("lure", "job_1")

        assert exc.value.status == 404

    async def test_workers_returns_the_worker_map(self):
        async def handler(request):
            assert (request.method, request.path) == ("GET", "/v1/jobs/state")
            return web.json_response(
                {"workers": {"worker-1": {"connected": True, "receivedAt": 1.0}}}
            )

        async with jobs_server(handler) as base:
            workers = await PoliSnatchJobs(base=base, key="k").workers()

        assert workers == {"worker-1": {"connected": True, "receivedAt": 1.0}}

    async def test_workers_empty_is_an_empty_dict(self):
        async def handler(request):
            return web.json_response({})

        async with jobs_server(handler) as base:
            assert await PoliSnatchJobs(base=base, key="k").workers() == {}

    async def test_command_is_queued_with_202(self):
        seen = []

        async def handler(request):
            seen.append(await request.json())
            return web.json_response({"queued": "restart", "worker": "*"}, status=202)

        async with jobs_server(handler) as base:
            body = await PoliSnatchJobs(base=base, key="k").command("*", "restart")

        assert body == {"queued": "restart", "worker": "*"}
        assert seen == [{"worker": "*", "action": "restart"}]


class TestIssueToken:
    async def test_posts_the_person_and_expects_201(self):
        seen = []

        async def handler(request):
            seen.append(
                (
                    request.path,
                    request.headers["Authorization"],
                    await request.json(),
                )
            )
            return web.json_response(
                {
                    "device_id": "dev_1",
                    "token": "psd_test",
                    "email": "a@b.c",
                    "scope": {
                        "max_workers": 2,
                        "channels": ["stable"],
                        "jobs": True,
                    },
                },
                status=201,
            )

        async with jobs_server(handler) as base:
            body = await PoliSnatchJobs(base=base, key="k").issue_token(
                email="a@b.c", workers=2
            )

        assert body["token"] == "psd_test"
        path, authorization, payload = seen[0]
        assert path == "/v1/jobs/token"
        assert authorization == "Bearer k"
        assert payload == {
            "email": "a@b.c",
            "name": "",
            "workers": 2,
            "channels": ["stable"],
        }

    async def test_no_key_never_sends(self):
        calls = []

        async def handler(request):
            calls.append(request.path)
            return web.json_response({})

        async with jobs_server(handler) as base:
            with pytest.raises(JobApiError) as exc:
                await PoliSnatchJobs(base=base, key="").issue_token(email="a@b.c")

        assert exc.value.code == "no_key"
        assert calls == []


class TestClientConfiguration:
    async def test_no_key_never_sends_a_request(self):
        calls = []

        async def handler(request):
            calls.append(request.path)
            return web.json_response({})

        async with jobs_server(handler) as base:
            with pytest.raises(JobApiError) as exc:
                await PoliSnatchJobs(base=base, key="").submit_lure(
                    nearest=True, lure_type=501
                )

        assert exc.value.code == "no_key"
        assert exc.value.status is None
        assert calls == []

    async def test_unreachable_server_is_a_job_api_error(self):
        client = PoliSnatchJobs(base="http://127.0.0.1:1/v1/jobs", key="k")
        with pytest.raises(JobApiError) as exc:
            await client.get("lure", "job_1")
        assert exc.value.code == "unreachable"
        assert exc.value.status is None


class FakeClient:
    def __init__(self, replies=None):
        self.replies = dict(replies or {})
        self.calls = []

    async def get(self, kind, job_id):
        self.calls.append((kind, job_id))
        reply = self.replies[job_id]
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeStore:
    def __init__(self, open_rows=(), unnotified=()):
        self.open_rows = list(open_rows)
        self.unnotified_rows = list(unnotified)
        self.open_error = None
        self.save_error = None
        self.unnotified_error = None
        self.notified_error = None
        self.saved = []
        self.notified = []

    def open_jobs(self):
        if self.open_error is not None:
            raise self.open_error
        return self.open_rows

    def save(self, job_id, body):
        if self.save_error is not None:
            raise self.save_error
        self.saved.append((job_id, body))
        return 1

    def unnotified(self):
        if self.unnotified_error is not None:
            raise self.unnotified_error
        return self.unnotified_rows

    def mark_notified(self, job_id):
        if self.notified_error is not None:
            raise self.notified_error
        self.notified.append(job_id)


class FakeUtility:
    def __init__(self):
        self.lines = []

    def log_to_file(self, message, level="INFO"):
        self.lines.append((message, level))


class FakeBot:
    def __init__(self):
        self.utility = FakeUtility()
        self._ready = asyncio.Event()
        self._ready.set()

    async def wait_until_ready(self):
        await self._ready.wait()


def make_poller(open_rows=(), unnotified=(), replies=None, announce=None):
    bot = FakeBot()
    client = FakeClient(replies)
    store = FakeStore(open_rows, unnotified)
    return JobPoller(bot, client, store, announce=announce), bot, client, store


OPEN_ROW = {
    "job_id": "job_1",
    "kind": "lure",
    "state": "queued",
    "channel_id": 9,
    "message_id": 555,
}


class TestPollerPolling:
    async def test_state_change_is_saved(self):
        poller, _, client, store = make_poller(
            open_rows=[OPEN_ROW], replies={"job_1": {"state": "claimed"}}
        )
        assert await poller.tick() == (1, 0)
        assert client.calls == [("lure", "job_1")]
        assert store.saved == [("job_1", {"state": "claimed"})]

    async def test_an_unchanged_open_job_is_not_rewritten(self):
        poller, _, _, store = make_poller(
            open_rows=[OPEN_ROW], replies={"job_1": {"state": "queued"}}
        )
        assert await poller.tick() == (1, 0)
        assert store.saved == []

    async def test_a_terminal_outcome_is_saved_once_and_announced(self):
        announced = []

        async def announce(row):
            announced.append(row["job_id"])

        finished = dict(OPEN_ROW, state="done", verified=1)
        poller, _, _, store = make_poller(
            open_rows=[OPEN_ROW],
            unnotified=[finished],
            replies={"job_1": {"state": "done", "verified": True}},
            announce=announce,
        )
        assert await poller.tick() == (1, 0)
        assert store.saved == [("job_1", {"state": "done", "verified": True})]
        assert announced == ["job_1"]
        assert store.notified == ["job_1"]

    async def test_an_unknown_job_becomes_a_recorded_failure(self):
        poller, _, _, store = make_poller(
            open_rows=[OPEN_ROW],
            replies={"job_1": JobApiError("unknown job", status=404, code="not_found")},
        )
        assert await poller.tick() == (1, 0)
        assert store.saved == [
            (
                "job_1",
                {"jobId": "job_1", "state": "failed", "failReason": "unknown_job"},
            )
        ]

    async def test_jobs_disabled_leaves_the_job_open_and_backs_off(self):
        poller, bot, _, store = make_poller(
            open_rows=[OPEN_ROW],
            replies={
                "job_1": JobApiError(
                    "jobs are not enabled", status=503, code="jobs_disabled"
                )
            },
        )
        assert await poller.tick() == (1, 1)
        assert store.saved == []
        assert [line for line in bot.utility.lines if line[1] == "ERROR"]
        # The same repeated error is logged once, not every tick.
        await poller.tick()
        assert len([line for line in bot.utility.lines if line[1] == "ERROR"]) == 1

    async def test_unreachable_leaves_the_job_open(self):
        poller, _, _, store = make_poller(
            open_rows=[OPEN_ROW],
            replies={"job_1": JobApiError("down", code="unreachable")},
        )
        assert await poller.tick() == (1, 1)
        assert store.saved == []

    async def test_unexpected_client_error_does_not_escape(self):
        poller, _, _, store = make_poller(
            open_rows=[OPEN_ROW], replies={"job_1": RuntimeError("boom")}
        )
        assert await poller.tick() == (1, 1)
        assert store.saved == []

    async def test_an_unknown_state_is_not_saved(self):
        poller, _, _, store = make_poller(
            open_rows=[OPEN_ROW], replies={"job_1": {"state": "teleported"}}
        )
        assert await poller.tick() == (1, 1)
        assert store.saved == []

    async def test_a_save_failure_leaves_the_job_for_the_next_tick(self):
        poller, _, _, store = make_poller(
            open_rows=[OPEN_ROW], replies={"job_1": {"state": "claimed"}}
        )
        store.save_error = RuntimeError("db down")
        assert await poller.tick() == (1, 1)

    async def test_no_open_jobs_means_no_client_calls(self):
        poller, _, client, store = make_poller()
        assert await poller.tick() == (0, 0)
        assert client.calls == []
        assert store.saved == []

    async def test_the_open_set_is_logged_once_per_change(self):
        poller, bot, client, store = make_poller(
            open_rows=[OPEN_ROW], replies={"job_1": {"state": "queued"}}
        )
        await poller.tick()
        await poller.tick()
        assert len(bot.utility.lines) == 1
        store.open_rows = []
        await poller.tick()
        assert len(bot.utility.lines) == 1


class TestPollerAnnouncements:
    async def test_announce_failure_keeps_the_row_unnotified(self):
        async def announce(row):
            raise RuntimeError("discord down")

        poller, bot, _, store = make_poller(
            unnotified=[dict(OPEN_ROW, state="failed")], announce=announce
        )
        await poller.tick()
        assert store.notified == []
        assert [line for line in bot.utility.lines if line[1] == "ERROR"]

    async def test_no_callback_does_not_read_or_mark_notices(self):
        poller, _, _, store = make_poller(unnotified=[dict(OPEN_ROW, state="done")])
        store.unnotified_error = AssertionError("must not be read")
        await poller.tick()
        assert store.notified == []

    async def test_mark_notified_failure_is_logged_not_raised(self):
        async def announce(row):
            return None

        poller, bot, _, store = make_poller(
            unnotified=[dict(OPEN_ROW, state="done")], announce=announce
        )
        store.notified_error = RuntimeError("db down")
        await poller.tick()
        assert [line for line in bot.utility.lines if line[1] == "ERROR"]

    async def test_open_jobs_read_failure_is_an_error_tick(self):
        poller, _, client, store = make_poller()
        store.open_error = RuntimeError("db down")
        assert await poller.tick() == (0, 1)
        assert client.calls == []


class TestPollerLifecycle:
    async def test_wake_ends_the_idle_sleep_early(self):
        poller, _, _, _ = make_poller()
        sleeping = asyncio.create_task(poller._sleep(30))
        await asyncio.sleep(0)
        poller.wake()
        await asyncio.wait_for(sleeping, timeout=1)

    async def test_a_wake_before_the_sleep_returns_immediately(self):
        poller, _, _, _ = make_poller()
        poller.wake()
        await asyncio.wait_for(poller._sleep(30), timeout=1)

    async def test_start_then_stop_cancels_the_task(self):
        poller, _, _, _ = make_poller()
        task = poller.start()
        await asyncio.sleep(0)
        await poller.stop()
        assert task.cancelled()

    async def test_a_dead_store_does_not_kill_the_loop(self):
        poller, _, _, store = make_poller()
        store.open_error = RuntimeError("db down")
        task = poller.start()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done()
        await poller.stop()
