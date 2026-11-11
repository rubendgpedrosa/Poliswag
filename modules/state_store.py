"""Small JSON state that has to survive a restart, one file per owner.

The files live in Config.STATE_DIR (state/, git-ignored and bind-mounted), not
with the logs. They are local files on purpose: what the map's self-healing
remembers (since when the map is down, which recovery steps it already tried)
has to be readable when the database is the thing that is down.
"""

import contextlib
import json
import os
import tempfile
import time
from pathlib import Path

from modules.config import Config

# An episode (map down, phone offline) left by the previous process is picked
# up again only if that process still saw it this recently. A deploy restarts
# the bot in seconds and the watchdog after ten minutes without a pass; an
# episode older than this ended, or changed, while nobody was looking, and is
# better started afresh than continued with its recovery steps marked spent.
EPISODE_MAX_AGE = 1800


def still_running(state, now=None) -> bool:
    """Was the episode in `state` seen recently enough to be continued?"""
    seen = state.get("seen_at")
    if not isinstance(seen, (int, float)):
        return False
    return 0 <= (now if now is not None else time.time()) - seen <= EPISODE_MAX_AGE


class StateFile:
    """A dict kept in `<STATE_DIR>/<name>.json`.

    load() never raises: a missing or damaged file is an empty state. save()
    replaces the file atomically and skips the write when nothing changed. A
    write that fails is reported through `log` and the caller carries on with
    what it has in memory.
    """

    def __init__(self, name, log=None):
        self.path = Path(Config.STATE_DIR) / f"{name}.json"
        self._log = log
        self._written = None

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            # Nothing to remember yet, and an empty state needs no file.
            self._written = "{}"
            return {}
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        self._written = json.dumps(data, sort_keys=True)
        return data

    def save(self, data: dict) -> None:
        text = json.dumps(data, sort_keys=True)
        if text == self._written:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(
                dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(text)
                os.replace(temporary, self.path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)
                raise
        except OSError as error:
            if self._log:
                self._log(f"Could not save state {self.path.name}: {error}", "ERROR")
            return
        self._written = text
