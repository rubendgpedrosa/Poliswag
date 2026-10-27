"""The PoliSnatch job API contract, shared by the bot and the resolver.

PoliSnatch owns the queue: Poliswag submits a link (friend code) or a lure,
gets a job id back, and polls it until it leaves queued/claimed. This module
holds the parts both callers must agree on - the base URL, the submit key, the
auth header and the error shape - and nothing else. Standard library only: a resolver
may run outside the bot's venv, where aiohttp is not installed.

    POST   /link       {friendCode}          -> 202 {jobId, state}
    POST   /lure       {lat, lon | nearest}  -> 202, or 200 {..., duplicate: true}
    GET    /link/<id> | /lure/<id>           -> {jobId, state, ...outcome}
    DELETE /link/<id> | /lure/<id>           -> cancels
    POST   /command    {worker, action}      -> 202 {queued, worker}
    GET    /state                            -> {workers: {...}}
    GET    /v1/health                        -> 200 {ok: true}   (outside /jobs)

The submit key is never logged, never put in a URL and never in an error
message: it comes from `POLISNATCH_JOBS_KEY` or the file below, read per call
so a rotation does not need a restart.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

DEFAULT_JOBS_URL = "https://mitm.pogoleiria.pt/v1/jobs"
DEFAULT_KEY_FILE = "/etc/poliswag/secrets/polisnatch-jobs-key"
JOBS_URL_ENV = "POLISNATCH_JOBS_URL"
KEY_FILE_ENV = "POLISNATCH_JOBS_KEY_FILE"
KEY_ENV = "POLISNATCH_JOBS_KEY"

KINDS = ("link", "lure")
OPEN_STATES = ("queued", "claimed")
TERMINAL_STATES = ("done", "failed", "cancelled")


class JobApiError(RuntimeError):
    """A failed job call. `message` is safe to show; the key is never in it.

    `status` is the HTTP status, or None when nothing was sent (no key,
    connection failure). `code` is the server's `error` slug when it gave one
    (`jobs_disabled`, `unauthorized`, ...) or a local one (`no_key`,
    `unreachable`).
    """

    def __init__(self, message, *, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


def jobs_url(env=None):
    """The jobs base, without a trailing slash (the paths start with `/`)."""
    env = os.environ if env is None else env
    return (env.get(JOBS_URL_ENV) or DEFAULT_JOBS_URL).rstrip("/")


def key_file(env=None):
    env = os.environ if env is None else env
    return env.get(KEY_FILE_ENV) or DEFAULT_KEY_FILE


def read_key(env=None):
    """The submit key from the env var, else the secret file; "" when absent."""
    env = os.environ if env is None else env
    key = (env.get(KEY_ENV) or "").strip()
    if key:
        return key
    try:
        return Path(key_file(env)).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def auth_headers(key):
    # The server accepts the bare key too, but it documents Bearer: send that.
    return {"Authorization": "Bearer %s" % key} if key else {}


def health_url(base=None):
    """`/v1/health` sits outside the jobs base, so derive it from the root."""
    base = (base or jobs_url()).rstrip("/")
    suffix = "/v1/jobs"
    root = base[: -len(suffix)] if base.endswith(suffix) else base
    return root + "/v1/health"


def job_path(kind, job_id):
    if kind not in KINDS:
        raise JobApiError("unknown job kind %r" % (kind,), code="bad_kind")
    if not job_id:
        raise JobApiError("the job has no id", code="bad_job_id")
    return "/%s/%s" % (kind, quote(str(job_id), safe=""))


def missing_key_error(env=None):
    """A configuration error, not a queue one: nothing was submitted."""
    return JobApiError(
        "no PoliSnatch submit key: set %s or write %s" % (KEY_ENV, key_file(env)),
        code="no_key",
    )


def decode(payload):
    """JSON object from bytes/str; {} for anything else (errors included)."""
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if not payload:
        return {}
    try:
        body = json.loads(payload)
    except (TypeError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def error_for(status, body):
    """The server's `{error, message}` as a JobApiError, bounded for Discord."""
    body = body if isinstance(body, dict) else {}
    message = body.get("message") or body.get("error") or "HTTP %s" % status
    return JobApiError(str(message)[:200], status=status, code=body.get("error"))


def check(status, body, expect=(200, 202)):
    """The JSON body of an expected status; raises JobApiError otherwise."""
    if status in expect:
        return body if isinstance(body, dict) else {}
    raise error_for(status, body)
