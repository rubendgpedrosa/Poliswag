#!/usr/bin/python
import os
import discord
from dotenv import load_dotenv
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


class Poliswag(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        intents.messages = True
        super().__init__(command_prefix="!", intents=intents)

        load_dotenv()

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
        self.QUEST_CHANNEL = await self.fetch_channel(
            int(os.environ.get("QUEST_CHANNEL_ID"))
        )
        self.CONVIVIO_CHANNEL = await self.fetch_channel(
            int(os.environ.get("CONVIVIO_CHANNEL_ID"))
        )
        self.MOD_CHANNEL = await self.fetch_channel(
            int(os.environ.get("MOD_CHANNEL_ID"))
        )
        self.ACCOUNTS_CHANNEL = await self.fetch_channel(
            int(os.environ.get("ACCOUNTS_CHANNEL_ID"))
        )


def main():
    poliswag = Poliswag()
    poliswag.run(os.environ.get("DISCORD_API_KEY"))


if __name__ == "__main__":
    main()
