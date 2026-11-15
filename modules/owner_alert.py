"""Send Pokémon GO operational alerts directly to the owner's Discord DMs."""

import time

import aiohttp
import discord

from modules.config import Config
from modules.embeds import status_embed
from modules.state_store import StateFile

RECOVERED_COLOR = 0x2ECC71


def _duration(seconds):
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


async def notify_owner(
    poliswag, text, *, title, tag, incident=None, resolved=False, color=None
):
    """Return True once Discord accepts the alert.

    Keep `tag` for compatibility with callers that identify alert categories.
    With `incident`, the alert is one message per incident (owner,
    2026-11-15): the first call sends it and remembers it; the call with
    ``resolved=True`` edits that message into the recovery (green, with how
    long it lasted) instead of sending another. If the message is gone, the
    recovery is sent as a new one.
    """
    if not Config.MY_ID:
        poliswag.utility.log_to_file("Owner alert: MY_ID is not configured", "ERROR")
        return False
    store = StateFile("owner-incidents") if incident else None
    open_incidents = store.load() if store else {}
    try:
        user = poliswag.get_user(Config.MY_ID) or await poliswag.fetch_user(
            Config.MY_ID
        )
        if resolved:
            opened = open_incidents.pop(incident, None) if incident else None
            if isinstance(opened, dict) and isinstance(opened.get("at"), (int, float)):
                text = f"{text}\nDurou {_duration(time.time() - opened['at'])}."
            embed = status_embed(title, text, color=color or RECOVERED_COLOR)
            edited = False
            if isinstance(opened, dict) and opened.get("message"):
                try:
                    channel = user.dm_channel or await user.create_dm()
                    await channel.get_partial_message(int(opened["message"])).edit(
                        embed=embed
                    )
                    edited = True
                except (discord.HTTPException, ValueError, TypeError):
                    edited = False
            if not edited:
                await user.send(embed=embed)
            if store:
                store.save(open_incidents)
            return True
        message = await user.send(embed=status_embed(title, text, color=color))
    except (discord.HTTPException, aiohttp.ClientError, TimeoutError, OSError) as error:
        poliswag.utility.log_to_file(
            f"Owner alert: Discord delivery failed ({type(error).__name__})", "ERROR"
        )
        return False
    if store:
        open_incidents[incident] = {
            "message": str(getattr(message, "id", "") or ""),
            "at": time.time(),
        }
        store.save(open_incidents)
    return True
