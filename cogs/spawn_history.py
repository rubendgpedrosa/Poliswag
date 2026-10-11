"""Owner access to the independent 48-hour raw spawn recorder."""

import asyncio
import io
import json

import discord
from discord.ext import commands, tasks

from modules import spawn_history
from modules.permissions import is_owner, owner_only


class SpawnHistory(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag

    def cog_check(self, ctx):
        return is_owner(ctx)

    async def cog_load(self):
        self.collect.start()

    async def cog_unload(self):
        self.collect.cancel()

    @tasks.loop(seconds=spawn_history.POLL_SECONDS)
    async def collect(self):
        try:
            await asyncio.to_thread(spawn_history.capture)
        except Exception as error:
            self.poliswag.utility.log_to_file(
                f"Spawn history capture failed: {type(error).__name__}", "ERROR"
            )

    @collect.before_loop
    async def before_collect(self):
        await self.poliswag.wait_until_ready()

    @commands.group(
        name="spawnhistory",
        invoke_without_command=True,
        brief="Estado do histórico de spawns (48 horas)",
    )
    @owner_only()
    async def history(self, ctx, hours: int = 24):
        data = await asyncio.to_thread(spawn_history.status, hours)
        text = "A recolha ainda não começou."
        if data["available"]:
            text = (
                f"{data['stored_spawns']:,} spawns guardados · retenção de 48 h.\n"
                f"Recolha iniciada: {data['collection_started_utc']}\n"
                f"Última recolha: {data['window_end_utc']}\n"
                f"Janela de {hours} h: {'completa' if data['complete_window'] else 'parcial'}."
            )
            if data["collector_stale"]:
                text += "\n⚠️ A recolha está atrasada."
        await ctx.send(text, allowed_mentions=discord.AllowedMentions.none())

    @history.command(name="export", brief="Exporta os spawns como JSON comprimido")
    @owner_only()
    async def export(self, ctx, hours: int = 24):
        output = await asyncio.to_thread(spawn_history.export_json, hours)
        attachment = discord.File(output, filename=f"spawns-{hours}h.json.gz")
        try:
            await ctx.send(
                "Histórico sem filtros por treinador; a cobertura está nos metadados.",
                file=attachment,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            attachment.close()
            output.close()

    @history.command(name="leaderboard", brief="Recalcula a leaderboard sem filtros")
    @owner_only()
    async def report(self, ctx, hours: int = 24):
        data = await asyncio.to_thread(spawn_history.leaderboard, hours)
        await ctx.send(
            data["metadata"].get("note", "A recolha ainda não começou."),
            file=discord.File(
                io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode()),
                filename=f"shiny-leaderboard-{hours}h.json",
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            return
        original = getattr(error, "original", error)
        text = (
            str(original)
            if isinstance(original, ValueError)
            else "Não consegui ler o histórico de spawns."
        )
        await ctx.send(text, allowed_mentions=discord.AllowedMentions.none())


async def setup(poliswag):
    await poliswag.add_cog(SpawnHistory(poliswag))
