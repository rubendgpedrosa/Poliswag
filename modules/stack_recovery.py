import asyncio
import os
import time

import discord

from modules.cached_bool_setting import CachedBoolSetting
from modules.config import Config
from modules.embeds import status_embed
from modules.logging_mixin import LoggingMixin


class StackRecovery(LoggingMixin):
    """Escalation ladder for a fully red map (every region all-workers-down).

    Two rungs, both timed from the start of the red episode and each fired at
    most once per episode:

    - 10 min: force-recreate the scanner containers (dragonite + rotom-ng)
      via docker compose.
    - 30 min: force-stop Pokémon GO + Pokémod on the device and start Aegis's
      mapping service again. Recreating containers cannot fix a wedged MITM,
      so the ladder has to reach the phone before it gives up.

    After both rungs, recovery stops trying automatically and just waits — no
    device reboot is ever triggered. The db/golbat/map containers are
    deliberately left alone.
    """

    # (seconds red before firing, rung) — ordered, one attempt each per episode.
    RECOVERY_LADDER = (
        (600, "containers"),
        (1800, "device"),
    )
    # How each rung is described in the mod-channel notifications.
    RUNG_LABELS = {
        "containers": "a recriar containers do scanner",
        "device": "a reiniciar Pokémon GO + Pokémod no telemóvel",
    }
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
        threshold, rung = self.RECOVERY_LADDER[self._recovery_attempts]
        if red_duration < threshold:
            return False

        self._recovery_attempts += 1
        attempt = self._recovery_attempts
        self._log(
            f"Map fully red for {int(red_duration // 60)} min — running "
            f"'{rung}' recovery (attempt {attempt}/{total})",
            "INFO",
        )
        ok = await self._run_rung(rung)
        await self._announce(rung, attempt, total, red_duration, ok)
        return ok

    async def _run_rung(self, rung: str) -> bool:
        if rung == "containers":
            return await self.recreate_services()
        return await self.poliswag.device_manager.restart_scanner_apps()

    def _minutes_to_next_rung(self, attempt: int, red_duration: float) -> int:
        """Minutes from now until rung ``attempt`` (0-based) fires."""
        return max(0, int((self.RECOVERY_LADDER[attempt][0] - red_duration) // 60))

    async def _announce(
        self, rung: str, attempt: int, total: int, red_duration: float, ok: bool
    ) -> None:
        label = self.RUNG_LABELS[rung]
        last_attempt = attempt >= total

        if not ok:
            remaining = (
                "Sem mais tentativas automáticas — intervenção manual necessária."
                if last_attempt
                else f"Nova tentativa em "
                f"**{self._minutes_to_next_rung(attempt, red_duration)} min** "
                f"({self.RUNG_LABELS[self.RECOVERY_LADDER[attempt][1]]}) se continuar."
            )
            await self._notify(
                "Recuperação automática — falhou",
                f"Contas em baixo — {label} falhou (tentativa {attempt}/{total}).\n"
                f"{remaining}",
                discord.Color.red(),
            )
            return

        if last_attempt:
            description = (
                f"Contas em baixo — {label} (tentativa {attempt}/{total}, "
                f"última tentativa automática).\n"
                f"Se continuar em baixo, intervenção manual é necessária."
            )
        else:
            description = (
                f"Contas em baixo — {label} (tentativa {attempt}/{total}).\n"
                f"Nova tentativa em "
                f"**{self._minutes_to_next_rung(attempt, red_duration)} min** "
                f"({self.RUNG_LABELS[self.RECOVERY_LADDER[attempt][1]]}) se continuar."
            )
        await self._notify(
            "Recuperação automática", description, discord.Color.orange()
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
