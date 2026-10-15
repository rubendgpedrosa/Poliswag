"""Send Pokémon GO operational alerts directly to the owner's Discord DMs."""

import aiohttp
import discord

from modules.config import Config
from modules.embeds import status_embed


async def notify_owner(poliswag, text, *, title, tag):
    """Return True once Discord accepts the alert.

    Keep `tag` for compatibility with callers that identify alert categories.
    """
    if not Config.MY_ID:
        poliswag.utility.log_to_file("Owner alert: MY_ID is not configured", "ERROR")
        return False
    try:
        user = poliswag.get_user(Config.MY_ID) or await poliswag.fetch_user(
            Config.MY_ID
        )
        await user.send(embed=status_embed(title, text))
    except (discord.HTTPException, aiohttp.ClientError, TimeoutError, OSError) as error:
        poliswag.utility.log_to_file(
            f"Owner alert: Discord delivery failed ({type(error).__name__})", "ERROR"
        )
        return False
    return True
