import discord, requests, os, io
from discord.ext import commands

class Accounts(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    @commands.command(name="accounts")
    async def account_report_cmd(self, ctx):
        try:
            if ctx.guild is not None:
                await ctx.message.delete()
        
            account_data = await self.poliswag.scanner_status.get_account_stats()

            if account_data:
                image_bytes = self.poliswag.image_generator.generate_image_from_account_stats(account_data)
                if image_bytes:
                    try:
                        with io.BytesIO(image_bytes) as image_file:
                            discord_file = discord.File(image_file, filename="account_status_report.png")
                            await ctx.send(file=discord_file)
                    except Exception as e:
                        error_message = f"Error sending image: {e}"
                        print(error_message)
                        self.poliswag.utility.log_to_file(error_message, "ERROR")
                        await ctx.send("Error sending image. Check logs.")

                else:
                    await ctx.send("Error generating account image. Check logs.")

            else:
                await ctx.send("Could not retrieve account data. Check logs.")

        except Exception as e:
            error_message = f"An error occurred: {e}"
            print(error_message)
            self.poliswag.utility.log_to_file(error_message, "ERROR")
            await ctx.send("An error occurred while generating the report. Check the logs.")

async def setup(poliswag):
    await poliswag.add_cog(Accounts(poliswag))