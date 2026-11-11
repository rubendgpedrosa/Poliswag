#!/usr/bin/python
import logging
import re

import discord
from discord.ext import commands

from cogs.event_panel import EventPanelView
from modules import scan_areas
from modules.account_guard import AccountGuard
from modules.account_monitor import AccountMonitor
from modules.config import Config
from modules.database_connector import DatabaseConnector
from modules.device_manager import DeviceManager
from modules.dm_policy import may_run_in_dm
from modules.embeds import status_embed
from modules.event_manager import EventManager
from modules.event_notices import EventNotices
from modules.event_stats import EventStats
from modules.help_command import EmbedHelpCommand
from modules.http_client import close_session
from modules.hundo_alerts import HundoAlerts
from modules.image_generator import ImageGenerator
from modules.lure_watcher import LureWatcher
from modules.mega_exporter import MegaExporter
from modules.mention_rules import directed_mention, replied_bot_message
from modules.migrations import apply_migrations
from modules.page_view_stats import PageViewStats
from modules.permissions import is_owner
from modules.poracle_client import PoracleClient
from modules.quest_exporter import QuestExporter
from modules.quest_search import QuestSearch
from modules.role_manager import RoleManager
from modules.scanner_manager import ScannerManager
from modules.scanner_status import ScannerStatus
from modules.shiny_alerts import ShinyAlerts
from modules.shiny_availability import ShinyAvailability
from modules.shiny_odds import ShinyOdds
from modules.stack_recovery import StackRecovery
from modules.tracking_health import TrackingHealth
from modules.trade_announcer import TradeAnnouncer
from modules.trade_digest import TradeDigest
from modules.trade_dm import TradeDM
from modules.trade_player_store import TradePlayerStore
from modules.trade_stats import TradeStats
from modules.utility import Utility

LOGGER = logging.getLogger(__name__)

_IGNORED = re.compile(r"\s*!ignore(?:\s|$)", re.I)
# "c <text>" or "!c <text>" in the owner DM goes to Claude instead of Hermes.
_CLAUDE = re.compile(r"\s*!?c(?:\s+|$)", re.I)
# With Claude as the default (!useclaude), "h " sends a DM to Hermes instead.
_HERMES = re.compile(r"\s*!?h(?:\s+|$)", re.I)


class Poliswag(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
            help_command=EmbedHelpCommand(),
        )

        # One handle per schema, all built here. A module asks the bot for
        # the handle it needs and never opens a connection itself: a single
        # statement goes through get_data_from_database /
        # execute_query_to_database on the handle's shared connection, and
        # anything bigger through handle.unit(work).
        self.db = DatabaseConnector()  # the bot's own schema
        # The scanner's own database (Golbat), read-only for the bot.
        self.scanner_db = DatabaseConnector(Config.DB_SCANNER_NAME)
        # The site's schema: trades, the Pokédex, page views.
        self.site_db = DatabaseConnector(Config.DB_POGOLEIRIA)
        # Poracle is optional: a missing schema must not prevent bot startup.
        # Its 100IV rules use independent units of work.
        self.poracle_db = DatabaseConnector("poracle", lazy=True)
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
        self.page_view_stats = PageViewStats(self)
        self.trade_player_store = TradePlayerStore(self.site_db)
        self.trade_stats = TradeStats(self.site_db)
        self.lure_watcher = LureWatcher(self)
        self.stack_recovery = StackRecovery(self)
        self.account_guard = AccountGuard(self)
        # What the scheduler runs (cogs/scheduled.py). Built here with every
        # other service, so that cog only decides when.
        self.event_notices = EventNotices(self)
        self.trade_announcer = TradeAnnouncer(self)
        self.trade_dm = TradeDM(self)
        self.trade_digest = TradeDigest(self)
        self.hundo_alerts = HundoAlerts(self)
        self.shiny_odds = ShinyOdds(self)
        self.shiny_availability = ShinyAvailability(self)
        self.shiny_alerts = ShinyAlerts(self)
        self.tracking_health = TrackingHealth(self.site_db)

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
        check raises CheckFailure, and a cog's own error handler may answer
        that with "não tens autorização" — which tells a stranger the
        command exists. Returning before invoke() says nothing at all.
        """
        if message.author.bot or message.webhook_id is not None:
            return
        if _IGNORED.match(message.content):
            # "!ignore <text>" is a note the bot must not act on: no Hermes
            # turn, no tag reply, no command.
            return
        ctx = await self.get_context(message)
        if self._mentions_poliswag(message):
            mentions = self.get_cog("Mentions")
            if mentions is not None:
                await mentions.handle(ctx)
            elif may_run_in_dm(ctx) and (ctx.guild is not None or is_owner(ctx)):
                # Fail closed if the cog is unavailable: a tag can never fall
                # through into a privileged command or an agent invocation.
                await ctx.send(
                    embed=status_embed(
                        "🐸 Poliswag", "Olá! Estou aqui — diz-me o que precisas. 🐸"
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return
        prefix = _CLAUDE.match(message.content)
        if prefix and ctx.guild is None and is_owner(ctx) and ctx.command is None:
            claude = self.get_cog("ClaudeChat")
            if claude is not None:
                await claude.handle(ctx, message.content[prefix.end() :])
            return
        if (
            ctx.guild is None
            and is_owner(ctx)
            and ctx.command is None
            and message.content.strip()
            and not message.content.lstrip().startswith("!")
        ):
            hermes = self.get_cog("Hermes")
            claude = self.get_cog("ClaudeChat")
            to_hermes = _HERMES.match(message.content)
            if to_hermes and hermes is not None:
                await hermes.handle(ctx, message.content[to_hermes.end() :])
                return
            claude_busy = claude is not None and claude.busy()
            hermes_busy = hermes is not None and hermes.busy()
            # Whoever is replying gets the follow-up (steer), whatever the
            # default; with both or neither busy, the default decides.
            if claude_busy and not hermes_busy:
                if await claude.handle(ctx, message.content, steer_only=True):
                    return
            elif hermes_busy and not claude_busy:
                await hermes.handle(ctx, message.content)
                return
            if Config.OWNER_DM_DEFAULT == "claude" and claude is not None:
                await claude.handle(ctx, message.content)
            elif hermes is not None:
                await hermes.handle(ctx, message.content)
            return
        if ctx.command is not None and not may_run_in_dm(ctx):
            return
        await self.invoke(ctx)

    def _mentions_poliswag(self, message):
        # Same rule as the Mentions cog: a tag in code or a quote is not a tag.
        if self.user is None:
            return False
        return directed_mention(message.content, self.user.id) or (
            replied_bot_message(message, self.user.id) is not None
        )

    def _is_bare_mention(self, message):
        return self.user is not None and message.content.strip() in (
            f"<@{self.user.id}>",
            f"<@!{self.user.id}>",
        )

    async def close(self):
        await close_session()
        await self.poracle.close()
        await super().close()

    async def setup_hook(self):
        if not Config.PRIVATE_NAMES:
            LOGGER.warning(
                "POLISWAG_PRIVATE_NAMES is unset; model replies that name the "
                "owner will not be filtered."
            )
        # Before any cog: several read their state columns in cog_load.
        await apply_migrations(self.db, self.utility.log_to_file)
        # Areas come from the registry plus Dragonite's fences; load them
        # before cogs so the first tick already classifies points correctly.
        scan_areas.use(self.db)
        await scan_areas.refresh(self.utility.log_to_file)
        await self.load_extension("cogs.quests")
        await self.load_extension("cogs.accounts")
        await self.load_extension("cogs.tracker")
        await self.load_extension("cogs.event")
        await self.load_extension("cogs.moderation")
        await self.load_extension("cogs.scheduled")
        await self.load_extension("cogs.webstats")
        await self.load_extension("cogs.pokedex")
        await self.load_extension("cogs.event_panel")
        await self.load_extension("cogs.announcements")
        await self.load_extension("cogs.giveaways")
        await self.load_extension("cogs.shiny")
        await self.load_extension("cogs.shiny_access_requests")
        await self.load_extension("cogs.spawn_history")
        await self.load_extension("cogs.hermes")
        await self.load_extension("cogs.claude_chat")
        await self.load_extension("cogs.mentions")
        await self.load_extension("cogs.support")
        # Re-registers the persistent view so the buttons on panels posted
        # for previous events keep working across restarts.
        self.add_view(EventPanelView(self))
        await self.tree.sync()

    async def get_channels(self):
        """Resolve each configured channel independently.

        A lookup failure for one channel (Forbidden/NotFound/HTTPException)
        clears that attribute to None, logs which channel failed, and still
        attempts the remaining channels. Unset ids also clear to None so a
        reconnect never keeps a stale reference. Cancellation and unrelated
        programmer errors propagate.
        """
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
                setattr(self, attr, None)
                continue
            try:
                setattr(self, attr, await self.fetch_channel(channel_id))
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as e:
                self.utility.log_to_file(
                    f"Failed to resolve {attr} ({channel_id}): "
                    f"{type(e).__name__}: {e}",
                    "ERROR",
                )
                setattr(self, attr, None)


def main():
    Config.validate()
    poliswag = Poliswag()
    poliswag.run(Config.DISCORD_API_KEY)


if __name__ == "__main__":
    main()
