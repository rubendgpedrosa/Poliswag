import asyncio
import time

from modules.config import Config


class DeviceManager:
    """Manages ADB interactions with the configured Android device."""

    # How long the device must be continuously offline before an auto-reboot fires.
    OFFLINE_BEFORE_REBOOT = 900  # 15 minutes offline before acting
    # Minimum gap between reboots — long enough for the device to fully boot + reconnect.
    AUTO_REBOOT_COOLDOWN = 1800  # 30 minutes between reboots

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self._last_auto_reboot: float = 0
        self._offline_since: float | None = None
        self._last_notification_time: float = 0

    @property
    def auto_reboot_enabled(self) -> bool:
        try:
            rows = self.poliswag.db.get_data_from_database(
                "SELECT auto_reboot_enabled FROM poliswag LIMIT 1"
            )
            return bool(rows[0]["auto_reboot_enabled"]) if rows else True
        except Exception:
            return True

    @auto_reboot_enabled.setter
    def auto_reboot_enabled(self, value: bool) -> None:
        try:
            self.poliswag.db.execute_query_to_database(
                "UPDATE poliswag SET auto_reboot_enabled = %s",
                params=(1 if value else 0,),
            )
        except Exception as e:
            self._log(f"Failed to persist auto_reboot_enabled: {e}")

    def _log(self, msg, level="ERROR"):
        self.poliswag.utility.log_to_file(msg, level)

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
            raise RuntimeError(f"Comando ADB expirou após {timeout}s")
        return stdout.decode().strip(), stderr.decode().strip(), proc.returncode

    async def _device_state(self, device: str) -> str:
        """adb's reported state for ``device`` ('device', 'unauthorized',
        'offline'), or '' when it can't be determined. Never raises."""
        try:
            stdout, _, rc = await self._adb("-s", device, "get-state", timeout=8)
        except RuntimeError:
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
        recoverable session never blocks an auto-reboot.
        """
        device = Config.ADB_DEVICE
        if not device:
            raise RuntimeError("ADB_DEVICE não está configurado no .env")

        # connect is idempotent — safe on every call
        await self._adb("connect", device, timeout=10)

        if await self._device_state(device) != "device":
            await self._recover_session(device)

        return await self._adb("-s", device, *args, timeout=timeout)

    async def is_reachable(self) -> bool:
        """Return True if the device responds to a basic shell command."""
        try:
            _, _, rc = await self.run("shell", "echo", "ping", timeout=8)
            return rc == 0
        except RuntimeError:
            return False

    async def get_model(self) -> str | None:
        """Return the device model string, or None on failure."""
        try:
            stdout, _, rc = await self.run(
                "shell", "getprop", "ro.product.model", timeout=8
            )
            return stdout if rc == 0 and stdout else None
        except RuntimeError:
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
            return f"Erro: {e}"

        output = stdout or stderr or ""
        return output if output else "(sem linhas com 'aegis' ou 'poke')"

    async def reboot(self) -> bool:
        """Send adb reboot. Returns True if the command was accepted."""
        try:
            _, _, rc = await self.run("reboot", timeout=10)
            return rc == 0
        except RuntimeError:
            return False

    def _next_notify_interval(self, offline_duration: float) -> float:
        """Escalating gap between repeated offline notifications."""
        if offline_duration < 6 * 3600:
            return 3600  # every 1 h for the first 6 h offline
        return 6 * 3600  # every 6 h after that

    async def auto_reboot_if_offline(self) -> bool:
        """Reboot the device if Rotom reports it offline long enough.

        Notification cadence:
        - First alert at OFFLINE_BEFORE_REBOOT (15 min).
        - Repeated every 1 h while offline < 6 h, then every 6 h.
        Reboot attempts respect AUTO_REBOOT_COOLDOWN (30 min) independently.
        """
        if not Config.ADB_DEVICE or not self.auto_reboot_enabled:
            return False

        device_alive = await self.poliswag.account_monitor.is_device_connected()
        now = time.time()

        if device_alive:
            self._offline_since = None
            self._last_notification_time = 0
            return False

        if self._offline_since is None:
            self._offline_since = now
            return False

        offline_duration = now - self._offline_since

        if offline_duration < self.OFFLINE_BEFORE_REBOOT:
            return False

        # Attempt reboot if cooldown allows
        since_last_reboot = now - self._last_auto_reboot
        if since_last_reboot >= self.AUTO_REBOOT_COOLDOWN:
            self._log(
                f"Device offline for {int(offline_duration)}s — triggering auto ADB reboot",
                "INFO",
            )
            rebooted = await self.reboot()
            self._last_auto_reboot = now
            if rebooted:
                self._offline_since = None
                self._last_notification_time = 0
                self._log("Auto ADB reboot sent successfully", "INFO")
                await self._notify(
                    f"📵 Dispositivo offline há **{int(offline_duration // 60)} min** — "
                    f"reboot automático via ADB enviado para `{Config.ADB_DEVICE}`."
                )
                return True

        # Notify with escalating throttle (first notification fires immediately)
        since_last_notify = now - self._last_notification_time
        if (
            self._last_notification_time > 0
            and since_last_notify < self._next_notify_interval(offline_duration)
        ):
            return False

        self._last_notification_time = now
        self._log("Auto ADB reboot command failed")
        await self._notify(
            f"⚠️ Dispositivo offline há **{int(offline_duration // 60)} min** — "
            f"tentativa de reboot ADB **falhou**. Intervenção manual necessária."
        )
        return False

    async def _notify(self, message: str) -> None:
        """Send a plain message to the mod channel if it's available."""
        try:
            channel = self.poliswag.MOD_CHANNEL
            if channel:
                await channel.send(message)
        except Exception as e:
            self._log(f"Failed to send device notification: {e}")
