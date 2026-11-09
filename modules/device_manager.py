import asyncio
import json
import time
from pathlib import Path

from modules.cached_bool_setting import CachedBoolSetting
from modules.config import Config
from modules.embeds import status_embed
from modules.logging_mixin import LoggingMixin
from modules.scanner_notice import post_scanner_status


class DeviceManager(LoggingMixin):
    """Manages ADB interactions with the configured Android device."""

    # How long the device must be continuously offline before alerting.
    OFFLINE_BEFORE_ALERT = 900  # 15 minutes offline before acting

    POGO_PACKAGE = "com.nianticlabs.pokemongo"
    POGO_ACTIVITY = (
        "com.nianticlabs.pokemongo/"
        "com.nianticproject.holoholo.libholoholo.unity.UnityMainActivity"
    )

    # PoliSnatch is the MITM since 2026-10-31 (Aegis retired): its loader
    # injects the release into a fresh game process and reconnects by
    # itself. A release supports one game build; a Play Store update to an
    # unsupported build leaves the loader showing "Unsupported" (2026-11-09).
    POLISNATCH_PACKAGE = "pt.leiria.deviceinfo"
    # Let the phone settle after bringing an app to the front.
    APP_RESTART_SETTLE = 5
    # How often a scanning phone's game version is re-read and remembered.
    VERSION_REMEMBER_EVERY = 3600

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self._offline_since: float | None = None
        self._last_notification_time: float = 0
        self._auto_reboot_setting = CachedBoolSetting(poliswag, "auto_reboot_enabled")
        self._version_read_at: float = 0

    async def get_auto_reboot_enabled(self) -> bool:
        return await self._auto_reboot_setting.get()

    async def set_auto_reboot_enabled(self, value: bool) -> None:
        await self._auto_reboot_setting.set(value)

    async def _adb(self, *args, timeout: int = 15) -> tuple[str, str, int]:
        """Run a raw ``adb`` invocation. Returns (stdout, stderr, returncode).

        Raises RuntimeError if the command exceeds ``timeout``.
        """
        proc = await asyncio.create_subprocess_exec(
            "adb",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"Comando ADB expirou após {timeout}s")
        return stdout.decode().strip(), stderr.decode().strip(), proc.returncode

    async def _device_state(self, device: str) -> str:
        """adb's reported state for ``device`` ('device', 'unauthorized',
        'offline'), or '' when it can't be determined. Never raises."""
        try:
            stdout, _, rc = await self._adb("-s", device, "get-state", timeout=8)
        except RuntimeError as e:
            self._log(f"Could not read ADB state for {device}: {e}")
            return ""
        return stdout if rc == 0 else ""

    async def _recover_session(self, device: str) -> None:
        """Clear a stale/unauthorized adb session and re-handshake.

        A device that flaps its connection commonly lingers as
        'unauthorized'/'offline'; a plain ``adb connect`` won't clear that, but
        the full ``disconnect → kill-server → start-server → connect`` cycle
        reliably promotes it back to 'device'. Best-effort: individual step
        failures are logged, not raised, so ``run`` can still attempt the
        command afterwards.
        """
        self._log(f"ADB session for {device} not ready — re-handshaking", "INFO")
        for cmd in (
            ("disconnect", device),
            ("kill-server",),
            ("start-server",),
            ("connect", device),
        ):
            try:
                await self._adb(*cmd, timeout=10)
            except RuntimeError as e:
                self._log(f"ADB recovery step '{' '.join(cmd)}' failed: {e}")

    async def run(self, *args, timeout: int = 15) -> tuple[str, str, int]:
        """Connect to the configured ADB device and run a command.

        Returns (stdout, stderr, returncode).
        Raises RuntimeError if ADB_DEVICE is not configured or adb is unavailable.

        If the device is reachable but its session is stale ('unauthorized' /
        'offline' — the usual aftermath of the device flapping its connection),
        a one-shot re-handshake is attempted before running the command, so a
        recoverable session never blocks a command that depends on it.
        """
        device = Config.ADB_DEVICE
        if not device:
            raise RuntimeError("ADB_DEVICE não está configurado no .env")

        # connect is idempotent — safe on every call
        await self._adb("connect", device, timeout=10)

        if await self._device_state(device) != "device":
            await self._recover_session(device)

        return await self._adb("-s", device, *args, timeout=timeout)

    async def get_model(self) -> str | None:
        """Return the device model string, or None on failure."""
        try:
            stdout, _, rc = await self.run(
                "shell", "getprop", "ro.product.model", timeout=8
            )
            return stdout if rc == 0 and stdout else None
        except RuntimeError as e:
            self._log(f"Could not read device model: {e}")
            return None

    async def logcat_filtered(self, lines: int = 10) -> str:
        """Return the last `lines` logcat entries that mention aegis or poke.

        Runs grep on the device side so the entire circular buffer is searched,
        not just a fixed recent window.
        """
        cmd = f"logcat -d | grep -iE 'aegis|poke' | tail -{lines}"
        try:
            stdout, stderr, rc = await self.run("shell", cmd, timeout=25)
        except RuntimeError as e:
            self._log(f"logcat fetch failed: {e}")
            return f"Erro: {e}"

        output = stdout or stderr or ""
        return output if output else "(sem linhas com 'aegis' ou 'poke')"

    async def reboot(self) -> bool:
        """Send adb reboot. Returns True if the command was accepted."""
        try:
            _, _, rc = await self.run("reboot", timeout=10)
            return rc == 0
        except RuntimeError as e:
            self._log(f"Reboot command failed: {e}")
            return False

    async def restart_app(self) -> bool:
        """Force-stop and relaunch Pokémon GO. Much lighter than a reboot.

        Aegis re-injects when the game starts, so a plain activity launch is
        enough to bring the MITM worker back after an app-level wedge.
        """
        try:
            _, _, rc = await self.run(
                "shell", "am", "force-stop", self.POGO_PACKAGE, timeout=15
            )
            if rc != 0:
                return False
            _, _, rc = await self.run(
                "shell", "am", "start", "-n", self.POGO_ACTIVITY, timeout=15
            )
            return rc == 0
        except RuntimeError as e:
            self._log(f"App restart failed: {e}")
            return False

    async def run_as_root(
        self, command: str, timeout: int = 15
    ) -> tuple[str, str, int]:
        """Run a shell command on the device through su (Magisk).

        MappingService is not exported, so starting it is not something adb's
        plain shell uid can be relied on to do.
        """
        return await self.run("shell", "su", "-c", command, timeout=timeout)

    async def restart_scanner_apps(self) -> bool:
        """The phone rung for PoliSnatch: put the game in front, or start the
        loader when the game is gone.

        PoliSnatch's runbook (CURRENT-RUNTIME.md): a game killed in the
        background comes back by bringing it to the front, and the loader
        reconnects it; with no game at all, the loader (which reconnects
        automatically) starts it. The game is never force-stopped here: a
        Connect is the loader's job and parks every account.
        """
        try:
            stdout, _, rc = await self.run(
                "shell", f"pidof {self.POGO_PACKAGE}", timeout=15
            )
            package = self.POGO_PACKAGE if stdout.strip() else self.POLISNATCH_PACKAGE
            stdout, stderr, rc = await self.run(
                "shell",
                f"monkey -p {package} -c android.intent.category.LAUNCHER 1",
                timeout=20,
            )
            if rc != 0 or "aborted" in f"{stdout} {stderr}".lower():
                self._log(f"Bringing {package} to the front failed (rc={rc})")
                return False
            await asyncio.sleep(self.APP_RESTART_SETTLE)
            return True
        except RuntimeError as e:
            self._log(f"Scanner app restart failed: {e}")
            return False

    async def phone_health(self) -> tuple[str, str]:
        """('healthy' | 'broken' | 'game_updated' | 'unreachable', reason).

        'game_updated' means the installed Pokémon GO is not the build the
        phone last scanned with: the Play Store updated it and the PoliSnatch
        release does not support it yet. Restarting anything cannot help;
        a release for the new build (or the old build back) can.
        """
        command = (
            f"l=$(pidof {self.POLISNATCH_PACKAGE}); g=$(pidof {self.POGO_PACKAGE}); "
            'echo "loader=$l"; echo "pogo=$g"; '
            f"dumpsys package {self.POGO_PACKAGE} | grep -m1 versionName; true"
        )
        try:
            stdout, stderr, rc = await self.run("shell", command, timeout=20)
        except RuntimeError as e:
            return "unreachable", str(e)
        fields = {}
        for line in stdout.splitlines():
            key, _, value = line.strip().partition("=")
            if key in ("loader", "pogo", "versionName"):
                fields[key] = value.strip()
        if rc != 0 or "loader" not in fields:
            return "unreachable", (stderr or stdout or f"adb rc={rc}").strip()
        version = fields.get("versionName", "")
        known = self.working_game_version()
        if version and known and version != known:
            return (
                "game_updated",
                f"Pokémon GO {version} instalado; o scanner funcionava com {known}",
            )
        if not fields.get("loader"):
            return "broken", "PoliSnatch loader is not running"
        if not fields.get("pogo"):
            return "broken", "Pokémon GO is not running"
        return "healthy", f"PoliSnatch and Pokémon GO {version} running"

    def _version_file(self):
        return Path(Config.SCANNER_GAME_VERSION_FILE)

    def working_game_version(self) -> str:
        """The game build the phone last scanned with, or ''."""
        try:
            data = json.loads(self._version_file().read_text())
            return str(data.get("version") or "")
        except (OSError, ValueError, AttributeError):
            return ""

    async def remember_working_version(self) -> None:
        """While the map scans, record the phone's game build (hourly)."""
        now = time.time()
        if now - self._version_read_at < self.VERSION_REMEMBER_EVERY:
            return
        self._version_read_at = now
        try:
            stdout, _, rc = await self.run(
                "shell",
                f"dumpsys package {self.POGO_PACKAGE} | grep -m1 versionName",
                timeout=20,
            )
        except RuntimeError:
            return
        version = stdout.strip().partition("=")[2].strip()
        if rc != 0 or not version or version == self.working_game_version():
            return
        path = self._version_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": version, "at": int(now)}))
        self._log(f"Scanner game version recorded: {version}", "INFO")

    def _next_notify_interval(self, offline_duration: float) -> float:
        """Escalating gap between repeated offline notifications."""
        if offline_duration < 6 * 3600:
            return 3600  # every 1 h for the first 6 h offline
        return 6 * 3600  # every 6 h after that

    async def alert_if_offline(self) -> bool:
        """Alert when Rotom reports the device offline long enough.

        The red-map ladder (StackRecovery) is the primary self-healing path;
        this watchdog covers the case where the worker-status endpoint is
        unreachable (no red signal) but the device itself is gone. It never
        reboots automatically — it only raises an alert so a human can act.

        Notification cadence:
        - First alert at OFFLINE_BEFORE_ALERT (15 min).
        - Repeated every 1 h while offline < 6 h, then every 6 h.
        """
        if not Config.ADB_DEVICE or not await self.get_auto_reboot_enabled():
            return False

        device_alive = await self.poliswag.account_monitor.is_device_connected()
        now = time.time()

        if device_alive:
            # Only if mods were told it was offline: close that thread.
            if self._last_notification_time and self._offline_since is not None:
                await self._notify(
                    f"🟢 Telemóvel do scanner de volta online após "
                    f"**{int((now - self._offline_since) // 60)} min** offline."
                )
            self._offline_since = None
            self._last_notification_time = 0
            return False

        if self._offline_since is None:
            self._offline_since = now
            return False

        offline_duration = now - self._offline_since

        if offline_duration < self.OFFLINE_BEFORE_ALERT:
            return False

        # Escalating throttle between repeated alerts (first one fires immediately).
        since_last_notify = now - self._last_notification_time
        if (
            self._last_notification_time > 0
            and since_last_notify < self._next_notify_interval(offline_duration)
        ):
            return False

        self._last_notification_time = now
        self._log("Device offline — no auto-reboot, alerting for manual action", "INFO")
        await self._notify(
            f"⚠️ Telemóvel do scanner offline há "
            f"**{int(offline_duration // 60)} min** — precisa de intervenção manual."
        )
        return True

    async def _notify(self, message: str) -> None:
        """Post `message` as an embed in the mod channel if it's available."""
        try:
            channel = self.poliswag.MOD_CHANNEL
            if channel:
                await post_scanner_status(
                    self.poliswag,
                    channel,
                    status_embed("Telemóvel do scanner", message),
                    up=message.startswith("🟢"),
                )
        except Exception as e:
            self._log(f"Failed to send device notification: {e}")
