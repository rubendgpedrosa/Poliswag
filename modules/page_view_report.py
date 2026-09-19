"""Turns modules.page_view_stats rows into a Discord report.

Pure: no database, no bot, and no discord import beyond building the Embed at
the very end. Everything here is a value in, a string out, so the whole layout
is testable without a client.

Two constraints drive the shape:

* Discord's mobile client does not wrap inside a fenced block, so a wide table
  becomes a horizontal scroll on the one device this is read on. Every
  rendered line is held to MAX_LINE, with long values ellipsised.
* `visitor` rotates at UTC midnight, so a unique count is only honest inside a
  single day. Period-level reach is sessions; the daily table is the only
  place a visitor count appears.
"""

from collections import namedtuple
from datetime import date

from modules.embeds import build_embed

MAX_LINE = 56

_MAX_DAY_ROWS = 14
_MAX_WEEK_ROWS = 20
_MAX_MONTH_ROWS = 24
_WEEKLY_ABOVE = 140

_BLOCKS = "▁▂▃▄▅▆▇█"
_IDLE = "·"

Period = namedtuple("Period", "days since until")
Column = namedtuple("Column", "header width align")
Section = namedtuple(
    "Section", "title columns rows note text", defaults=(None, None, None)
)
Report = namedtuple("Report", "description kpis sections")


def build_sections(stats, period):
    """The whole report as values: a description, six KPIs, and the sections."""
    totals = _first(stats.get("totals"))
    daily = stats.get("daily") or []
    sessions = _int(totals.get("sessions"))

    sections = [
        _daily_section(daily),
        _hourly_section(stats.get("hourly") or []),
        _views_section(stats.get("views") or []),
        _devices_section(stats.get("devices") or []),
        _breakdown_section(stats, sessions),
        _referrers_section(stats.get("referrers") or [], sessions),
        _paths_section(stats.get("paths") or []),
    ]
    sections += _optional_sections(stats, sessions)

    return Report(
        description=_description(period, _int(totals.get("days"))),
        kpis=_kpis(totals, daily, sessions),
        sections=[s for s in sections if s is not None],
    )


def render_table(section):
    """A section as fixed-width text, every line padded to one width."""
    if section.text is not None:
        lines = section.text.splitlines()
    else:
        lines = [_line([c.header for c in section.columns], section.columns)]
        lines += [_line(row, section.columns) for row in section.rows]

    width = max((len(line) for line in lines), default=0)
    return "\n".join(line.ljust(width) for line in lines)


# --------------------------------------------------------------------------- KPIs


def _kpis(totals, daily, sessions):
    views = _int(totals.get("views"))
    uniques = [_int(row.get("visitors")) for row in daily]

    return [
        ("Loads", str(_int(totals.get("loads")))),
        ("Views", str(views)),
        ("Sessões", str(sessions)),
        ("Views/sessão", f"{views / sessions:.2f}" if sessions else "—"),
        (
            "Únicos/dia",
            (
                f"{sum(uniques) / len(uniques):.1f} média · {max(uniques)} máx"
                if uniques
                else "—"
            ),
        ),
        (
            "PWA instalada",
            (
                f"{_int(totals.get('pwa_sessions'))} de {sessions} "
                f"({_percent(_int(totals.get('pwa_sessions')), sessions)})"
                if sessions
                else "—"
            ),
        ),
    ]


def _description(period, days_with_data):
    span = "sempre" if period.days is None else f"{period.days} dias"
    return (
        f"{period.since} → {period.until} · {span} · UTC · "
        f"{days_with_data} dias com dados"
    )


# ----------------------------------------------------------------------- sections


def _daily_section(daily):
    if not daily:
        return None

    rows = _rollup(daily)
    columns = (
        Column("dia", 10, "l"),
        Column("loads", 6, "r"),
        Column("views", 6, "r"),
        Column("sess", 5, "r"),
        Column("únicos", 7, "r"),
    )
    note = None
    if len(daily) > _WEEKLY_ABOVE:
        note = f"(últimos {_MAX_MONTH_ROWS} meses)"
    return Section("Tráfego diário", columns, rows, note)


def _rollup(daily):
    """Days as they come, or grouped so the field cannot outgrow its limit."""
    if len(daily) <= _MAX_DAY_ROWS:
        return [
            (
                _day(row).isoformat(),
                _int(row.get("loads")),
                _int(row.get("views")),
                _int(row.get("sessions")),
                _int(row.get("visitors")),
            )
            for row in daily
        ]

    if len(daily) <= _WEEKLY_ABOVE:
        key, cap = _iso_week, _MAX_WEEK_ROWS
    else:
        key, cap = _month, _MAX_MONTH_ROWS

    groups = {}
    for row in daily:
        groups.setdefault(key(_day(row)), []).append(row)

    rows = [
        (
            label,
            sum(_int(r.get("loads")) for r in members),
            sum(_int(r.get("views")) for r in members),
            sum(_int(r.get("sessions")) for r in members),
            round(sum(_int(r.get("visitors")) for r in members) / len(members)),
        )
        for label, members in groups.items()
    ]
    return rows[-cap:]


def _hourly_section(hourly):
    if not hourly:
        return None

    views = [0] * 24
    for row in hourly:
        views[_int(row.get("h")) % 24] = _int(row.get("views"))

    peak = max(views)
    spark = "".join(_IDLE if v == 0 else _BLOCKS[_bucket(v, peak)] for v in views)
    axis = "0h".ljust(21) + "23h"
    tail = f"pico {views.index(peak)}h · {peak} views" if peak else "sem atividade"
    return Section(
        "Atividade por hora (UTC)", None, None, None, f"{axis}\n{spark}\n{tail}"
    )


def _bucket(value, peak):
    """Scale to the busiest hour so the shape of the day is visible at any volume."""
    if peak <= 0:
        return 0
    return min(len(_BLOCKS) - 1, round((value / peak) * (len(_BLOCKS) - 1)))


def _views_section(views):
    if not views:
        return None

    total = sum(_int(row.get("views")) for row in views)
    columns = (
        Column("vista", 8, "l"),
        Column("loads", 6, "r"),
        Column("switch", 7, "r"),
        Column("views", 6, "r"),
        Column("%", 5, "r"),
    )
    rows = [
        (
            row.get("view"),
            _int(row.get("loads")),
            _int(row.get("switches")),
            _int(row.get("views")),
            _percent(_int(row.get("views")), total),
        )
        for row in views
    ]
    return Section("Vistas", columns, rows)


def _devices_section(devices):
    if not devices:
        return None

    columns = (
        Column("device", 7, "l"),
        Column("os", 8, "l"),
        Column("browser", 16, "l"),
        Column("sess", 5, "r"),
        Column("views", 6, "r"),
    )
    rows = [
        (
            row.get("device"),
            row.get("os"),
            row.get("browser"),
            _int(row.get("sessions")),
            _int(row.get("views")),
        )
        for row in devices
    ]
    return Section("Dispositivo / OS / Browser", columns, rows)


def _breakdown_section(stats, sessions):
    """Screen, language and country stacked — side by side they would not fit."""
    blocks = [
        _mini_list(stats.get("screens") or [], "bucket", sessions),
        _mini_list(stats.get("langs") or [], "lang", sessions),
        _mini_list(stats.get("countries") or [], "country", sessions),
    ]
    blocks = [block for block in blocks if block]
    if not blocks:
        return None
    return Section("Ecrã · Idioma · País", None, None, None, "\n\n".join(blocks))


def _mini_list(rows, field, total):
    columns = (Column("", 9, "l"), Column("", 5, "r"), Column("", 5, "r"))
    return "\n".join(
        _line(
            (
                row.get(field),
                _int(row.get("sessions")),
                _percent(_int(row.get("sessions")), total),
            ),
            columns,
        )
        for row in rows
    )


def _referrers_section(referrers, sessions):
    if not referrers:
        return None

    columns = (Column("referrer", 34, "l"), Column("sess", 5, "r"), Column("%", 5, "r"))
    rows = [
        (
            row.get("referrer"),
            _int(row.get("sessions")),
            _percent(_int(row.get("sessions")), sessions),
        )
        for row in referrers
    ]
    return Section("Referrers", columns, rows)


def _paths_section(paths):
    if not paths:
        return None

    columns = (
        Column("caminho", 38, "l"),
        Column("views", 6, "r"),
        Column("sess", 5, "r"),
    )
    rows = [
        (row.get("path"), _int(row.get("views")), _int(row.get("sessions")))
        for row in paths
    ]
    return Section("Caminhos", columns, rows)


def _optional_sections(stats, sessions):
    """Only present once the landing app's 002 migration has run."""
    sections = []

    if stats.get("os_versions"):
        sections.append(
            _version_section(
                "Versões de OS", stats["os_versions"], "os", "os_version", 8, sessions
            )
        )
    if stats.get("browser_versions"):
        sections.append(
            _version_section(
                "Versões de browser",
                stats["browser_versions"],
                "browser",
                "browser_version",
                16,
                sessions,
            )
        )
    if stats.get("models"):
        columns = (
            Column("modelo", 34, "l"),
            Column("sess", 5, "r"),
            Column("%", 5, "r"),
        )
        rows = [
            (
                row.get("val"),
                _int(row.get("sessions")),
                _percent(_int(row.get("sessions")), sessions),
            )
            for row in stats["models"]
        ]
        sections.append(
            Section("Modelos", columns, rows, _coverage(stats["models"], sessions))
        )

    hardware = _hardware_section(stats, sessions)
    if hardware:
        sections.append(hardware)
    return sections


def _version_section(title, rows, key, version, key_width, sessions):
    columns = (
        Column(key, key_width, "l"),
        Column("versão", 8, "l"),
        Column("sess", 5, "r"),
        Column("%", 5, "r"),
    )
    body = [
        (
            row.get(key),
            row.get(version),
            _int(row.get("sessions")),
            _percent(_int(row.get("sessions")), sessions),
        )
        for row in rows
    ]
    return Section(title, columns, body, _coverage(rows, sessions))


def _hardware_section(stats, sessions):
    """arch, bitness, cores and memory share one field: all are low-cardinality."""
    labels = (
        ("arch", "arch"),
        ("bitness", "bits"),
        ("cpu_cores", "cores"),
        ("device_memory", "RAM"),
    )
    blocks, covered = [], []
    for key, label in labels:
        rows = stats.get(key)
        if not rows:
            continue
        covered += rows
        columns = (Column("", 9, "l"), Column("", 5, "r"))
        blocks.append(
            "\n".join(
                _line((f"{label} {row.get('val')}", _int(row.get("sessions"))), columns)
                for row in rows
            )
        )

    if not blocks:
        return None
    return Section(
        "Hardware", None, None, _coverage(covered, sessions), "\n\n".join(blocks)
    )


def _coverage(rows, sessions):
    """A low count must read as "not reported", never as "nobody"."""
    covered = sum(_int(row.get("sessions")) for row in rows)
    return f"cobertura {covered}/{sessions} sessões ({_percent(covered, sessions)})"


# ------------------------------------------------------------------------ helpers


def _line(values, columns):
    return " ".join(_cell(value, column) for value, column in zip(values, columns))


def _cell(value, column):
    text = "" if value is None else str(value)
    if len(text) > column.width:
        text = text[: column.width - 1] + "…"
    return text.ljust(column.width) if column.align == "l" else text.rjust(column.width)


def _percent(part, whole):
    return f"{round(part / whole * 100)}%" if whole else "—"


def _first(rows):
    return (rows or [{}])[0] or {}


def _int(value):
    return int(value) if value is not None else 0


def _day(row):
    value = row.get("d")
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _iso_week(day):
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def _month(day):
    return f"{day.year}-{day.month:02d}"


# --------------------------------------------------------------------- delivery

TITLE = "📊 Estatísticas do site"
EMPTY = "Sem dados no período."
FOOTER = "visitor roda à meia-noite UTC — únicos só por dia; sessões para o período."

_MAX_FIELDS = 25
_MAX_FIELD_VALUE = 1024
# Under Discord's 6000 so a miscount cannot turn into an HTTPException.
_MAX_TOTAL = 5500


def fits(report):
    """Whether this report can go as an embed at all. Pure, so the branch is testable."""
    fields = _fields(report)
    if len(fields) > _MAX_FIELDS:
        return False
    if any(len(value) > _MAX_FIELD_VALUE for _name, value, _inline in fields):
        return False
    total = len(TITLE) + len(report.description) + len(FOOTER)
    total += sum(len(name) + len(value) for name, value, _inline in fields)
    return total <= _MAX_TOTAL


def build_dm_embed(report):
    """The report as one embed, or None when it would not fit — caller sends a file."""
    if not fits(report):
        return None

    embed = build_embed(
        TITLE, report.description if report.sections else EMPTY, footer=FOOTER
    )
    for name, value, inline in _fields(report):
        embed.add_field(name=name, value=value, inline=inline)
    return embed


def render_text_report(report):
    """The same content as a plain .txt, with no field or width caps to respect."""
    lines = [TITLE, report.description, ""]
    for label, value in report.kpis:
        lines.append(f"{label}: {value}")
    for section in report.sections:
        lines += ["", section.title, render_table(section)]
        if section.note:
            lines.append(section.note)
    lines += ["", FOOTER]
    return "\n".join(lines)


def _fields(report):
    """(name, value, inline) triples — six scalars, then one block per section."""
    if not report.sections:
        return []

    fields = [(label, value, True) for label, value in report.kpis]
    for section in report.sections:
        body = render_table(section)
        if section.note:
            body = f"{body}\n{section.note}"
        fields.append((section.title, f"```\n{body}\n```", False))
    return fields
