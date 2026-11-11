import asyncio
import datetime
import json
import os
import re
import time
import traceback

import aiohttp
import discord
from discord.ext import commands, tasks

from modules import polisnatch_api, scan_areas, tracking_health
from modules.config import Config
from modules.embeds import build_embed, status_embed
from modules.event_notices import EventNotices
from modules.http_client import get_session
from modules.hundo_alerts import HundoAlerts
from modules.owner_alert import notify_owner
from modules.permissions import mods_only
from modules.pokemon_costume_sync import sync_pokemon_costumes
from modules.pokemon_name_sync import sync_pokemon_names
from modules.shiny_alerts import ShinyAlerts
from modules.shiny_availability import ShinyAvailability
from modules.shiny_odds import ShinyOdds
from modules.trade_announcer import TradeAnnouncer
from modules.trade_digest import TradeDigest
from modules.trade_dm import TradeDM

# A step that alone outlasts the 60s tick interval delays every step after it
# and the next tick. Logged as ERROR so the owner's daily error digest sees it: the
# 2026-09-23 rename stall ran for 5 hours visible only as discord.http
# warnings on stdout.
_SLOW_STEP_SECONDS = 60


# The daily error digest: an error.log entry's first line, split into its
# timestamp and its message. Repeats are grouped on the message with every
# run of digits made equal, so "took 61s" and "took 128s" are one problem.
_ERROR_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - \w+ - (.*)$", re.DOTALL
)
_DIGITS_RE = re.compile(r"\d+")
_DIGEST_MAX_GROUPS = 10
_DIGEST_MESSAGE_LIMIT = 300
_DIGEST_DESCRIPTION_LIMIT = 4000


def _group_error_entries(
    entries,
) -> list[tuple[str, int, datetime.datetime | None, datetime.datetime | None]]:
    """(text, count, first time, last time) per distinct error, in order of
    first occurrence. The text is the group's first message; an entry that
    is not a log line is grouped on its whole text and has no times."""
    groups = {}
    for entry in entries:
        match = _ERROR_LINE_RE.match(entry)
        if match:
            when = datetime.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            text = match.group(2)
            key = ("log", _DIGITS_RE.sub("#", text))
        else:
            when = None
            text = entry
            key = ("raw", _DIGITS_RE.sub("#", text))
        group = groups.get(key)
        if group is None:
            groups[key] = [text, 1, when, when]
            continue
        group[1] += 1
        if when is not None:
            group[2] = when if group[2] is None else min(group[2], when)
            group[3] = when if group[3] is None else max(group[3], when)
    return [tuple(group) for group in groups.values()]


def _digest_line(text, count, first, last, today) -> str:
    """One bullet: '3× message · 08/11 18:45 – 09/11 09:30' or
    'message · 09:00'. Only the time when every time shown is today."""
    if len(text) > _DIGEST_MESSAGE_LIMIT:
        text = text[: _DIGEST_MESSAGE_LIMIT - 1].rstrip() + "…"
    line = discord.utils.escape_markdown(text)
    if count > 1:
        line = f"{count}× {line}"
    times = [t for t in (first, last) if t is not None]
    if count == 1 or first == last:
        times = times[:1]
    if times:
        fmt = "%H:%M" if all(t.date() == today for t in times) else "%d/%m %H:%M"
        line += " · " + " – ".join(t.strftime(fmt) for t in times)
    return f"• {line}"


def _error_digest_description(entries, today) -> str:
    groups = _group_error_entries(entries)
    lines = [_digest_line(*group, today) for group in groups[:_DIGEST_MAX_GROUPS]]
    left_out = len(groups) - _DIGEST_MAX_GROUPS
    if left_out > 0:
        kinds = "tipo" if left_out == 1 else "tipos"
        lines.append(f"… e mais {left_out} {kinds} de erro.")
    return "\n".join(lines)[:_DIGEST_DESCRIPTION_LIMIT]


class Scheduled(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        # Loaded in cog_load (async) instead of here — __init__ can't await.
        self._last_weekly_digest_monday = None
        self._last_progress_embed_state = None
        self._last_quest_export = None
        self._last_error_digest_at = None
        self._last_lure_status_count = None
        self._tracking = tracking_health.TrackingHealth()
        self._last_tracking_alert_at = None
        # False until the first successful write to poliswag.pokemon_name.
        # The masterfile is loaded before this cog exists, so without the
        # flag the table would stay empty until the next 24h reload.
        self._pokemon_names_synced = False
        # As the names: QuestSearch loads the masterfile in __init__, so the
        # first tick never sees a reload, and megas.json waited up to a day
        # after a restart (a fix to the exporter reached the site only then).
        self._megas_exported = False
        self._trade_announcer = TradeAnnouncer(poliswag)
        self._trade_digest = TradeDigest(poliswag)
        self._trade_dm = TradeDM(poliswag)
        self._hundo_alerts = HundoAlerts(poliswag)
        self._shiny_alerts = ShinyAlerts(poliswag)
        self._shiny_odds = ShinyOdds(poliswag)
        self._shiny_availability = ShinyAvailability(poliswag)
        self._event_notices = EventNotices(poliswag)

    async def _load_digest_date(self):
        try:
            rows = await self.poliswag.db.get_data_from_database(
                "SELECT last_weekly_digest_date FROM poliswag"
            )
            if rows and rows[0]["last_weekly_digest_date"]:
                val = rows[0]["last_weekly_digest_date"]
                return (
                    val
                    if isinstance(val, datetime.date)
                    else datetime.date.fromisoformat(str(val))
                )
        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"Failed to load last_weekly_digest_date: {e}"
            )
        return None

    async def _save_digest_date(self, date):
        await self.poliswag.db.execute_query_to_database(
            "UPDATE poliswag SET last_weekly_digest_date = %s",
            params=(str(date),),
        )

    async def _load_error_digest_at(self):
        try:
            rows = await self.poliswag.db.get_data_from_database(
                "SELECT last_error_digest_at FROM poliswag"
            )
            if rows and rows[0]["last_error_digest_at"]:
                val = rows[0]["last_error_digest_at"]
                return (
                    val
                    if isinstance(val, datetime.datetime)
                    else datetime.datetime.fromisoformat(str(val))
                )
        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"Failed to load last_error_digest_at: {e}"
            )
        return None

    async def _save_error_digest_at(self, when):
        await self.poliswag.db.execute_query_to_database(
            "UPDATE poliswag SET last_error_digest_at = %s",
            params=(when.strftime("%Y-%m-%d %H:%M:%S"),),
        )

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")
        self._last_weekly_digest_monday = await self._load_digest_date()
        self._last_error_digest_at = await self._load_error_digest_at()
        self._last_tracking_alert_at = await self._load_tracking_alert_at()
        self.scheduled_tasks.start()

    async def cog_unload(self):
        self.scheduled_tasks.cancel()
        print(f"{self.__class__.__name__} unloaded!")

    @mods_only()
    @commands.command(
        name="weeklydigest", brief="Envia resumo semanal de eventos (admin)"
    )
    async def weeklydigestcmd(self, ctx):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.message.delete()
        await self._event_notices.send_weekly_digest(channel=ctx.channel)

    @mods_only()
    @commands.command(
        name="testevent", brief="Simula _check_events em HH:MM hoje (admin)"
    )
    async def testeventcmd(self, ctx, time_arg: str = None):
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.message.delete()

        at_time = None
        if time_arg:
            try:
                hour, minute = time_arg.split(":")
                at_time = datetime.datetime.combine(
                    datetime.date.today(), datetime.time(int(hour), int(minute))
                )
            except Exception:
                await ctx.channel.send(
                    embed=status_embed(
                        "Formato inválido. Usa `!testevent HH:MM`",
                        color=discord.Color.red(),
                    )
                )
                return

        when_label = at_time.strftime("%H:%M") if at_time else "agora"
        changed = await self.poliswag.event_manager.check_current_events_changes(
            at_time=at_time, dry_run=True
        )

        started_names = [e["name"] for e in changed["started"]] if changed else []
        ended_names = [e["name"] for e in changed["ended"]] if changed else []
        debug_lines = [
            f"**Debug** (simulação @ {when_label}):",
            f"Started ({len(started_names)}): {', '.join(started_names) or 'nenhum'}",
            f"Ended ({len(ended_names)}): {', '.join(ended_names) or 'nenhum'}",
        ]
        await ctx.channel.send(
            embed=discord.Embed(
                description="\n".join(debug_lines), color=Config.EMBED_COLOR
            )
        )

        if not changed:
            return

        await self._event_notices.send_change_notifications(ctx.channel, changed)

    async def _refresh_masterfile_data(self):
        await asyncio.to_thread(self.poliswag.quest_search.load_translation_data)
        masterfile_refreshed = await asyncio.to_thread(
            self.poliswag.quest_search.load_masterfile_data
        )
        if masterfile_refreshed:
            await asyncio.to_thread(
                self.poliswag.quest_search.generate_pokemon_item_name_map
            )
        if masterfile_refreshed or not self._megas_exported:
            await asyncio.to_thread(self.poliswag.mega_exporter.export)
            self._megas_exported = True
        if masterfile_refreshed or not self._pokemon_names_synced:
            await self._sync_pokemon_names()

    async def _sync_pokemon_names(self):
        masterfile = self.poliswag.quest_search.masterfile_data
        if not masterfile:
            return
        await sync_pokemon_names(self.poliswag.db, masterfile)
        await sync_pokemon_costumes(self.poliswag.db, masterfile)
        self._pokemon_names_synced = True

    async def _run_tick_step(self, step):
        """Run one scheduled_tasks step in isolation.

        Each tick's responsibilities are unrelated to one another (version
        checks, quest scanning, lure status, ...) -- a bug or transient
        failure in one must not stop the rest from running, especially
        _check_workers, which feeds StackRecovery's self-healing.
        """
        started = time.monotonic()
        try:
            await step()
        except Exception as e:
            print("CRASH ---", e)
            traceback.print_exc()
            self.poliswag.utility.log_to_file(
                f"{str(e)}\n{traceback.format_exc()}", "CRASH"
            )
        finally:
            elapsed = time.monotonic() - started
            if elapsed >= _SLOW_STEP_SECONDS:
                name = getattr(step, "__qualname__", None) or repr(step)
                self.poliswag.utility.log_to_file(
                    f"Scheduler step {name} took {elapsed:.0f}s "
                    f"(tick interval is 60s); later steps waited on it",
                    "ERROR",
                )

    async def _refresh_scan_areas(self):
        """Keep the registry and Dragonite fences fresh (every ~10 min)."""
        await scan_areas.ensure(self.poliswag.utility.log_to_file)

    @tasks.loop(seconds=60)
    async def scheduled_tasks(self):
        for step in (
            self._refresh_scan_areas,
            self._refresh_masterfile_data,
            self.poliswag.event_manager.fetch_events,
            self._check_version_update,
            self._check_quest_scan_progress,
            self._check_quest_export,
            self._check_events,
            self._check_workers,
            self.poliswag.account_guard.tick,
            self._update_lure_status,
            self._update_accounts_display,
            self._check_weekly_digest,
            self._check_daily_error_digest,
            self._check_tracking_health,
            self._trade_announcer.tick,
            self._trade_dm.tick,
            self._hundo_alerts.tick,
            self._shiny_odds.tick,
            self._shiny_availability.tick,
            self._shiny_alerts.tick,
            self._check_trade_digest,
        ):
            await self._run_tick_step(step)
        self._write_heartbeat()

    def _write_heartbeat(self):
        """Proof for the host watchdog (scripts/watchdog.sh) that ticks still run."""
        try:
            with open(Config.HEARTBEAT_FILE, "w") as f:
                f.write(
                    datetime.datetime.now(datetime.timezone.utc)
                    .replace(tzinfo=None)
                    .isoformat()
                )
        except OSError as e:
            self.poliswag.utility.log_to_file(
                f"Could not write heartbeat: {e}", "ERROR"
            )

    @scheduled_tasks.before_loop
    async def before_scheduled_tasks(self):
        await self.poliswag.wait_until_ready()

    async def _check_version_update(self):
        new_version = await self.poliswag.utility.get_new_pokemongo_version()
        if new_version is not None:
            # The phone keeps scanning on its build until the Play Store
            # updates it (2026-11-02 → 2026-11-09 for 0.435.1): the owner
            # gets the lead time to publish a PoliSnatch release first.
            working = self.poliswag.device_manager.working_game_version()
            await notify_owner(
                self.poliswag,
                f"A Niantic passou a exigir o Pokémon GO **{new_version}**. "
                f"O scanner funciona com {working or 'a versão atual'}. Quando a "
                "Play Store atualizar o telemóvel, o PoliSnatch só volta a "
                "mapear com uma release para esta versão: publica-a antes disso.",
                title="🟠 Nova versão obrigatória do Pokémon GO",
                tag="force-update",
            )
            await self.poliswag.CONVIVIO_CHANNEL.send(
                embed=build_embed(
                    "PAAAAAAAAAUUUUUUUUUU!!! FORCE UPDATE!",
                    f"Nova versão: {new_version}",
                )
            )
        await self._check_polisnatch_update()

    async def _check_polisnatch_update(self):
        url = getattr(
            Config,
            "POLISNATCH_STATUS_URL",
            "https://polisnatch.pogoleiria.pt/v1/game/status",
        )
        # The endpoint takes the jobs submit key and nothing else. Without a key
        # there is nothing to ask: a keyless request only counts as a failed
        # login on the server, and those throttle the jobs API for this host.
        key = polisnatch_api.read_key()
        if not key:
            return
        try:
            session = get_session()
            async with session.get(
                url,
                headers=polisnatch_api.auth_headers(key),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"Polisnatch status poll failed: {e}", "DEBUG"
            )
            return

        # mirror_version is the build that came out; supported_version is the
        # newest one PoliSnatch already runs.
        version = data.get("mirror_version") or data.get("supported_version")
        supported = data.get("supported_version") or "desconhecida"
        lib_changed = bool(data.get("lib_changed", False))
        status = data.get("status", "unknown")
        # A new version needs a profile and a release even when its library is
        # the one already supported, so the server says so explicitly.
        needs_notification = (
            bool(data.get("action_needed"))
            or lib_changed
            or status
            in (
                "staged_on_lab",
                "profile_recovered_pending_test",
                "profile_updated",
            )
        )

        if not needs_notification or not version:
            return

        state = self._load_polisnatch_update_state()
        if (
            state.get("last_notified_version") == version
            and state.get("last_notified_status") == status
        ):
            return

        sha = data.get("lib_sha256") or "desconhecido"
        sha_short = sha[:16] + "…" if len(sha) > 16 else sha
        if lib_changed:
            library = "Alterada (`libNianticLabsPlugin.so`)"
        elif data.get("same_lib_as"):
            library = f"Igual à da `{data['same_lib_as']}`"
        else:
            library = "Sem alteração conhecida"
        msg_detail = data.get("message", "")

        text = (
            f"🔔 **PoliSnatch: Nova Atualização do Pokémon GO detectada (`{version}`)**\n\n"
            f"• **Biblioteca Nativa:** {library}\n"
            f"• **SHA-256:** `{sha_short}`\n"
            f"• **Estado:** `{status}`\n"
            f"• **Versão suportada hoje:** `{supported}`\n"
            f"• **Proteção:** O telemóvel de produção (`.222`) **NÃO** foi tocado.\n\n"
            f"Detalhes: {msg_detail}"
        )

        sent = await notify_owner(
            self.poliswag,
            text,
            title=f"🔔 PoliSnatch: Atualização {version}",
            tag="polisnatch-update",
        )
        if sent:
            state["last_notified_version"] = version
            state["last_notified_status"] = status
            state["last_notified_at"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
            self._save_polisnatch_update_state(state)

    def _load_polisnatch_update_state(self) -> dict:
        state_file = getattr(
            Config, "POLISNATCH_UPDATE_STATE_FILE", "logs/polisnatch-update-state.json"
        )
        if os.path.isfile(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_polisnatch_update_state(self, state: dict) -> None:
        state_file = getattr(
            Config, "POLISNATCH_UPDATE_STATE_FILE", "logs/polisnatch-update-state.json"
        )
        try:
            os.makedirs(os.path.dirname(state_file), exist_ok=True)
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"Failed to save polisnatch update state: {e}", "ERROR"
            )

    async def _check_quest_scan_progress(self):
        await scan_areas.ensure()
        day_changed = await self.poliswag.scanner_manager.is_day_change()
        if day_changed:
            self.poliswag.scanner_status.reset_quest_plateau()
            self.poliswag.quest_scanning_message = (
                await self.poliswag.QUEST_CHANNEL.send(
                    embed=build_embed(
                        "SCAN DE QUESTS INICIADO!",
                        f"A recolher quests em {scan_areas.labels(' e ')}...",
                    )
                )
            )
            return

        if self.poliswag.quest_scanning_message is None:
            self.poliswag.quest_scanning_message = (
                await self.poliswag.utility.find_quest_scanning_message(
                    self.poliswag.QUEST_CHANNEL
                )
            )

        quest_completed = (
            await self.poliswag.scanner_status.is_quest_scanning_complete()
        )
        if quest_completed is None:
            return

        progress = quest_completed["areas"]
        is_complete = quest_completed["completed"]
        if is_complete:
            area_lines = "\n".join(
                f"**{scan_areas.label(key)}:** {data['scanned']}/{data['total']} Quests"
                for key, data in progress.items()
            )
            commands = []
            for key in progress:
                legacy = scan_areas.LEGACY_QUEST_COMMANDS.get(key)
                commands.append(
                    f"`!{legacy} <QUEST/ITEM>`"
                    if legacy
                    else f"`!quest {key} <QUEST/ITEM>`"
                )
            embed = build_embed(
                "✅ SCAN DE QUESTS CONCLUÍDO!",
                (
                    "**Concluída a verificação de todas as PokéStops nas áreas de "
                    f"{scan_areas.labels(' e ')}. Lista de quests finalizada!**\n\n"
                    f"{area_lines}\n\n"
                    "📋 **Como consultar:**\n" + "\n".join(commands) + "\n"
                ),
                footer=f"{datetime.datetime.now().strftime('%d/%m/%Y às %H:%M')}",
            )
            await self.poliswag.quest_search.check_tracked(
                self.poliswag.CONVIVIO_CHANNEL
            )
            await self.poliswag.quest_exporter.export()
            self._last_quest_export = datetime.datetime.now()
            await self.poliswag.scanner_manager.update_quest_scanning_state()
            await self.poliswag.scanner_status.record_quest_scan_completion(
                {key: data["scanned"] for key, data in progress.items()}
            )
            self._last_progress_embed_state = None
        else:
            state = tuple(data["scanned"] for data in progress.values())
            if (
                self._last_progress_embed_state == state
                and self.poliswag.quest_scanning_message is not None
            ):
                return
            self._last_progress_embed_state = state
            embed = self._build_progress_embed(quest_completed)

        if self.poliswag.quest_scanning_message:
            try:
                await self.poliswag.quest_scanning_message.edit(embed=embed)
                return
            except discord.NotFound:
                # Message was deleted out from under us — fall through to
                # resend and re-cache below.
                self.poliswag.quest_scanning_message = None

        if self.poliswag.QUEST_CHANNEL:
            self.poliswag.quest_scanning_message = (
                await self.poliswag.QUEST_CHANNEL.send(embed=embed)
            )

    async def _check_quest_export(self):
        """Periodic safety-net export so quests.json never goes stale even when the
        scan-completion trigger is missed (also runs once on startup). The exporter
        itself skips the write when the quest content is unchanged, so this is cheap
        to run on a timer."""
        now = datetime.datetime.now()
        if (
            self._last_quest_export is not None
            and now - self._last_quest_export < datetime.timedelta(minutes=30)
        ):
            return
        self._last_quest_export = now
        await self.poliswag.quest_exporter.export()

    def _build_progress_embed(self, quest_completed):
        bar_length = 20
        progress = quest_completed["areas"]
        blocks = []
        for key, data in progress.items():
            filled = int((data["percentage"] / 100) * bar_length)
            bar = "█" * filled + "░" * (bar_length - filled)
            blocks.append(
                f"**{scan_areas.label(key)}:** {data['scanned']}/{data['total']} "
                f"Quests ({data['percentage']:.1f}%)\n{bar}"
            )
        total_percentage = sum(data["percentage"] for data in progress.values()) / max(
            len(progress), 1
        )
        if total_percentage < 25:
            status_emoji = "🔍"
        elif total_percentage < 50:
            status_emoji = "⏳"
        elif total_percentage < 75:
            status_emoji = "⌛"
        else:
            status_emoji = "🔜"

        return build_embed(
            f"{status_emoji} SCAN DE QUESTS EM PROGRESSO...",
            "\n\n".join(blocks),
            footer=f"Última atualização: {datetime.datetime.now().strftime('%H:%M')}",
        )

    async def _check_events(self):
        if not self.poliswag.CONVIVIO_CHANNEL:
            return
        changed = await self.poliswag.event_manager.check_current_events_changes()
        if not changed:
            return
        await self._event_notices.send_change_notifications(
            self.poliswag.CONVIVIO_CHANNEL, changed, acknowledge=True
        )

    async def _check_workers(self):
        workers_status = await self.poliswag.scanner_status.get_workers_with_issues()
        await self.poliswag.scanner_status.rename_voice_channels(workers_status)
        await self.poliswag.device_manager.alert_if_offline()
        await self.poliswag.device_manager.ensure_wifi()

    async def _update_lure_status(self):
        """Reflect the live active-lure count in the bot's Discord presence.

        Skips the API call when the count has not changed since last tick.
        The count is cached only after change_presence succeeds, so a
        transport failure retries on the next tick instead of losing a change.
        """
        count = await self.poliswag.lure_watcher.count_active_lures()
        if count == self._last_lure_status_count:
            return
        await self.poliswag.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name=f"{count} lures active"
            )
        )
        self._last_lure_status_count = count

    async def _update_accounts_display(self):
        await self.poliswag.account_monitor.update_channel_accounts_stats()

    async def _check_weekly_digest(self):
        """Send Monday's calendar once; persist the marker only after success.

        A quiet week (no events) or a truly unconfigured channel (id 0) also
        advances the marker so they do not retry every tick. A transport
        failure -- including a configured channel whose lookup still fails --
        leaves the marker unset so the next tick retries; the retry happens
        on the next 60s tick (bounded, no tight loop). Discord acceptance
        and the marker write cannot be atomic: a crash between them may
        duplicate the digest on the next tick.
        """
        now = datetime.datetime.now()
        today = now.date()
        if today.weekday() != 0:
            return
        if self._last_weekly_digest_monday == today:
            return
        if now.hour < 9:
            return
        try:
            await self._event_notices.send_weekly_digest()
        except discord.HTTPException as e:
            self.poliswag.utility.log_to_file(
                f"Weekly digest send failed: {e}", "ERROR"
            )
            return
        self._last_weekly_digest_monday = today
        await self._save_digest_date(today)

    async def _check_trade_digest(self):
        """The 09:00 post naming what was added to the lists since yesterday.
        Silent on a quiet day; the module owns the timing and the watermark."""
        await self._trade_digest.tick(datetime.datetime.now())

    async def _check_daily_error_digest(self):
        """Once a day, if anything new landed in error.log, DM a summary to
        MY_ID (it went to MOD_CHANNEL until 2026-09-26). Silent when there is
        nothing new to report.

        The marker is written only after a successful DM -- or immediately on
        a quiet day / missing recipient, so neither retries every tick. A
        transport failure (including fetch_user) leaves the marker unset so
        the next 60s tick retries (bounded, no tight loop). Discord acceptance
        and the marker write cannot be atomic: a crash between them may
        duplicate the digest on the next tick.
        """
        now = datetime.datetime.now()
        if (
            self._last_error_digest_at
            and self._last_error_digest_at.date() == now.date()
        ):
            return
        if now.hour < 9:
            return

        since = self._last_error_digest_at or (now - datetime.timedelta(days=1))
        entries = self.poliswag.utility.read_new_error_entries(since)

        if not entries or not Config.MY_ID:
            self._last_error_digest_at = now
            await self._save_error_digest_at(now)
            return

        count = len(entries)
        noun = "erro novo" if count == 1 else "erros novos"
        embed = build_embed(
            f"⚠️ {count} {noun} desde o último resumo",
            _error_digest_description(entries, now.date()),
        )
        try:
            user = self.poliswag.get_user(
                Config.MY_ID
            ) or await self.poliswag.fetch_user(Config.MY_ID)
            if user is None:
                self.poliswag.utility.log_to_file(
                    "Could not DM the daily error digest", "ERROR"
                )
                return
            await user.send(
                embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException:
            self.poliswag.utility.log_to_file(
                "Could not DM the daily error digest", "ERROR"
            )
            return

        self._last_error_digest_at = now
        await self._save_error_digest_at(now)

    async def _check_tracking_health(self):
        """Log traffic silence/resumption once per episode, without pushing DMs."""
        last_event_at = await self._tracking.last_event_at()
        action, silence = tracking_health.decide(
            last_event_at, self._last_tracking_alert_at
        )
        if action is None:
            return

        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        when = now if action == "alert" else None
        await self._save_tracking_alert_at(when)
        self._last_tracking_alert_at = when
        self.poliswag.utility.log_to_file(
            tracking_health.message(action, silence), "INFO"
        )

    async def _load_tracking_alert_at(self):
        try:
            rows = await self.poliswag.db.get_data_from_database(
                "SELECT last_tracking_alert_at FROM poliswag"
            )
            if rows and rows[0]["last_tracking_alert_at"]:
                value = rows[0]["last_tracking_alert_at"]
                return (
                    value
                    if isinstance(value, datetime.datetime)
                    else datetime.datetime.fromisoformat(str(value))
                )
        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"Failed to load last_tracking_alert_at: {e}"
            )
        return None

    async def _save_tracking_alert_at(self, when):
        await self.poliswag.db.execute_query_to_database(
            "UPDATE poliswag SET last_tracking_alert_at = %s",
            params=(when.strftime("%Y-%m-%d %H:%M:%S") if when else None,),
        )


async def setup(poliswag):
    await poliswag.add_cog(Scheduled(poliswag))
