"""Mentions: private owner requests, bounded public community replies."""

import asyncio
import io
import re

import discord
from discord.ext import commands, tasks

from modules import scan_areas
from modules.config import Config
from modules.conversation_model import (
    AGENT_INSTRUCTIONS,
    ConversationModel,
    clean_history,
    public_prompt,
)
from modules.embeds import react_to_request
from modules.hermiswag_client import HermiswagClient
from modules.hermiswag_followup import (
    FOLLOWUP_FORMAT,
    FOLLOWUP_RULES,
    ActiveChannels,
    followup_reply,
    new_since_poliswag,
)
from modules.hermiswag_learning import (
    FIRST_RUN,
    LEARN_INSTRUCTIONS,
    MAX_PER_CHANNEL,
    LearningState,
    batches,
    learnable,
)
from modules.hermiswag_proactive import (
    FORMAT_REMINDER,
    PER_CHANNEL,
    PROACTIVE_RULES,
    ProactiveState,
    candidates,
    confirmed_reply,
    utcnow,
)
from modules.permissions import is_owner
from modules.public_conversation import MentionLimiter, PublicConversation, safe_reply
from modules.public_dumps import PublicDumps, dump_request
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
        self.hermiswag = HermiswagClient(
            Config.HERMISWAG_API_URL, Config.HERMISWAG_API_KEY
        )
        self.learning = LearningState(Config.HERMISWAG_LEARNING_STATE)
        self.proactive_state = ProactiveState(Config.HERMISWAG_PROACTIVE_STATE)
        self.active = ActiveChannels()
        self.followup_handled = set()
        self.public = PublicConversation(
            poliswag.db,
            quest_search.db,
            ConversationModel(Config.AGENT_JOBS_DIR, self.hermiswag),
            PublicMapContext(quest_search.db, pokemon_names),
        )
        self.dumps = PublicDumps(quest_search.db, self.public.map_context)
        self.limiter = MentionLimiter()

    async def handle(self, ctx):
        message = ctx.message
        user = self.poliswag.user
        if user is None or message.author.bot or message.webhook_id is not None:
            return False
        pattern = rf"<@!?{user.id}>"
        replied = None
        if not directed_mention(message.content, user.id):
            replied = replied_bot_message(message, user.id)
            if replied is None:
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
            await scan_areas.ensure()
        except Exception:
            pass
        dump = dump_request(text)
        if dump is not None:
            await self.send_dump(ctx, *dump)
            return True
        # Every channel keeps its conversation (owner, 2026-11-09): one
        # Hermiswag session per channel per day plus the channel's recent
        # messages, so a follow-up never needs the whole context again.
        session = HermiswagClient.conversation(ctx.channel.id)
        history = await self.channel_history(ctx) or []
        if replied is not None and not any(
            item["text"] == replied.content.strip() for item in history
        ):
            # A reply to an older Poliswag answer: that answer is the context.
            history.insert(0, {"author": "Poliswag", "text": replied.content[:300]})
        history = history or None
        try:
            # Map data (5 s) plus the model wait (PUBLIC_WAIT_SECONDS) and a margin.
            reply = await asyncio.wait_for(
                self.public.reply(text, session=session, history=history),
                timeout=65,
            )
        except Exception:
            # Neither prompt text nor exception details are logged or published.
            reply = self.public.unavailable()
        await self.respond(ctx, reply)
        self.active.spoke(ctx.channel.id, invited=True, partner=ctx.author.id)
        return True

    async def cog_load(self):
        if self.hermiswag.enabled:
            self.learn.start()
            self.proactive.start()
            self.followup.start()

    async def cog_unload(self):
        self.learn.cancel()
        self.proactive.cancel()
        self.followup.cancel()

    @tasks.loop(minutes=10)
    async def proactive(self):
        """Answer the questions nobody answered (hermiswag_proactive)."""
        user = self.poliswag.user
        if user is None:
            return
        state = self.proactive_state.load()
        now = utcnow()
        for channel_id in Config.HERMISWAG_PROACTIVE_CHANNELS:
            channel = self.poliswag.get_channel(channel_id)
            if channel is None:
                continue
            handled = state.setdefault(str(channel_id), [])
            try:
                messages = [m async for m in channel.history(limit=60)]
                messages.reverse()
                for message, following in candidates(
                    messages, now, set(handled), user.id
                )[:PER_CHANNEL]:
                    handled.append(message.id)
                    self.proactive_state.save(state)
                    await self._answer_open_question(channel, message, following)
            except Exception as error:
                self.poliswag.utility.log_to_file(
                    f"Hermiswag proactive #{getattr(channel, 'name', channel_id)} "
                    f"failed: {type(error).__name__}",
                    "ERROR",
                )

    @tasks.loop(minutes=1)
    async def followup(self):
        """Continue a conversation Poliswag is in (hermiswag_followup)."""
        user = self.poliswag.user
        if user is None:
            return
        now = utcnow()
        for channel_id in self.active.due():
            channel = self.poliswag.get_channel(channel_id)
            if channel is None:
                continue
            try:
                messages = [m async for m in channel.history(limit=30)]
                messages.reverse()
                new = new_since_poliswag(
                    messages,
                    user.id,
                    self.followup_handled,
                    now,
                    partner=self.active.partner(channel_id),
                )
                if not new:
                    continue
                self.followup_handled.update(m.id for m in new)
                if len(self.followup_handled) > 2000:
                    self.followup_handled = set(list(self.followup_handled)[-1000:])
                await self._continue_conversation(channel, messages, new)
            except Exception as error:
                self.poliswag.utility.log_to_file(
                    f"Hermiswag follow-up #{getattr(channel, 'name', channel_id)} "
                    f"failed: {type(error).__name__}",
                    "ERROR",
                )

    @followup.before_loop
    async def before_followup(self):
        await self.poliswag.wait_until_ready()

    async def _continue_conversation(self, channel, messages, new):
        own = self.poliswag.user.id
        first_new = messages.index(new[0])
        last_new = messages.index(new[-1])
        history = []
        # Context: what came before, plus what others said in between.
        context = messages[max(0, first_new - 15) : last_new + 1]
        for item in [m for m in context if m not in new]:
            body = named_mentions(item.content or "", item).strip()
            if (
                body
                and item.webhook_id is None
                and (not item.author.bot or item.author.id == own)
            ):
                history.append(
                    {
                        "author": (
                            "Poliswag"
                            if item.author.id == own
                            else item.author.display_name
                        ),
                        "text": body,
                    }
                )
        lines = [
            f"{m.author.display_name}: {named_mentions(m.content or '', m).strip()}"
            for m in new
            if (m.content or "").strip()
        ]
        if not lines:
            return
        payload = {
            "text": "\n".join(lines)[:2000],
            "snapshot": None,
            "facts": None,
            "recent_channel_messages": clean_history(history),
        }
        raw = await self.hermiswag.ask(
            HermiswagClient.conversation(channel.id),
            AGENT_INSTRUCTIONS + "\n\n" + FOLLOWUP_RULES,
            public_prompt("greeting", payload) + "\n\n" + FOLLOWUP_FORMAT,
        )
        reply = safe_reply(followup_reply(raw) or "")
        if not reply:
            return
        try:
            await new[-1].reply(
                reply,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            self.active.followed_up(channel.id)
        except discord.HTTPException:
            pass

    @proactive.before_loop
    async def before_proactive(self):
        await self.poliswag.wait_until_ready()

    async def _answer_open_question(self, channel, message, following):
        text = named_mentions(message.content or "", message).strip()
        history = []
        for item in following:
            body = named_mentions(item.content or "", item).strip()
            if body and item.webhook_id is None:
                own = item.author.id == self.poliswag.user.id
                history.append(
                    {
                        "author": "Poliswag" if own else item.author.display_name,
                        "text": body,
                    }
                )
        payload = {
            "text": text[:2000],
            "snapshot": None,
            "facts": None,
            "recent_channel_messages": clean_history(history),
        }
        raw = await self.hermiswag.ask(
            HermiswagClient.conversation(channel.id),
            AGENT_INSTRUCTIONS + "\n\n" + PROACTIVE_RULES,
            public_prompt("greeting", payload) + "\n\n" + FORMAT_REMINDER,
        )
        reply = safe_reply(confirmed_reply(raw) or "")
        if not reply:
            return
        try:
            await message.reply(
                reply,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            self.active.spoke(channel.id, invited=False, partner=message.author.id)
        except discord.HTTPException:
            pass

    @tasks.loop(hours=6)
    async def learn(self):
        """Hand Hermiswag the new messages of the learning channels."""
        state = self.learning.load()
        for channel_id in Config.HERMISWAG_LEARN_CHANNELS:
            channel = self.poliswag.get_channel(channel_id)
            if channel is None:
                continue
            try:
                after = state.get(str(channel_id))
                if after:
                    items = [
                        m
                        async for m in channel.history(
                            limit=MAX_PER_CHANNEL,
                            after=discord.Object(id=after),
                            oldest_first=True,
                        )
                    ]
                else:
                    items = [m async for m in channel.history(limit=FIRST_RUN)]
                    items.reverse()
                lines = []
                conversation = channel_id in Config.HERMISWAG_SESSION_CHANNELS
                for item in items:
                    if not learnable(item, self.poliswag.user, conversation):
                        continue
                    text = named_mentions(item.content or "", item).strip()
                    if text:
                        lines.append((item.author.display_name, text))
                for prompt in batches(channel.name, lines):
                    answer = await self.hermiswag.ask(
                        "hermiswag:learning", LEARN_INSTRUCTIONS, prompt, timeout=180
                    )
                    if answer is None:
                        raise RuntimeError("learning run failed")
                if items:
                    state[str(channel_id)] = items[-1].id
                    self.learning.save(state)
            except Exception as error:
                self.poliswag.utility.log_to_file(
                    f"Hermiswag learning #{getattr(channel, 'name', channel_id)} "
                    f"failed: {type(error).__name__}",
                    "ERROR",
                )

    @learn.before_loop
    async def before_learn(self):
        await self.poliswag.wait_until_ready()

    async def channel_history(self, ctx, limit=15):
        """The channel's last messages before this one, oldest first, as
        {"author", "text"}: Poliswag's own replies included, other bots,
        webhooks and empty messages left out. Tags become names."""
        out = []
        try:
            async for item in ctx.channel.history(limit=limit, before=ctx.message):
                if item.webhook_id is not None:
                    continue
                own = (
                    self.poliswag.user is not None
                    and item.author.id == self.poliswag.user.id
                )
                if item.author.bot and not own:
                    continue
                text = named_mentions(item.content or "", item).strip()
                if not text:
                    continue
                author = "Poliswag" if own else item.author.display_name
                out.append({"author": author, "text": text})
        except Exception:
            return None
        return list(reversed(out)) or None

    async def send_dump(self, ctx, kind, area):
        try:
            built = await asyncio.wait_for(self.dumps.build(kind, area), timeout=15)
        except Exception:
            built = None
        if built is None:
            await self.respond(
                ctx,
                "Poli-poli! 💧 Não consegui montar essa lista agora. Tenta daqui a pouco.",
            )
            return
        name, data, _, caption = built
        try:
            await ctx.reply(
                caption,
                file=discord.File(io.BytesIO(data), filename=name),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass

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
