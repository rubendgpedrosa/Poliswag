"""The Discord snapshot embed, and the sanitized JSON export.

The long report these used to sit beside is a live page now
(apps/landing/app/webstats/[token] in the PoGoLeiria repo), so the section
builders, the text attachment and the hand-rolled HTML renderer that made up
most of this module have gone with it.

All period reach is browser DOCUMENTS. Daily IP/UA hashes are estimates and
rotate at UTC midnight. No activity-session or installation count is implied.
"""

from datetime import date, datetime
from decimal import Decimal
import json
import re

from modules.embeds import build_embed

DIMENSIONS = {
    "devices": ("Dispositivo / sistema / navegador", ("device", "os", "browser")),
    "screens": ("Largura de ecrã (CSS px)", ("screen_w",)),
    "langs": ("Idioma", ("lang",)),
    "countries": ("País", ("country",)),
    "os_versions": ("Versões do sistema", ("os", "os_version")),
    "browser_versions": ("Versões do navegador", ("browser", "browser_version")),
    "models": ("Modelos", ("model",)),
    "arch": ("Arquitetura", ("arch",)),
    "bitness": ("Bits", ("bitness",)),
    "cpu_cores": ("Núcleos CPU", ("cpu_cores",)),
    "device_memory": ("Memória reportada (GiB)", ("device_memory",)),
}


def number(value):
    return int(value or 0)


def source(value):
    # No raw paths/URLs/credentials, even from legacy or hand-written payloads.
    text = str(value or "").lower()
    if (
        re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}", text)
        and len(text) <= 100
    ):
        return text
    return "direto/desconhecido"


def hundo_line(hundo):
    """Players with 100IV DMs on, and whatever stands between the rest and
    their alerts; the extras only when there is something to say."""
    line = (
        f"**100IV por DM:** {hundo['active']} ativos "
        f"(Leiria {hundo['leiria']} · Marinha {hundo['marinha']})"
    )
    extras = [
        f"{hundo[key]} {label}"
        for key, label in (
            ("waiting", "à espera de confirmação"),
            ("dms_closed", "com DMs fechadas"),
            ("unhealthy", "sem alertas a chegar"),
        )
        if hundo.get(key)
    ]
    return line + (" · " + " · ".join(extras) if extras else "")


def build_snapshot_embed(stats, trade_stats=None, report_url=None):
    """A short operational snapshot for Discord; the link carries the detail."""
    daily = stats.get("daily") or []
    as_of = stats.get("as_of")
    today = as_of.date() if isinstance(as_of, datetime) else None
    today_row = next((row for row in reversed(daily) if row.get("d") == today), None)
    visitors = number(today_row.get("visitors")) if today_row else 0
    pages = {
        row.get("view"): number(row.get("views")) for row in stats.get("views") or []
    }
    lines = [
        f"**Hoje:** ~{visitors} visitantes estimados",
        "**Últimas 24h:** "
        f"Mapa {pages.get('map', 0)} · Quests {pages.get('quests', 0)} · "
        # The Pokédex's view was "dex", then "trades", now "pokedex"; landing
        # db/012 moves old rows, and a day's report can straddle it.
        f"Pokédex {sum(pages.get(v, 0) for v in ('pokedex', 'trades', 'dex'))}",
    ]
    if trade_stats and "entries" in trade_stats:
        lines.append(
            f"**Novas entradas nas trades:** {number(trade_stats.get('entries'))} Pokémon "
            f"({number(trade_stats.get('users'))} utilizadores)"
        )
    hundo = (trade_stats or {}).get("hundo")
    if hundo is not None:
        lines.append(hundo_line(hundo))
    if report_url:
        lines.append(f"\n[Abrir o relatório completo]({report_url})")
    return build_embed(
        "Webstats · últimas 24h",
        "\n".join(lines),
        footer="Visitantes são uma estimativa diária UTC · o relatório escolhe o período",
    )


def render_export(stats):
    """Aggregate-only JSON, full rows, no raw paths or visitor/document IDs.

    Select fields explicitly: adding an internal collector field cannot silently
    expose it in the export. Legacy referrers are normalized again here.
    """
    allowed = {
        "totals": (
            "views",
            "loads",
            "sessions",
            "entries",
            "pwa_sessions",
            "days",
            "unknown_visitor_events",
        ),
        "daily": ("d", "loads", "views", "sessions", "visitors"),
        "views": ("view", "loads", "switches", "views", "sessions"),
        "hourly": ("h", "views"),
        "referrers": ("referrer", "sessions"),
        "actions": ("view", "event_name", "events", "documents"),
        "versions": ("schema_version", "events"),
        **{name: (*cols, "sessions") for name, (_, cols) in DIMENSIONS.items()},
    }
    out = {
        "metric_version": 2,
        "since": stats.get("since"),
        "as_of": stats.get("as_of"),
        "definitions": {
            "sessions": "browser documents, not activity sessions",
            "views": "tool openings",
            "visitors": "estimated daily UTC IP/UA hashes",
        },
    }
    for name, columns in allowed.items():
        if name not in stats:
            continue
        out[name] = []
        for row in stats[name]:
            safe = {key: row.get(key) for key in columns}
            if name == "referrers":
                safe["referrer"] = source(safe.get("referrer"))
            out[name].append(safe)

    def encode(value):
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        raise TypeError(type(value).__name__)

    return json.dumps(out, ensure_ascii=False, indent=2, default=encode)
