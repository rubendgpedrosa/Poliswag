"""!stats — a snapshot in Discord, and the link to the live report.

Two ids, two jobs: `MY_ID` is the only one who may run this, and the only one
who receives it. Nothing is ever rendered into the invoking channel; the
message is deleted on sight and only validation or failure notices appear
there.

The report itself lives at pogoleiria.pt/webstats/<token> and queries the
database on load, so the link is permanent and always current. This cog used
to render the whole report as HTML and publish a snapshot per invocation,
expiring after a day. What is left here is the part Discord is actually good
at: a few numbers at a glance, and a way in.
"""

import io
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from modules.config import Config
from modules.embeds import build_embed
from modules.logging_mixin import LoggingMixin
from modules.page_view_report import build_snapshot_embed, render_export
from modules.page_view_stats import resolve_period, since_for
from modules.trade_stats import TradeStats
from modules.webstats_access import WebStatsAccess

_USAGE = (
    "Utilização: `!stats` para o resumo e o link · "
    "`!stats export [7d|30d|Nd|all]` para os agregados em JSON · "
    "`!stats novocodigo` para trocar o link."
)
# Discord refuses larger attachments, and an export past this is a sign the
# period is wrong rather than something worth splitting up.
_MAX_EXPORT_BYTES = 7_000_000


class WebStats(commands.Cog, LoggingMixin):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.page_view_stats = poliswag.page_view_stats
        self.access = WebStatsAccess()
        self.trade_stats = getattr(poliswag, "trade_stats", None)

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        # Deliberately narrower than every other cog's ADMIN_USERS_IDS gate.
        return str(ctx.author.id) == str(Config.MY_ID)

    async def cog_command_error(self, ctx, error):
        # Silent for a refused caller: a private report should not tell a
        # stranger that it exists. Anything else is logged and acknowledged.
        if isinstance(error, commands.CheckFailure):
            return
        if isinstance(
            error, (commands.CommandOnCooldown, commands.MaxConcurrencyReached)
        ):
            await ctx.send(
                embed=build_embed(
                    "ESTATÍSTICAS",
                    "Já estou a tratar disso. Tenta outra vez daqui a uns segundos.",
                )
            )
            return
        self._log(f"[WEBSTATS] command failed: {type(error).__name__}", "ERROR")
        await ctx.send(embed=build_embed("ESTATÍSTICAS", "Correu algo mal."))

    @commands.command(
        name="stats",
        aliases=("webstats",),
        brief="Resumo do site e link para o relatório",
        help="Envia por DM as últimas 24 horas e o link permanente para o "
        "relatório completo, onde podes trocar o período. "
        "`!stats novocodigo` emite um link novo e desliga o anterior.",
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.max_concurrency(1, per=commands.BucketType.user, wait=False)
    async def webstats(
        self, ctx, argumento: str | None = None, periodo: str | None = None
    ):
        argument = (argumento or "").strip().lower()
        if argument and argument not in {"novocodigo", "novolink", "export"}:
            await ctx.send(embed=build_embed("ESTATÍSTICAS", _USAGE))
            return
        if periodo is not None and argument != "export":
            await ctx.send(embed=build_embed("ESTATÍSTICAS", _USAGE))
            return

        if not Config.MY_ID:
            self._log("[WEBSTATS] MY_ID is unset; nowhere to send the report", "ERROR")
            await ctx.send(
                embed=build_embed("ESTATÍSTICAS", "`MY_ID` não está definido.")
            )
            return

        await self._clear_invocation(ctx)

        if argument == "export":
            await self._export(ctx, periodo)
            return

        rotating = bool(argument)
        try:
            url = (
                await self.access.rotate()
                if rotating
                else await self.access.current_or_issue()
            )
        except Exception as error:
            self._log(f"[WEBSTATS] access failed: {type(error).__name__}", "ERROR")
            await ctx.send(
                embed=build_embed("ESTATÍSTICAS", "Não consegui emitir o link.")
            )
            return

        stats, trade_stats = await self._snapshot()
        if stats is None:
            await ctx.send(
                embed=build_embed("ESTATÍSTICAS", "Não consegui ler a base de dados.")
            )
            return

        await self._deliver(stats, trade_stats, url, rotating)

    async def _export(self, ctx, periodo):
        """Aggregate rows as JSON, by DM. No events, no visitor or document ids.

        The report answers questions; this is for the ones it does not, and
        for keeping a copy of a period before retention trims the raw rows.
        """
        try:
            days = resolve_period(periodo)
        except ValueError:
            await ctx.send(embed=build_embed("PERÍODO INVÁLIDO", _USAGE))
            return

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            stats = await self.page_view_stats.collect(
                since_for(days, now=datetime.now(timezone.utc)),
                until=now,
                detail="export",
            )
        except Exception as error:
            self._log(
                f"[WEBSTATS] export collect failed: {type(error).__name__}", "ERROR"
            )
            await ctx.send(
                embed=build_embed("ESTATÍSTICAS", "Não consegui ler a base de dados.")
            )
            return

        body = render_export(stats).encode("utf-8")
        label = "all" if days is None else f"{days}d"
        try:
            user = self.poliswag.get_user(
                Config.MY_ID
            ) or await self.poliswag.fetch_user(Config.MY_ID)
            if len(body) > _MAX_EXPORT_BYTES:
                await user.send(
                    f"A exportação de {label} ocupa {len(body) // 1_000_000} MB "
                    "e não cabe no Discord. Escolhe um período mais curto."
                )
                return
            await user.send(
                file=discord.File(
                    io.BytesIO(body),
                    filename=f"webstats-{label}-{now.date()}.json",
                )
            )
        except discord.Forbidden:
            await self._warn_mods(
                "Não consegui enviar a exportação por DM (DMs fechadas?)."
            )
        except discord.HTTPException:
            await self._warn_mods("Não consegui enviar a exportação por DM.")

    async def _snapshot(self):
        """The last 24 hours, for the embed. The report covers the rest."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        since = now - timedelta(hours=24)
        try:
            stats = await self.page_view_stats.collect(
                since, until=now, detail="summary"
            )
        except Exception as error:
            self._log(f"[WEBSTATS] collect failed: {type(error).__name__}", "ERROR")
            return None, None

        trade_stats = None
        if isinstance(self.trade_stats, TradeStats):
            try:
                trade_stats = await self.trade_stats.collect(since, until=now)
            except Exception as error:
                # A missing trades figure costs one line of the embed; it must
                # not cost the whole snapshot.
                self._log(
                    f"[WEBSTATS] trade stats failed: {type(error).__name__}", "ERROR"
                )
        return stats, trade_stats

    async def _clear_invocation(self, ctx):
        """A guild channel keeps no trace; a DM message cannot be deleted anyway."""
        if isinstance(ctx.channel, discord.DMChannel):
            return
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    async def _deliver(self, stats, trade_stats, url, rotating):
        try:
            user = self.poliswag.get_user(
                Config.MY_ID
            ) or await self.poliswag.fetch_user(Config.MY_ID)
            await user.send(embed=build_snapshot_embed(stats, trade_stats, url))
            if url and rotating:
                # The card above already carries the link. What it cannot say
                # is that every link before it just stopped working.
                await user.send(
                    "Link novo — o anterior deixou de funcionar. "
                    "Está no cartão acima; guarda-o, não consigo reenviá-lo."
                )
            elif url:
                await user.send(
                    "Guarda o link do cartão acima: só guardo o hash, "
                    "por isso não consigo reenviá-lo depois."
                )
            else:
                # Only the hash is stored, so an existing link cannot be read
                # back out. Saying so beats quietly sending no link at all.
                await user.send(
                    "O link continua o que já tens aqui em cima. "
                    "Se o perdeste, `!stats novocodigo` emite outro."
                )
        except discord.Forbidden:
            await self._warn_mods(
                "Não consegui enviar as estatísticas por DM (DMs fechadas?)."
            )
        except discord.HTTPException:
            await self._warn_mods("Não consegui enviar as estatísticas por DM.")

    async def _warn_mods(self, message):
        self._log(f"[WEBSTATS] {message}", "ERROR")
        if self.poliswag.MOD_CHANNEL:
            await self.poliswag.MOD_CHANNEL.send(message)


async def setup(poliswag):
    await poliswag.add_cog(WebStats(poliswag))
