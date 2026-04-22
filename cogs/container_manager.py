from discord.ext import commands

from modules.config import Config


class ContainerManagerCog(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.SCANNER_CONTAINER_NAME = Config.SCANNER_CONTAINER_NAME

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        return ctx.author.id == Config.MY_ID

    @commands.group(name="container", invoke_without_command=True)
    async def container(self, ctx):
        await ctx.send(
            "Invalid container command. Use `container start` or `container stop`."
        )

    @container.command(name="start")
    async def start_container(self, ctx):
        await ctx.send(
            f"Attempting to start container '{self.SCANNER_CONTAINER_NAME}'..."
        )
        try:
            self.poliswag.scanner_manager.change_scanner_status("start")
            await ctx.send(
                f"Container '{self.SCANNER_CONTAINER_NAME}' start command sent."
            )
        except Exception as e:
            error_message = f"Error starting container: {e}"
            print(error_message)
            self.poliswag.utility.log_to_file(error_message, "ERROR")
            await ctx.send(error_message)

    @container.command(name="stop")
    async def stop_container(self, ctx):
        await ctx.send(
            f"Attempting to stop container '{self.SCANNER_CONTAINER_NAME}'..."
        )
        try:
            self.poliswag.scanner_manager.change_scanner_status("stop")
            await ctx.send(
                f"Container '{self.SCANNER_CONTAINER_NAME}' stop command sent."
            )
        except Exception as e:
            error_message = f"Error stopping container: {e}"
            print(error_message)
            self.poliswag.utility.log_to_file(error_message, "ERROR")
            await ctx.send(error_message)

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("You are not authorized to use this command.")
            return
        if isinstance(error, commands.CommandNotFound):
            await ctx.send(
                "Invalid container command. Use `container start` or `container stop`."
            )
            return
        error_message = f"An error occurred: {error}"
        print(error_message)
        self.poliswag.utility.log_to_file(error_message, "ERROR")
        await ctx.send(error_message)


async def setup(bot):
    await bot.add_cog(ContainerManagerCog(bot))
