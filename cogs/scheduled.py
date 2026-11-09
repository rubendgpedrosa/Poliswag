import asyncio
import dataclasses
import datetime
import io
import json
import time
import traceback

import discord
from discord.ext import commands, tasks

from modules import scan_areas, tracking_health
from modules.config import Config
from modules.embeds import build_embed, status_embed
from modules.event_images import usable_image
from modules.hundo_alerts import HundoAlerts
from modules.locale_pt import PT_DAYS_SHORT
from modules.owner_alert import notify_owner
from modules.permissions import mods_only
from modules.pokemon_costume_sync import sync_pokemon_costumes
from modules.pokemon_name_sync import sync_pokemon_names
from modules.shiny_alerts import ShinyAlerts
from modules.shiny_availability import ShinyAvailability
from modules.shiny_odds import ShinyOdds
from modules.stat_card import StatCard
from modules.trade_announcer import TradeAnnouncer
from modules.trade_digest import TradeDigest
from modules.trade_dm import TradeDM
from modules.week_board import WeekEvent

# A step that alone outlasts the 60s tick interval delays every step after it
# and the next tick. Logged as ERROR so the owner's daily error digest sees it: the
# 2026-09-23 rename stall ran for 5 hours visible only as discord.http
# warnings on stdout.
_SLOW_STEP_SECONDS = 60


def _event_time(event, key) -> datetime.datetime:
    """Event rows hold Lisbon wall time as `%Y-%m-%d %H:%M:%S`."""
    return datetime.datetime.strptime(str(event[key]), "%Y-%m-%d %H:%M:%S")


def _extra(event):
    try:
        data = json.loads(event.get("extra_data") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _event_extras(event):
    """(Pokémon, bonuses) for a new event's panel, from ScrapedDuck's
    extraData: raid bosses, Community Day spawns and bonuses, the Spotlight
    Hour Pokémon and its bonus. Pokémon are (name, icon URL, can be shiny)."""
    extra = _extra(event).get("extraData") or {}
    pokemon, bonuses = [], []
    raids = extra.get("raidbattles") or {}
    for boss in raids.get("bosses") or []:
        pokemon.append(
            (boss.get("name"), boss.get("image"), bool(boss.get("canBeShiny")))
        )
    cd = extra.get("communityday") or {}
    shinies = {mon.get("name") for mon in cd.get("shinies") or []}
    for mon in cd.get("spawns") or []:
        pokemon.append((mon.get("name"), mon.get("image"), mon.get("name") in shinies))
    bonuses += [bonus.get("text") for bonus in cd.get("bonuses") or []]
    spotlight = extra.get("spotlight") or {}
    for mon in spotlight.get("list") or []:
        pokemon.append((mon.get("name"), mon.get("image"), bool(mon.get("canBeShiny"))))
    if spotlight.get("bonus"):
        bonuses.append(spotlight["bonus"])
    return (
        tuple((name, image or "", shiny) for name, image, shiny in pokemon if name),
        tuple(bonus for bonus in bonuses if bonus),
    )


def _duration(span):
    """'1 h', '3 h 30 min', '5 dias'."""
    minutes = round(span.total_seconds() / 60)
    if minutes >= 24 * 60:
        days = round(minutes / (24 * 60))
        return f"{days} dia" if days == 1 else f"{days} dias"
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} min"
    return f"{hours} h {rest} min" if rest else f"{hours} h"


# The weekly query has no ScrapedDuck heading; these read like one.
_TYPE_LABELS = {
    "pokemon-spotlight-hour": "Spotlight Hour",
    "pokemon-go-tour": "Pokémon GO Tour",
    "go-pass": "GO Pass",
    "event": "Evento",
    "season": "Temporada",
    "research": "Pesquisa",
}


def _type_label(event_type):
    """'raid-hour' -> 'Raid Hour'; a few read better spelled out."""
    key = (event_type or "").lower()
    if key in _TYPE_LABELS:
        return _TYPE_LABELS[key]
    return key.replace("-", " ").title().replace("Go ", "GO ") or "Evento"


def _event_heading(event):
    """ScrapedDuck's own label for the event ("Raid Battles", "Wild Area"),
    for the kicker of a panel without stats; "Evento" without one."""
    return _extra(event).get("heading") or "Evento"


# Discord caps an embed description at 4096 chars and a message's embeds at
# 6000 chars total. The weekly calendar is chunked at line boundaries so no
# entry is dropped or truncated.
_WEEKLY_DESCRIPTION_LIMIT = 4096


def _split_weekly_description(text, limit=_WEEKLY_DESCRIPTION_LIMIT):
    """Split a long digest at line boundaries, preserving every entry."""
    lines = text.split("\n")
    chunks = []
    current = []
    current_len = 0
    for line in lines:
        if len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            for i in range(0, len(line), limit):
                chunks.append(line[i : i + limit])
            continue
        extra = len(line) + (1 if current else 0)
        if current and current_len + extra > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += extra
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


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
        await self._send_weekly_digest(channel=ctx.channel)

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

        await self._send_event_change_notifications(ctx.channel, changed)

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
        await self._send_event_change_notifications(
            self.poliswag.CONVIVIO_CHANNEL, changed, acknowledge=True
        )

    async def _send_event_change_notifications(
        self, channel, changed, acknowledge=False
    ):
        """Acknowledge each successful message, leaving failed batches retryable.

        Manual previews use acknowledge=False. Discord acceptance and the DB
        update cannot be atomic: a crash between them may still cause a repeat.
        """
        if changed["ended"]:
            panels, summaries, groups = [], [], []
            for event in changed["ended"]:
                summary = await self.poliswag.event_stats.get_summary(event)
                if summary and summary in summaries:
                    # "Super Mega Raid Day" and "Staraptor Super Mega Raid
                    # Day" are one event under two names: one panel.
                    groups[summaries.index(summary)].append(event)
                    continue
                summaries.append(summary)
                groups.append([event])
                panels.append(await self._ended_panel(event, summary))

            async def ended_cards():
                return [
                    await self._build_event_embed(
                        group[0], is_ended=True, summary=summary
                    )
                    for group, summary in zip(groups, summaries)
                ]

            await self._send_boards(
                channel,
                "Eventos que terminaram",
                panels,
                groups,
                ended_cards,
                is_end=True,
                acknowledge=acknowledge,
            )
        if changed["started"]:
            started = changed["started"]

            async def started_cards():
                return [await self._build_event_embed(event) for event in started]

            await self._send_boards(
                channel,
                "Novos eventos",
                [await self._started_panel(event) for event in started],
                [[event] for event in started],
                started_cards,
                is_end=False,
                acknowledge=acknowledge,
            )

    async def _ack_events(self, events, is_end):
        for event in events:
            date = datetime.datetime.fromisoformat(
                str(event["end" if is_end else "start"])
            )
            await self.poliswag.event_manager.mark_event_notified(
                event, date, is_end=is_end
            )

    async def _send_event_batches(
        self, channel, header, embeds, groups, plain, *, is_end, acknowledge
    ):
        offset = 0
        first = True
        while first or offset < len(embeds):
            end, size = offset, 0
            while end < len(embeds) and end - offset < 10:
                length = len(embeds[end])
                if length > 6000:
                    raise ValueError("Event embed exceeds Discord's message limit")
                if size + length > 6000:
                    break
                size += length
                end += 1
            batch = embeds[offset:end]
            if first:
                await channel.send(
                    header,
                    embeds=batch,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await channel.send(
                    embeds=batch, allowed_mentions=discord.AllowedMentions.none()
                )
            if acknowledge:
                events = [event for group in groups[offset:end] for event in group]
                await self._ack_events((plain if first else []) + events, is_end=is_end)
            first = False
            offset = end

    async def _ended_panel(self, event, summary):
        """The event's panel on the "Eventos que terminaram" image: its game
        image and name, then its stats, or when it ended if it has none."""
        title = event["name"]
        image = await usable_image(event.get("image")) or ""
        if summary and summary.card:
            return dataclasses.replace(summary.card, title=title, image=image)
        event_end = _event_time(event, "end")
        return StatCard(
            kicker=_event_heading(event),
            hero=None,
            title=title,
            image=image,
            ended=self.poliswag.event_manager.format_end_time(
                event_end, verb="Terminou"
            ),
        )

    async def _started_panel(self, event):
        """A new event's panel: its game image and name, until when it runs
        and for how long, then its Pokémon and bonuses where ScrapedDuck
        lists them."""
        end = _event_time(event, "end")
        start = None
        if event.get("start"):
            start = _event_time(event, "start")
        if start and start.date() == end.date():
            chips = [("Horário", f"{start:%H:%M} – {end:%H:%M}")]
        else:
            day = PT_DAYS_SHORT[end.weekday()]
            chips = [("Até", f"{day} {end:%d/%m} · {end:%H:%M}")]
        if start:
            chips.append(("Duração", _duration(end - start)))
        pokemon, bonuses = _event_extras(event)
        return StatCard(
            kicker=_event_heading(event),
            hero=None,
            title=event["name"],
            image=await usable_image(event.get("image")) or "",
            chips=tuple(chips),
            pokemon=pokemon,
            bonuses=bonuses,
        )

    async def _send_boards(
        self, channel, heading, panels, groups, text_cards, *, is_end, acknowledge
    ):
        """One image per event, each in its own message as the embeds were,
        so Discord shows every one full width; the first message carries the
        post's title. If any image doesn't render, the whole post goes out as
        the text cards (`text_cards()`) instead, so an event is never dropped
        for a renderer fault."""
        pngs = [
            await self.poliswag.image_generator.generate_stat_board([panel], "")
            for panel in panels
        ]
        if pngs and all(pngs):
            kind = "evento-terminado" if is_end else "novo-evento"
            for index, (png, group) in enumerate(zip(pngs, groups)):
                await channel.send(
                    f"**{heading}**" if index == 0 else None,
                    file=discord.File(
                        io.BytesIO(png), filename=f"{kind}-{index + 1}.png"
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                if acknowledge:
                    await self._ack_events(group, is_end=is_end)
            return
        await self._send_event_batches(
            channel,
            f"**{heading}**",
            await text_cards(),
            groups,
            [],
            is_end=is_end,
            acknowledge=acknowledge,
        )

    async def _build_event_embed(self, event, is_ended=False, summary=None):
        event_end = _event_time(event, "end")
        emoji = self.poliswag.event_manager.get_event_emoji(event["event_type"])
        event_link = self.poliswag.event_manager.get_event_link(event)
        event_type_key = self.poliswag.event_manager.get_event_type_key(
            event["event_type"]
        )
        color = self.poliswag.event_manager.event_colors.get(event_type_key, 0x3498DB)
        if is_ended:
            description = (
                summary.headline
                if summary
                else self.poliswag.event_manager.format_end_time(
                    event_end, verb="Terminou"
                )
            )
        else:
            description = self.poliswag.event_manager.format_end_time(event_end)
        embed = discord.Embed(
            title=f"{emoji} {event['name']}"[:256],
            url=event_link,
            description=description,
            color=color,
        )
        if is_ended and summary:
            # One column per area, side by side; the caveat as the footer.
            for name, value in summary.areas:
                embed.add_field(name=name, value=value, inline=True)
            embed.set_footer(text=summary.note)
        image = await usable_image(event.get("image"))
        if image:
            embed.set_thumbnail(url=image)
        return embed

    async def _check_workers(self):
        workers_status = await self.poliswag.scanner_status.get_workers_with_issues()
        await self.poliswag.scanner_status.rename_voice_channels(workers_status)
        await self.poliswag.device_manager.alert_if_offline()

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

    async def _send_weekly_digest(self, channel=None) -> bool:
        """Post the weekly calendar; split across messages within Discord limits.

        Returns True when at least one message was sent, False on a quiet
        week (no events after filtering) or when the channel is truly
        unconfigured (id 0). A quiet week or unconfigured channel still
        counts as done for _check_weekly_digest's dedup marker, so neither
        retries every tick. A configured id whose object is missing (startup
        lookup failed) is recovered via fetch_channel; a lookup failure
        raises so _check_weekly_digest leaves the marker unset for retry.

        Discord caps an embed description at 4096 chars and a message's
        embeds at 6000 chars total. The calendar is chunked at line
        boundaries so no entry is dropped or truncated; mentions stay off on
        every send. Discord acceptance and the DB marker cannot be atomic: a
        crash between two batches, or between the last send and the marker
        write in _check_weekly_digest, may resend an earlier batch on retry
        (partial-batch duplicates).
        """
        now = datetime.datetime.now()
        today = now.date()
        events = await self.poliswag.event_manager.get_weekly_events()
        if not events:
            return False

        week_end = today + datetime.timedelta(days=6)
        date_range = f"{today.strftime('%d/%m')} a {week_end.strftime('%d/%m')}"

        def when(moment):
            # The year only when it isn't this one: "até 11/04" read as next
            # month for an event that ends next April.
            fmt = "%d/%m" if moment.year == today.year else "%d/%m/%Y"
            return moment.strftime(fmt)

        ongoing = []
        upcoming_by_day = {}
        week_events = []

        for event in events:
            event_type = event["event_type"].lower()
            if "battle" in event_type or "league" in event_type:
                continue
            week_events.append(event)

            event_start = _event_time(event, "start")
            event_end = _event_time(event, "end")
            emoji = self.poliswag.event_manager.get_event_emoji(event["event_type"])

            if event_start <= now:
                ongoing.append(
                    (event_end, f"{emoji} **{event['name']}** · até {when(event_end)}")
                )
            else:
                day_key = (
                    f"Hoje {event_start.strftime('%d/%m')}"
                    if event_start.date() == today
                    else f"{PT_DAYS_SHORT[event_start.weekday()]} {event_start.strftime('%d/%m')}"
                )
                if day_key not in upcoming_by_day:
                    upcoming_by_day[day_key] = []
                end_str = (
                    event_end.strftime("%H:%M")
                    if event_end.date() == event_start.date()
                    else f"{when(event_end)} {event_end.strftime('%H:%M')}"
                )
                upcoming_by_day[day_key].append(
                    f"{emoji} **{event['name']}** · {event_start.strftime('%H:%M')}–{end_str}"
                )

        blocks = []
        if ongoing:
            # Soonest to end first: that's the one to hurry for.
            ongoing.sort(key=lambda item: item[0])
            blocks.append(
                "\n".join(["**A decorrer**", *(line for _end, line in ongoing)])
            )
        for day, day_events in upcoming_by_day.items():
            blocks.append("\n".join([f"**{day}**", *day_events]))

        if not blocks:
            return False

        target = channel or self.poliswag.CONVIVIO_CHANNEL
        if target is None:
            if channel is None and Config.CONVIVIO_CHANNEL_ID:
                # Configured id but the object is missing (startup lookup
                # failed): recover here instead of losing the delivery.
                try:
                    target = await self.poliswag.fetch_channel(
                        Config.CONVIVIO_CHANNEL_ID
                    )
                except (
                    discord.Forbidden,
                    discord.NotFound,
                    discord.HTTPException,
                ) as e:
                    self.poliswag.utility.log_to_file(
                        f"Weekly digest: CONVIVIO lookup failed: {e}", "ERROR"
                    )
                    raise
                self.poliswag.CONVIVIO_CHANNEL = target
            if target is None:
                self.poliswag.utility.log_to_file(
                    "Weekly digest: no channel configured", "ERROR"
                )
                return False

        board = await self.poliswag.image_generator.generate_week_board(
            [
                WeekEvent(
                    title=event["name"],
                    kicker=_type_label(event["event_type"]),
                    image=event.get("image") or "",
                    start=_event_time(event, "start"),
                    end=_event_time(event, "end"),
                )
                for event in week_events
            ],
            now,
        )
        if board:
            await target.send(
                file=discord.File(io.BytesIO(board), filename="eventos-da-semana.png"),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True

        # The image didn't render: the text calendar, as before.
        title = f"Eventos da semana · {date_range}"
        footer = f"Atualizado a {now.strftime('%d/%m/%Y %H:%M')}"
        chunks = _split_weekly_description("\n\n".join(blocks))
        for index, chunk in enumerate(chunks):
            embed = build_embed(
                title if index == 0 else f"{title} (cont.)",
                description=chunk,
                footer=footer if index == len(chunks) - 1 else None,
            )
            await target.send(
                embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
        return True

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
            await self._send_weekly_digest()
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

        preview = entries[:10]
        description = "\n".join(f"• {entry}" for entry in preview)
        if len(entries) > len(preview):
            description += f"\n… e mais {len(entries) - len(preview)}."

        embed = build_embed(
            f"⚠️ {len(entries)} erro(s) novo(s) desde o último resumo",
            description[:4000],
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
