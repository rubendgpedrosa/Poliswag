from discord.ext import commands
import os

OWNER_ID = int(os.getenv("MY_ID"))
SCANNER_CONTAINER_NAME = os.getenv("SCANNER_CONTAINER_NAME")


class ContainerManagerCog(commands.Cog):
    def __init__(self, poliswag):
        self.bot = bot
        self.SCANNER_CONTAINER_NAME = os.getenv("SCANNER_CONTAINER_NAME")

    def cog_check(self, ctx):
        return ctx.author.id == OWNER_ID

    @commands.group(name="container", invoke_without_command=True)
    async def container(self, ctx):
        await ctx.send(
            "Invalid container command. Use `container start` or `container stop`."
        )

    @container.command(name="start")
    async def start_container(self, ctx):
        """
        Starts the scanner container.
        """
        await ctx.send(
            f"Attempting to start container '{self.SCANNER_CONTAINER_NAME}'..."
        )
        try:
            # Assuming you have a ScannerManager instance available as bot.scanner_manager
            self.bot.scanner_manager.change_scanner_status("start")
            await ctx.send(
                f"Container '{self.SCANNER_CONTAINER_NAME}' start command sent."
            )
        except Exception as e:
            await ctx.send(f"Error starting container: {e}")

    @container.command(name="stop")
    async def stop_container(self, ctx):
        """
        Stops the scanner container.
        """
        await ctx.send(
            f"Attempting to stop container '{self.SCANNER_CONTAINER_NAME}'..."
        )
        try:
            # Assuming you have a ScannerManager instance available as bot.scanner_manager
            self.bot.scanner_manager.change_scanner_status("stop")
            await ctx.send(
                f"Container '{self.SCANNER_CONTAINER_NAME}' stop command sent."
            )
        except Exception as e:
            await ctx.send(f"Error stopping container: {e}")

    @container.error
    async def container_error(self, ctx, error):
        """
        Error handler for the container commands.
        """
        if isinstance(error, commands.CheckFailure):
            await ctx.send("You are not authorized to use this command.")
        elif isinstance(error, commands.CommandNotFound):
            await ctx.send(
                "Invalid container command. Use `container start` or `container stop`."
            )
        else:
            await ctx.send(f"An error occurred: {error}")

    @start_container.error
    async def start_container_error(self, ctx, error):
        """
        Error handler for the start_container command.
        """
        if isinstance(error, commands.CheckFailure):
            await ctx.send("You are not authorized to use this command.")
        else:
            await ctx.send(f"An error occurred: {error}")

    @stop_container.error
    async def stop_container_error(self, ctx, error):
        """
        Error handler for the stop_container command.
        """
        if isinstance(error, commands.CheckFailure):
            await ctx.send("You are not authorized to use this command.")
        else:
            await ctx.send(f"An error occurred: {error}")


async def setup(bot):
    """
    Setup function to add the cog to the bot.
    """
    await bot.add_cog(ContainerManagerCog(bot))
