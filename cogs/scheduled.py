import datetime
import time
import traceback
import asyncio
import discord
from discord.ext import commands, tasks

from modules.config import Config
from modules.embeds import build_embed, status_embed
from modules.locale_pt import PT_DAYS_SHORT
from modules.pokemon_name_sync import sync_pokemon_names
from modules import tracking_health
from modules.trade_announcer import TradeAnnouncer
from modules.trade_digest import TradeDigest
from modules.trade_dm import TradeDM

# A step that alone outlasts the 60s tick interval delays every step after it
# and the next tick. Logged as ERROR so the daily error review sees it: the
# 2026-09-23 rename stall ran for 5 hours visible only as discord.http
# warnings on stdout.
_SLOW_STEP_SECONDS = 60


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

    @commands.command(
        name="weeklydigest", brief="Envia resumo semanal de eventos (admin)"
    )
    async def weeklydigestcmd(self, ctx):
        if str(ctx.author.id) not in self.poliswag.ADMIN_USERS_IDS:
            return
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.message.delete()
        await self._send_weekly_digest(channel=ctx.channel)

    @commands.command(
        name="testevent", brief="Simula _check_events em HH:MM hoje (admin)"
    )
    async def testeventcmd(self, ctx, time_arg: str = None):
        if str(ctx.author.id) not in self.poliswag.ADMIN_USERS_IDS:
            return
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

    @tasks.loop(seconds=60)
    async def scheduled_tasks(self):
        for step in (
            self._refresh_masterfile_data,
            self.poliswag.event_manager.fetch_events,
            self._check_version_update,
            self._check_quest_scan_progress,
            self._check_quest_export,
            self._check_events,
            self._check_workers,
            self._update_lure_status,
            self._update_accounts_display,
            self._check_weekly_digest,
            self._check_daily_error_digest,
            self._check_tracking_health,
            self._trade_announcer.tick,
            self._trade_dm.tick,
            self._check_trade_digest,
        ):
            await self._run_tick_step(step)

    @scheduled_tasks.before_loop
    async def before_scheduled_tasks(self):
        await self.poliswag.wait_until_ready()

    async def _check_version_update(self):
        new_version = await self.poliswag.utility.get_new_pokemongo_version()
        if new_version is not None:
            await self.poliswag.CONVIVIO_CHANNEL.send(
                embed=build_embed(
                    "PAAAAAAAAAUUUUUUUUUU!!! FORCE UPDATE!",
                    f"Nova versão: {new_version}",
                )
            )

    async def _check_quest_scan_progress(self):
        day_changed = await self.poliswag.scanner_manager.is_day_change()
        if day_changed:
            self.poliswag.scanner_status.reset_quest_plateau()
            self.poliswag.quest_scanning_message = (
                await self.poliswag.QUEST_CHANNEL.send(
                    embed=build_embed(
                        "SCAN DE QUESTS INICIADO!",
                        "A recolher quests em Leiria e Marinha Grande...",
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

        is_complete = (
            quest_completed["leiriaCompleted"] and quest_completed["marinhaCompleted"]
        )
        if is_complete:
            embed = build_embed(
                "✅ SCAN DE QUESTS CONCLUÍDO!",
                (
                    "**Concluída a verificação de todas as PokéStops nas áreas de Leiria e Marinha Grande. Lista de quests finalizada!**\n\n"
                    f"**Leiria:** {quest_completed['leiriaScanned']}/{quest_completed['leiriaTotal']} Quests\n"
                    f"**Marinha Grande:** {quest_completed['marinhaScanned']}/{quest_completed['marinhaTotal']} Quests\n\n"
                    "📋 **Como consultar:**\n"
                    "`!questleiria <QUEST/ITEM>`\n"
                    "`!questmarinha <QUEST/ITEM>`\n"
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
                quest_completed["leiriaScanned"], quest_completed["marinhaScanned"]
            )
            self._last_progress_embed_state = None
        else:
            state = (
                quest_completed["leiriaScanned"],
                quest_completed["marinhaScanned"],
            )
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
        leiria_filled = int((quest_completed["leiriaPercentage"] / 100) * bar_length)
        marinha_filled = int((quest_completed["marinhaPercentage"] / 100) * bar_length)
        leiria_bar = "█" * leiria_filled + "░" * (bar_length - leiria_filled)
        marinha_bar = "█" * marinha_filled + "░" * (bar_length - marinha_filled)

        total_percentage = (
            quest_completed["leiriaPercentage"] + quest_completed["marinhaPercentage"]
        ) / 2
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
            f"**Leiria:** {quest_completed['leiriaScanned']}/{quest_completed['leiriaTotal']} Quests ({quest_completed['leiriaPercentage']:.1f}%)\n"
            + f"{leiria_bar}\n\n"
            + f"**Marinha:** {quest_completed['marinhaScanned']}/{quest_completed['marinhaTotal']} Quests ({quest_completed['marinhaPercentage']:.1f}%)\n"
            + f"{marinha_bar}",
            footer=f"Última atualização: {datetime.datetime.now().strftime('%H:%M')}",
        )

    async def _check_events(self):
        if not self.poliswag.CONVIVIO_CHANNEL:
            return
        changed = await self.poliswag.event_manager.check_current_events_changes()
        if not changed:
            return
        await self._send_event_change_notifications(
            self.poliswag.CONVIVIO_CHANNEL, changed
        )

    async def _send_event_change_notifications(self, channel, changed):
        if changed["ended"]:
            # A card per event only where there are numbers to show. The
            # rest (GO Battle League, research, a raid rotation) used to get
            # a card each with nothing but a title; they are one line apiece
            # under the header now.
            with_stats, plain = [], []
            for event in changed["ended"]:
                summary = await self.poliswag.event_stats.get_summary(event)
                if summary:
                    with_stats.append((event, summary))
                else:
                    plain.append(event)
            lines = ["**Eventos que terminaram**"]
            for event in plain:
                emoji = self.poliswag.event_manager.get_event_emoji(event["event_type"])
                lines.append(f"{emoji} {event['name']}")
            await channel.send("\n".join(lines)[:2000])
            for event, summary in with_stats:
                embed = await self._build_event_embed(
                    event, is_ended=True, summary=summary
                )
                await channel.send(embed=embed)
        if changed["started"]:
            await channel.send("**Novos eventos**")
            for event in changed["started"]:
                embed = await self._build_event_embed(event)
                await channel.send(embed=embed)

    async def _build_event_embed(self, event, is_ended=False, summary=None):
        event_end = datetime.datetime.strptime(str(event["end"]), "%Y-%m-%d %H:%M:%S")
        emoji = self.poliswag.event_manager.get_event_emoji(event["event_type"])
        event_link = self.poliswag.event_manager.get_event_link(event)
        event_type_key = self.poliswag.event_manager.get_event_type_key(
            event["event_type"]
        )
        color = self.poliswag.event_manager.event_colors.get(event_type_key, 0x3498DB)
        if is_ended:
            description = summary
        else:
            description = self.poliswag.event_manager.format_end_time(event_end)
        embed = discord.Embed(
            title=f"{emoji} {event['name']}",
            url=event_link,
            description=description,
            color=color,
        )
        if event.get("image"):
            embed.set_thumbnail(url=event["image"])
        return embed

    async def _check_workers(self):
        workers_status = await self.poliswag.scanner_status.get_workers_with_issues()
        await self.poliswag.scanner_status.rename_voice_channels(workers_status)
        await self.poliswag.device_manager.alert_if_offline()

    async def _update_lure_status(self):
        """Reflect the live active-lure count in the bot's Discord presence.
        Skips the API call when the count has not changed since last tick."""
        count = await self.poliswag.lure_watcher.count_active_lures()
        if count == self._last_lure_status_count:
            return
        self._last_lure_status_count = count
        await self.poliswag.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name=f"{count} lures active"
            )
        )

    async def _update_accounts_display(self):
        await self.poliswag.account_monitor.update_channel_accounts_stats()

    async def _send_weekly_digest(self, channel=None) -> bool:
        now = datetime.datetime.now()
        today = now.date()
        events = await self.poliswag.event_manager.get_weekly_events()
        if not events:
            return False

        week_end = today + datetime.timedelta(days=6)
        date_range = f"{today.strftime('%d/%m')} – {week_end.strftime('%d/%m/%Y')}"

        ongoing = []
        upcoming_by_day = {}

        for event in events:
            event_type = event["event_type"].lower()
            if "battle" in event_type or "league" in event_type:
                continue

            event_start = datetime.datetime.strptime(
                str(event["start"]), "%Y-%m-%d %H:%M:%S"
            )
            event_end = datetime.datetime.strptime(
                str(event["end"]), "%Y-%m-%d %H:%M:%S"
            )
            emoji = self.poliswag.event_manager.get_event_emoji(event["event_type"])

            if event_start <= now:
                ongoing.append(
                    f"{emoji} **{event['name']}** · `{event_end.strftime('%d/%m %H:%M')}`"
                )
            else:
                day_key = (
                    f"HOJE {event_start.strftime('%d/%m')}"
                    if event_start.date() == today
                    else f"{PT_DAYS_SHORT[event_start.weekday()]} {event_start.strftime('%d/%m')}"
                )
                if day_key not in upcoming_by_day:
                    upcoming_by_day[day_key] = []
                same_day = event_end.date() == event_start.date()
                end_str = (
                    event_end.strftime("%H:%M")
                    if same_day
                    else event_end.strftime("%d/%m %H:%M")
                )
                upcoming_by_day[day_key].append(
                    f"{emoji} **{event['name']}** · `{event_start.strftime('%H:%M')} – {end_str}`"
                )

        lines = []
        if ongoing:
            lines.append("**A DECORRER**")
            lines.extend(ongoing)

        if upcoming_by_day:
            for i, (day, day_events) in enumerate(upcoming_by_day.items()):
                if lines or i > 0:
                    lines.append("")
                lines.append(f"**{day.upper()}**")
                lines.extend(day_events)

        if not lines:
            return False

        embed = build_embed(
            f"Eventos desta Semana  |  {date_range}",
            description="\n".join(lines),
            footer=f"Actualizado a {now.strftime('%d/%m/%Y %H:%M')}",
        )

        target = channel or self.poliswag.CONVIVIO_CHANNEL
        await target.send(embed=embed)
        return True

    async def _check_weekly_digest(self):
        now = datetime.datetime.now()
        today = now.date()
        if today.weekday() != 0:
            return
        if self._last_weekly_digest_monday == today:
            return
        if now.hour < 9:
            return
        self._last_weekly_digest_monday = today
        await self._save_digest_date(today)
        await self._send_weekly_digest()

    async def _check_trade_digest(self):
        """The 09:00 post naming what was added to the lists since yesterday.
        Silent on a quiet day; the module owns the timing and the watermark."""
        await self._trade_digest.tick(datetime.datetime.now())

    async def _check_daily_error_digest(self):
        """Once a day, if anything new landed in error.log, summarize it in
        MOD_CHANNEL. Silent when there is nothing new to report."""
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

        self._last_error_digest_at = now
        await self._save_error_digest_at(now)

        if not entries or not self.poliswag.MOD_CHANNEL:
            return

        preview = entries[:10]
        description = "\n".join(f"• {entry}" for entry in preview)
        if len(entries) > len(preview):
            description += f"\n… e mais {len(entries) - len(preview)}."

        embed = build_embed(
            f"⚠️ {len(entries)} erro(s) novo(s) desde o último resumo",
            description[:4000],
        )
        await self.poliswag.MOD_CHANNEL.send(embed=embed)

    async def _check_tracking_health(self):
        """Tell MY_ID when the site stops recording, and when it starts again.

        The statistics report cannot do this job: it only speaks to whoever
        opens it, and a dead collector looks exactly like a quiet week on the
        page. This is the one analytics fact worth pushing.
        """
        if not Config.MY_ID:
            return

        last_event_at = await self._tracking.last_event_at()
        action, silence = tracking_health.decide(
            last_event_at, self._last_tracking_alert_at
        )
        if action is None:
            return

        now = datetime.datetime.utcnow()
        user = self.poliswag.get_user(Config.MY_ID) or await self.poliswag.fetch_user(
            Config.MY_ID
        )
        try:
            await user.send(tracking_health.message(action, silence))
        except discord.HTTPException:
            # A failed DM must not burn the alert: leaving the state alone
            # means the next tick tries again.
            self.poliswag.utility.log_to_file(
                "Could not DM the tracking-health alert", "ERROR"
            )
            return

        self._last_tracking_alert_at = now if action == "alert" else None
        await self._save_tracking_alert_at(self._last_tracking_alert_at)

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
