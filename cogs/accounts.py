import discord
import io
from discord.ext import commands

from modules.embeds import status_embed
from modules.permissions import is_owner


class Accounts(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        return is_owner(ctx)

    @commands.command(
        name="accounts", brief="Gera imagem do atual número de contas do mapa"
    )
    async def account_report_cmd(self, ctx):
        try:
            if ctx.guild is not None:
                await ctx.message.delete()

            # Same card as the accounts channel: both pools + area footer.
            image_bytes = await self.poliswag.account_monitor.build_account_card()
            if image_bytes:
                try:
                    discord_file = discord.File(
                        io.BytesIO(image_bytes), filename="account_status_report.png"
                    )
                    await ctx.send(file=discord_file)
                except Exception as e:
                    self.poliswag.utility.log_to_file(
                        f"Error sending image: {e}", "ERROR"
                    )
                    await ctx.send(
                        embed=status_embed(
                            "❌ Erro ao enviar a imagem. Verifica os logs.",
                            color=discord.Color.red(),
                        )
                    )
            else:
                await ctx.send(
                    embed=status_embed(
                        "❌ Erro ao gerar a imagem de contas. Verifica os logs.",
                        color=discord.Color.red(),
                    )
                )

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
