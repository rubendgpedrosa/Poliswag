"""The generic stat card: data in, a view model for templates/stat-card.html out.

Any stats source (a finished raid rotation, a Community Day, a Raid Hour)
fills a StatCard; this module does the arithmetic the renderer can't. The
renderer is wkhtmltoimage's old WebKit (no grid, no gap, unreliable
flex-grow), so bar widths and the card's pixel height are decided here and
the template only places fixed-size boxes. The pixel constants below mirror
the template's CSS: change one, change the other.

Layout, top to bottom:

    KICKER                                    period
    297  RAIDS  Sableye                  [ badge ]
    LABEL      LABEL      LABEL                      (optional chips)
    value      value      value
    ─────────────────────────────────────────────────
    ● Leiria          ██████████████████    207 raids
    ● Marinha Grande  ███                    23 raids
    note
"""

from dataclasses import dataclass, field

from modules import scan_areas

CARD_WIDTH = 800
STAGE_PAD = 16
INNER_PAD_X = 28  # left and right
INNER_PAD_TOP = 22
INNER_PAD_BOTTOM = 20
CONTENT_WIDTH = CARD_WIDTH - 2 * STAGE_PAD - 2 * INNER_PAD_X  # 712
HEADER_H = 20
HERO_H = 64  # 8 margin + 56 line (44px figure)
CHIPS_H = 58  # 14 margin + 14 label + 4 + 26 value
DIVIDER_H = 37  # 18 margin + 1 line + 18 margin
ROW_H = 36
NOTE_H = 32  # 18 margin + 14 line

# Row columns: area name, bar, figure (+ unit), then one column per extra.
ROW_LABEL_W = 168
ROW_VALUE_W = 118
ROW_EXTRA_W = 84
ROW_BAR_GAP = 16  # space between the bar and the figure
BAR_MIN = 4

RENDER_SCALE = 2


@dataclass(frozen=True)
class StatRow:
    """One area: `value` drives the bar; None means no data for it."""

    key: str  # scan area key, for its colour
    label: str
    value: int | None
    unit: str
    extras: tuple = ()  # ((label, value), ...) small figures after the main one


@dataclass(frozen=True)
class StatCard:
    kicker: str  # small caps line: "Raids Mega", "Community Day"
    hero: int  # the headline figure
    hero_unit: str  # "raids", "spawns"
    subject: str  # who it was about: "Sableye", "Nickit"
    period: str = ""  # "01/11 – 08/11"
    badge: str = ""  # "3,3× o dia anterior", "~37 por dia"
    trend: str = ""  # "up", "down" or "" (neutral badge)
    chips: tuple = ()  # ((label, value), ...) secondary totals
    rows: tuple = field(default_factory=tuple)  # StatRow, ...
    note: str = ""


def number(value):
    """1 234 567, the PT thin grouping the embeds use."""
    return f"{value:,}".replace(",", " ")


def card_height(card):
    inner = INNER_PAD_TOP + HEADER_H + HERO_H
    if card.chips:
        inner += CHIPS_H
    if card.rows:
        inner += DIVIDER_H + ROW_H * len(card.rows)
    if card.note:
        inner += NOTE_H
    return inner + INNER_PAD_BOTTOM


def build_view(card):
    """Everything the template needs, sizes in CSS pixels."""
    extras = max((len(row.extras) for row in card.rows), default=0)
    bar_track = (
        CONTENT_WIDTH - ROW_LABEL_W - ROW_BAR_GAP - ROW_VALUE_W - extras * ROW_EXTRA_W
    )
    top = max((row.value or 0 for row in card.rows), default=0)
    rows = []
    for row in card.rows:
        width = 0
        if row.value and top:
            width = max(BAR_MIN, round(bar_track * row.value / top))
        rows.append(
            {
                "label": row.label,
                "color": scan_areas.color_for(row.key),
                "value": None if row.value is None else number(row.value),
                "unit": row.unit,
                "bar": width,
                "extras": [
                    {"label": label, "value": number(value)}
                    for label, value in row.extras
                ],
            }
        )
    height = card_height(card)
    return {
        "width": CARD_WIDTH,
        "height": height + 2 * STAGE_PAD,
        "card_height": height,
        "scale": RENDER_SCALE,
        "bar_track": bar_track,
        "kicker": card.kicker,
        "period": card.period,
        "hero": number(card.hero),
        "hero_unit": card.hero_unit,
        "subject": card.subject,
        "badge": card.badge,
        "trend": card.trend or "flat",
        "chips": [{"label": label, "value": value} for label, value in card.chips],
        "rows": rows,
        "note": card.note,
    }
