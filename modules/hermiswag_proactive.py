"""Poliswag answers questions nobody answered.

Every minute, in the conversation channels the owner picked (convívio
and suporte-tecnico), a member's question that has waited at least 5
minutes (and at most three hours) with no reply gets an answer from
Hermiswag, as a plain reply: no ping and no "I saw you asked". Hermiswag
decides whether it really is an open question it can help with; an empty
reply means "leave it". Every message looked at is remembered in
state/hermiswag-proactive.json, so nothing is weighed or answered twice.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

MIN_WAIT = timedelta(minutes=5)
MAX_AGE = timedelta(hours=3)
PER_CHANNEL = 3  # questions weighed per channel per run
KEEP = 300  # message ids remembered per channel

PROACTIVE_RULES = (
    "Ninguém te mencionou: estás a ver se uma pergunta do canal ficou sem "
    "resposta há mais de 5 minutos. O campo text é essa mensagem e "
    "recent_channel_messages são as mensagens que vieram depois. Devolve "
    '{"reply": ""} (vazio) se não for uma pergunta sobre Pokémon GO, a '
    "comunidade, o servidor, o site ou um problema técnico do jogo; se for "
    "conversa entre amigos, uma pergunta dirigida a uma pessoa concreta ou "
    "retórica; ou se alguém já respondeu nas mensagens seguintes. Caso "
    "contrário, responde diretamente à pergunta, curto e útil: não menciones "
    "a pessoa, não digas que viste a pergunta nem que ninguém respondeu, não "
    "fales das mensagens anteriores. Como ninguém te chamou, só respondes "
    "com o que consegues apoiar: confirma primeiro com as tuas ferramentas "
    "(community_knowledge, map_query ou a pesquisa web nas fontes de "
    "confiança). Em problemas técnicos ou de conta podes sugerir as causas e "
    'soluções que costumam resultar ("costuma resolver…", "experimenta…"), '
    "desde que estejam confirmadas nessas fontes, e podes acabar a pedir um "
    "detalhe que ajude. O que não fazes é inventar: sem base nas fontes, "
    "devolve vazio. Nesta tarefa, em vez da regra da chave única, o JSON "
    "tem sempre estas três chaves: "
    '{"reply": "...", "confident": true ou false, "verified_with": '
    '"community_knowledge", "map_query", "web" ou ""}. Só é publicada com '
    "confident true (a resposta ou as sugestões estão apoiadas) e "
    "verified_with preenchido."
)

_QUESTION = re.compile(
    r"\?|^\s*(alguém|alguem|como|onde|quando|qual|quais|quem|porque|porquê|"
    r"por que|é possível|e possivel|sabem|sabes|há|ha alguma|existe|dá para|"
    r"da para|consigo|posso|vale a pena)\b",
    re.I,
)


def looks_like_question(text):
    text = (text or "").strip()
    return len(text) >= 12 and bool(_QUESTION.search(text))


def candidates(messages, now, handled, own_id):
    """Open questions in `messages` (oldest first), with the messages that
    followed each: [(message, following)], oldest first."""
    replied_to = {
        getattr(m.reference, "message_id", None)
        for m in messages
        if getattr(m, "reference", None) is not None
    }
    out = []
    for index, message in enumerate(messages):
        author = message.author
        age = now - message.created_at
        if (
            author.bot
            or getattr(message, "webhook_id", None) is not None
            or message.id in handled
            or message.id in replied_to
            or not MIN_WAIT <= age <= MAX_AGE
            or any(u.id == own_id for u in getattr(message, "mentions", ()))
            or not looks_like_question(message.content)
        ):
            continue
        out.append((message, messages[index + 1 : index + 11]))
    return out


class ProactiveState:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            data = json.loads(self.path.read_text())
            return {str(k): [int(x) for x in v] for k, v in data.items()}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        trimmed = {k: v[-KEEP:] for k, v in state.items()}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(trimmed))
        tmp.replace(self.path)


def utcnow():
    return datetime.now(timezone.utc)


# Repeated after the member data: the general rules ask for a single "reply"
# key, and without this the extra keys were often dropped.
FORMAT_REMINDER = (
    'Responde só com o JSON de três chaves: {"reply": "...", "confident": '
    'true/false, "verified_with": "community_knowledge"|"map_query"|"web"|""}.'
)

VERIFIED = {"community_knowledge", "map_query", "web"}


def confirmed_reply(raw):
    """The reply text only when Hermiswag says it is backed and names the
    tool it checked with; anything else (an unbacked guess, plain text) is
    dropped. Suggested fixes for a member's own problem are fine when the
    sources back them."""
    if not isinstance(raw, str) or len(raw) > 4096:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("confident") is not True:
        return None
    if data.get("verified_with") not in VERIFIED:
        return None
    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        return None
    return reply.strip()
