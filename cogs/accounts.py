import discord
from discord.ext import commands

from modules.embeds import status_embed


class Accounts(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    @commands.command(name="accounts", brief="Mostra o estado atual do pool de contas")
    async def account_report_cmd(self, ctx):
        try:
            if ctx.guild is not None:
                await ctx.message.delete()

            account_data = await self.poliswag.account_monitor.get_account_stats()
            device_status = await self.poliswag.account_monitor.is_device_connected()
            embed = self.poliswag.account_monitor.build_status_embed(
                account_data, device_status
            )
            await ctx.send(embed=embed)

        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"Error in account_report_cmd: {e}", "ERROR"
            )
            await ctx.send(
                embed=status_embed(
                    "❌ Ocorreu um erro ao gerar o relatório. Verifica os logs.",
                    color=discord.Color.red(),
                )
            )


async def setup(poliswag):
    await poliswag.add_cog(Accounts(poliswag))
