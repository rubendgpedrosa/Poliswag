import asyncio
import json
import os
from pathlib import Path
import re
import time

import discord

from modules.cached_bool_setting import CachedBoolSetting
from modules.config import Config
from modules.embeds import status_embed
from modules.logging_mixin import LoggingMixin
from modules.http_client import fetch_data


class StackRecovery(LoggingMixin):
    """Recovery for a map that stopped scanning, aimed at what actually broke.

    Every tick sorts the scanner into one problem (see _diagnose), and each
    gets its own answer:

    - phone: no Aegis workers in rotom-ng and the phone says it's broken
      (mapping service or Pokémon GO not running, or Aegis stopped logging
      "running and injected"). Restart the phone's apps.
    - stack: Dragonite or rotom-ng doesn't answer. Recreate them, then
      restart the phone's apps so they reconnect to the fresh rotom-ng.
    - link: no workers in rotom-ng but the phone is healthy. Aegis
      reconnects by itself (2026-09-28: back within a minute), so only the
      last rung acts.
    - no_data: workers connected but nothing scanning (accounts, the game's
      servers). A restart can't fix that: tell the mods and wait.
    - unreachable: adb can't reach the phone. Nothing can be restarted
      remotely: tell the mods.

    Rungs are timed from the start of the episode (5 and 15 min), each used
    at most once. The whole of it — recreate + phone apps — is the last
    rung for anything restartable. After that it only waits; no device
    reboot is ever triggered. db/golbat/map containers are left alone.
    """

    # Seconds into an episode before each rung — ordered, one each.
    RECOVERY_LADDER = (300, 900)
    # Lets rotom-ng come up before the phone's apps try to reach it.
    DEVICE_AFTER_CONTAINERS = 15
    # docker compose can take a while pulling/recreating; don't hang the loop.
    RECREATE_TIMEOUT = 180

    # What each problem gets at each rung: "apps" restarts the phone's apps,
    # "full" recreates the containers and then restarts the apps, None waits.
    ACTIONS = {
        "phone": ("apps", "full"),
        "stack": ("full", "full"),
        "link": (None, "full"),
        "no_data": (None, None),
        "unreachable": (None, None),
    }
    # Why the map is down, for the mods: the state, not the mechanics.
    CAUSES = {
        "phone": "o Pokémod parou no telemóvel",
        "stack": "o scanner deixou de responder",
        "link": "o telemóvel não volta a ligar ao scanner",
    }

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self._red_since: float | None = None
        # Rungs used in the current episode (0..len(RECOVERY_LADDER)).
        self._recovery_attempts: int = 0
        # A "waiting" notice (no_data / unreachable) went out this episode.
        self._wait_announced: bool = False
        self._session_wait_announced = False
        self._session_guard_deferred = False
        self._auto_recreate_setting = CachedBoolSetting(
            poliswag, "auto_recreate_enabled"
        )

    async def get_auto_recreate_enabled(self) -> bool:
        return await self._auto_recreate_setting.get()

    async def set_auto_recreate_enabled(self, value: bool) -> None:
        await self._auto_recreate_setting.set(value)

    async def observe(self, all_red: bool | None, workers: int | None) -> bool:
        """Advance the recovery ladder one tick.

        ``all_red`` is Dragonite's view (every worker down; None when it
        didn't answer), ``workers`` the Aegis workers in rotom-ng (None when
        it didn't answer). Returns True when a rung ran and succeeded.
        """
        now = time.time()
        problem = await self._diagnose(all_red, workers)

        if problem is None:
            # Mods saw a post about it, so they hear it came back too;
            # otherwise the last word in the channel is "em baixo".
            if self._red_since is not None and (
                self._recovery_attempts
                or self._wait_announced
                or self._session_wait_announced
            ):
                await self._announce_recovered(now - self._red_since)
            self._red_since = None
            self._recovery_attempts = 0
            self._wait_announced = False
            self._session_wait_announced = False
            return False

        if self._red_since is None:
            self._red_since = now

        if not await self.get_auto_recreate_enabled():
            return False

        elapsed = now - self._red_since
        due = sum(1 for t in self.RECOVERY_LADDER if elapsed >= t)
        if due <= self._recovery_attempts:
            return False

        action = self.ACTIONS[problem][due - 1]
        if action is None:
            if problem in ("no_data", "unreachable") and not self._wait_announced:
                self._wait_announced = True
                await self._announce_waiting(problem, elapsed)
            return False

        # Rungs whose time has passed are spent, so a late first action
        # (e.g. at 15 min) doesn't fire the next rung on the following tick.
        self._log(
            f"Map down for {int(elapsed // 60)} min ({problem}) — running "
            f"'{action}' recovery (rung {due}/{len(self.RECOVERY_LADDER)})",
            "INFO",
        )
        ok = await self._recover(action)
        if self._session_guard_deferred:
            # An account guard is a deferral, not a spent recovery attempt.
            # Recheck on the next tick so a genuine empty window can be used.
            return False
        self._recovery_attempts = due
        await self._announce(problem, elapsed, ok)
        return ok

    async def _diagnose(self, all_red: bool | None, workers: int | None):
        """The current problem (see the class docstring), or None if scanning."""
        if all_red is None or workers is None:
            return "stack"
        if workers == 0:
            # Only now is the phone asked: it's the one case where the answer
            # decides between restarting its apps and waiting.
            state, reason = await self.poliswag.device_manager.phone_health()
            self._log(f"No workers in rotom-ng; phone {state}: {reason}", "INFO")
            return {"broken": "phone", "unreachable": "unreachable"}.get(state, "link")
        if all_red:
            return "no_data"
        return None

    async def _recover(self, action: str) -> bool:
        """'apps' restarts the phone's apps; 'full' recreates the containers
        first. The phone runs even if the containers failed."""
        self._session_guard_deferred = False
        if Config.IS_PRODUCTION and not await self._sessions_allow_reset():
            self._session_guard_deferred = True
            return False
        containers_ok = True
        if action == "full":
            containers_ok = await self.recreate_services()
            await asyncio.sleep(self.DEVICE_AFTER_CONTAINERS)
            # The restarted controller may already have logged accounts in
            # during those 15s. Do not then reset their phone apps as well.
            if Config.IS_PRODUCTION and not await self._sessions_allow_reset():
                return containers_ok
        device_ok = await self.poliswag.device_manager.restart_scanner_apps()
        return containers_ok and device_ok

    async def _sessions_allow_reset(self) -> bool:
        """Fresh direct counts; an unavailable endpoint is never a zero."""
        try:
            accounts = await fetch_data("account_status", log_fn=self._log, timeout=5)
            devices = await fetch_data("device_status", log_fn=self._log, timeout=5)
            count = accounts.get("in_use") if isinstance(accounts, dict) else None
            rows = devices.get("devices") if isinstance(devices, dict) else None
            if type(count) is not int or count < 0 or not isinstance(rows, list):
                raise ValueError("account or device usage is unavailable")
            worker_counts = [row.get("worker_in_use_count") for row in rows]
            if any(type(n) is not int or n < 0 for n in worker_counts):
                raise ValueError("Rotom worker usage is unavailable")
            if count == 0 and sum(worker_counts) == 0:
                self._session_wait_announced = False
                return True
            reason = f"{count} account(s), {sum(worker_counts)} worker(s) still in use"
        except Exception as error:
            reason = str(error)
        self._log(
            f"Scanner reset deferred to preserve account sessions: {reason}", "INFO"
        )
        if not self._session_wait_announced:
            self._session_wait_announced = True
            await self._notify(
                "🟠 Reinício do scanner adiado",
                "Ainda há contas em utilização ou não consegui confirmar que "
                "todas as sessões terminaram. O reinício fica adiado para evitar "
                "novas autenticações. Vou voltar a verificar.",
                discord.Color.orange(),
            )
        return False

    async def _announce(self, problem: str, elapsed: float, ok: bool) -> None:
        title = f"🔴 Mapa em baixo há {int(elapsed // 60)} min"
        cause = f"Causa: {self.CAUSES[problem]}.\n"
        attempt = self._recovery_attempts
        if attempt >= len(self.RECOVERY_LADDER):
            description = (
                "Última tentativa de recuperação automática feita. "
                "Se não voltar, é preciso intervir manualmente."
            )
        else:
            minutes = max(0, int((self.RECOVERY_LADDER[attempt] - elapsed) // 60))
            description = (
                "Tentativa de recuperação automática feita. "
                f"Se não voltar, nova tentativa daqui a **{minutes} min**."
            )
        if not ok:
            description = f"A recuperação automática deu erro.\n{description}"
        await self._notify(
            title,
            cause + description,
            discord.Color.orange() if ok else discord.Color.red(),
        )

    async def _announce_waiting(self, problem: str, elapsed: float) -> None:
        minutes = int(elapsed // 60)
        if problem == "no_data":
            await self._notify(
                f"🟠 Mapa sem dados há {minutes} min",
                "O telemóvel está ligado ao scanner, mas não chegam dados "
                "(contas ou servidores do jogo). Reiniciar não resolve; "
                "fico à espera.",
                discord.Color.orange(),
            )
        else:
            await self._notify(
                f"🔴 Mapa em baixo há {minutes} min",
                "Não consigo chegar ao telemóvel (desligado ou sem rede). "
                "É preciso intervir manualmente.",
                discord.Color.red(),
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
        ]
        staged = Path(Config.UNOWNHASH_COMPOSE_FILE).with_name(
            "scanner-updates.staged.json"
        )
        try:
            if staged.exists():
                images = json.loads(staged.read_text())["services"]
                # Root-owned file contains only immutable image references.
                # Invalid staging must not prevent ordinary guarded recovery.
                if (
                    isinstance(images, dict)
                    and images
                    and all(
                        name in {"dragonite", "admin", "golbat", "rotom-ng"}
                        and isinstance(options, dict)
                        and set(options) == {"image"}
                        and isinstance(options["image"], str)
                        and re.search(r"@sha256:[0-9a-f]{64}$", options["image"])
                        for name, options in images.items()
                    )
                ):
                    cmd += ["-f", str(staged)]
        except (OSError, ValueError, KeyError, TypeError):
            self._log("Ignoring invalid scanner update staging file", "INFO")
        cmd += [
            "up",
            "-d",
            "--force-recreate",
            # Only these services: compose otherwise also recreates any
            # dependency whose config drifted, and on 2026-09-26 that
            # recreated the database (db) with a new port binding mid-recovery.
            "--no-deps",
            # Images were downloaded beforehand. Recovery never discovers an
            # unreviewed mutable-tag build while bringing the scanner back.
            "--pull",
            "never",
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
