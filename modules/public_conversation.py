"""Public banter and a small allowlist of game data.

The model receives only a fixed topic, never user text or database rows.
No filesystem, history, profile, or operational snapshot enters this
path. Facts are rendered locally; errors never enter a reply.
"""

import asyncio
import random
import re
import time
import unicodedata
from collections import OrderedDict, deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord

_PRIVATE = re.compile(
    r"\b(account\w*|conta\w*|scanner\w*|scan\w*|codigo\w*|code\w*|"
    r"implement\w*|intern\w*|env\w*|credential\w*|credencia\w*|"
    r"password\w*|senha\w*|token\w*|secret\w*|segredo\w*|api\w*|"
    r"chave\w*|database\w*|sql\w*|docker\w*|claude\w*|codex\w*|"
    r"openai\w*|shiny\w*|log\w*|ficheiro\w*|file\w*|prompt\w*|"
    r"system\w*|sistema\w*|root\w*|worker\w*|rotom\w*|dragonite\w*|"
    r"golbat\w*|ssh\w*|poracle\w*)\b"
)
_STATS = re.compile(r"\b(estatistica\w*|stat\w*|numero\w*|total\w*)\b")
_EVENTS = re.compile(
    r"\b(event\w*|evento\w*|calendario\w*|community|spotlight|raid\w*|"
    r"hoje|amanha|semana|today|tomorrow)\b"
)
_AREAS = {"Leiria": "Leiria", "MarinhaGrande": "Marinha Grande"}
_JOKES = (
    "Chamaste? Já estava a treinar o meu melhor ataque: conversa fiada. 🐸",
    "Presente! Ainda não sou lendário, mas já tenho estilo. 🐸",
    "Olá! Tenho mais conversa do que Poké Balls. Que se passa? 🐸",
    "Apareci! Agora só falta alguém atirar uma Poké Ball. 😄",
)
_PRIVATE_REPLY = (
    "Hoje só trago boa disposição, eventos e números públicos do jogo. "
    "Os bastidores ficam fora da conversa. 🐸"
)
_UNAVAILABLE = "Hoje os números estão tímidos. Tenta de novo daqui a pouco! 🐸"


def normalized(text):
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(c)
    )


def private_topic(text):
    return bool(_PRIVATE.search(normalized(text)))


def safe_banter(value):
    """Model text cannot carry numbers, links, code, identifiers or internals."""
    if not isinstance(value, str) or not 1 <= len(value) <= 180:
        return None
    if private_topic(value) or re.search(r"\S{30,}|[\d@`<>/\\=_{}\[\]]", value):
        return None
    if re.search(r"https?\b|www\.|\w+\.\w+|\b[A-Z]{3,}\b", value):
        return None
    if any(unicodedata.category(c).startswith("C") for c in value):
        return None
    return discord.utils.escape_markdown(value)


def event_name(value):
    """Only public calendar titles, never raw rows/links/extra_data."""
    if not isinstance(value, str) or not 1 <= len(value) <= 160:
        return None
    if private_topic(value) or re.search(r"\S{40,}", value):
        return None
    if any(not (c.isalnum() or c in " '’()!:&.,+-") for c in value):
        return None
    return discord.utils.escape_markdown(value)


def event_time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise ValueError("Not a calendar date")
    lisbon = ZoneInfo("Europe/Lisbon")
    return (
        value.replace(tzinfo=lisbon)
        if value.tzinfo is None
        else value.astimezone(lisbon)
    )


def public_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if 0 <= number <= 10**12 and number == value else None


class MentionLimiter:
    """Bounded cooldown storage: one reply per member/15s, five per guild/30s."""

    def __init__(self):
        self.members = OrderedDict()
        self.guilds = OrderedDict()

    def allow(self, guild_id, member_id):
        now = time.monotonic()
        key = (guild_id, member_id)
        if now - self.members.get(key, -1000) < 15:
            return False
        bucket = self.guilds.setdefault(guild_id, deque())
        while bucket and now - bucket[0] >= 30:
            bucket.popleft()
        if len(bucket) >= 5:
            return False
        self.members[key] = now
        self.members.move_to_end(key)
        bucket.append(now)
        self.guilds.move_to_end(guild_id)
        while len(self.members) > 1000:
            self.members.popitem(last=False)
        while len(self.guilds) > 100:
            self.guilds.popitem(last=False)
        return True


class PublicConversation:
    def __init__(self, event_db, game_stats_db, model=None):
        # Receive only the two query interfaces, never the bot/services/Config.
        self.event_db = event_db
        self.game_stats_db = game_stats_db
        self.model = model

    async def reply(self, text):
        if len(text) > 2000 or private_topic(text):
            return _PRIVATE_REPLY
        topic = normalized(text)
        facts = None
        if _STATS.search(topic):
            facts = await asyncio.wait_for(self.statistics(), timeout=5)
            kind = "statistics"
        elif _EVENTS.search(topic):
            facts = await asyncio.wait_for(self.events(), timeout=5)
            kind = "events"
        else:
            kind = "greeting"
        line = None
        if self.model is not None:
            try:
                line = safe_banter(await self.model.line(kind))
            except Exception:
                pass
        if facts is not None:
            return f"{line}\n\n{facts}" if line else facts
        return line or random.choice(_JOKES)

    async def events(self):
        now = datetime.now(ZoneInfo("Europe/Lisbon"))
        rows = await self.event_db.get_data_from_database(
            "SELECT name, start, end FROM event "
            "WHERE end > %s AND start < %s ORDER BY start LIMIT 10",
            params=(
                now.replace(tzinfo=None),
                (now + timedelta(days=7)).replace(tzinfo=None),
            ),
        )
        lines = []
        for row in rows:
            name = event_name(row.get("name"))
            if not name:
                continue
            try:
                start, end = event_time(row.get("start")), event_time(row.get("end"))
            except (ValueError, TypeError):
                continue
            if end <= now or start >= now + timedelta(days=7) or end <= start:
                continue
            when = "em curso" if start <= now else start.strftime("%d/%m às %H:%M")
            lines.append(f"• **{name}** — {when}, até {end.strftime('%d/%m às %H:%M')}")
            if len(lines) == 5:
                break
        return (
            "📅 Eventos em curso e nos próximos 7 dias (hora de Portugal):\n"
            + "\n".join(lines)
            if lines
            else "Ainda não tenho eventos confirmados para os próximos dias. 🐸"
        )

    async def statistics(self):
        day = (datetime.now(ZoneInfo("Europe/Lisbon")) - timedelta(days=1)).date()
        pokemon = await self.game_stats_db.get_data_from_database(
            "SELECT area, SUM(count) AS total FROM pokemon_stats "
            "WHERE date = %s AND area IN (%s, %s) GROUP BY area",
            params=(day, *_AREAS),
        )
        raids = await self.game_stats_db.get_data_from_database(
            "SELECT area, SUM(count) AS total FROM raid_stats "
            "WHERE date = %s AND area IN (%s, %s) GROUP BY area",
            params=(day, *_AREAS),
        )

        def counts(rows):
            out = {}
            for row in rows:
                area, number = row.get("area"), public_number(row.get("total"))
                if area in _AREAS and number is not None:
                    out[area] = number
            return out

        pokemon, raids = counts(pokemon), counts(raids)
        lines = []
        for area, label in _AREAS.items():
            if area not in pokemon and area not in raids:
                continue
            p = (
                f"{pokemon[area]:,}".replace(",", " ")
                if area in pokemon
                else "sem dados"
            )
            r = f"{raids[area]:,}".replace(",", " ") if area in raids else "sem dados"
            lines.append(f"• **{label}**: {p} Pokémon · {r} raids")
        return (
            f"📊 Números do jogo de ontem ({day.strftime('%d/%m')}):\n"
            + "\n".join(lines)
            if lines
            else "Ainda não tenho números do jogo para ontem. 🐸"
        )

    @staticmethod
    def unavailable():
        return _UNAVAILABLE
