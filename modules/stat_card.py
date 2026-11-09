"""The generic stat board: data in, a view model for templates/stat-card.html out.

One image holds a post's panels, stacked in one column, phone first: three
side by side made a 1600px-wide image that a phone shrank until nothing
read; one 520px column shows near full width. Each panel is a StatCard:
the event's own game image as a banner with its name on it, then the figure
it was about, an optional row of secondary totals, and a bar per scan area.
A panel without numbers is the banner and a line saying when it ended; a
new event's panel is the banner, when it runs until, its Pokémon and its
bonuses.

The renderer is wkhtmltoimage's old WebKit (no grid, no gap, unreliable
flex-grow), so bar widths, panel heights and the image size are decided here
and the template only places fixed-size boxes. The pixel constants mirror the
template's CSS: change one, change the other.

Colours are the PoGoLeiria tokens (packages/ui/src/tokens.css in the site
repo), converted from oklch: the tone ramp for surfaces and ink, ember for the
bars, success/urgent for a rise or fall. Ember is the suite's main colour.
"""

from dataclasses import dataclass

# Board.
BOARD_PAD = 24
HEAD_H = 40
HEAD_GAP = 20
PANEL_GAP = 16
PER_IMAGE = 4  # panels per image, stacked; more go in another image

# Panel.
PANEL_W = 520
BORDER = 1
BANNER_H = 176
BODY_PAD_TOP = 18
BODY_PAD_BOTTOM = 20
BODY_PAD_X = 24
CONTENT_W = PANEL_W - 2 * BORDER - 2 * BODY_PAD_X  # 470
HERO_H = 56
CHIPS_H = 58  # 12 margin + 14 label + 4 + 28 value
CHIPS_MARGIN = 12  # dropped when the chips open the body
POKEMON_H = 124  # 18 margin + 14 label + 8 + 64 tile + 6 + 14 name
POKEMON_TILE = 64
POKEMON_GAP = 10
POKEMON_SHOWN = 6  # tiles that fit across; the rest are "+N"
BONUS_MARGIN = 14
BONUS_LINE = 24
BONUSES_SHOWN = 5
DIVIDER_H = 33  # 16 margin + 1 line + 16 margin
ROW_H = 32
NOTE_H = 30  # 16 margin + 14 line
ENDED_H = 24

# Row columns: area name, bar, figure (+ unit), then one per extra.
ROW_LABEL_W = 120
ROW_BAR_GAP = 12
ROW_VALUE_W = 112
ROW_EXTRA_W = 64
BAR_MIN = 4

RENDER_SCALE = 2


@dataclass(frozen=True)
class StatRow:
    """One area: `value` drives the bar; None means no data for it."""

    key: str
    label: str
    value: int | None
    unit: str
    extras: tuple = ()  # ((label, value), ...) small figures after the main one


@dataclass(frozen=True)
class StatCard:
    kicker: str  # small caps on the banner: "Raids Mega", "Community Day"
    hero: int | None  # the headline figure; None for a panel without stats
    hero_unit: str = ""
    subject: str = ""  # shown after the figure unless the title already says it
    period: str = ""  # "01/11 – 08/11"
    badge: str = ""  # "3,3× o dia anterior", "~37 por dia"
    trend: str = ""  # "up", "down" or "" (neutral badge)
    chips: tuple = ()  # ((label, value), ...) secondary totals
    rows: tuple = ()  # StatRow, ...
    note: str = ""
    title: str = ""  # the event's name, on the banner
    image: str = ""  # the banner: a URL or data: URI; "" for a plain one
    ended: str = ""  # "Terminou às 09:00", for a panel without stats
    pokemon: tuple = ()  # ((name, icon URL or data: URI, can be shiny), ...)
    bonuses: tuple = ()  # ("2× Catch Candy", ...)


def number(value):
    """1 234 567, the PT grouping the embeds use."""
    return f"{value:,}".replace(",", " ")


def _bonus_lines(card):
    shown = min(len(card.bonuses), BONUSES_SHOWN)
    return shown + (1 if len(card.bonuses) > BONUSES_SHOWN else 0)


def panel_height(card):
    body = BODY_PAD_TOP + BODY_PAD_BOTTOM
    if card.hero is not None:
        body += HERO_H
    if card.chips:
        body += CHIPS_H if card.hero is not None else CHIPS_H - CHIPS_MARGIN
    if card.pokemon:
        body += POKEMON_H
    if card.bonuses:
        body += BONUS_MARGIN + BONUS_LINE * _bonus_lines(card)
    if card.hero is not None:
        if card.rows:
            body += DIVIDER_H + ROW_H * len(card.rows)
        if card.note:
            body += NOTE_H
    if card.ended:
        body += ENDED_H
    return 2 * BORDER + BANNER_H + body


def _panel_view(card):
    extras = max((len(row.extras) for row in card.rows), default=0)
    bar_track = (
        CONTENT_W - ROW_LABEL_W - ROW_BAR_GAP - ROW_VALUE_W - extras * ROW_EXTRA_W
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
                "value": None if row.value is None else number(row.value),
                "unit": row.unit,
                "bar": width,
                "lead": bool(top) and row.value == top,
                "extras": [
                    {"label": label, "value": number(value)}
                    for label, value in row.extras
                ],
            }
        )
    # Six tiles fit; with more, five and a "+N" tile in the sixth place.
    tiles = (
        len(card.pokemon) if len(card.pokemon) <= POKEMON_SHOWN else POKEMON_SHOWN - 1
    )
    subject = card.subject
    if subject and subject.lower() in card.title.lower():
        subject = ""  # "Sableye" under "Mega Sableye in Mega Raids"
    return {
        "height": panel_height(card),
        "bar_track": bar_track,
        "title": card.title,
        "image": card.image,
        "kicker": card.kicker,
        "period": card.period,
        "stats": card.hero is not None,
        "hero": None if card.hero is None else number(card.hero),
        "hero_unit": card.hero_unit,
        "subject": subject,
        "badge": card.badge,
        "trend": card.trend or "flat",
        "chips": [{"label": label, "value": value} for label, value in card.chips],
        "rows": rows,
        "note": card.note,
        "ended": card.ended,
        "pokemon": [
            {"name": name, "image": image, "shiny": shiny}
            for name, image, shiny in card.pokemon[:tiles]
        ],
        "pokemon_more": len(card.pokemon) - tiles,
        "bonuses": list(card.bonuses[:BONUSES_SHOWN]),
        "bonuses_more": max(0, len(card.bonuses) - BONUSES_SHOWN),
    }


def build_board_view(cards, heading="", date="", brand=""):
    """The template's view for one image of up to PER_IMAGE panels.

    Without a heading the image is bare: the panels alone, no board frame,
    wordmark or brand art. That is how an event post goes out, one image
    per event in its own message, the post's title as the message text."""
    if not cards:
        raise ValueError("a stat board needs at least one panel")
    cards = list(cards)[:PER_IMAGE]
    panels = [_panel_view(card) for card in cards]
    grid = [[panel] for panel in panels]  # one panel per row
    row_heights = [max(p["height"] for p in row) for row in grid]
    bare = not heading
    frame = 0 if bare else 2 * BOARD_PAD + 2 * BORDER
    width = frame + PANEL_W
    height = (
        frame
        + (0 if bare else HEAD_H + HEAD_GAP)
        + sum(row_heights)
        + PANEL_GAP * (len(grid) - 1)
    )
    return {
        "width": width,
        "height": height,
        "scale": RENDER_SCALE,
        "bare": bare,
        "heading": heading,
        "date": date,
        "brand": "" if bare else brand,
        "rows": [
            {"height": h, "panels": row, "last": i == len(grid) - 1}
            for i, (h, row) in enumerate(zip(row_heights, grid))
        ],
    }
