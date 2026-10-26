"""Poliswag's side of the PoliSnatch job API: submit, poll, then forget.

PoliSnatch owns the queue (link lobby and lure placement); Poliswag owns
everything around it - the stop lock, the cooldown, and whether a failure is
worth retrying. Two rules shape this module:

* A submitted job is written to `polisnatch_job` before anything waits on it
  (`modules/polisnatch_store.py`), so a restart can resume the ones still open.
* Polling only happens while a job is open. With an empty table the loop reads
  the database and nothing else - no 24/7 traffic to a server with no work.

The first tick after Discord is ready is the startup sweep: every row a
previous process left queued or claimed is polled again, and an outcome that
was recorded but not announced (a crash between the two) is announced then.
"""

import asyncio

import aiohttp

from modules.config import Config
from modules.http_client import get_session
from modules.polisnatch_api import (
    OPEN_STATES,
    TERMINAL_STATES,
    JobApiError,
    check,
    decode,
    job_path,
    missing_key_error,
    read_key,
)

DEFAULT_TIMEOUT = 10.0


class PoliSnatchJobs:
    """Thin async wrapper over the submit-key routes the bot uses."""

    def __init__(self, base=None, key=None, timeout=DEFAULT_TIMEOUT):
        self.base = (base or Config.POLISNATCH_JOBS_URL).rstrip("/")
        self._key = key
        self.timeout = timeout

    def _key_value(self):
        key = self._key
        if key is None:
            key = Config.POLISNATCH_JOBS_KEY.strip() or read_key()
        if not key:
            raise missing_key_error()
        return key

    async def _call(self, method, path, body=None, expect=(200, 202)):
        headers = {"Authorization": "Bearer " + self._key_value()}
        try:
            async with get_session().request(
                method,
                self.base + path,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as reply:
                return check(reply.status, decode(await reply.read()), expect)
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise JobApiError(
                "PoliSnatch jobs unreachable: %s" % type(error).__name__,
                code="unreachable",
            ) from None

    async def submit_lure(
        self, *, lat=None, lon=None, nearest=False, lure_type=None, requested_by=None
    ):
        # `nearest` is explicit: the server validates lat/lon unless it is set.
        body = {"nearest": bool(nearest), "lureType": lure_type}
        if not nearest:
            body["lat"], body["lon"] = lat, lon
        if requested_by is not None:
            body["requestedBy"] = str(requested_by)
        # 202 queued, 200 already in flight for this stop - both are answers.
        return await self._call("POST", "/lure", body, expect=(200, 202))

    async def get(self, kind, job_id):
        return await self._call("GET", job_path(kind, job_id), expect=(200,))

    async def command(self, worker, action):
        body = {"worker": worker or "*", "action": action}
        return await self._call("POST", "/command", body, expect=(202,))

    async def workers(self):
        body = await self._call("GET", "/state", expect=(200,))
        return body.get("workers") or {}


class JobPoller:
    """Follows open jobs and announces each outcome exactly once."""

    POLL_INTERVAL = 3.0
    IDLE_INTERVAL = 30.0
    ERROR_BACKOFF = 30.0

    def __init__(self, poliswag, client, store, announce=None):
        self.poliswag = poliswag
        self.client = client
        self.store = store
        self.announce = announce
        self._task = None
        self._wake = asyncio.Event()
        self._open_ids = ()
        self._last_error = None

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return self._task

    async def stop(self):
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def wake(self):
        """A job was just recorded: no need to wait out the idle interval."""
        self._wake.set()

    async def tick(self):
        """One sweep: poll every open job, then deliver recorded outcomes.

        Returns (open jobs seen, poll errors). Exposed for tests and for the
        startup sweep, which is simply the first tick.
        """
        try:
            rows = self.store.open_jobs()
        except Exception as error:  # noqa: BLE001 - the loop must survive
            self._warn("cannot read open jobs: %s" % error)
            return 0, 1
        ids = tuple(row.get("job_id") for row in rows)
        if ids != self._open_ids:
            self._open_ids = ids
            if ids:
                self._log("PoliSnatch: %d job(s) em aberto" % len(ids))
        errors = 0
        for row in rows:
            try:
                if not await self._poll(row):
                    errors += 1
            except Exception as error:  # noqa: BLE001 - one job can't stall the rest
                self._warn("poll %s: %s" % (row.get("job_id"), error))
                errors += 1
        await self._announce_pending()
        if not errors:
            self._last_error = None
        return len(rows), errors

    async def _poll(self, row):
        job_id = row.get("job_id")
        try:
            body = await self.client.get(row.get("kind"), job_id)
        except JobApiError as error:
            if error.status == 404:
                # The server no longer knows it (purged or cancelled elsewhere):
                # record an outcome instead of polling this row forever.
                body = {
                    "jobId": job_id,
                    "state": "failed",
                    "failReason": "unknown_job",
                }
            else:
                self._warn("poll %s: HTTP %s %s" % (job_id, error.status, error.code))
                return False
        except Exception as error:  # noqa: BLE001 - a bug here is not an outcome
            self._warn("poll %s: %s" % (job_id, error))
            return False
        state = body.get("state")
        if state not in OPEN_STATES and state not in TERMINAL_STATES:
            self._warn("poll %s: unknown state %r" % (job_id, state))
            return False
        if state == row.get("state"):
            return True
        try:
            self.store.save(job_id, body)
        except Exception as error:  # noqa: BLE001 - keep the row open, retry
            self._warn("cannot save %s: %s" % (job_id, error))
            return False
        return True

    async def _announce_pending(self):
        if self.announce is None:
            return
        try:
            rows = self.store.unnotified()
        except Exception as error:  # noqa: BLE001 - retried next tick
            self._warn("cannot read jobs to announce: %s" % error)
            return
        for row in rows:
            job_id = row.get("job_id")
            try:
                await self.announce(row)
            except Exception as error:  # noqa: BLE001 - leave it unannounced, retry
                self._warn("cannot announce %s: %s" % (job_id, error))
                continue
            try:
                self.store.mark_notified(job_id)
            except Exception as error:  # noqa: BLE001 - retried next tick
                self._warn("cannot mark %s announced: %s" % (job_id, error))

    async def _run(self):
        await self.poliswag.wait_until_ready()
        while True:
            try:
                open_jobs, errors = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - a poller that dies is silent
                self._warn("loop: %s" % error)
                open_jobs, errors = 0, 1
            if open_jobs and not errors:
                delay = self.POLL_INTERVAL
            elif open_jobs:
                delay = self.ERROR_BACKOFF
            else:
                delay = self.IDLE_INTERVAL
            await self._sleep(delay)

    async def _sleep(self, seconds):
        # A job recorded while the tick was still running must shorten this
        # interval, not wait out the idle one because the event was reset.
        if self._wake.is_set():
            self._wake.clear()
            return
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        finally:
            self._wake.clear()

    def _warn(self, message):
        # The server may be undeployed for a while: log a repeated error once.
        if message == self._last_error:
            return
        self._last_error = message
        self._log("PoliSnatch jobs: " + message, "ERROR")

    def _log(self, message, level="INFO"):
        log = getattr(getattr(self.poliswag, "utility", None), "log_to_file", None)
        if not callable(log):
            return
        try:
            log(message, level)
        except Exception:  # noqa: BLE001 - logging must never stop the loop
            pass
