"""Verify map fitting and readable labels for clustered quest locations."""

from io import BytesIO
import math

from PIL import Image
import pytest

from modules.quest_map import draw_quest_map, fit_quest_map, place_labels


@pytest.mark.parametrize(
    "stops",
    [
        [{"lat": 39.7, "lon": -8.8}],
        [{"lat": 39.7, "lon": -8.8}, {"lat": 39.75, "lon": -8.85}],
        [{"lat": 39.7, "lon": -8.8}, {"lat": 39.72, "lon": -8.8}],
        [{"lat": 39.7, "lon": -8.8}, {"lat": 39.7, "lon": -8.85}],
    ],
)
def test_fit_keeps_stops_within_padded_viewport(stops):
    fitted = fit_quest_map(stops, "osm-bright")
    for _, x, y in fitted.points:
        assert 79.9 <= x <= 1120.1
        assert 79.9 <= y <= 520.1
    assert 1 <= fitted.payload["zoom"] <= 16


def test_invalid_coordinates_keep_original_list_letters():
    fitted = fit_quest_map(
        [
            {"lat": None, "lon": -8.8},
            {"lat": "nan", "lon": -8.8},
            {"lat": "inf", "lon": -8.8},
            {"lat": 91, "lon": -8.8},
            {"lat": "39.7", "lon": "-8.8"},
        ],
        "osm-bright",
    )
    assert fitted.points == [("E", 600, 300)]
    assert fit_quest_map([{"lat": 39.7}], "osm-bright") is None


def test_single_stop_and_duplicate_stops_use_neighborhood_zoom():
    fitted = fit_quest_map([{"lat": 39.7, "lon": -8.8}] * 10, "osm-bright")
    assert fitted.payload["zoom"] == 16
    placed = place_labels(fitted.points)
    assert len(placed) == 10
    assert [p[0] for p in placed] == list("ABCDEFGHIJ")
    for i, (_, x, y, anchor_x, anchor_y) in enumerate(placed):
        assert (anchor_x, anchor_y) == (600, 300)
        assert 15 <= x <= 1185
        assert 15 <= y <= 555
        for other in placed[:i]:
            assert math.hypot(x - other[1], y - other[2]) >= 27


def test_nearby_stops_have_distinct_labels_and_exact_anchors():
    points = [("G", 500, 450), ("H", 523, 450), ("I", 500, 475)]
    placed = place_labels(points)
    assert [(p[0], p[3], p[4]) for p in placed] == points
    assert len({(p[1], p[2]) for p in placed}) == 3


def test_diagonal_sprites_stay_at_their_locations_when_badges_fit():
    points = [("B", 585, 110), ("C", 527, 139)]
    placed = place_labels(points)
    assert [(p[0], p[3], p[4]) for p in placed] == points
    assert [(p[1] - p[3], p[2] - p[4]) for p in placed] == [(16, 14), (16, 14)]


def test_letter_badge_cannot_cover_a_neighbor_sprite():
    points = [("A", 500, 450), ("B", 523, 450)]
    placed = place_labels(points)
    assert placed[0][1:3] != (516, 464)
    assert [(p[0], p[3], p[4]) for p in placed] == points


def test_wrong_sized_server_image_is_rejected():
    fitted = fit_quest_map([{"lat": 39.7, "lon": -8.8}], "osm-bright")
    image = BytesIO()
    Image.new("RGB", (600, 300)).save(image, format="PNG")
    with pytest.raises(ValueError, match="unexpected map size"):
        draw_quest_map(image.getvalue(), fitted)


def test_reward_sprite_and_letter_badge_render_together():
    fitted = fit_quest_map(
        [{"lat": 39.7, "lon": -8.8, "quest_slug": "pokemon/928.png"}], "osm-bright"
    )
    base = BytesIO()
    Image.new("RGB", (1200, 600), "#F0F0F0").save(base, format="PNG")
    sprite = Image.new("RGBA", (128, 128))
    # A synthetic sprite with transparent padding proves cropping and alpha
    # compositing preserve the map rather than painting an opaque square.
    sprite.paste((190, 40, 80, 255), (48, 48, 80, 80))
    icon = BytesIO()
    sprite.save(icon, format="PNG")
    result = draw_quest_map(
        base.getvalue(), fitted, icons={"pokemon/928.png": icon.getvalue()}
    )
    with Image.open(BytesIO(result)) as image:
        assert image.getpixel((600, 300)) == (190, 40, 80)
        assert image.getpixel((565, 265)) == (240, 240, 240)
        assert image.getpixel((625, 318)) == (52, 152, 219)


def test_corrupt_reward_icon_falls_back_to_letter_marker():
    fitted = fit_quest_map(
        [{"lat": 39.7, "lon": -8.8, "quest_slug": "pokemon/928.png"}], "osm-bright"
    )
    base = BytesIO()
    Image.new("RGB", (1200, 600), "white").save(base, format="PNG")
    result = draw_quest_map(
        base.getvalue(), fitted, icons={"pokemon/928.png": b"bad icon"}
    )
    with Image.open(BytesIO(result)) as image:
        assert image.size == (1200, 600)


def test_crowded_icons_stay_at_exact_locations_without_connectors():
    fitted = fit_quest_map([{"lat": 39.7, "lon": -8.8}], "osm-bright")
    fitted.points = [("G", 500, 450), ("H", 523, 450)]
    fitted.icon_slugs = {"G": "pokemon/928.png", "H": "pokemon/928.png"}
    base = BytesIO()
    Image.new("RGB", (1200, 600), "white").save(base, format="PNG")
    icon = BytesIO()
    Image.new("RGBA", (32, 32), (190, 40, 80, 255)).save(icon, format="PNG")
    result = draw_quest_map(
        base.getvalue(), fitted, icons={"pokemon/928.png": icon.getvalue()}
    )
    with Image.open(BytesIO(result)) as image:
        assert image.getpixel((500, 450)) == (190, 40, 80)
        assert image.getpixel((523, 450)) == (190, 40, 80)
        assert image.getpixel((523, 420)) == (255, 255, 255)
