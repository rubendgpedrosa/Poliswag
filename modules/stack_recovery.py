import asyncio
import os
import time

import discord

from modules.config import Config


class StackRecovery:
    """Escalation ladder for a fully red map (every region all-workers-down).

    On red, force-recreate the scanner containers (dragonite + rotom-ng) via
    docker compose — first at RECREATE_THRESHOLDS[0] (10 min) into the red
    episode, and again at RECREATE_THRESHOLDS[1] (45 min) if it's still red.
    After both attempts, recovery stops trying automatically and just waits
    — no device reboot is ever triggered. The db/golbat/map containers are
    deliberately left alone.
    """

    # How long a red episode must persist before each recreate attempt.
    RECREATE_THRESHOLDS = (600, 2700)  # 10 min, 45 min
    # docker compose can take a while pulling/recreating; don't hang the loop.
    RECREATE_TIMEOUT = 180

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self._red_since: float | None = None
        # Recreate attempts used in the current red episode (0..len(RECREATE_THRESHOLDS)).
        self._recreate_attempts: int = 0

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
        Returns True when a recreate attempt was made and succeeded.
        """
        now = time.time()

        if not all_red:
            self._red_since = None
            self._recreate_attempts = 0
            return False

        if self._red_since is None:
            self._red_since = now

        if not self.auto_recreate_enabled:
            return False

        total = len(self.RECREATE_THRESHOLDS)
        if self._recreate_attempts >= total:
            # Both attempts already used up for this episode — stop attempting
            # and just wait for manual intervention.
            return False

        red_duration = now - self._red_since
        if red_duration < self.RECREATE_THRESHOLDS[self._recreate_attempts]:
            return False

        self._recreate_attempts += 1
        attempt = self._recreate_attempts
        self._log(
            f"Map fully red for {int(red_duration // 60)} min — recreating "
            f"{Config.RECREATE_SERVICES} (attempt {attempt}/{total})",
            "INFO",
        )
        ok = await self.recreate_services()
        if ok:
            if attempt < total:
                next_in = int((self.RECREATE_THRESHOLDS[attempt] - red_duration) // 60)
                await self._notify(
                    "Recuperação automática",
                    f"Contas em baixo — a reiniciar sistema (tentativa {attempt}/{total}).\n"
                    f"Nova tentativa em **{next_in} min** se continuar.",
                    discord.Color.orange(),
                )
            else:
                await self._notify(
                    "Recuperação automática",
                    f"Contas em baixo — a reiniciar sistema (tentativa {attempt}/{total}, "
                    f"última tentativa automática).\n"
                    f"Se continuar em baixo, intervenção manual é necessária.",
                    discord.Color.orange(),
                )
        else:
            remaining = (
                "Nova tentativa mais tarde."
                if attempt < total
                else "Sem mais tentativas automáticas — intervenção manual necessária."
            )
            await self._notify(
                "Recuperação automática — falhou",
                f"Contas em baixo — reinício do sistema falhou (tentativa {attempt}/{total}).\n"
                f"{remaining}",
                discord.Color.red(),
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
                await proc.wait()
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

    async def _notify(self, title: str, description: str, color: discord.Color) -> None:
        try:
            channel = self.poliswag.MOD_CHANNEL
            if channel:
                embed = discord.Embed(title=title, description=description, color=color)
                await channel.send(embed=embed)
        except Exception as e:
            self._log(f"Failed to send stack recovery notification: {e}")
