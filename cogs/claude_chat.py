"""The owner's private Claude Code conversation in CT 109, picked per DM by prefix."""

from discord.ext import commands

from cogs.hermes import Hermes
from modules.config import Config
from modules.env_file import set_env_value
from modules.hermes_client import HermesClient
from modules.permissions import owner_only


class ClaudeChat(Hermes):
    """Same DM flow as Hermes (queue, live card, stop/steer, recovery).

    The CT 109 claude-bridge speaks the Hermes Runs API subset the client uses
    and keeps one persistent Claude Code session for the owner.
    """

    NAME = "Claude"
    # Laranja da marca, e "a responder" em vez de "a trabalhar".
    CARD_COLOUR = 0xD97757
    CARD_VERB = "a responder"

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.client = HermesClient(
            Config.CLAUDE_API_URL,
            Config.CLAUDE_API_KEY,
            Config.MY_ID,
            runs_dir=Config.CLAUDE_RUNS_DIR,
        )
        self.live = set()

    async def handle(self, ctx, text, steer_only=False):
        if isinstance(text, str) and not text.strip() and ctx.guild is None:
            await self.respond(ctx, "Escreve o pedido para o Claude depois de `c `.")
            return True
        return await super().handle(ctx, text, steer_only=steer_only)

    async def _set_default(self, ctx, agent):
        Config.OWNER_DM_DEFAULT = agent
        try:
            set_env_value("OWNER_DM_DEFAULT", agent, Config.ENV_FILE)
            saved = "Fica gravado para os próximos arranques."
        except (OSError, ValueError):
            saved = (
                "Não consegui gravar no .env: volta ao anterior no próximo arranque."
            )
        other = "Hermes" if agent == "claude" else "Claude"
        prefix = "h " if agent == "claude" else "c "
        await ctx.send(
            f"As tuas mensagens sem prefixo vão agora para o **{agent.capitalize()}**. "
            f"Para o {other}, começa com `{prefix}`. {saved}"
        )

    @commands.command(name="useclaude", brief="DMs sem prefixo vão para o Claude")
    @owner_only()
    async def useclaude(self, ctx):
        await self._set_default(ctx, "claude")

    @commands.command(name="usehermes", brief="DMs sem prefixo vão para o Hermes")
    @owner_only()
    async def usehermes(self, ctx):
        await self._set_default(ctx, "hermes")


async def setup(poliswag):
    cog = ClaudeChat(poliswag)
    await poliswag.add_cog(cog)
    poliswag.loop.create_task(cog.recover_pending())
