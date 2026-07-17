import asyncio
import time

from modules.config import Config


class StackRecovery:
    """Recreates the scanner-side containers when the map goes fully red.

    "Red" here means every region reports all workers down while the MITM
    device is still connected — the signature of an exhausted/stuck account
    pool rather than a device problem. Experience shows a fresh dragonite
    (and rotom-ng) container clears it, so after the red state persists past
    RED_BEFORE_RECREATE the affected services are force-recreated via docker
    compose. The db/golbat/map containers are deliberately left alone.
    """

    # How long the full-red state must persist before acting.
    RED_BEFORE_RECREATE = 600  # 10 minutes
    # Minimum gap between recreations — enough for a full account warm-up.
    RECREATE_COOLDOWN = 2700  # 45 minutes
    # docker compose can take a while pulling/recreating; don't hang the loop.
    RECREATE_TIMEOUT = 180

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self._red_since: float | None = None
        self._last_recreate: float = 0

    def _log(self, msg, level="ERROR"):
        self.poliswag.utility.log_to_file(msg, level)

    @property
    def auto_recreate_enabled(self) -> bool:
        try:
            rows = self.poliswag.db.get_data_from_database(
                "SELECT auto_recreate_enabled FROM poliswag LIMIT 1"
            )
            return bool(rows[0]["auto_recreate_enabled"]) if rows else True
        except Exception:
            return True

    @auto_recreate_enabled.setter
    def auto_recreate_enabled(self, value: bool) -> None:
        try:
            self.poliswag.db.execute_query_to_database(
                "UPDATE poliswag SET auto_recreate_enabled = %s",
                params=(1 if value else 0,),
            )
        except Exception as e:
            self._log(f"Failed to persist auto_recreate_enabled: {e}")

    async def observe(self, all_red: bool) -> bool:
        """Track the red state across ticks; recreate once it persists.

        Called every scheduler tick from the voice-channel status pass.
        Returns True when a recreation was triggered this tick.
        """
        now = time.time()

        if not all_red:
            self._red_since = None
            return False

        if self._red_since is None:
            self._red_since = now
            return False

        red_duration = now - self._red_since
        if red_duration < self.RED_BEFORE_RECREATE:
            return False

        if not self.auto_recreate_enabled:
            return False

        if now - self._last_recreate < self.RECREATE_COOLDOWN:
            return False

        self._last_recreate = now
        self._log(
            f"Map fully red for {int(red_duration)}s with device connected — "
            f"recreating {Config.RECREATE_SERVICES}",
            "INFO",
        )
        ok = await self.recreate_services()
        minutes = int(red_duration // 60)
        if ok:
            await self._notify(
                f"🔄 Mapa em baixo há **{minutes} min** com o dispositivo ligado — "
                f"containers `{Config.RECREATE_SERVICES}` recriados automaticamente."
            )
            # Fresh containers need time to come up; restart the red clock.
            self._red_since = None
        else:
            await self._notify(
                f"⚠️ Mapa em baixo há **{minutes} min** — recriação automática dos "
                f"containers **falhou**. Intervenção manual necessária."
            )
        return ok

    async def recreate_services(self) -> bool:
        """Run docker compose up -d --force-recreate on the scanner services."""
        services = Config.RECREATE_SERVICES.split()
        cmd = [
            "docker-compose",
            "-f",
            Config.UNOWNHASH_COMPOSE_FILE,
            "up",
            "-d",
            "--force-recreate",
        ] + services

        if not Config.IS_PRODUCTION:
            self._log(f"[DEV] Would run: {' '.join(cmd)}", "INFO")
            return True

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=self.RECREATE_TIMEOUT
                )
            except asyncio.TimeoutError:
                proc.kill()
                self._log(f"Recreate timed out after {self.RECREATE_TIMEOUT}s")
                return False
            output = stdout.decode().strip()
            if proc.returncode != 0:
                self._log(f"Recreate failed (rc={proc.returncode}): {output[-500:]}")
                return False
            self._log(f"Recreated services: {Config.RECREATE_SERVICES}", "INFO")
            return True
        except Exception as e:
            self._log(f"Error recreating services: {e}")
            return False

    async def _notify(self, message: str) -> None:
        try:
            channel = self.poliswag.MOD_CHANNEL
            if channel:
                await channel.send(message)
        except Exception as e:
            self._log(f"Failed to send stack recovery notification: {e}")
