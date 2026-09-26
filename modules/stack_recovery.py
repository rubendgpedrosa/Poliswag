import asyncio
import os
import time

import discord

from modules.cached_bool_setting import CachedBoolSetting
from modules.config import Config
from modules.embeds import status_embed
from modules.logging_mixin import LoggingMixin


class StackRecovery(LoggingMixin):
    """Recovery for a fully red map (every region all-workers-down).

    Two attempts, timed from the start of the red episode (10 and 30 min),
    each fired at most once per episode. Each attempt does the whole thing:
    force-recreate the scanner containers (dragonite + rotom-ng), then
    force-stop Pokémon GO + Pokémod on the phone and start Aegis's mapping
    service again, so it reconnects to the fresh rotom. Until 2026-09-26 the
    phone waited for the second attempt, and on 2026-09-25 that cost 20 min
    of a map a phone restart fixed.

    After both, recovery stops trying automatically and just waits — no
    device reboot is ever triggered. The db/golbat/map containers are
    deliberately left alone.
    """

    # Seconds red before each attempt — ordered, one each per episode.
    RECOVERY_LADDER = (600, 1800)
    # Lets rotom-ng come up before the phone's apps try to reach it.
    DEVICE_AFTER_CONTAINERS = 15
    # docker compose can take a while pulling/recreating; don't hang the loop.
    RECREATE_TIMEOUT = 180

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self._red_since: float | None = None
        # Rungs used in the current red episode (0..len(RECOVERY_LADDER)).
        self._recovery_attempts: int = 0
        self._auto_recreate_setting = CachedBoolSetting(
            poliswag, "auto_recreate_enabled"
        )

    async def get_auto_recreate_enabled(self) -> bool:
        return await self._auto_recreate_setting.get()

    async def set_auto_recreate_enabled(self, value: bool) -> None:
        await self._auto_recreate_setting.set(value)

    async def observe(self, all_red: bool | None) -> bool:
        """Advance the red escalation ladder one tick.

        Called every scheduler tick from the voice-channel status pass.
        ``True`` advances a confirmed all-red episode, ``False`` resets it
        after confirmed scanner activity, and ``None`` preserves the current
        episode while scanner status is unavailable.
        Returns True when a recovery rung was run and succeeded.
        """
        now = time.time()

        if all_red is None:
            # A forced recreate briefly makes Dragonite's status endpoint
            # unavailable.  Treating that as recovery would re-arm the ladder
            # and let attempts 1/2 and 2/2 repeat forever.
            return False

        if not all_red:
            # Mods saw the recovery attempts, so they hear it came back too;
            # otherwise the last word in the channel is "em baixo".
            if self._recovery_attempts and self._red_since is not None:
                await self._announce_recovered(now - self._red_since)
            self._red_since = None
            self._recovery_attempts = 0
            return False

        if self._red_since is None:
            self._red_since = now

        if not await self.get_auto_recreate_enabled():
            return False

        total = len(self.RECOVERY_LADDER)
        if self._recovery_attempts >= total:
            # Every rung already used up for this episode — stop attempting
            # and just wait for manual intervention.
            return False

        red_duration = now - self._red_since
        if red_duration < self.RECOVERY_LADDER[self._recovery_attempts]:
            return False

        self._recovery_attempts += 1
        attempt = self._recovery_attempts
        self._log(
            f"Map fully red for {int(red_duration // 60)} min — recreating "
            f"containers and restarting the phone's apps (attempt {attempt}/{total})",
            "INFO",
        )
        ok = await self._recover()
        await self._announce(attempt, total, red_duration, ok)
        return ok

    async def _recover(self) -> bool:
        """Containers, then the phone. The phone runs even if containers failed."""
        containers_ok = await self.recreate_services()
        await asyncio.sleep(self.DEVICE_AFTER_CONTAINERS)
        device_ok = await self.poliswag.device_manager.restart_scanner_apps()
        return containers_ok and device_ok

    async def _announce(
        self, attempt: int, total: int, red_duration: float, ok: bool
    ) -> None:
        # Mods need the state, not the mechanics: those are in the logs.
        title = f"🔴 Mapa em baixo há {int(red_duration // 60)} min"
        if attempt >= total:
            description = (
                "Última tentativa de recuperação automática feita. "
                "Se não voltar, é preciso intervir manualmente."
            )
        else:
            minutes = max(0, int((self.RECOVERY_LADDER[attempt] - red_duration) // 60))
            description = (
                "Tentativa de recuperação automática feita. "
                f"Se não voltar, nova tentativa daqui a **{minutes} min**."
            )
        if not ok:
            description = f"A recuperação automática deu erro.\n{description}"
        await self._notify(
            title, description, discord.Color.orange() if ok else discord.Color.red()
        )

    async def _announce_recovered(self, red_duration: float) -> None:
        await self._notify(
            "🟢 Mapa de volta",
            f"Voltou ao normal após **{int(red_duration // 60)} min** em baixo.",
            discord.Color.green(),
        )

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
                embed = status_embed(title, description, color=color)
                await channel.send(embed=embed)
        except Exception as e:
            self._log(f"Failed to send stack recovery notification: {e}")
