"""Verify map fitting, the site's Pokéstop on each point and readable letters."""

from io import BytesIO

from PIL import Image
import pytest

from modules.quest_map import SCALE, draw_quest_map, fit_quest_map, place_labels


@pytest.mark.parametrize(
    "stops",
    [
        [{"lat": 39.7, "lon": -8.8}],
        [{"lat": 39.7, "lon": -8.8}, {"lat": 39.75, "lon": -8.85}],
        [{"lat": 39.7, "lon": -8.8}, {"lat": 39.72, "lon": -8.8}],
        [{"lat": 39.7, "lon": -8.8}, {"lat": 39.7, "lon": -8.85}],
    ],
)
def test_fit_leaves_room_above_each_stop_for_its_pin(stops):
    fitted = fit_quest_map(stops, "osm-bright")
    for _, x, y in fitted.points:
        assert 47.9 <= x <= 1152.1
        # 32 CSS px above for the stop, 18 below for its base and the credit.
        assert 63.9 <= y <= 564.1
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
    assert fitted.points == [("E", 600, pytest.approx(314))]
    assert fit_quest_map([{"lat": 39.7}], "osm-bright") is None


def test_single_stop_and_duplicate_stops_use_neighborhood_zoom():
    fitted = fit_quest_map([{"lat": 39.7, "lon": -8.8}] * 10, "osm-bright")
    assert fitted.payload["zoom"] == 16
    placed = place_labels(fitted.points)
    assert [p[0] for p in placed] == list("ABCDEFGHIJ")
    for _, x, y, anchor_x, anchor_y in placed:
        assert (anchor_x, anchor_y) == (600, pytest.approx(314))
        assert 13 <= x <= 1187
        assert 13 <= y <= 557


def test_letter_sits_beside_its_own_post():
    _, x, y, ax, ay = place_labels([("A", 600, 314)])[0]
    assert (x - ax, y - ay) == (-17, -9)


def test_letter_avoids_a_neighbouring_stop():
    points = [("A", 600, 314), ("B", 570, 314)]
    placed = place_labels(points)
    assert [(p[0], p[3], p[4]) for p in placed] == points
    # A's default spot is on B's stop, so it moves to the other side of its post.
    assert placed[0][1] > 600


def test_wrong_sized_server_image_is_rejected():
    fitted = fit_quest_map([{"lat": 39.7, "lon": -8.8}], "osm-bright")
    image = BytesIO()
    Image.new("RGB", (600, 300)).save(image, format="PNG")
    with pytest.raises(ValueError, match="unexpected map size"):
        draw_quest_map(image.getvalue(), fitted)


def _map(colour="#F0F0F0"):
    base = BytesIO()
    Image.new("RGB", (1200, 600), colour).save(base, format="PNG")
    return base.getvalue()


def test_the_stops_foot_is_on_its_point_and_its_letter_beside_it():
    fitted = fit_quest_map([{"lat": 39.7, "lon": -8.8}], "osm-bright")
    _, ax, ay = fitted.points[0]
    result = draw_quest_map(_map(), fitted, color="#3498DB")
    with Image.open(BytesIO(result)) as image:
        # The round blue base is on the point; the cube's blue side above it.
        base = image.getpixel((round(ax), round(ay)))
        assert base[2] > base[0] + 40
        cube = image.getpixel((round(ax) - 6, round(ay) - 22))
        assert cube[2] > 150 and cube[0] < 120
        # Nothing of the reward above the stop.
        assert image.getpixel((round(ax), round(ay) - 50)) == (240, 240, 240)
        # The letter, left of the post.
        assert image.getpixel((round(ax) - 17 - 6, round(ay) - 9)) == (52, 152, 219)


def test_southern_stop_is_drawn_over_the_northern_one():
    fitted = fit_quest_map([{"lat": 39.7, "lon": -8.8}], "osm-bright")
    fitted.points = [("A", 600, 330), ("B", 600, 310)]
    with Image.open(BytesIO(draw_quest_map(_map(), fitted))) as image:
        # A's cube (further south) covers B's post.
        cube = image.getpixel((594, 330 - 22))
        assert cube[2] > 150 and cube[0] < 120


def test_pins_are_placed_on_the_map_servers_256_pixel_world():
    # The tileserver's zoom counts a 256-pixel world at zoom 0. Assuming 512
    # drew every pin at twice its real offset from the centre: measured on
    # 2026-10-10 against OpenStreetMap roundabout centres in Leiria, the pins
    # were about 500 m out on a two-stop map.
    fitted = fit_quest_map(
        [{"lat": 39.74, "lon": -8.81}, {"lat": 39.74, "lon": -8.80}], "style"
    )
    (_, west, _), (_, east, _) = fitted.points
    world = 256 * 2 ** fitted.payload["zoom"] * SCALE
    assert east - west == pytest.approx(0.01 / 360 * world)
