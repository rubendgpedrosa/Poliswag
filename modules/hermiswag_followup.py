"""Poliswag keeps a conversation going without being tagged again.

After Poliswag answers in a channel, the channel stays "active" for
ACTIVE_WINDOW. A one-minute loop looks at what members wrote after its last
message there; once they went quiet for QUIET, Hermiswag reads the recent
channel and decides whether any of it is still a conversation with Poliswag
(a follow-up question, an answer to something it asked). It answers only
then, as a plain reply. Limits keep it from barging in: at most one
follow-up per MIN_GAP per channel and MAX_CHAIN in a row without a tag or a
reply to it; a tag resets the chain. It follows one person at a time:
whoever it last answered; what others write meanwhile is only context.
"""

import json
import time
from datetime import datetime, timezone

ACTIVE_WINDOW = 15 * 60
QUIET = 45
MIN_GAP = 3 * 60
MAX_CHAIN = 3

FOLLOWUP_RULES = (
    "Ninguém te mencionou agora. Há pouco falaste neste canal, e o campo "
    "text tem as mensagens que os membros escreveram depois da tua última "
    "resposta; recent_channel_messages tem a conversa antes disso, incluindo "
    "o que tu disseste. Responde só se essas mensagens forem claramente a "
    "continuação da conversa contigo: uma pergunta de seguimento ao que "
    "disseste, uma resposta a algo que perguntaste, ou um pedido que ficou "
    "para ti. Se for conversa entre membros, um simples obrigado, uma "
    "reação, outro assunto, ou se alguém já respondeu, não respondes. Na "
    "dúvida, não respondes. Nesta tarefa o JSON tem duas chaves: "
    '{"reply": "...", "for_me": true ou false}. Só é publicado com for_me '
    "true e reply preenchido."
)
FOLLOWUP_FORMAT = (
    'Responde só com o JSON de duas chaves: {"reply": "...", "for_me": true/false}.'
)


class ActiveChannels:
    """In-memory: where Poliswag spoke lately and how much it followed up."""

    def __init__(self):
        self._state = {}

    def spoke(self, channel_id, *, invited, partner=None, now=None):
        """Poliswag answered `partner` here; `invited` (tag/reply) resets the
        chain. Follow-ups only ever continue with that one person."""
        now = time.time() if now is None else now
        entry = self._state.setdefault(
            channel_id, {"at": now, "chain": 0, "followed_at": float("-inf")}
        )
        entry["at"] = now
        entry["partner"] = partner
        if invited:
            entry["chain"] = 0

    def partner(self, channel_id):
        return self._state.get(channel_id, {}).get("partner")

    def followed_up(self, channel_id, now=None):
        now = time.time() if now is None else now
        entry = self._state.setdefault(
            channel_id, {"at": now, "chain": 0, "followed_at": float("-inf")}
        )
        entry["at"] = now
        entry["followed_at"] = now
        entry["chain"] += 1

    def due(self, now=None):
        """Channels worth a look now: recently active, not over the limits."""
        now = time.time() if now is None else now
        out = []
        for channel_id, entry in list(self._state.items()):
            if now - entry["at"] > ACTIVE_WINDOW:
                del self._state[channel_id]
                continue
            if entry["chain"] >= MAX_CHAIN or now - entry["followed_at"] < MIN_GAP:
                continue
            out.append(channel_id)
        return out


def new_since_poliswag(messages, own_id, handled, now, partner=None):
    """The partner's messages after Poliswag's last message (messages oldest
    first; everyone's when there is no partner), or [] when there are none,
    they were looked at already, they tag Poliswag (on_message answers
    those), or the newest is too fresh."""
    last = None
    for index, message in enumerate(messages):
        if message.author.id == own_id:
            last = index
    if last is None:
        return []
    after = [
        m
        for m in messages[last + 1 :]
        if not m.author.bot
        and getattr(m, "webhook_id", None) is None
        and (partner is None or m.author.id == partner)
    ]
    if not after or any(m.id in handled for m in after):
        return []
    for m in after:
        if any(u.id == own_id for u in getattr(m, "mentions", ())):
            return []
    newest = after[-1].created_at
    if (now - newest).total_seconds() < QUIET:
        return []
    return after


def followup_reply(raw):
    """The reply only when Hermiswag says the messages are for it."""
    if not isinstance(raw, str) or len(raw) > 4096:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("for_me") is not True:
        return None
    reply = data.get("reply")
    return reply.strip() if isinstance(reply, str) and reply.strip() else None


def utcnow():
    return datetime.now(timezone.utc)
