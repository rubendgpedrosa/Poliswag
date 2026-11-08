"""Sorteios por reação: quem reage com o emoji entra, e à hora marcada o bot
sorteia o vencedor.

Uma linha por sorteio na tabela `giveaway` (migração 042), chave o id da
mensagem do anúncio. Os participantes nunca são guardados: são as reações da
própria mensagem, lidas na hora (quem tira a reação sai). Só os vencedores
ficam na linha, para que `!sorteio repetir` não volte a tirar o mesmo.
"""

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
    "participants = NULL, drawn_at = NULL"
)
SELECT_SQL = "SELECT * FROM giveaway WHERE message_id = %s"
OPEN_SQL = "SELECT * FROM giveaway WHERE status = 'open' ORDER BY draw_at"
DUE_SQL = (
    "SELECT * FROM giveaway WHERE status = 'open' AND draw_at <= UTC_TIMESTAMP() "
    "ORDER BY draw_at"
)
# Só fecha um sorteio ainda no estado esperado, para que dois ticks (ou um
# tick e um !sorteio sortear) nunca anunciem dois vencedores.
CLOSE_SQL = (
    "UPDATE giveaway SET status = 'drawn', winner_ids = %s, participants = %s, "
    "drawn_at = UTC_TIMESTAMP() WHERE message_id = %s AND status = %s"
)
CANCEL_SQL = (
    "UPDATE giveaway SET status = 'cancelled' WHERE message_id = %s "
    "AND status = 'open'"
)


def parse_link(text):
    """(channel_id, message_id) de um link de mensagem, ou None."""
    match = _LINK.search(text or "")
    if not match:
        return None
    return int(match.group(2)), int(match.group(3))


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


def winner_ids(row):
    return [int(x) for x in str(row.get("winner_ids") or "").split(",") if x]


def pick(candidates, count, exclude=()):
    """`count` vencedores ao acaso (secrets, não random) entre os candidatos
    que ainda não ganharam."""
    excluded = set(exclude)
    pool = [c for c in candidates if c not in excluded]
    return secrets.SystemRandom().sample(pool, min(count, len(pool)))


def message_link(row):
    return (
        f"https://discord.com/channels/{row['guild_id']}/"
        f"{row['channel_id']}/{row['message_id']}"
    )
