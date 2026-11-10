"""Sorteios por reação: quem reage com o emoji entra (e quem esteve no
Campfire), à hora marcada o bot tira um nome e um mod decide se o publica.

Uma linha por sorteio na tabela `giveaway` (migrações 042 e 043), chave o id
da mensagem do anúncio. Os participantes do Discord nunca são guardados: são
as reações da própria mensagem, lidas na hora (quem tira a reação sai). Os do
Campfire ficam em `campfire_names`, lidos das capturas.

Estados: `open` espera pela hora; `drawn` com `awaiting_review = 1` espera
que um mod decida na mensagem de revisão (`pending_ids` = quem saiu);
`drawn` com `awaiting_review = 0` está publicado (`winner_ids`); `cancelled`.
Cada UPDATE só vale no estado esperado (e na mensagem de revisão atual), por
isso dois cliques ou dois ticks nunca publicam duas vezes.

Uma entrada é um int (utilizador do Discord) ou um str (treinador do
Campfire que não corresponde a ninguém que reagiu).
"""

import json
import re
import secrets
from datetime import datetime, timezone

# https://discord.com/channels/<guild>/<channel>/<message> (também ptb./canary.)
_LINK = re.compile(
    r"https?://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/"
    r"(\d+|@me)/(\d+)/(\d+)"
)
# <t:1794348000> ou <t:1794348000:f>
_TIMESTAMP = re.compile(r"<t:(\d{9,11})(?::[tTdDfFR])?>")
_CUSTOM_EMOJI = re.compile(r"<a?:\w{2,32}:\d{15,21}>")

INSERT_SQL = (
    "INSERT INTO giveaway (message_id, channel_id, guild_id, emoji, draw_at, "
    "winners, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s) "
    "ON DUPLICATE KEY UPDATE channel_id = VALUES(channel_id), "
    "guild_id = VALUES(guild_id), emoji = VALUES(emoji), "
    "draw_at = VALUES(draw_at), winners = VALUES(winners), "
    "created_by = VALUES(created_by), status = 'open', winner_ids = NULL, "
    "participants = NULL, drawn_at = NULL, awaiting_review = 0, "
    "pending_ids = NULL, excluded_ids = NULL, review_message_id = NULL, "
    "announcement_reply_id = NULL, replacing_ids = NULL"
)
SELECT_SQL = "SELECT * FROM giveaway WHERE message_id = %s"
OPEN_SQL = "SELECT * FROM giveaway WHERE status = 'open' ORDER BY draw_at"
DUE_SQL = (
    "SELECT * FROM giveaway WHERE status = 'open' AND draw_at <= UTC_TIMESTAMP() "
    "ORDER BY draw_at"
)
# Abertos ou já sorteados (em revisão ou publicados), o mais recente primeiro.
RECENT_SQL = (
    "SELECT * FROM giveaway WHERE status IN ('open', 'drawn') "
    "ORDER BY COALESCE(drawn_at, created_at) DESC LIMIT 5"
)
# Abre uma revisão: de `open` (o sorteio), de publicado (`!sorteio reroll`) ou
# de uma revisão (nova mensagem). Guardado pelo estado de onde parte.
REVIEW_SQL = (
    "UPDATE giveaway SET status = 'drawn', awaiting_review = 1, "
    "pending_ids = %s, replacing_ids = %s, participants = %s, "
    "review_message_id = NULL, "
    "drawn_at = UTC_TIMESTAMP() "
    "WHERE message_id = %s AND status = %s AND awaiting_review = %s"
)
REVIEW_MESSAGE_SQL = (
    "UPDATE giveaway SET review_message_id = %s "
    "WHERE message_id = %s AND awaiting_review = 1 AND review_message_id IS NULL"
)
# Os botões só valem na mensagem de revisão atual e sobre o nome que mostram.
REROLL_SQL = (
    "UPDATE giveaway SET pending_ids = %s, excluded_ids = %s, participants = %s "
    "WHERE message_id = %s AND awaiting_review = 1 AND review_message_id = %s "
    "AND pending_ids <=> %s"
)
PUBLISH_SQL = (
    "UPDATE giveaway SET awaiting_review = 0, winner_ids = %s, excluded_ids = %s, "
    "pending_ids = NULL, replacing_ids = NULL "
    "WHERE message_id = %s AND awaiting_review = 1 AND review_message_id = %s "
    "AND pending_ids <=> %s"
)
# Cancelar uma revisão: sem vencedor publicado o sorteio fica cancelado; depois
# de um reroll, volta ao vencedor que já estava publicado.
STOP_SQL = (
    "UPDATE giveaway SET awaiting_review = 0, pending_ids = NULL, "
    "replacing_ids = NULL, status = IF(winner_ids IS NULL, 'cancelled', 'drawn') "
    "WHERE message_id = %s AND awaiting_review = 1 AND review_message_id <=> %s"
)
CANCEL_SQL = (
    "UPDATE giveaway SET status = 'cancelled' WHERE message_id = %s "
    "AND status = 'open'"
)
REPLY_SQL = "UPDATE giveaway SET announcement_reply_id = %s WHERE message_id = %s"
CAMPFIRE_SQL = "UPDATE giveaway SET campfire_names = %s WHERE message_id = %s"
# Treinadores associados a uma conta do Discord (`!shiny <código>`), para que
# quem reagiu e também esteve no Campfire conte uma só vez.
TRAINERS_SQL = (
    "SELECT discord_id, trainer_name FROM shiny_link "
    "WHERE status = 'linked' AND trainer_name IS NOT NULL"
)


def parse_link(text):
    """(channel_id, message_id) do primeiro link de mensagem, ou None."""
    links = parse_links(text)
    return links[0] if links else None


def parse_links(text):
    """Todos os (channel_id, message_id) de links de mensagem no texto."""
    return [(int(m.group(2)), int(m.group(3))) for m in _LINK.finditer(text or "")]


def parse_draw_at(text):
    """O primeiro `<t:…>` (ou um número unix sozinho) como datetime UTC sem
    tzinfo, que é como a coluna DATETIME o guarda."""
    text = (text or "").strip()
    match = _TIMESTAMP.search(text)
    if match:
        seconds = int(match.group(1))
    elif re.fullmatch(r"\d{9,11}", text):
        seconds = int(text)
    else:
        return None
    return datetime.fromtimestamp(seconds, timezone.utc).replace(tzinfo=None)


def find_emoji(text):
    """O primeiro emoji personalizado do texto (`<:pikaswag:123>`), ou None."""
    match = _CUSTOM_EMOJI.search(text or "")
    return match.group(0) if match else None


def unix(moment):
    """Segundos unix de um DATETIME UTC lido da base de dados."""
    return int(moment.replace(tzinfo=timezone.utc).timestamp())


def entries(value):
    """Uma lista guardada numa coluna: JSON (desde 043) ou `1,2,3` (042)."""
    text = str(value or "").strip()
    if not text:
        return []
    if text.startswith("["):
        return [e for e in json.loads(text) if isinstance(e, (int, str)) and e != ""]
    return [int(x) for x in text.split(",") if x.strip()]


def dump(values):
    """O inverso de `entries`: None quando a lista está vazia."""
    values = list(values)
    return json.dumps(values, ensure_ascii=False) if values else None


def winner_ids(row):
    return entries(row.get("winner_ids"))


def in_review(row):
    return row.get("status") == "drawn" and bool(row.get("awaiting_review"))


def name_key(name):
    """Como dois nomes de treinador se comparam: sem maiúsculas nem espaços."""
    return re.sub(r"\s+", "", str(name or "")).casefold()


def merge_names(*lists):
    """Junta listas de nomes sem repetir (pela `name_key`), na ordem em que
    aparecem pela primeira vez."""
    seen, merged = set(), []
    for names in lists:
        for name in names:
            name = str(name).strip()
            key = name_key(name)
            if key and key not in seen:
                seen.add(key)
                merged.append(name)
    return merged


def pool(reactors, campfire, aliases=None, blocked_names=()):
    """Todos os participantes, cada pessoa uma vez.

    `reactors`: ids do Discord de quem reagiu. `campfire`: nomes lidos do
    Campfire. `aliases`: {name_key: discord_id} (nomes do Discord e
    treinadores associados); um nome do Campfire que corresponda a quem reagiu
    não conta outra vez. `blocked_names`: name_keys de quem não pode ganhar
    (o autor do anúncio, quem registou). Devolve (entradas, nomes do Campfire
    que já estavam pela reação)."""
    aliases = aliases or {}
    reacted = set(reactors)
    blocked = set(blocked_names)
    result = list(dict.fromkeys(reactors))
    duplicates = []
    for name in merge_names(campfire):
        key = name_key(name)
        if key in blocked:
            continue
        if aliases.get(key) in reacted:
            duplicates.append(name)
            continue
        result.append(name)
    return result, duplicates


def pick(candidates, count, exclude=(), avoid=()):
    """`count` vencedores ao acaso (secrets, não random) entre os candidatos
    fora de `exclude`. `avoid` (quem está a ser substituído num reroll normal)
    só sai outra vez se não houver mais ninguém."""
    excluded = set(exclude)
    available = [c for c in candidates if c not in excluded]
    fresh = [c for c in available if c not in set(avoid)]
    if len(fresh) >= min(count, len(available)):
        available = fresh
    return secrets.SystemRandom().sample(available, min(count, len(available)))


def show(entry):
    """Como uma entrada aparece numa mensagem: menção ou nome do Campfire."""
    if isinstance(entry, int):
        return f"<@{entry}>"
    return f"**{entry}** (Campfire)"


def message_link(row):
    return (
        f"https://discord.com/channels/{row['guild_id']}/"
        f"{row['channel_id']}/{row['message_id']}"
    )
