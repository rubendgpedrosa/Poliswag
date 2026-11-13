"""When a message is addressed to Poliswag.

Shared by main.py, which decides whether a message goes to the Mentions cog
before any command runs, and by that cog. Kept out of the cog so the entry
point does not import one.
"""

import re

import discord

from modules.config import Config
from modules.hermiswag_proactive import looks_like_question


def directed_mention(content, bot_id):
    """Only a visible, unescaped bot tag counts; ignore code/quoted literals."""
    if not isinstance(content, str):
        return False
    visible = re.sub(r"```.*?```", " ", content, flags=re.S)
    visible = re.sub(r"```.*", " ", visible, flags=re.S)
    visible = re.sub(r"`[^`\n]*`", " ", visible)
    lines = []
    in_block_quote = False
    for line in visible.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(">>>"):
            in_block_quote = True
            continue
        if in_block_quote or stripped.startswith(">"):
            continue
        lines.append(line)
    visible = "\n".join(lines)
    return bool(re.search(rf"(?<!\\)<@!?{bot_id}>", visible))


_NAMED = re.compile(r"(?<!\w)poli(?:swag|wag)?(?!\w)", re.I)


def named_in_staff_channel(message):
    """In the moderation channel, saying Poliswag's name, or asking a
    question, is enough: the team talks with it more directly there (owner,
    2026-11-13). Whether it then answers is Hermiswag's call."""
    if getattr(message, "guild", None) is None or message.author.bot:
        return False
    if getattr(message.channel, "id", None) != Config.MOD_CHANNEL_ID:
        return False
    content = message.content or ""
    if content.lstrip().startswith("!"):
        return False
    # A team question goes straight to the gate too, no wait (owner,
    # 2026-11-13); Hermiswag decides whether it is one for it.
    return bool(_NAMED.search(content)) or looks_like_question(content)


def replied_bot_message(message, bot_id):
    """The Poliswag chat message this guild message replies to, or None.

    Replying to one of Poliswag's conversational answers counts like a tag
    (owner, 2026-11-09). Only plain-text answers count: replying to an embed
    or a file (event boards, scanner cards, CSV lists, giveaway results) is
    talk between members, not a question for the bot. Commands never count.
    """
    if bot_id is None or getattr(message, "guild", None) is None:
        return None
    if (message.content or "").lstrip().startswith("!"):
        return None
    reference = getattr(message, "reference", None)
    replied = getattr(reference, "resolved", None) if reference else None
    if not isinstance(replied, discord.Message):
        return None
    if replied.author.id != bot_id or replied.embeds or replied.attachments:
        return None
    return replied if (replied.content or "").strip() else None
