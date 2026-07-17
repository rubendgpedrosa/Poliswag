import asyncio
import os
import time

from modules.config import Config


class StackRecovery:
    """Escalation ladder for a fully red map (every region all-workers-down).

    Rung 1 — first red tick: force-recreate the scanner containers (dragonite
    + rotom-ng) via docker compose. Red already implies ~10 min of dead
    workers (the liveness window), so no extra debounce is needed. The
    db/golbat/map containers are deliberately left alone.

    Rung 2 — red persists past DEVICE_REBOOT_AFTER: reboot the phone via ADB
    (through DeviceManager, sharing its reboot cooldown so the offline path
    can't double-reboot).
    """

    # Minimum gap between recreations — enough for a full account warm-up.
    RECREATE_COOLDOWN = 1800  # 30 minutes
    # Red persisting this long after rung 1 escalates to a device reboot.
    DEVICE_REBOOT_AFTER = 900  # 15 minutes
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
        """Advance the red escalation ladder one tick.

        Called every scheduler tick from the voice-channel status pass.
        Returns True when an action (recreate or reboot) was taken.
        """
        now = time.time()

        if not all_red:
            self._red_since = None
            return False

        if self._red_since is None:
            self._red_since = now

        if not self.auto_recreate_enabled:
            return False

        red_duration = now - self._red_since

        # Rung 1: fresh containers, immediately on red.
        if now - self._last_recreate >= self.RECREATE_COOLDOWN:
            self._last_recreate = now
            self._log(
                f"Map fully red — recreating {Config.RECREATE_SERVICES}",
                "INFO",
            )
            ok = await self.recreate_services()
            if ok:
                await self._notify(
                    f"🔄 Mapa em baixo — containers `{Config.RECREATE_SERVICES}` "
                    f"recriados automaticamente. Reboot do dispositivo em "
                    f"{self.DEVICE_REBOOT_AFTER // 60} min se continuar em baixo."
                )
            else:
                await self._notify(
                    "⚠️ Mapa em baixo — recriação automática dos containers "
                    "**falhou**. Intervenção manual necessária."
                )
            return ok

        # Rung 2: red survived the fresh containers — reboot the phone.
        if red_duration >= self.DEVICE_REBOOT_AFTER:
            rebooted = await self.poliswag.device_manager.reboot_with_cooldown()
            if rebooted:
                await self._notify(
                    f"📵 Mapa em baixo há **{int(red_duration // 60)} min** apesar dos "
                    f"containers novos — reboot do dispositivo enviado via ADB."
                )
                # Give the phone a full boot cycle before judging red again.
                self._red_since = None
                return True

        return False

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

        # The stack's compose file interpolates ${PWD} in its bind-mount
        # sources. cwd alone doesn't update the PWD env var for a subprocess,
        # so set both — otherwise a recreate mounts blank paths and dragonite
        # comes up without its config.
        stack_dir = os.path.dirname(Config.UNOWNHASH_COMPOSE_FILE)
        env = {**os.environ, "PWD": stack_dir}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=stack_dir,
                env=env,
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
