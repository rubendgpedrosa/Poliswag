"""The PoliSnatch submit key, for the one call the bot still makes.

The scheduler polls PoliSnatch's game-status endpoint (cogs/scheduled.py) and
authenticates with the jobs submit key. The key is never logged, never put in
a URL and never in an error message: it comes from `POLISNATCH_JOBS_KEY` or
the file below, read per call so a rotation does not need a restart.

The job contract that lived here (link and lure jobs) went with the lure
commands on 2026-11-11.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_KEY_FILE = "/etc/poliswag/secrets/polisnatch-jobs-key"
KEY_FILE_ENV = "POLISNATCH_JOBS_KEY_FILE"
KEY_ENV = "POLISNATCH_JOBS_KEY"


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
