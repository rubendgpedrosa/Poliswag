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

from PIL import Image, ImageDraw, ImageFont

MAX_BARS = 25
_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
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


def _number(value):
    if value >= 100 or value == int(value):
        return f"{value:,.0f}".replace(",", " ")
    return f"{value:.1f}".replace(".", ",")


def render_bar(spec):
    """PNG bytes: horizontal bars, Discord dark colours, labels and values."""
    labels, values = spec["labels"], spec["values"]
    font = ImageFont.truetype(_FONT, 22)
    title_font = ImageFont.truetype(_BOLD, 28)
    row, top, left_pad = 40, 80 if spec["title"] else 30, 24
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    label_w = max(probe.textlength(label, font=font) for label in labels)
    width = 1000
    height = top + row * len(labels) + 30
    image = Image.new("RGB", (width, height), (43, 45, 49))
    draw = ImageDraw.Draw(image)
    if spec["title"]:
        draw.text((left_pad, 24), spec["title"], font=title_font, fill=(242, 243, 245))
    bar_x = left_pad + label_w + 16
    unit = f" {spec['unit']}" if spec["unit"] else ""
    value_w = max(probe.textlength(_number(v) + unit, font=font) for v in values)
    bar_max = max(width - bar_x - value_w - 40, 50)
    peak = max(values) or 1
    for index, (label, value) in enumerate(zip(labels, values)):
        y = top + index * row
        draw.text((left_pad, y + 6), label, font=font, fill=(219, 222, 225))
        length = max(int(bar_max * value / peak), 2)
        draw.rounded_rectangle(
            (bar_x, y + 6, bar_x + length, y + row - 8), radius=6, fill=(65, 105, 225)
        )
        draw.text(
            (bar_x + length + 10, y + 6),
            _number(value) + unit,
            font=font,
            fill=(181, 186, 193),
        )
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


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
