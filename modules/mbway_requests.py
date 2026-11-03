"""Quem pediu os dados de MB Way, e quantas vezes.

O dono quer saber a quem estão a ser enviados os dados de pagamento e se é o
primeiro pedido ou um hábito. Esta é a única fonte dessa contagem: a tabela
`mbway_requests` (migração 039) guarda o id, o nome e o número de pedidos, e
nunca os dados em si — esses só existem em `MBWAY_DETAILS` no `.env`.
"""

# A contagem passa a existir depois do INSERT; pedidos repetidos no mesmo dia
# actualizam `last_requested_at` sem criar linha nova.
RECORD_SQL = (
    "INSERT INTO mbway_requests (discord_id, username, display_name, requests) "
    "VALUES (%s, %s, %s, 1) "
    "ON DUPLICATE KEY UPDATE username = VALUES(username), "
    "display_name = VALUES(display_name), requests = requests + 1, "
    "last_requested_at = CURRENT_TIMESTAMP"
)
READ_SQL = (
    "SELECT requests, first_requested_at, last_requested_at "
    "FROM mbway_requests WHERE discord_id = %s"
)


def ordinal(number):
    """«1.º», «2.º», «11.º» — o português só distingue género no um e no dois."""
    if number == 1:
        return "1.º"
    if number == 2:
        return "2.º"
    return f"{number}.º"


def _name(author):
    """O nome que o dono reconhece: o do servidor, com o handle entre parênteses."""
    display = getattr(author, "display_name", None) or getattr(author, "name", "")
    handle = getattr(author, "name", "")
    if display and handle and display != handle:
        return f"{display} ({handle})"
    return display or handle or "membro desconhecido"


def _date(value):
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y")
    return str(value)[:10] if value else "?"


def owner_text(author, tally, where, unset=False):
    """A mensagem que o dono recebe por DM quando alguém pede os dados.

    Diz quem pediu, por onde, se foi o primeiro pedido ou o enésimo, e se o
    bot respondeu com os dados ou com o link do Revolut por não os ter.
    """
    ident = getattr(author, "id", "?")
    count = int(tally.get("requests") or 0)
    lines = [f"**{_name(author)}** — id `{ident}`, {where}."]
    if count <= 1:
        lines.append("Primeiro pedido.")
    else:
        lines.append(
            f"{ordinal(count)} pedido; o primeiro foi a {_date(tally.get('first'))}."
        )
    if unset:
        lines.append(
            "Os dados de MB Way não estão configurados: respondeu com o link do "
            "Revolut."
        )
    return "\n".join(lines)


def where_text(ctx):
    guild = getattr(ctx, "guild", None)
    if guild is None:
        return "por mensagem direta"
    channel = getattr(ctx, "channel", None)
    channel_name = getattr(channel, "name", None)
    guild_name = getattr(guild, "name", None)
    if channel_name and guild_name:
        return f"no canal #{channel_name} de {guild_name}"
    if channel_name:
        return f"no canal #{channel_name}"
    return "num canal do servidor"


async def record(db, author):
    """Soma um pedido a este membro e devolve o total já com este incluído."""
    ident = getattr(author, "id", 0)
    name = str(getattr(author, "name", "") or "")[:64]
    display = str(getattr(author, "display_name", "") or "")[:64]
    await db.execute_query_to_database(RECORD_SQL, params=(ident, name, display))
    rows = await db.get_data_from_database(READ_SQL, params=(ident,))
    row = rows[0] if rows else {}
    return {
        "requests": int(row.get("requests") or 0),
        "first": row.get("first_requested_at"),
        "last": row.get("last_requested_at"),
    }
