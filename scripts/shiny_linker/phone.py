"""The scanner phone: process checks over adb, and the temporary hook.

The scanner's agent (Aegis) only forwards a fixed list of game methods, and
the ones the linker needs are not on it. The hook rewrites a GET_PLAYER
request that carries this run's random nonce into one of five listed
methods; every other request passes untouched.

Patching the game's send function while workers are calling it crashed the
game twice on 2026-10-10 (SIGSEGV at the function's entry), so Dragonite is
frozen for a few seconds around attach and around detach, and the injector
is only ever killed after the hook has removed itself.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .proto import blob, integer

GAME = "com.nianticlabs.pokemongo"
MAPPING = "com.pokemod.aegis:mapping"
INJECTOR = "/data/user/0/com.pokemod.aegis/files/injector/pokemod-injector-arm64"
# Social proxy (search by friend code), lobby join, lobby leave, free-ticket
# claim, raid invitation.
METHODS = [5012, 159, 160, 815, 1504]
SOCIAL_ACTIONS = [10000]
# The hook removes itself after this long whatever happens.
LIFETIME_MS = 600_000
LOGGER = logging.getLogger(__name__)
ERROR_DETAIL_LIMIT = 500
_HOOK_SOURCE = Path(__file__).with_name("native-method-redirect.js")


class PhoneError(RuntimeError):
    pass


class HookError(RuntimeError):
    """The hook itself misbehaved. The linker switches itself off on this."""


def _serial():
    return os.environ.get("ADB_DEVICE") or "192.168.1.222:5555"


def subprocess_detail(error):
    """Describe a failed tool without printing its potentially private argv."""
    argv = getattr(error, "cmd", None)
    executable = (
        Path(argv[0]).name if isinstance(argv, (list, tuple)) and argv else "tool"
    )
    # Only these tools receive infrastructure commands, never login/RPC data.
    tool = executable if executable in {"adb", "docker"} else "subprocess"
    if isinstance(error, subprocess.TimeoutExpired):
        return f"{tool} timed out after {error.timeout}s"
    returncode = getattr(error, "returncode", None)
    detail = f"{tool} failed"
    if returncode is not None:
        detail += f" (exit {returncode})"
    if tool in {"adb", "docker"} and getattr(error, "stderr", None):
        stderr = error.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf8", "replace")
        detail += ": " + " ".join(stderr.split())[:ERROR_DETAIL_LIMIT]
    return detail


def adb(command, timeout=20):
    done = subprocess.run(
        ["adb", "-s", _serial(), "exec-out", command],
        capture_output=True,
        timeout=timeout,
    )
    if done.returncode:
        raise PhoneError(
            subprocess_detail(
                subprocess.CalledProcessError(
                    done.returncode, ["adb"], stderr=done.stderr
                )
            )
        )
    return done.stdout.decode("utf8", "replace").strip()


def _pid(process):
    try:
        out = adb(f"pidof {process}")
    except (PhoneError, subprocess.TimeoutExpired) as error:
        detail = (
            subprocess_detail(error)
            if isinstance(error, subprocess.SubprocessError)
            else str(error)
        )
        LOGGER.warning("Phone check unavailable: %s", detail)
        return None
    return int(out.split()[0]) if out else None


def game_pid():
    return _pid(GAME)


def mapping_pid():
    return _pid(MAPPING)


def game_version():
    try:
        out = adb(f"dumpsys package {GAME}")
    except (PhoneError, subprocess.TimeoutExpired) as error:
        detail = (
            subprocess_detail(error)
            if isinstance(error, subprocess.SubprocessError)
            else str(error)
        )
        LOGGER.warning("Phone check unavailable: %s", detail)
        return None
    match = re.search(r"\bversionName=([0-9.]+)", out)
    return match.group(1) if match else None


class quiet_scanner:
    """Freeze Dragonite so no worker request reaches the game meanwhile."""

    def __enter__(self):
        subprocess.run(
            ["docker", "pause", "dragonite"], capture_output=True, check=True
        )
        time.sleep(2)

    def __exit__(self, *error):
        subprocess.run(
            ["docker", "unpause", "dragonite"], capture_output=True, check=True
        )


class Hook:
    def __init__(self):
        self.nonce = uuid.uuid4().hex
        self.injector_pid = None
        self._script = f"/data/local/tmp/shiny-linker-{self.nonce}.js"
        self._log = f"/data/data/{GAME}/cache/shiny-linker-{self.nonce}.log"

    def log(self):
        return adb(f"su -c 'cat {self._log} 2>/dev/null'")

    def wrap(self, method, payload=b""):
        """A GET_PLAYER payload the hook turns into `method` with `payload`."""
        return blob(4095, self.nonce) + blob(4094, payload) + integer(4093, method)

    def attach(self, pid):
        config = {
            "nonce": list(self.nonce.encode()),
            "apiFieldCount": 36,
            "sendIndex": 28,
            "logPath": self._log,
            "methods": METHODS,
            "socialActions": SOCIAL_ACTIONS,
            "lifetimeMs": LIFETIME_MS,
            "detachDelayMs": 1500,
        }
        source = _HOOK_SOURCE.read_text().replace(
            "__OWNED_REDIRECT_CONFIG__", json.dumps(config)
        )
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".js") as local:
                local.write(source)
                local.flush()
                subprocess.run(
                    ["adb", "-s", _serial(), "push", local.name, self._script],
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
            with quiet_scanner():
                self.injector_pid = int(
                    adb(
                        f"su -c 'nohup {INJECTOR} -p {pid} -s {self._script} -R v8 "
                        "> /dev/null 2>&1 & echo $!'"
                    )
                )
                for _ in range(20):
                    log = self.log()
                    if "OWNED_REDIRECT_READY" in log:
                        break
                    if "OWNED_REDIRECT_SETUP_ERROR" in log:
                        raise HookError("hook setup failed")
                    time.sleep(0.5)
                else:
                    raise HookError("hook did not become ready")
                time.sleep(1)
        except (PhoneError, subprocess.SubprocessError, ValueError) as error:
            detail = (
                subprocess_detail(error)
                if isinstance(error, subprocess.SubprocessError)
                else str(error)
            )
            raise HookError(f"hook attach failed: {detail}") from error
        finally:
            try:
                adb(f"su -c 'rm -f {self._script}'")
            except (PhoneError, subprocess.SubprocessError):
                pass

    def detach(self, send):
        """Ask the hook to remove itself, then stop the injector.

        `send(payload)` delivers a GET_PLAYER payload through a logged-in
        session. Returns False if the hook is still in the game; the
        injector is then left running (killing it with the hook attached is
        what crashed the game) and the hook removes itself at LIFETIME_MS.
        """
        if self.injector_pid is None:
            return True
        try:
            with quiet_scanner():
                if "OWNED_REDIRECT_DETACHED" not in self.log():
                    try:
                        send(self.wrap(0))
                    except Exception:
                        pass
                    time.sleep(4)
                    if "OWNED_REDIRECT_DETACHED" not in self.log():
                        return False
                adb(f"su -c 'kill {self.injector_pid}'")
                time.sleep(1.5)
            adb(f"su -c 'rm -f {self._log}'")
            return True
        except (PhoneError, subprocess.SubprocessError):
            return False
