"""Helpful public replies and a small allowlist of public game data.

The model receives the member's sanitized question plus a bounded public map
snapshot. No filesystem, history, account, profile, or operational data enters
this path. Local calendar/statistics facts remain authoritative and errors
never enter a reply.
"""

import asyncio
import re
import time
import unicodedata
from collections import OrderedDict, deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord

# Only requests for private/operational material or code are rejected. Normal
# game words (shiny, Dragonite, Golbat, quest, raid, map) are allowed.
_PRIVATE = re.compile(
    r"(?:"
    r"(?:password|passwd|senha|token|secret|segredo|credential|credencia)\w*|"
    r"\b(?:accounts?|api|ssh|docker|sql|database|scanner|scan\w*|poracle|"
    r"claude|codex|openai|worker|root|deploy\w*|deployment|config\w*|"
    r"implement\w*|prompt\w*|logs?|files?|ficheiros?|source|"
    r"codigo(?:-fonte)?|code|script\w*|programa\w*|python|javascript|"
    r"privileg\w*|intern[oa]s?|bastidores|backstage)\b|"
    r"\.env\b|"
    r"\bsystem\s+prompt\b|\bprompt\s+do\s+sistema\b|"
    r"\b(?:a|as|minha|minhas|tua|tuas|sua|suas|nossa|vossa)\s+contas?\b|"
    r"\bcontas?\s+(?:de|do|da|dos|das|discord|jogo|pokemon|scanner)\b|"
    r"\bbase\s+de\s+dados\b|\bchave\s+(?:api|secreta|de\s+acesso)\b"
    r")"
)
_STATS = re.compile(r"\b(statistic\w*|estatistic\w*|stats?|numeros?)\b")
_HISTORICAL = re.compile(r"\b(statistic\w*|estatistic\w*|stats?)\b")
_HISTORY = re.compile(r"\b(ontem|yesterday|historic\w*|anterior\w*)\b")
_CURRENT = re.compile(r"\b(agora|atual\w*|neste\s+momento|hoje|live|ao\s+vivo)\b")
_EVENTS = re.compile(
    r"\b(event\w*|evento\w*|calendario\w*|calendar|community|spotlight|"
    r"dia\s+comunitario|proximos?\s+dias?)\b"
)
_CAPABILITY = re.compile(
    r"(?:"
    r"\b(?:can\s+you|podes|consegues)\s+(?:modify|alterar|editar|mudar|change|"
    r"programar|codar)\s+(?:yourself|your\s+code|your\s+source|te|"
    r"a\s+ti\s+mesmo|o\s+teu\s+codigo)\b|"
    r"\b(?:modify|alterar|editar|mudar|change)\s+(?:yourself|your\s+code|"
    r"your\s+source)\b|"
    r"\b(?:alterar|editar|mudar|modificar)-te\b|"
    r"\b(?:teu|seu|your)\s+(?:codigo|code|source|prompt|instructions|"
    r"instrucoes)\b|"
    r"\b(?:quem|who)\s+(?:pode|can|authorizes?|autoriza)\b[^?]{0,100}"
    r"\b(?:alterar|editar|mudar|code|codigo|acesso|permissoes|permissions|"
    r"owner|dono)\b|"
    r"\b(?:owner|dono|admin|administrador)\s+(?:only|apenas|pode|can)\b"
    r"[^?]{0,100}\b(?:code|codigo|alterar|editar|mudar|alterac\w*|mudanc\w*|"
    r"changes?|edits?|acesso|permissoes|permissions)\b|"
    r"\b(?:quem|who)\s+(?:e|is)\s+(?:o\s+)?(?:dono|owner|admin|administrador)\b|"
    r"\b(?:dono|owner|admin|administrador)\s+(?:do|of)\s+(?:bot|poliswag|"
    r"servidor|discord)\b|"
    r"\b(?:so|apenas|only)\s+(?:o\s+)?(?:dono|owner|admin|faynn)\b"
    r"[^?]{0,100}\b(?:alterac\w*|mudanc\w*|changes?|edits?)\b|"
    r"\b(?:are\s+you|es|e)\s+(?:a|an|um|uma)?\s*(?:model|bot|ai|llm|"
    r"language\s+model)\b|"
    r"\b(?:sou|i\s+am)\s+(?:a|an|um|uma)?\s*(?:model|llm|language\s+model|"
    r"ai)\b"
    r")",
    re.I,
)
_AREAS = {"Leiria": "Leiria", "MarinhaGrande": "Marinha Grande"}
_PRIVATE_REPLY = (
    "Posso ajudar com Pokémon GO e a comunidade PoGoLeiria: mapa, spawns, "
    "Raids, Quests, Eggs, Lures, eventos, dicas de jogo e links úteis. "
    "Diz-me o que queres saber sobre o jogo."
)
_NEUTRAL_HELP = (
    "Posso ajudar com Pokémon GO e a comunidade PoGoLeiria: mapa, spawns, "
    "Raids, Quests, Eggs, Lures, eventos e dicas de jogo. "
    "Podes ver o mapa em https://pogoleiria.pt/mapa."
)
_UNAVAILABLE = (
    "Não consegui preparar uma resposta útil agora. Tenta novamente daqui a "
    "pouco; entretanto podes consultar https://pogoleiria.pt/mapa."
)
_MAP_UNAVAILABLE = (
    "Não tenho agora uma amostra atualizada do mapa para responder a isso. "
    "Podes ver https://pogoleiria.pt/mapa ou tentar daqui a pouco."
)
_MODEL_UNAVAILABLE = (
    "Não consegui gerar uma resposta agora, mas podes consultar o mapa público "
    "em https://pogoleiria.pt/mapa e as quests em https://pogoleiria.pt/quests."
)
_GREETING = (
    "Olá! Sou o Poliswag, da comunidade PoGoLeiria. Pergunta-me sobre Pokémon "
    "GO, spawns, raids, quests, eventos ou o mapa de Leiria e Marinha Grande."
)

# Public output screening: game terms, numbers, accents and line breaks pass;
# code, credentials, internal paths, mentions and foreign links do not.
_MENTION = re.compile(r"<@[!&]?\d+>|@everyone|@here", re.I)
_URLISH = re.compile(r"(?:https?://|www\.)\S+", re.I)
_DOMAINISH = re.compile(r"(?<![\w.-])(?:[\w-]+\.)+[a-z]{2,}(?:/[^\s<>()]*)?", re.I)
_ALLOWED_LINK = re.compile(r"(?:https://)?pogoleiria\.pt(?:/[^\s<>()]*)?", re.I)
_SENSITIVE_OUTPUT = re.compile(
    r"(?:\b(?:password\w*|passwd|senha\w*|token\w*|secret\w*|segredo\w*|"
    r"credential\w*|credencia\w*|api[_-]?key|scanner|scan|poracle|"
    r"docker|kubernetes|ssh|database|sql|worker|accounts?|contas|logs?|"
    r"files?|ficheiros?|privileg\w*|intern[oa]s?|bastidores|backstage)\b|"
    r"\bconfig\w*\b|\bdeploy\w*\b|\bsource\s+code\b|\bcodigo-fonte\b|"
    r"\bsystem\s+prompt\b|\bprompt\s+do\s+sistema\b|"
    r"\.env\b|/root|/opt|/etc)",
    re.I,
)
_PRIVATE_CLAIM = re.compile(
    r"(?:"
    r"\b(?:posso|podes|i\s+can|can\s+you|nao|no)\s+(?:alterar|altero|editar|"
    r"edito|mudar|mudo|modificar|modifico|modify|change|edit)\s+(?:o\s+)?"
    r"(?:codigo|code|source|codigo-fonte)\b|"
    r"\b(?:alterar|altero|editar|edito|mudar|mudo|modificar|modifico|"
    r"programar|programo|escrever|escrevo|write|modify|change|edit)\b"
    r"[^.\n]{0,60}\b(?:codigo|code|source|codigo-fonte)\b|"
    r"\b(?:codigo|code|source|codigo-fonte)\b[^.\n]{0,60}"
    r"\b(?:privado|private|interno|internal|owner|dono|admin|alterar|editar|"
    r"mudar|modify|change|programar)\b|"
    r"\b(?:alterar|editar|mudar|modify|change)\s+(?:o\s+)?(?:teu|seu|your)\s+"
    r"(?:codigo|code|source)\b|"
    r"\bfaynn\b[^.\n]{0,80}\b(?:alterar|alterac\w*|editar|mudar|mudanc\w*|"
    r"modify|change|changes?|edits?|codigo|code|source|programar|autoriza|"
    r"authorizes|owner|admin|private|privado)\b|"
    r"\b(?:alterar|alterac\w*|editar|mudar|mudanc\w*|modify|change|changes?|"
    r"edits?|codigo|code|source|programar|autoriza|authorizes|owner|admin|"
    r"private|privado)\b[^.\n]{0,80}\bfaynn\b|"
    r"\b(?:so|apenas|only)\s+(?:o\s+)?faynn\b[^.\n]{0,80}\b(?:pode|can|"
    r"authorizes?|autoriza)\b|"
    r"\b(?:dono|owner|admin|administrador)\b[^.\n]{0,80}"
    r"\b(?:alterar|alterac\w*|editar|mudar|mudanc\w*|modify|change|changes?|"
    r"edits?|codigo|code|source|programar|permissoes|permissions)\b|"
    r"\b(?:alterar|alterac\w*|editar|mudar|mudanc\w*|modify|change|changes?|"
    r"edits?|codigo|code|source|programar)\b[^.\n]{0,80}"
    r"\b(?:dono|owner|admin|administrador)\b|"
    r"\b(?:agente|agent|codex|claude|openai|anthropic|worker|provider)\b"
    r"[^.\n]{0,80}\b(?:privado|private|owner|dono|job|tarefa)\b|"
    r"\b(?:sou|i\s+am)\s+(?:da|do|de|from|with)?\s*(?:openai|codex|claude|"
    r"anthropic)\b|"
    r"\b(?:sou|i\s+am)\s+(?:a|an|um|uma)?\s*(?:modelo|model|llm|ai|"
    r"language\s+model)\b"
    r")",
    re.I,
)
_ASSIGNMENT = re.compile(
    r"(?:password|passwd|senha|token|secret|segredo|api[_-]?key|db_\w+|env|"
    r"credential)\w*\s*[:=]\s*\S+",
    re.I,
)
_CODE = re.compile(
    r"(?:```|`|\b(?:SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE)\b|"
    r"\b(?:def|class|import|lambda|function|print)\b\s*[\(\{:]|"
    r"\b(?:int|void|float|double|char|bool)\s+\w+\s*\([^)]*\)\s*\{|"
    r"\b(?:const|let|var)\s+\w+\s*=|"
    r"\b\w+\s*\([^)]*\)\s*\{[^}]*\}|"
    r"\breturn\s*[;{}()\d\"']|"
    r"\b(?:sudo|apt-get|systemctl|docker)\b|\$\s)",
    re.I,
)
_INTERNAL_PATH = re.compile(
    r"(?:^|\s)(?:/root|/opt|/etc|/home|/var|/app|/usr|/proc|/sys|~/)",
    re.I,
)
_INTERNAL_FILE = re.compile(
    r"\b[\w./-]+\.(?:py|env|json|ya?ml|sql|log|sh|toml|ini)\b", re.I
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalized(text):
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(c)
    )


def private_topic(text):
    topic = normalized(text)
    # A Friend Code is game lingo; asking about the concept is public, while
    # actual codes/accounts remain private and are never supplied by the bot.
    topic = re.sub(r"\bfriend\s+code\b|\bcodigo\s+de\s+amigo\b", " ", topic)
    return bool(_PRIVATE.search(topic) or _CAPABILITY.search(topic))


def clean_question(text):
    """Strip controls and Discord mentions before a question leaves the bot."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _MENTION.sub(" ", text)
    text = "".join(
        c if c in "\n\t" or not unicodedata.category(c).startswith("C") else " "
        for c in text
    )
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:2000]


_LINK_TRAILING = ").,;:!?>\"]}'"


def _link_token(value):
    return value.rstrip(_LINK_TRAILING)


def _safe_links(value):
    if re.search(r"http://", value, re.I):
        return False
    for match in _URLISH.finditer(value):
        if not _ALLOWED_LINK.fullmatch(_link_token(match.group(0))):
            return False
    for match in _DOMAINISH.finditer(value):
        if not _ALLOWED_LINK.fullmatch(_link_token(match.group(0))):
            return False
    return True


def safe_reply(value):
    """Screen model text without defeating ordinary useful game answers."""
    if not isinstance(value, str):
        return None
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not 1 <= len(value) <= 1800:
        return None
    if _CONTROL.search(value):
        return None
    if any(unicodedata.category(c).startswith("C") for c in value if c != "\n"):
        return None
    normalized_value = re.sub(
        r"\bfriend\s+code\b|\bcodigo\s+de\s+amigo\b", " ", normalized(value)
    )
    if (
        _SENSITIVE_OUTPUT.search(normalized_value)
        or _PRIVATE_CLAIM.search(normalized_value)
        or _ASSIGNMENT.search(value)
    ):
        return None
    if _CODE.search(value) or _INTERNAL_PATH.search(value):
        return None
    if _INTERNAL_FILE.search(value):
        return None
    if re.search(r"@everyone|@here|<@[!&]?\d+>|(?:^|\s)@\w+", value, re.I):
        return None
    if not _safe_links(value):
        return None
    if re.search(r"\b(?:sk-|ghp_|xox[baprs]-|AKIA|eyJ)[A-Za-z0-9._-]{8,}", value):
        return None
    if re.search(r"\b[A-Z][A-Z0-9_]{7,}\b", value):
        return None
    if re.search(r"\S{200,}", value):
        return None
    return value


def _combine(line, facts):
    """Keep the model's answer first and leave room for local facts."""
    if not facts:
        return line
    combined = f"{line}\n\n{facts}"
    if len(combined) <= 2000:
        return combined
    room = max(0, 2000 - len(line) - 2)
    return f"{line}\n\n{facts[:room]}".rstrip()


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
    def __init__(self, event_db, game_stats_db, model=None, map_context=None):
        # Receive only query interfaces and the bounded map helper, never the
        # bot, services or Config.
        self.event_db = event_db
        self.game_stats_db = game_stats_db
        self.model = model
        self.map_context = map_context

    async def reply(self, text):
        if not isinstance(text, str) or len(text) > 2000 or private_topic(text):
            return _PRIVATE_REPLY
        question = clean_question(text)
        if not question:
            return _GREETING
        topic = normalized(question)
        kind = "greeting"
        if _STATS.search(topic) and (
            _HISTORY.search(topic)
            or (_HISTORICAL.search(topic) and not _CURRENT.search(topic))
        ):
            kind = "statistics"
        elif _EVENTS.search(topic):
            kind = "events"
        snapshot = None
        if self.map_context is not None:
            try:
                snapshot = await asyncio.wait_for(
                    self.map_context.snapshot(question), timeout=5
                )
            except Exception:
                snapshot = None
            if snapshot is not None and kind == "greeting":
                kind = "map"
        facts = None
        if kind == "statistics":
            try:
                facts = await asyncio.wait_for(self.statistics(), timeout=5)
            except Exception:
                facts = None
        elif kind == "events":
            try:
                facts = await asyncio.wait_for(self.events(), timeout=5)
            except Exception:
                facts = None
        facts_payload = (
            {"kind": kind, "text": facts[:2000]}
            if kind in {"events", "statistics"} and isinstance(facts, str) and facts
            else None
        )
        model_context = (
            {"snapshot": snapshot, "facts": facts_payload}
            if snapshot is not None or facts_payload is not None
            else None
        )
        line = None
        unsafe = False
        if self.model is not None:
            try:
                raw = await self.model.line(kind, text=question, context=model_context)
                line = safe_reply(raw)
                unsafe = isinstance(raw, str) and bool(raw.strip()) and line is None
            except Exception:
                line = None
        if (
            line
            and facts
            and re.search(
                r"\b(não tenho|nao tenho|sem dados|não há dados|desconheço)\b",
                line,
                re.I,
            )
        ):
            line = None
        if line:
            return _combine(line, facts)
        if unsafe:
            return facts if facts is not None else _NEUTRAL_HELP
        if facts is not None:
            return facts
        if kind == "map":
            return _MODEL_UNAVAILABLE if snapshot is not None else _MAP_UNAVAILABLE
        return _UNAVAILABLE

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
            else "Ainda não tenho eventos confirmados para os próximos dias."
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
            else "Ainda não tenho números do jogo para ontem."
        )

    @staticmethod
    def unavailable():
        return _UNAVAILABLE
