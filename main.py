#!/usr/bin/python
import discord
from discord.ext import commands

from modules.role_manager import RoleManager
from modules.scanner_status import ScannerStatus
from modules.scanner_manager import ScannerManager
from modules.utility import Utility
from modules.database_connector import DatabaseConnector
from modules.image_generator import ImageGenerator
from modules.quest_search import QuestSearch
from modules.event_manager import EventManager
from modules.quest_exporter import QuestExporter
from modules.account_monitor import AccountMonitor
from modules.config import Config
from modules.http_client import close_session


class Poliswag(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        intents.messages = True
        super().__init__(command_prefix="!", intents=intents)

        self.db = DatabaseConnector()
        self.role_manager = RoleManager()
        self.utility = Utility(self)
        self.scanner_status = ScannerStatus(self)
        self.scanner_manager = ScannerManager(self)
        self.image_generator = ImageGenerator(self)
        self.quest_search = QuestSearch(self)
        self.event_manager = EventManager(self)
        self.quest_exporter = QuestExporter(self)
        self.account_monitor = AccountMonitor(self)

        self.QUEST_CHANNEL = None
        self.CONVIVIO_CHANNEL = None
        self.MOD_CHANNEL = None
        self.ACCOUNTS_CHANNEL = None

        self.ADMIN_USERS_IDS = Config.ADMIN_USERS_IDS

        self.quest_scanning_message = None

    async def on_ready(self):
        await self.get_channels()

    async def close(self):
        await close_session()
        await super().close()

    async def setup_hook(self):
        await self.load_extension("cogs.quests")
        await self.load_extension("cogs.accounts")
        await self.load_extension("cogs.tracker")
        await self.load_extension("cogs.event")
        await self.load_extension("cogs.container_manager")
        await self.load_extension("cogs.moderation")
        await self.load_extension("cogs.scheduled")
        await self.tree.sync()

    async def get_channels(self):
        channels = {
            "QUEST_CHANNEL": Config.QUEST_CHANNEL_ID,
            "CONVIVIO_CHANNEL": Config.CONVIVIO_CHANNEL_ID,
            "MOD_CHANNEL": Config.MOD_CHANNEL_ID,
            "ACCOUNTS_CHANNEL": Config.ACCOUNTS_CHANNEL_ID,
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
    poliswag = Poliswag()
    poliswag.run(Config.DISCORD_API_KEY)


if __name__ == "__main__":
    main()
