"""Alerts for the owner go to his phone through Home Assistant, not a Discord DM.

HA turns the POST into an iOS notification (webhook `poliswag_owner`, the
automation "Poliswag: owner alerts"). Discord markdown means nothing there, so
it is stripped on the way. When HA can't be reached, or outside production,
the alert falls back to the DM it used to be, so it is never lost.

The daily error digest is not an alert and stays a DM (cogs/scheduled.py).
"""

import re

import aiohttp
import discord

from modules.config import Config
from modules.http_client import get_session


def plain(text):
    """Drop Discord's **bold** and `code` markers for a phone notification."""
    return re.sub(r"\*\*|`", "", text)


async def notify_owner(poliswag, text, *, title, tag):
    """Send `text` to the owner; True once HA or the DM fallback accepted it.

    `tag` groups the notification on the phone: a newer alert with the same
    tag replaces the older one (a "back up" replaces its "down").
    """
    url = Config.ENDPOINTS.get("owner_alert")
    if url and Config.IS_PRODUCTION:
        try:
            async with get_session().post(
                url,
                json={"title": title, "message": plain(text), "tag": tag},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status < 300:
                    return True
                poliswag.utility.log_to_file(
                    f"Owner alert: Home Assistant answered {response.status}; "
                    "falling back to a DM",
                    "ERROR",
                )
        except (aiohttp.ClientError, TimeoutError) as error:
            poliswag.utility.log_to_file(
                f"Owner alert: Home Assistant unreachable ({type(error).__name__}); "
                "falling back to a DM",
                "ERROR",
            )

    if not Config.MY_ID:
        return False
    user = poliswag.get_user(Config.MY_ID) or await poliswag.fetch_user(Config.MY_ID)
    try:
        await user.send(text)
    except discord.HTTPException:
        return False
    return True
