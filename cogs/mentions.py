"""Mentions: private owner requests, bounded public community replies."""

import asyncio
import re

import discord
from discord.ext import commands

from modules.config import Config
from modules.embeds import react_to_request
from modules.conversation_model import ConversationModel
from modules.permissions import is_owner
from modules.public_conversation import MentionLimiter, PublicConversation
from modules.public_map_context import PublicMapContext

_GREETING = "Olá! Estou aqui — diz-me o que precisas. 🐸"


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


def named_mentions(text, message):
    """Turn member tags into names so the public model knows who is meant.

    The owner is always called faynn (never his account or real name); other
    tagged members become their public display name.
    """
    names = {Config.MY_ID: "faynn"} if Config.MY_ID > 0 else {}
    for member in getattr(message, "mentions", None) or ():
        if member.id not in names and not member.bot:
            name = getattr(member, "display_name", "") or ""
            name = re.sub(r"[<>@`*_~|]", "", name).strip()[:32]
            if name:
                names[member.id] = name

    def name_for(match):
        return names.get(int(match.group(1)), match.group(0))

    return re.sub(r"<@!?(\d+)>", name_for, text)


class Mentions(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        quest_search = poliswag.quest_search
        # Pass only the query interface and a copy of public species names.
        pokemon_names = dict(getattr(quest_search, "pokemon_name_map", {}) or {})
        self.public = PublicConversation(
            poliswag.db,
            quest_search.db,
            ConversationModel(Config.AGENT_JOBS_DIR),
            PublicMapContext(quest_search.db, pokemon_names),
        )
        self.limiter = MentionLimiter()

    async def handle(self, ctx):
        message = ctx.message
        user = self.poliswag.user
        if user is None or message.author.bot or message.webhook_id is not None:
            return False
        pattern = rf"<@!?{user.id}>"
        if not directed_mention(message.content, user.id):
            return False
        text = re.sub(pattern, "", message.content).strip()
        if ctx.guild is None and is_owner(ctx):
            agents = self.poliswag.get_cog("Agents")
            if not text:
                await self.respond(ctx, _GREETING)
            elif agents is None:
                await self.respond(
                    ctx, "Estou aqui, mas não consigo iniciar o pedido agora."
                )
            else:
                await agents.submit_natural_request(ctx, text)
            return True
        # Never permit conversational DMs, webhooks, or a forged author identity.
        if ctx.guild is None:
            return True
        if (
            not isinstance(ctx.author, discord.Member)
            or ctx.author.guild.id != ctx.guild.id
        ):
            return True
        if not self.limiter.allow(ctx.guild.id, ctx.author.id):
            return True
        await react_to_request(message)
        text = named_mentions(text, message)
        try:
            reply = await asyncio.wait_for(self.public.reply(text), timeout=42)
        except Exception:
            # Neither prompt text nor exception details are logged or published.
            reply = self.public.unavailable()
        await self.respond(ctx, reply)
        return True

    @staticmethod
    async def respond(ctx, text):
        try:
            await ctx.reply(
                text[:2000],
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass


async def setup(poliswag):
    await poliswag.add_cog(Mentions(poliswag))
