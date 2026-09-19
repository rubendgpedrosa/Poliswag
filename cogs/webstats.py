"""!webstats — the landing site's page views, by DM.

Two ids, two jobs: `MY_ID` is the only one who may run this, and the only one
who receives it. The statistics never touch the invoking channel — the command
is typed in a shared guild channel, where the message is deleted and only
validation or failure notices are ever rendered.
"""

import io
from datetime import datetime, timezone

import discord
from discord.ext import commands

from modules.config import Config
from modules.embeds import build_embed
from modules.logging_mixin import LoggingMixin
from modules.page_view_report import (
    build_dm_embed,
    build_sections,
    render_text_report,
    Period,
)
from modules.page_view_stats import resolve_period, since_for

_USAGE = "Utilização: `!webstats [hoje|7d|30d|Nd|all]`"


class WebStats(commands.Cog, LoggingMixin):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.page_view_stats = poliswag.page_view_stats

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        # Deliberately narrower than every other cog's ADMIN_USERS_IDS gate.
        return str(ctx.author.id) == str(Config.MY_ID)

    async def cog_command_error(self, ctx, error):
        # Silent for a refused caller: a private stats dump should not tell a
        # stranger that it exists. Anything else is logged and acknowledged.
        if isinstance(error, commands.CheckFailure):
            return
        self._log(f"[WEBSTATS] {error}", "ERROR")
        await ctx.send(embed=build_embed("ESTATÍSTICAS", "Correu algo mal."))

    @commands.command(
        name="webstats",
        brief="Estatísticas do site por DM",
        help="Envia por DM as estatísticas de visitas de pogoleiria.pt. "
        "Período opcional: hoje, 7d, 30d, Nd (1-365) ou all. Por omissão, 7d.",
    )
    async def webstats(self, ctx, periodo: str | None = None):
        try:
            days = resolve_period(periodo)
        except ValueError:
            await ctx.send(embed=build_embed("PERÍODO INVÁLIDO", _USAGE))
            return

        if not Config.MY_ID:
            self._log("[WEBSTATS] MY_ID is unset; nowhere to send the report", "ERROR")
            await ctx.send(
                embed=build_embed("ESTATÍSTICAS", "`MY_ID` não está definido.")
            )
            return

        await self._clear_invocation(ctx)

        since = since_for(days)
        try:
            stats = await self.page_view_stats.collect(since)
        except Exception as error:
            self._log(f"[WEBSTATS] collect failed: {error}", "ERROR")
            await ctx.send(
                embed=build_embed("ESTATÍSTICAS", "Não consegui ler a base de dados.")
            )
            return

        report = build_sections(stats, self._period(days, since))
        await self._deliver(report, days)

    async def _clear_invocation(self, ctx):
        """A guild channel keeps no trace; a DM message cannot be deleted anyway."""
        if isinstance(ctx.channel, discord.DMChannel):
            return
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    def _period(self, days, since):
        return Period(
            days=days,
            since=since.date(),
            until=datetime.now(timezone.utc).date(),
        )

    async def _deliver(self, report, days):
        user = self.poliswag.get_user(Config.MY_ID) or await self.poliswag.fetch_user(
            Config.MY_ID
        )
        embed = build_dm_embed(report)
        try:
            if embed is None:
                await user.send(file=self._as_file(report, days))
            else:
                await user.send(embed=embed)
        except discord.Forbidden:
            await self._warn_mods(
                "Não consegui enviar as estatísticas por DM (DMs fechadas?)."
            )
        except discord.HTTPException as error:
            # The embed passed `fits` and Discord still refused it: fall back to
            # the same report as a file rather than losing it.
            self._log(f"[WEBSTATS] embed rejected: {error}", "ERROR")
            try:
                await user.send(file=self._as_file(report, days))
            except discord.HTTPException as retry_error:
                await self._warn_mods(
                    f"Não consegui enviar as estatísticas: {retry_error}"
                )

    def _as_file(self, report, days):
        label = "all" if days is None else f"{days}d"
        stamp = datetime.now(timezone.utc).date()
        return discord.File(
            io.BytesIO(render_text_report(report).encode("utf-8")),
            filename=f"webstats-{label}-{stamp}.txt",
        )

    async def _warn_mods(self, message):
        self._log(f"[WEBSTATS] {message}", "ERROR")
        if self.poliswag.MOD_CHANNEL:
            await self.poliswag.MOD_CHANNEL.send(message)


async def setup(poliswag):
    await poliswag.add_cog(WebStats(poliswag))
