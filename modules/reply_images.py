"""Images Hermiswag may attach to a reply (owner, 2026-11-21).

The model never sends a file or a link: its JSON may carry one small,
validated spec under "image", and Poliswag draws or looks it up itself.

  {"type": "bar", "title": "...", "labels": [...], "values": [...], "unit": "..."}
      a bar chart of numbers the agent read from its tools (up to 25 bars)
  {"type": "pokemon", "name": "Dragonite", "shiny": false}
      that Pokémon's artwork, from the same sprite table the alerts use
"""

import io
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

MAX_BARS = 25
_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_TEXT_RE = re.compile(r"[\x00-\x1f\x7f<>@`]")


def _text(value, limit):
    if not isinstance(value, str):
        return None
    value = _TEXT_RE.sub("", value).strip()
    return value[:limit] if value else None


def parse_image(raw):
    """The validated image spec in a Hermiswag JSON reply, or None."""
    if not isinstance(raw, str) or len(raw) > 8192:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return None
    spec = data.get("image") if isinstance(data, dict) else None
    if not isinstance(spec, dict):
        return None
    kind = spec.get("type")
    if kind == "pokemon":
        name = _text(spec.get("name"), 30)
        if not name:
            return None
        return {"type": "pokemon", "name": name, "shiny": spec.get("shiny") is True}
    if kind == "bar":
        labels, values = spec.get("labels"), spec.get("values")
        if (
            not isinstance(labels, list)
            or not isinstance(values, list)
            or not 1 <= len(labels) <= MAX_BARS
            or len(labels) != len(values)
        ):
            return None
        clean_labels = [_text(label, 40) for label in labels]
        if any(label is None for label in clean_labels):
            return None
        clean_values = []
        for value in values:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                or value > 1e9
            ):
                return None
            clean_values.append(float(value))
        return {
            "type": "bar",
            "title": _text(spec.get("title"), 80) or "",
            "labels": clean_labels,
            "values": clean_values,
            "unit": _text(spec.get("unit"), 20) or "",
        }
    return None


def pokemon_id(name, names):
    """The species id for a name the agent gave, from {id: name}."""
    wanted = name.strip().casefold()
    for key, value in (names or {}).items():
        if isinstance(value, str) and value.casefold() == wanted:
            try:
                return int(key)
            except (TypeError, ValueError):
                return None
    return None


# ---- drawing: the PoGoLeiria board design (owner, 2026-11-21) ----
# Same tokens, type and frame as the stat boards (templates/_board-base.css,
# stat-card.html): tone ramp #01040A -> #0C131D -> #161E2A, hairline #262E3B,
# ink #F1F5FC/#D3DAE4/#A7B0BE/#778190, ember #B6322B (lead #E36654), gold
# #F4CC64; Figtree for text and the wordmark, Bricolage 800 for figures; the
# brand artwork at 18% behind the heading. Drawn at 2x for crisp text.

_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
_FONTS = _TEMPLATES / "fonts"
SCALE = 2
BG, CARD, SUNKEN, HAIR = (1, 4, 10), (12, 19, 29), (22, 30, 42), (38, 46, 59)
INK, INK2, INK3, INK4 = (
    (241, 245, 252),
    (211, 218, 228),
    (167, 176, 190),
    (119, 129, 144),
)
EMBER, EMBER_LEAD, GOLD = (182, 50, 43), (227, 102, 84), (244, 204, 100)
WIDTH = 560


def _font(name, size):
    try:
        return ImageFont.truetype(str(_FONTS / name), size * SCALE)
    except OSError:
        return ImageFont.truetype(_BOLD, size * SCALE)


def _s(value):
    return int(round(value * SCALE))


def _caps(draw, xy, text, size, fill, spacing=1.2):
    """Uppercase with letter spacing, as the .caps class does."""
    font = _font("figtree-800.ttf", size)
    x, y = xy
    for char in text.upper():
        draw.text((x, y), char, font=font, fill=fill)
        x += draw.textlength(char, font=font) + _s(spacing)
    return x


def _board(height, heading):
    """The frame: rounded board, brand art with its fade, wordmark | heading."""
    w, h = _s(WIDTH), _s(height)
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, w - 1, h - 1), radius=_s(24), fill=BG, outline=HAIR, width=_s(1)
    )
    try:
        art = (
            Image.open(_TEMPLATES / "brand-header-400.png")
            .convert("RGBA")
            .resize((_s(220), _s(220)))
        )
        alpha = art.getchannel("A").point(lambda a: int(a * 0.18))
        art.putalpha(alpha)
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        layer.paste(art, (w - _s(230), _s(-40)), art)
        fade = Image.new("RGBA", (_s(140), _s(180)), (0, 0, 0, 0))
        for x in range(fade.width):
            a = int(255 * (1 - x / fade.width))
            ImageDraw.Draw(fade).line((x, 0, x, fade.height), fill=BG + (a,))
        layer.alpha_composite(fade, (w - _s(230), 0))
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, w - 1, h - 1), radius=_s(24), fill=255
        )
        clipped = Image.new("RGBA", image.size, (0, 0, 0, 0))
        clipped.paste(layer, (0, 0), mask)
        image.alpha_composite(clipped)
        draw = ImageDraw.Draw(image)
    except OSError:
        pass
    # The hairline goes on last so the brand art never covers it.
    draw.rounded_rectangle(
        (0, 0, w - 1, h - 1), radius=_s(24), outline=HAIR, width=_s(1)
    )
    x, y = _s(24), _s(24)
    word = _font("figtree-900.ttf", 22)
    draw.text((x, y + _s(5)), "POGOLEIRIA", font=word, fill=INK)
    x += draw.textlength("POGOLEIRIA", font=word) + _s(14)
    draw.line((x, y + _s(11), x, y + _s(29)), fill=HAIR, width=_s(1))
    if heading:
        _caps(draw, (x + _s(14), y + _s(13)), heading[:48], 11, INK3)
    return image, draw


def _png(image):
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _figure(value):
    if value >= 100 or value == int(value):
        return f"{value:,.0f}".replace(",", " ")
    return f"{value:.1f}".replace(".", ",")


def render_bar(spec):
    """A stat-board panel of bars: label, track with an ember bar (the
    largest in bright ember), figure and unit."""
    labels, values = spec["labels"], spec["values"]
    row, pad = 32, 24
    height = 24 + 40 + 20 + pad * 2 + row * len(labels) + 24
    image, draw = _board(height, spec["title"])
    left, top = _s(24), _s(24 + 40 + 20)
    right = _s(WIDTH - 24)
    draw.rounded_rectangle(
        (left, top, right, _s(height - 24)),
        radius=_s(20),
        fill=CARD,
        outline=HAIR,
        width=_s(1),
    )
    label_font, fig_font = _font("figtree-700.ttf", 13), _font("bricolage-800.ttf", 17)
    unit_font = _font("figtree-800.ttf", 10)
    unit = spec["unit"].upper()
    label_w = min(
        max(draw.textlength(text, font=label_font) for text in labels), _s(150)
    )
    value_w = max(draw.textlength(_figure(v), font=fig_font) for v in values)
    value_w += (draw.textlength(unit, font=unit_font) + _s(4)) if unit else 0
    track_x = left + _s(pad) + label_w + _s(14)
    track_end = right - _s(pad) - value_w - _s(12)
    peak = max(values) or 1
    for i, (label, value) in enumerate(zip(labels, values)):
        y = top + _s(pad) + _s(row) * i
        mid = y + _s(row) // 2
        text = label
        while draw.textlength(text, font=label_font) > label_w and len(text) > 2:
            text = text[:-2] + "…"
        draw.text((left + _s(pad), mid), text, font=label_font, fill=INK2, anchor="lm")
        draw.rounded_rectangle(
            (track_x, mid - _s(4), track_end, mid + _s(4)), radius=_s(4), fill=SUNKEN
        )
        length = max(int((track_end - track_x) * value / peak), _s(8))
        colour = EMBER_LEAD if value == peak else EMBER
        draw.rounded_rectangle(
            (track_x, mid - _s(4), track_x + length, mid + _s(4)),
            radius=_s(4),
            fill=colour,
        )
        fx = right - _s(pad) - value_w
        draw.text((fx, mid), _figure(value), font=fig_font, fill=INK, anchor="lm")
        if unit:
            ux = fx + draw.textlength(_figure(value), font=fig_font) + _s(4)
            draw.text((ux, mid + _s(1)), unit, font=unit_font, fill=INK4, anchor="lm")
    return _png(image)


def render_pokemon(sprite, name, shiny):
    """A stat-board panel with the Pokémon on the ember banner and its name."""
    height = 24 + 40 + 20 + 300 + 24
    image, draw = _board(height, "Shiny" if shiny else "Pokémon")
    left, top, right = _s(24), _s(84), _s(WIDTH - 24)
    bottom = _s(height - 24)
    draw.rounded_rectangle(
        (left, top, right, bottom), radius=_s(20), fill=CARD, outline=HAIR, width=_s(1)
    )
    banner_h = _s(220)
    banner = Image.new("RGBA", (right - left - _s(2), banner_h), (0, 0, 0, 0))
    bw, bh = banner.size
    grad = Image.new("RGBA", banner.size)
    gd = ImageDraw.Draw(grad)
    start, end = (104, 18, 15), SUNKEN
    for x in range(bw):
        t = x / bw
        gd.line(
            (x, 0, x, bh),
            fill=tuple(int(start[k] + (end[k] - start[k]) * t) for k in range(3))
            + (255,),
        )
    mask = Image.new("L", banner.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, bw - 1, bh + _s(20)), radius=_s(19), fill=255
    )
    banner.paste(grad, (0, 0), mask)
    image.alpha_composite(banner, (left + _s(1), top + _s(1)))
    try:
        art = Image.open(io.BytesIO(sprite)).convert("RGBA")
        box = art.getbbox()
        if box:
            art = art.crop(box)
        ratio = min(_s(190) / art.width, _s(190) / art.height)
        art = art.resize(
            (max(1, int(art.width * ratio)), max(1, int(art.height * ratio))),
            Image.LANCZOS,
        )
        image.alpha_composite(
            art,
            (
                (left + right - art.width) // 2,
                top + _s(1) + (banner_h - art.height) // 2,
            ),
        )
    except Exception:
        pass
    draw = ImageDraw.Draw(image)
    name_font = _font("bricolage-800.ttf", 26)
    ny = top + banner_h + _s(40)
    draw.text((left + _s(24), ny), name, font=name_font, fill=INK, anchor="lm")
    if shiny:
        x = left + _s(24) + draw.textlength(name, font=name_font) + _s(14)
        draw.ellipse((x, ny - _s(4), x + _s(8), ny + _s(4)), fill=GOLD)
        _caps(draw, (x + _s(14), ny - _s(7)), "Shiny", 11, INK3)
    return _png(image)
