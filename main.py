#!/usr/bin/python
import discord
from discord.ext import commands

from modules.role_manager import RoleManager
from cogs.event_panel import EventPanelView
from modules.scanner_status import ScannerStatus
from modules.scanner_manager import ScannerManager
from modules.utility import Utility
from modules.database_connector import DatabaseConnector
from modules.image_generator import ImageGenerator
from modules.quest_search import QuestSearch
from modules.event_manager import EventManager
from modules.event_stats import EventStats
from modules.quest_exporter import QuestExporter
from modules.mega_exporter import MegaExporter
from modules.account_monitor import AccountMonitor
from modules.poracle_client import PoracleClient
from modules.device_manager import DeviceManager
from modules.lure_manager import LureManager
from modules.page_view_stats import PageViewStats
from modules.lure_watcher import LureWatcher
from modules.trade_player_store import TradePlayerStore
from modules.trade_stats import TradeStats
from modules.stack_recovery import StackRecovery
from modules.help_command import EmbedHelpCommand
from modules.config import Config
from modules.dm_policy import may_run_in_dm
from modules.http_client import close_session
from modules.migrations import apply_migrations


class Poliswag(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        intents.messages = True
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
            help_command=EmbedHelpCommand(),
        )

        self.db = DatabaseConnector()
        self.role_manager = RoleManager()
        self.utility = Utility(self)
        self.scanner_status = ScannerStatus(self)
        self.scanner_manager = ScannerManager(self)
        self.image_generator = ImageGenerator(self)
        self.quest_search = QuestSearch(self)
        self.event_manager = EventManager(self)
        self.event_stats = EventStats(self)
        self.quest_exporter = QuestExporter(self)
        self.mega_exporter = MegaExporter(self)
        self.account_monitor = AccountMonitor(self)
        self.poracle = PoracleClient(self)
        self.device_manager = DeviceManager(self)
        self.lure_manager = LureManager(self)
        self.page_view_stats = PageViewStats(self)
        self.trade_player_store = TradePlayerStore()
        self.trade_stats = TradeStats()
        self.lure_watcher = LureWatcher(self)
        self.stack_recovery = StackRecovery(self)

        self.QUEST_CHANNEL = None
        self.CONVIVIO_CHANNEL = None
        self.MOD_CHANNEL = None
        self.ACCOUNTS_CHANNEL = None
        self.TRAP_CHANNEL = None
        self.EVENT_PANEL_CHANNEL = None

        self.ADMIN_USERS_IDS = Config.ADMIN_USERS_IDS

        self.quest_scanning_message = None

    async def on_ready(self):
        await self.get_channels()

    async def process_commands(self, message):
        """Drop a DM we don't answer before it ever reaches a command.

        A global check would be the obvious place for this, but a failed
        check raises CheckFailure, and ContainerManagerCog answers that
        with "não tens autorização" — which tells a stranger the command
        exists. Returning before invoke() says nothing at all.
        """
        if message.author.bot:
            return
        ctx = await self.get_context(message)
        if ctx.command is not None and not may_run_in_dm(ctx):
            return
        await self.invoke(ctx)

    async def close(self):
        await close_session()
        await self.poracle.close()
        await super().close()

    async def setup_hook(self):
        # Before any cog: several read their state columns in cog_load.
        await apply_migrations(self.db, self.utility.log_to_file)
        await self.load_extension("cogs.quests")
        await self.load_extension("cogs.accounts")
        await self.load_extension("cogs.tracker")
        await self.load_extension("cogs.event")
        await self.load_extension("cogs.container_manager")
        await self.load_extension("cogs.moderation")
        await self.load_extension("cogs.notifications")
        await self.load_extension("cogs.scheduled")
        await self.load_extension("cogs.lures")
        await self.load_extension("cogs.webstats")
        await self.load_extension("cogs.trades")
        await self.load_extension("cogs.event_panel")
        await self.load_extension("cogs.announcements")
        # Re-registers the persistent view so the buttons on panels posted
        # for previous events keep working across restarts.
        self.add_view(EventPanelView(self))
        await self.tree.sync()

    async def get_channels(self):
        channels = {
            "QUEST_CHANNEL": Config.QUEST_CHANNEL_ID,
            "CONVIVIO_CHANNEL": Config.CONVIVIO_CHANNEL_ID,
            "MOD_CHANNEL": Config.MOD_CHANNEL_ID,
            "ACCOUNTS_CHANNEL": Config.ACCOUNTS_CHANNEL_ID,
            "TRAP_CHANNEL": Config.TRAP_CHANNEL_ID,
            "EVENT_PANEL_CHANNEL": Config.EVENT_PANEL_CHANNEL_ID,
        }
        for attr, channel_id in channels.items():
            if not channel_id:
                self.utility.log_to_file(
                    f"{attr}_ID is unset or 0; channel will not be resolved",
                    "ERROR",
                )
                continue
            setattr(self, attr, await self.fetch_channel(channel_id))


def main():
    Config.validate()
    poliswag = Poliswag()
    poliswag.run(Config.DISCORD_API_KEY)


if __name__ == "__main__":
    main()
