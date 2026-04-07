import discord
import io
from discord.ext import commands


class Accounts(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    @commands.command(
        name="accounts", brief="Gera imagem do atual número de contas do mapa"
    )
    async def account_report_cmd(self, ctx):
        try:
            if ctx.guild is not None:
                await ctx.message.delete()

            account_data = await self.poliswag.account_monitor.get_account_stats()
            device_status = await self.poliswag.account_monitor.is_device_connected()

            image_bytes = (
                await self.poliswag.image_generator.generate_image_from_account_stats(
                    account_data, device_status
                )
            )
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
                    await ctx.send("Error sending image. Check logs.")
            else:
                await ctx.send("Error generating account image. Check logs.")

        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"Error in account_report_cmd: {e}", "ERROR"
            )
            await ctx.send(
                "An error occurred while generating the report. Check the logs."
            )


async def setup(poliswag):
    await poliswag.add_cog(Accounts(poliswag))
