"""Fit quest locations on a self-hosted map and draw matching list labels."""

from dataclasses import dataclass
from io import BytesIO
import math

from PIL import Image, ImageDraw, ImageFilter, ImageFont

WIDTH, HEIGHT, SCALE = 600, 300, 2
_PADDING = 40
_ICON_SIZE = 32
_SPRITE_TILE = 44
_BADGE_RADIUS = 12
_CREDIT = "© OpenStreetMap contributors · OpenMapTiles"


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
    icon_slugs: dict[str, str]


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
        coordinates.append((chr(65 + index), x, y, stop.get("quest_slug", "")))
    if not coordinates:
        return None

    min_x, max_x = min(p[1] for p in coordinates), max(p[1] for p in coordinates)
    min_y, max_y = min(p[2] for p in coordinates), max(p[2] for p in coordinates)
    center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2
    # MapLibre's world is 512 pixels at zoom 0. Leave room for marker badges;
    # cap close-up zoom so a single stop still shows its surrounding streets.
    zoom = min(
        16.0,
        math.log2((WIDTH - 2 * _PADDING) / (512 * max(max_x - min_x, 1e-12))),
        math.log2((HEIGHT - 2 * _PADDING) / (512 * max(max_y - min_y, 1e-12))),
    )
    zoom = max(1.0, zoom)
    world_size = 512 * 2**zoom
    return QuestMap(
        payload={
            "style": style,
            "latitude": math.degrees(
                math.atan(math.sinh(math.pi * (1 - 2 * center_y)))
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
                (HEIGHT / 2 + (y - center_y) * world_size) * SCALE,
            )
            for label, x, y, _ in coordinates
        ],
        icon_slugs={label: slug for label, _, _, slug in coordinates if slug},
    )


def place_labels(points):
    """Fit letter badges around fixed sprites without moving stop locations."""
    placed = []
    offsets = [
        (16, 14),
        (-16, 14),
        (16, -14),
        (-16, -14),
        (0, 32),
        (0, -32),
        (32, 0),
        (-32, 0),
        (32, 28),
        (-32, 28),
        (32, -28),
        (-32, -28),
        (0, 46),
        (0, -46),
        (46, 0),
        (-46, 0),
        (0, 62),
        (0, -62),
        (62, 0),
        (-62, 0),
    ]
    for index, (label, anchor_x, anchor_y) in enumerate(points):
        candidates = []
        for offset_x, offset_y in offsets:
            x, y = anchor_x + offset_x, anchor_y + offset_y
            if not (
                _BADGE_RADIUS + 3 <= x <= WIDTH * SCALE - _BADGE_RADIUS - 3
                and _BADGE_RADIUS + 3 <= y <= HEIGHT * SCALE - _BADGE_RADIUS - 33
            ):
                continue
            # Avoid obscuring a neighboring sprite or an earlier letter badge.
            collisions = sum(
                math.hypot(x - px, y - py) < _ICON_SIZE / 2 + _BADGE_RADIUS + 3
                for other_index, (_, px, py) in enumerate(points)
                if other_index != index
            ) + sum(
                math.hypot(x - px, y - py) < 2 * _BADGE_RADIUS + 3
                for _, px, py, _, _ in placed
            )
            candidates.append((collisions, x, y))
            if collisions == 0:
                break
        if candidates:
            _, x, y = min(candidates, key=lambda candidate: candidate[0])
        else:
            x = min(max(anchor_x, _BADGE_RADIUS + 3), WIDTH * SCALE - _BADGE_RADIUS - 3)
            y = min(
                max(anchor_y, _BADGE_RADIUS + 3), HEIGHT * SCALE - _BADGE_RADIUS - 33
            )
        placed.append((label, x, y, anchor_x, anchor_y))
    return placed


def _reward_sprite(image_bytes):
    """Prepare the existing alert sprite with a light outline for street maps."""
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            sprite = source.convert("RGBA")
        # Sprite sheets have transparent margins. Trim them so all reward
        # types occupy the same visual space without stretching their shape.
        bounds = sprite.getbbox()
        if not bounds:
            return None
        sprite = sprite.crop(bounds)
        sprite.thumbnail((_ICON_SIZE, _ICON_SIZE), Image.Resampling.LANCZOS)
    except (OSError, ValueError, TypeError):
        return None
    tile = Image.new("RGBA", (_SPRITE_TILE, _SPRITE_TILE))
    tile.alpha_composite(
        sprite,
        ((_SPRITE_TILE - sprite.width) // 2, (_SPRITE_TILE - sprite.height) // 2),
    )
    alpha = tile.getchannel("A")
    outline = alpha.filter(ImageFilter.MaxFilter(3))
    shadow = Image.new("RGBA", tile.size, (25, 38, 47, 0))
    shadow.putalpha(outline.filter(ImageFilter.GaussianBlur(1)).point(lambda a: a // 3))
    halo = Image.new("RGBA", tile.size, "#F8FBFD")
    halo.putalpha(outline)
    shadow.alpha_composite(halo)
    shadow.alpha_composite(tile)
    return shadow


def draw_quest_map(image_bytes, quest_map, is_leiria=True, icons=None):
    with Image.open(BytesIO(image_bytes), formats=["PNG", "WEBP"]) as source:
        if source.size != (WIDTH * SCALE, HEIGHT * SCALE):
            raise ValueError("Tile server returned an unexpected map size")
        image = source.convert("RGBA")
    draw = ImageDraw.Draw(image)
    color = "#3498DB" if is_leiria else "#2ECC71"
    font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
    sprites = {slug: _reward_sprite(data) for slug, data in (icons or {}).items()}
    placements = place_labels(quest_map.points)
    for label, badge_x, badge_y, anchor_x, anchor_y in placements:
        sprite = sprites.get(quest_map.icon_slugs.get(label, ""))
        if sprite is not None:
            half_tile = _SPRITE_TILE // 2
            image.alpha_composite(
                sprite, (round(anchor_x) - half_tile, round(anchor_y) - half_tile)
            )
            radius = _BADGE_RADIUS
        else:
            # The fallback also stays exactly on the stop's coordinates.
            badge_x, badge_y, radius = anchor_x, anchor_y, 18
        draw.ellipse(
            (badge_x - radius, badge_y - radius, badge_x + radius, badge_y + radius),
            fill=color,
            outline="#F8FBFD",
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
