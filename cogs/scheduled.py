import datetime
import traceback
import asyncio
import discord
from discord.ext import commands, tasks


class Scheduled(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self._last_weekly_digest_monday = self._load_digest_date()

    def _load_digest_date(self):
        try:
            rows = self.poliswag.db.get_data_from_database(
                "SELECT last_weekly_digest_date FROM poliswag"
            )
            if rows and rows[0]["last_weekly_digest_date"]:
                val = rows[0]["last_weekly_digest_date"]
                return (
                    val
                    if isinstance(val, datetime.date)
                    else datetime.date.fromisoformat(str(val))
                )
        except Exception:
            pass
        return None

    def _save_digest_date(self, date):
        self.poliswag.db.execute_query_to_database(
            "UPDATE poliswag SET last_weekly_digest_date = %s",
            params=(str(date),),
        )

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")
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
                await ctx.channel.send("Formato inválido. Usa `!testevent HH:MM`")
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
        await ctx.channel.send(content="\n".join(debug_lines))

        if not changed:
            return

        await self._send_event_change_notifications(ctx.channel, changed)

    @tasks.loop(seconds=60)
    async def scheduled_tasks(self):
        try:
            await asyncio.to_thread(self.poliswag.quest_search.load_translation_data)
            masterfile_refreshed = await asyncio.to_thread(
                self.poliswag.quest_search.load_masterfile_data
            )
            if masterfile_refreshed:
                await asyncio.to_thread(
                    self.poliswag.quest_search.generate_pokemon_item_name_map
                )
            await self.poliswag.event_manager.fetch_events()

            await self._check_version_update()
            await self._check_quest_scan_progress()
            await self._check_events()
            await self._check_workers()
            await self._update_accounts_display()
            await self._check_weekly_digest()
        except Exception as e:
            print("CRASH ---", e)
            traceback.print_exc()
            self.poliswag.utility.log_to_file(
                f"{str(e)}\n{traceback.format_exc()}", "CRASH"
            )

    @scheduled_tasks.before_loop
    async def before_scheduled_tasks(self):
        await self.poliswag.wait_until_ready()

    async def _check_version_update(self):
        new_version = await self.poliswag.utility.get_new_pokemongo_version()
        if new_version is not None:
            await self.poliswag.CONVIVIO_CHANNEL.send(
                embed=self.poliswag.utility.build_embed_object_title_description(
                    "PAAAAAAAAAUUUUUUUUUU!!! FORCE UPDATE!",
                    f"Nova versão: {new_version}",
                )
            )

    async def _check_quest_scan_progress(self):
        day_changed = self.poliswag.scanner_manager.is_day_change()
        if day_changed:
            self.poliswag.quest_scanning_message = (
                await self.poliswag.QUEST_CHANNEL.send(
                    embed=self.poliswag.utility.build_embed_object_title_description(
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

        if quest_completed["leiriaCompleted"] and quest_completed["marinhaCompleted"]:
            embed = self.poliswag.utility.build_embed_object_title_description(
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
            self.poliswag.scanner_manager.update_quest_scanning_state()
        else:
            embed = self._build_progress_embed(quest_completed)

        if self.poliswag.quest_scanning_message:
            await self.poliswag.quest_scanning_message.edit(embed=embed)
        elif self.poliswag.QUEST_CHANNEL:
            self.poliswag.quest_scanning_message = (
                await self.poliswag.QUEST_CHANNEL.send(embed=embed)
            )

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

        return self.poliswag.utility.build_embed_object_title_description(
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
            await channel.send(content="**EVENTOS QUE TERMINARAM**")
            for event in changed["ended"]:
                await channel.send(embed=self._build_event_embed(event, is_ended=True))
        if changed["started"]:
            await channel.send(content="**EVENTOS A COMEÇAR**")
            for event in changed["started"]:
                await channel.send(embed=self._build_event_embed(event))

    def _build_event_embed(self, event, is_ended=False):
        event_end = datetime.datetime.strptime(str(event["end"]), "%Y-%m-%d %H:%M:%S")
        emoji = self.poliswag.event_manager.get_event_emoji(event["event_type"], None)
        event_link = self.poliswag.event_manager.get_event_link(event)
        event_type_key = self.poliswag.event_manager.get_event_type_key(
            event["event_type"]
        )
        color = self.poliswag.event_manager.event_colors.get(event_type_key, 0x3498DB)
        verb = "Terminou" if is_ended else "Termina"
        embed = discord.Embed(
            title=f"{emoji} {event['name']}",
            url=event_link,
            description=self.poliswag.event_manager.format_end_time(
                event_end, verb=verb
            ),
            color=color,
        )
        if event.get("image"):
            embed.set_image(url=event["image"])
        return embed

    async def _check_workers(self):
        workers_status = await self.poliswag.scanner_status.get_workers_with_issues()
        await self.poliswag.scanner_status.rename_voice_channels(
            workers_status["downDevicesLeiria"],
            workers_status["downDevicesMarinha"],
        )

    async def _update_accounts_display(self):
        await self.poliswag.account_monitor.update_channel_accounts_stats()

    async def _send_weekly_digest(self, channel=None) -> bool:
        PT_DAYS = {0: "Seg", 1: "Ter", 2: "Qua", 3: "Qui", 4: "Sex", 5: "Sáb", 6: "Dom"}
        now = datetime.datetime.now()
        today = now.date()
        events = self.poliswag.event_manager.get_weekly_events()
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
            emoji = self.poliswag.event_manager.get_event_emoji(
                event["event_type"], None
            )

            if event_start <= now:
                ongoing.append(
                    f"{emoji} **{event['name']}** · `{event_end.strftime('%d/%m %H:%M')}`"
                )
            else:
                day_key = (
                    f"HOJE {event_start.strftime('%d/%m')}"
                    if event_start.date() == today
                    else f"{PT_DAYS[event_start.weekday()]} {event_start.strftime('%d/%m')}"
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

        embed = self.poliswag.utility.build_embed_object_title_description(
            f"Eventos desta Semana  |  {date_range}",
            description="\n".join(lines),
            footer=f"Actualizado a {now.strftime('%d/%m/%Y %H:%M')}",
        )

        target = channel or self.poliswag.CONVIVIO_CHANNEL
        await target.send(embed=embed)
        return True

    async def _check_weekly_digest(self):
        today = datetime.datetime.now().date()
        if today.weekday() != 0:
            return
        if self._last_weekly_digest_monday == today:
            return
        self._last_weekly_digest_monday = today
        self._save_digest_date(today)
        await self._send_weekly_digest()


async def setup(poliswag):
    await poliswag.add_cog(Scheduled(poliswag))
