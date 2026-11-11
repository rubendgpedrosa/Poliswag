import asyncio
import datetime
import json
import os
import re
import time

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
from modules.scheduler import Step, StepRunner
from modules.shiny_alerts import ShinyAlerts
from modules.shiny_availability import ShinyAvailability
from modules.shiny_odds import ShinyOdds
from modules.trade_announcer import TradeAnnouncer
from modules.trade_digest import TradeDigest
from modules.trade_dm import TradeDM

# Every loop passes once a minute; a step with `every` skips the passes in
# between. The steps are grouped by what they must not wait behind:
#
# - health: is the map scanning, and the ladder that heals it. Nothing slow
#   or third-party goes in front of it.
# - alerts: what members are waiting for, read from our own databases.
# - display: the account card, a render and an upload that have stalled for
#   minutes (19 of the 20 slow steps logged from 2026-09-24 to 2026-11-10).
# - chores: third-party endpoints and the jobs that are due once a day.
#
# Each loop starts this many seconds after the bot is ready, so the four do
# not reach for the database and Discord in the same second.
_PASS_SECONDS = 60
_LOOP_OFFSETS = {"health": 0, "alerts": 15, "display": 30, "chores": 45}
_LOOP_TITLES = {
    "health": "Saúde do mapa",
    "alerts": "Alertas",
    "display": "Painel de contas",
    "chores": "Tarefas",
}


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


def _seconds(value) -> str:
    """'0,4 s', '12 s', '3 min', '2 h'."""
    if value < 10:
        return f"{value:.1f} s".replace(".", ",")
    if value < 120:
        return f"{value:.0f} s"
    if value < 7200:
        return f"{value / 60:.0f} min"
    return f"{value / 3600:.0f} h"


def _step_line(step, now) -> str:
    """One step for `!ticks`: how long its latest run took and when it was."""
    if step.last_started is None:
        state = "ainda não correu"
    elif step.running:
        state = f"a correr há {_seconds(now - step.last_started)}"
    else:
        state = (
            f"{_seconds(step.last_duration or 0)} · "
            f"há {_seconds(now - step.last_started)}"
        )
    icon = "🔴" if step.overdue else "🟠" if step.last_error else "🟢"
    notes = []
    if step.every:
        minutes = round(step.every / 60)
        notes.append(f"de {minutes} em {minutes} min")
    if step.failures:
        notes.append("1 falha" if step.failures == 1 else f"{step.failures} falhas")
    if step.overruns:
        notes.append(f"{step.overruns}× além do tempo")
    if step.longest >= 10:
        notes.append(f"máx. {_seconds(step.longest)}")
    line = f"{icon} `{step.name}` {state}"
    return f"{line} ({', '.join(notes)})" if notes else line


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
        self._runner = StepRunner(
            lambda message, level: poliswag.utility.log_to_file(message, level)
        )
        for name, steps in self._steps().items():
            self._runner.add_loop(name, steps)

    def _steps(self):
        """The steps of each loop, in the order a pass offers them.

        `every` is in seconds, for a step that has no reason to run each
        minute. `budget` is how long the loop waits for a step before going
        on without it (modules/scheduler.py); the default is one pass. Each
        step looks its method up when it runs, so replacing one on the cog or
        on a service takes effect on the next pass.
        """
        poliswag = self.poliswag
        return {
            "health": [
                Step("scan_areas", lambda: self._refresh_scan_areas()),
                # A full recovery recreates the scanner containers (up to
                # 180 s) and then restarts the phone's apps.
                Step("workers", lambda: self._check_workers(), budget=300),
                Step("account_guard", lambda: poliswag.account_guard.tick()),
            ],
            "alerts": [
                # The end of the scan also notifies trackers and exports.
                Step(
                    "quest_scan",
                    lambda: self._check_quest_scan_progress(),
                    budget=120,
                ),
                Step("quest_export", lambda: self._check_quest_export()),
                Step("lure_presence", lambda: self._update_lure_status()),
                Step("trade_announcer", lambda: self._trade_announcer.tick()),
                Step("trade_dm", lambda: self._trade_dm.tick()),
                Step("hundo_alerts", lambda: self._hundo_alerts.tick()),
                # The alerts read the odds and the confirmed variants the two
                # steps before them have just written.
                Step("shiny_odds", lambda: self._shiny_odds.tick()),
                Step("shiny_availability", lambda: self._shiny_availability.tick()),
                Step("shiny_alerts", lambda: self._shiny_alerts.tick()),
            ],
            "display": [
                Step(
                    "account_card",
                    lambda: self._update_accounts_display(),
                    budget=90,
                ),
            ],
            "chores": [
                # The fetch stays right before the check that reads what it
                # stored, and paces itself (a 15 min cache, a retry each pass
                # while ScrapedDuck fails): an interval here as well made it
                # run every 15 or every 20 minutes by a few milliseconds'
                # chance. A new event's post renders one image per event.
                Step("events_fetch", lambda: poliswag.event_manager.fetch_events()),
                Step("events", lambda: self._check_events(), budget=120),
                # A forced update comes with days of notice; once a minute
                # was 1,440 requests a day to Niantic and half the error log.
                Step(
                    "game_version",
                    lambda: self._check_version_update(),
                    every=300,
                ),
                Step(
                    "polisnatch_update",
                    lambda: self._check_polisnatch_update(),
                    every=300,
                ),
                Step("weekly_digest", lambda: self._check_weekly_digest()),
                Step("error_digest", lambda: self._check_daily_error_digest()),
                Step("trade_digest", lambda: self._check_trade_digest()),
                Step(
                    "tracking_health",
                    lambda: self._check_tracking_health(),
                    every=900,
                ),
                # Last: a masterfile reload exports the megas, which fetches
                # from PokeAPI and has taken over a minute.
                Step(
                    "masterfile",
                    lambda: self._refresh_masterfile_data(),
                    every=300,
                    budget=180,
                ),
            ],
        }

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
        for loop in self._task_loops():
            loop.start()

    async def cog_unload(self):
        for loop in self._task_loops():
            loop.cancel()
        self._runner.cancel_running()
        print(f"{self.__class__.__name__} unloaded!")

    def _task_loops(self):
        return (self.health_loop, self.alerts_loop, self.display_loop, self.chores_loop)

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

    @mods_only()
    @commands.command(
        name="ticks", brief="Estado do agendador: passos e duração (admin)"
    )
    async def tickscmd(self, ctx):
        # No docstring: discord.py would show it as the command's help text.
        now = time.time()
        embed = status_embed(
            "Agendador",
            "Duração e idade da última execução de cada passo. "
            "🟠 a última falhou · 🔴 ainda a correr além do tempo previsto.",
        )
        for name, steps in self._runner.loops.items():
            embed.add_field(
                name=_LOOP_TITLES[name],
                value="\n".join(_step_line(step, now) for step in steps)[:1024],
                inline=False,
            )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

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

    async def _refresh_scan_areas(self):
        """Keep the registry and Dragonite fences fresh (every ~10 min)."""
        await scan_areas.ensure(self.poliswag.utility.log_to_file)

    async def _pass(self, name):
        """One pass of a loop, then the heartbeat for the host watchdog."""
        await self._runner.run_pass(name)
        self._write_heartbeat()

    async def _start_after(self, name):
        await self.poliswag.wait_until_ready()
        await asyncio.sleep(_LOOP_OFFSETS[name])

    @tasks.loop(seconds=_PASS_SECONDS)
    async def health_loop(self):
        await self._pass("health")

    @health_loop.before_loop
    async def before_health_loop(self):
        await self._start_after("health")

    @tasks.loop(seconds=_PASS_SECONDS)
    async def alerts_loop(self):
        await self._pass("alerts")

    @alerts_loop.before_loop
    async def before_alerts_loop(self):
        await self._start_after("alerts")

    @tasks.loop(seconds=_PASS_SECONDS)
    async def display_loop(self):
        await self._pass("display")

    @display_loop.before_loop
    async def before_display_loop(self):
        await self._start_after("display")

    @tasks.loop(seconds=_PASS_SECONDS)
    async def chores_loop(self):
        await self._pass("chores")

    @chores_loop.before_loop
    async def before_chores_loop(self):
        await self._start_after("chores")

    def _write_heartbeat(self):
        """Tell the host watchdog (scripts/watchdog.sh) the scheduler is alive.

        The file's time is the oldest sign of life among the loops and any
        step running past its budget (StepRunner.progress_at), so one loop
        that stops, or one step that hangs for good, lets it go stale even
        while the other loops keep passing.
        """
        progress = self._runner.progress_at()
        try:
            with open(Config.HEARTBEAT_FILE, "w") as f:
                f.write(
                    datetime.datetime.fromtimestamp(progress, datetime.timezone.utc)
                    .replace(tzinfo=None)
                    .isoformat()
                )
            os.utime(Config.HEARTBEAT_FILE, (progress, progress))
        except OSError as e:
            self.poliswag.utility.log_to_file(
                f"Could not write heartbeat: {e}", "ERROR"
            )

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

    async def _check_polisnatch_update(self):
        url = Config.POLISNATCH_STATUS_URL
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
        state_file = Config.POLISNATCH_UPDATE_STATE_FILE
        if os.path.isfile(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_polisnatch_update_state(self, state: dict) -> None:
        state_file = Config.POLISNATCH_UPDATE_STATE_FILE
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
