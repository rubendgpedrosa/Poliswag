"""One live "Estado do scanner" card in the mod channel instead of a stream.

Every scanner notice (phone offline, recovery deferred, map down, back up)
goes through post_scanner_status. The card is edited in place while it is
the channel's latest message; if anyone wrote after it, a fresh card is
posted and the old one deleted, so the channel never holds more than one.
The card keeps when the incident started ("Em baixo desde") until the map is
back, and then closes with how long it was down. The card's id and the start
time live in SCANNER_CARD_FILE, so a restart keeps them.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from modules.config import Config

FOOTER = "Estado do scanner"
_LISBON = ZoneInfo("Europe/Lisbon")


def _load():
    try:
        data = json.loads(Path(Config.SCANNER_CARD_FILE).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state):
    path = Path(Config.SCANNER_CARD_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path)


def _clock(epoch):
    return datetime.fromtimestamp(epoch, _LISBON).strftime("%d/%m às %H:%M")


def _duration(seconds):
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def _is_scanner_card(message, bot_user):
    if bot_user is None or message.author.id != bot_user.id or not message.embeds:
        return False
    footer = message.embeds[0].footer
    return (getattr(footer, "text", None) or "").startswith(FOOTER)


async def post_scanner_status(poliswag, channel, embed, *, up=None):
    """Show `embed` as the one scanner card. `up` True closes the incident
    (map back), False/None keeps or opens it; None infers it from a 🟢 title."""
    if up is None:
        up = (embed.title or "").startswith("🟢")
    state = _load()
    now = time.time()
    if not up:
        state.setdefault("down_since", now)
        embed.add_field(name="Em baixo desde", value=_clock(state["down_since"]))
    elif state.get("down_since"):
        embed.add_field(
            name="Esteve em baixo",
            value=f"{_duration(now - state['down_since'])} "
            f"(desde {_clock(state['down_since'])})",
        )
        state.pop("down_since", None)
    embed.set_footer(text=f"{FOOTER} · atualizado {_clock(now)}")

    try:
        latest = [m async for m in channel.history(limit=1)]
    except Exception:
        latest = []
    bot_user = getattr(poliswag, "user", None)
    if latest and _is_scanner_card(latest[0], bot_user):
        try:
            await latest[0].edit(embed=embed)
            state["message_id"] = latest[0].id
            _save(state)
            return
        except Exception:
            pass  # deleted or not editable: post a new card
    previous = state.get("message_id")
    message = await channel.send(embed=embed)
    state["message_id"] = getattr(message, "id", None)
    _save(state)
    if previous and previous != state["message_id"]:
        try:
            old = await channel.fetch_message(int(previous))
            await old.delete()
        except Exception:
            pass  # already gone
