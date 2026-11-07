"""Fit quest locations on a self-hosted map and mark each with the site's Pokéstop.

Each stop is the Pokéstop file the site's map draws (PoGoLeiria
`apps/landing/public/map-pins/pokestop.svg`), its foot exactly on the stop's
coordinates, with the A–J letter that ties it to its line in the embed beside
its post. The reward is not drawn (owner, 2026-11-07): the embed's title and
thumbnail already say what it is, and the art hid where the stop was.
"""

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT, SCALE = 600, 300, 2
# Room round the stops in CSS px: a stop stands about 19 px above its point.
_PAD_SIDE, _PAD_TOP, _PAD_BOTTOM = 24, 32, 18
_BADGE_RADIUS = 10
_CREDIT = "© OpenStreetMap contributors · OpenMapTiles"
# Pixels of the whole world at zoom 0, as the tileserver's /staticmap counts
# zoom. This was 512 until 2026-10-10, which put every pin at twice its real
# offset from the map centre; 256 was measured against OpenStreetMap roundabout
# centres (the pins then land on them).
_WORLD = 256

# The stop in output pixels: pokestop.svg is 64 × 80 units with its ground
# point at y = 75 (the centre of its round base), drawn here at half a pixel a
# unit, so 32 × 40 with the foot 2.5 px above the image's bottom.
_STOP_W, _STOP_H, _STOP_GROUND = 32, 40, 2.5
_STOP_FILE = Path(__file__).resolve().parents[1] / "templates/map-pins/pokestop.png"
# lib/domain-colors.ts PIN_EDGE (from OKLCH): the letter's light rim.
PIN_EDGE = "#FAFCFF"
# Where the letter sits, output px from the stop's point: left of its post,
# under its cube.
_LETTER_AT = (-17, -9)


def _project(latitude, longitude):
    sin_latitude = math.sin(math.radians(latitude))
    return (
        (longitude + 180) / 360,
        0.5 - math.log((1 + sin_latitude) / (1 - sin_latitude)) / (4 * math.pi),
    )


@dataclass
class QuestMap:
    payload: dict
    # Original list indices are retained when a malformed coordinate is skipped.
    points: list[tuple[str, float, float]]


def fit_quest_map(pokestops, style):
    coordinates = []
    for index, stop in enumerate(pokestops):
        try:
            latitude, longitude = float(stop["lat"]), float(stop["lon"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not (-85.05112878 <= latitude <= 85.05112878 and -180 <= longitude <= 180):
            continue
        x, y = _project(latitude, longitude)
        coordinates.append((chr(65 + index), x, y))
    if not coordinates:
        return None

    min_x, max_x = min(p[1] for p in coordinates), max(p[1] for p in coordinates)
    min_y, max_y = min(p[2] for p in coordinates), max(p[2] for p in coordinates)
    center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2
    # The tileserver's zoom counts a 256-pixel world at zoom 0 (the raster-tile
    # convention), not MapLibre's 512. Leave room for the stops, which stand
    # above their points; cap close-up zoom so a single stop still shows its
    # surrounding streets.
    zoom = min(
        16.0,
        math.log2((WIDTH - 2 * _PAD_SIDE) / (_WORLD * max(max_x - min_x, 1e-12))),
        math.log2(
            (HEIGHT - _PAD_TOP - _PAD_BOTTOM) / (_WORLD * max(max_y - min_y, 1e-12))
        ),
    )
    zoom = max(1.0, zoom)
    world_size = _WORLD * 2**zoom
    # The stops' box sits in the room left under the pins, not the image's middle.
    map_center_y = center_y - (_PAD_TOP - _PAD_BOTTOM) / 2 / world_size
    return QuestMap(
        payload={
            "style": style,
            "latitude": math.degrees(
                math.atan(math.sinh(math.pi * (1 - 2 * map_center_y)))
            ),
            "longitude": center_x * 360 - 180,
            "zoom": zoom,
            "width": WIDTH,
            "height": HEIGHT,
            "scale": SCALE,
            "format": "png",
        },
        points=[
            (
                label,
                (WIDTH / 2 + (x - center_x) * world_size) * SCALE,
                (HEIGHT / 2 + (y - map_center_y) * world_size) * SCALE,
            )
            for label, x, y in coordinates
        ],
    )


def _stop_box(anchor_x, anchor_y):
    """What a stop covers, in output px."""
    return (
        anchor_x - _STOP_W / 2,
        anchor_y + _STOP_GROUND - _STOP_H,
        anchor_x + _STOP_W / 2,
        anchor_y + _STOP_GROUND,
    )


def _circle_hits_box(x, y, radius, box):
    nearest_x = min(max(x, box[0]), box[2])
    nearest_y = min(max(y, box[1]), box[3])
    return math.hypot(x - nearest_x, y - nearest_y) < radius


def place_labels(points):
    """Put each letter beside its own stop's post, clear of other stops and letters."""
    left, up = _LETTER_AT
    offsets = [
        (left, up),
        (-left, up),
        (left, up - 22),
        (-left, up - 22),
        (left - 12, up + 12),
        (-left + 12, up + 12),
        (0, 14),
        (left - 22, up),
        (-left + 22, up),
        (0, -48),
    ]
    boxes = [_stop_box(x, y) for _, x, y in points]
    low, right, bottom = _BADGE_RADIUS + 3, WIDTH * SCALE, HEIGHT * SCALE - 33
    placed = []
    for index, (label, anchor_x, anchor_y) in enumerate(points):
        candidates = []
        for offset_x, offset_y in offsets:
            x, y = anchor_x + offset_x, anchor_y + offset_y
            if not (low <= x <= right - low and low <= y <= bottom - _BADGE_RADIUS):
                continue
            collisions = sum(
                _circle_hits_box(x, y, _BADGE_RADIUS + 1, box)
                for other, box in enumerate(boxes)
                if other != index
            ) + sum(
                math.hypot(x - px, y - py) < 2 * _BADGE_RADIUS + 2
                for _, px, py, _, _ in placed
            )
            candidates.append((collisions, x, y))
            if collisions == 0:
                break
        if candidates:
            _, x, y = min(candidates, key=lambda candidate: candidate[0])
        else:
            x = min(max(anchor_x, low), right - low)
            y = min(max(anchor_y, low), bottom - _BADGE_RADIUS)
        placed.append((label, x, y, anchor_x, anchor_y))
    return placed


@lru_cache(maxsize=1)
def _stop_image():
    with Image.open(_STOP_FILE) as source:
        return source.convert("RGBA").resize(
            (_STOP_W, _STOP_H), Image.Resampling.LANCZOS
        )


def draw_quest_map(image_bytes, quest_map, color="#3498DB"):
    with Image.open(BytesIO(image_bytes), formats=["PNG", "WEBP"]) as source:
        if source.size != (WIDTH * SCALE, HEIGHT * SCALE):
            raise ValueError("Tile server returned an unexpected map size")
        image = source.convert("RGBA")
    stop = _stop_image()
    # The south over the north, as the site's map draws them.
    for _, anchor_x, anchor_y in sorted(quest_map.points, key=lambda p: p[2]):
        image.alpha_composite(
            stop,
            (round(anchor_x - _STOP_W / 2), round(anchor_y + _STOP_GROUND - _STOP_H)),
        )

    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("DejaVuSans-Bold.ttf", 15)
    for label, badge_x, badge_y, _, _ in place_labels(quest_map.points):
        radius = _BADGE_RADIUS
        draw.ellipse(
            (badge_x - radius, badge_y - radius, badge_x + radius, badge_y + radius),
            fill=color,
            outline=PIN_EDGE,
            width=2,
        )
        draw.text((badge_x, badge_y), label, font=font, fill="white", anchor="mm")

    credit_font = ImageFont.truetype("DejaVuSans.ttf", 17)
    credit_width = draw.textbbox((0, 0), _CREDIT, font=credit_font)[2]
    width, height = image.size
    draw.rectangle(
        (width - credit_width - 20, height - 30, width, height), fill="white"
    )
    draw.text(
        (width - 9, height - 9), _CREDIT, font=credit_font, fill="#333333", anchor="rs"
    )
    output = BytesIO()
    image.convert("RGB").save(output, format="PNG")
    return output.getvalue()
