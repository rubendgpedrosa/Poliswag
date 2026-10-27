"""The owner's private Claude Code conversation in CT 109, picked per DM by prefix."""

from cogs.hermes import Hermes
from modules.config import Config
from modules.hermes_client import HermesClient


class ClaudeChat(Hermes):
    """Same DM flow as Hermes (queue, live card, stop/steer, recovery).

    The CT 109 claude-bridge speaks the Hermes Runs API subset the client uses
    and keeps one persistent Claude Code session for the owner.
    """

    NAME = "Claude"

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.client = HermesClient(
            Config.CLAUDE_API_URL,
            Config.CLAUDE_API_KEY,
            Config.MY_ID,
            runs_dir=Config.CLAUDE_RUNS_DIR,
        )
        self.live = set()

    async def handle(self, ctx, text):
        if isinstance(text, str) and not text.strip() and ctx.guild is None:
            await self.respond(ctx, "Escreve o pedido para o Claude depois de `c `.")
            return True
        return await super().handle(ctx, text)


async def setup(poliswag):
    cog = ClaudeChat(poliswag)
    await poliswag.add_cog(cog)
    poliswag.loop.create_task(cog.recover_pending())
