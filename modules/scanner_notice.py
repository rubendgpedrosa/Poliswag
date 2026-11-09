"""One live "Estado do scanner" card in the mod channel instead of a stream.

Every scanner notice (phone offline, recovery deferred, map down, back up)
goes through post_scanner_status. If the channel's latest message is already
Poliswag's scanner card, that card is edited in place; if anyone wrote after
it, a fresh card is posted, so news is never buried above the conversation.
The footer marks the card and says when it was last updated.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

FOOTER = "Estado do scanner"
_LISBON = ZoneInfo("Europe/Lisbon")


def _is_scanner_card(message, bot_user):
    if bot_user is None or message.author.id != bot_user.id or not message.embeds:
        return False
    footer = message.embeds[0].footer
    text = getattr(footer, "text", None) or ""
    return text.startswith(FOOTER)


async def post_scanner_status(poliswag, channel, embed):
    """Edit the latest scanner card in place, or post a new one."""
    stamp = datetime.now(_LISBON).strftime("%d/%m %H:%M")
    embed.set_footer(text=f"{FOOTER} · atualizado {stamp}")
    try:
        latest = [m async for m in channel.history(limit=1)]
    except Exception:
        latest = []
    if latest and _is_scanner_card(latest[0], getattr(poliswag, "user", None)):
        try:
            await latest[0].edit(embed=embed)
            return
        except Exception:
            pass  # deleted or not editable: post a new card
    await channel.send(embed=embed)
