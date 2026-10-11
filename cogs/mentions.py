"""Mentions: private owner requests, bounded public community replies."""

import asyncio
import re

import discord
from discord.ext import commands

from modules.config import Config
from modules.conversation_model import ConversationModel
from modules.permissions import is_owner
from modules.public_conversation import MentionLimiter, PublicConversation

_GREETING = "Olá! Estou aqui — diz-me o que precisas. 🐸"


class Mentions(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.public = PublicConversation(
            poliswag.db,
            poliswag.quest_search.db,
            ConversationModel(Config.AGENT_JOBS_DIR),
        )
        self.limiter = MentionLimiter()

    async def handle(self, ctx):
        message = ctx.message
        user = self.poliswag.user
        if user is None or message.author.bot or message.webhook_id is not None:
            return False
        pattern = rf"<@!?{user.id}>"
        if not re.search(pattern, message.content):
            return False
        text = re.sub(pattern, "", message.content).strip()
        if is_owner(ctx):
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
                text[:1900],
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass


async def setup(poliswag):
    await poliswag.add_cog(Mentions(poliswag))
