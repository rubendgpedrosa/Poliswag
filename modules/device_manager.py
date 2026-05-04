import asyncio
import time

from modules.config import Config


class DeviceManager:
    """Manages ADB interactions with the configured Android device."""

    # How long the device must be continuously offline before an auto-reboot fires.
    OFFLINE_BEFORE_REBOOT = 300  # 5 minutes offline before acting
    # Minimum gap between reboots — long enough for the device to fully boot + reconnect.
    AUTO_REBOOT_COOLDOWN = 1800  # 30 minutes between reboots

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self._last_auto_reboot: float = 0
        self._offline_since: float | None = (
            None  # timestamp when device first went offline
        )

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

    async def run(self, *args, timeout: int = 15) -> tuple[str, str, int]:
        """Connect to the configured ADB device and run a command.

        Returns (stdout, stderr, returncode).
        Raises RuntimeError if ADB_DEVICE is not configured or adb is unavailable.
        """
        device = Config.ADB_DEVICE
        if not device:
            raise RuntimeError("ADB_DEVICE não está configurado no .env")

        # connect is idempotent — safe on every call
        conn = await asyncio.create_subprocess_exec(
            "adb",
            "connect",
            device,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await conn.communicate()

        proc = await asyncio.create_subprocess_exec(
            "adb",
            "-s",
            device,
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

    async def auto_reboot_if_offline(self) -> bool:
        """Reboot the device if Rotom reports it offline long enough.

        Guards:
        - Device must be offline for at least OFFLINE_BEFORE_REBOOT seconds
          (gives it time to self-recover after a transient glitch).
        - At least AUTO_REBOOT_COOLDOWN seconds must have passed since the last
          reboot (prevents reboot loops while the device is still booting).

        Returns True if a reboot was triggered.
        """
        if not Config.ADB_DEVICE or not self.auto_reboot_enabled:
            return False

        device_alive = await self.poliswag.account_monitor.is_device_connected()
        now = time.time()

        if device_alive:
            self._offline_since = None  # reset the offline timer
            return False

        # Device is offline — start or continue the offline timer
        if self._offline_since is None:
            self._offline_since = now
            return False

        offline_duration = now - self._offline_since
        since_last_reboot = now - self._last_auto_reboot

        if offline_duration < self.OFFLINE_BEFORE_REBOOT:
            return False  # hasn't been offline long enough yet

        if since_last_reboot < self.AUTO_REBOOT_COOLDOWN:
            return False  # too soon since last reboot — still booting

        self._log(
            f"Device offline for {int(offline_duration)}s — triggering auto ADB reboot",
            "INFO",
        )
        rebooted = await self.reboot()
        if rebooted:
            self._last_auto_reboot = now
            self._offline_since = None  # reset; will re-arm if still offline after boot
            self._log("Auto ADB reboot sent successfully", "INFO")
            await self._notify(
                f"📵 Dispositivo offline há **{int(offline_duration // 60)} min** — "
                f"reboot automático via ADB enviado para `{Config.ADB_DEVICE}`."
            )
        else:
            self._log("Auto ADB reboot command failed")
            await self._notify(
                f"⚠️ Dispositivo offline há **{int(offline_duration // 60)} min** — "
                f"tentativa de reboot ADB **falhou**. Intervenção manual necessária."
            )
        return rebooted

    async def _notify(self, message: str) -> None:
        """Send a plain message to the mod channel if it's available."""
        try:
            channel = self.poliswag.MOD_CHANNEL
            if channel:
                await channel.send(message)
        except Exception as e:
            self._log(f"Failed to send device notification: {e}")
