"""The roll must match the community's JavaScript bit for bit: the vectors
below were produced by that snippet under Node, not by this module."""

import pytest

from modules.shiny_roll import is_player_id, next_int, normalise_friend_code, shiny_die

HEX = "0123456789abcdef"
GOOGLE = "123456789012345678901"


@pytest.mark.parametrize(
    "encounter_id, player_id, roll",
    [
        ("1", HEX, 113),
        ("9007199254740993", HEX, 408),  # above 2**53
        ("18446744073709551615", HEX, 253),  # largest uint64
        ("12345678901234567890", GOOGLE, 265),
        ("4611686018427387904", GOOGLE, 442),
        ("5550001112223334445", "fedcba9876543210", 27),
        ("10000000000000000168", HEX, 0),
        ("10000000000000000398", HEX, 0),
        ("7000000000000000028", GOOGLE, 0),
    ],
)
def test_matches_the_reference_implementation(encounter_id, player_id, roll):
    assert shiny_die(encounter_id, player_id) == roll


def test_an_integer_encounter_id_rolls_the_same():
    assert shiny_die(10000000000000000168, HEX) == 0


def test_a_non_numeric_encounter_id_is_an_error():
    with pytest.raises(ValueError):
        shiny_die("abc", HEX)


OBJECT_ID = "0123456789abcdef01234567"  # 24 hex: the owner's own account, 2026-10-10


@pytest.mark.parametrize("value", [HEX, GOOGLE, OBJECT_ID, "fedcba9876543210"])
def test_player_ids_in_the_shapes_seen_so_far(value):
    assert is_player_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        None,
        "0123456789ABCDEF",  # upper case
        "0123456789abcde",  # too short for hex
        "0123456789abcdef" * 2 + "0",  # 33: too long
        "12345678901234",  # 14 digits
        "E:abc+/=",  # the viewer-specific social ID
        "E:" + "A" * 43 + "=",  # the same, full length
        HEX + "\n",
        "0123456789abcdeg",  # not hex
    ],
)
def test_anything_else_is_not_a_player_id(value):
    assert not is_player_id(value)


def test_the_roll_takes_a_24_character_id():
    assert 0 <= shiny_die("10000000000000000168", OBJECT_ID) < 512


@pytest.mark.parametrize(
    "raw", ["123456789012", "1234 5678 9012", "1234-5678-9012", " 1234 5678 9012 "]
)
def test_friend_codes_are_normalised_to_twelve_digits(raw):
    assert normalise_friend_code(raw) == "123456789012"


@pytest.mark.parametrize(
    "raw", ["", None, "12345678901", "1234567890123", "1234 5678 901a", "remover"]
)
def test_what_is_not_a_friend_code(raw):
    assert normalise_friend_code(raw) is None


# Produced by the real java.util.Random (OpenJDK 17), seeded as the roll is.
JAVA_ROLLS = [
    ("1", "0123456789abcdef", 512, 113),
    ("1", "0123456789abcdef", 25, 15),
    ("1", "0123456789abcdef", 20, 5),
    ("1", "0123456789abcdef", 64, 14),
    ("1", "0123456789abcdef", 128, 28),
    ("1", "0123456789abcdef", 450, 215),
    ("1", "123456789012345678901", 512, 9),
    ("1", "123456789012345678901", 25, 12),
    ("1", "123456789012345678901", 20, 12),
    ("1", "123456789012345678901", 64, 1),
    ("1", "123456789012345678901", 128, 2),
    ("1", "123456789012345678901", 450, 312),
    ("1", "0123456789abcdef01234567", 512, 498),
    ("1", "0123456789abcdef01234567", 25, 19),
    ("1", "0123456789abcdef01234567", 20, 9),
    ("1", "0123456789abcdef01234567", 64, 62),
    ("1", "0123456789abcdef01234567", 128, 124),
    ("1", "0123456789abcdef01234567", 450, 419),
    ("9007199254740993", "0123456789abcdef", 512, 408),
    ("9007199254740993", "0123456789abcdef", 25, 22),
    ("9007199254740993", "0123456789abcdef", 20, 7),
    ("9007199254740993", "0123456789abcdef", 64, 51),
    ("9007199254740993", "0123456789abcdef", 128, 102),
    ("9007199254740993", "0123456789abcdef", 450, 397),
    ("9007199254740993", "123456789012345678901", 512, 55),
    ("9007199254740993", "123456789012345678901", 25, 9),
    ("9007199254740993", "123456789012345678901", 20, 9),
    ("9007199254740993", "123456789012345678901", 64, 6),
    ("9007199254740993", "123456789012345678901", 128, 13),
    ("9007199254740993", "123456789012345678901", 450, 209),
    ("9007199254740993", "0123456789abcdef01234567", 512, 241),
    ("9007199254740993", "0123456789abcdef01234567", 25, 21),
    ("9007199254740993", "0123456789abcdef01234567", 20, 11),
    ("9007199254740993", "0123456789abcdef01234567", 64, 30),
    ("9007199254740993", "0123456789abcdef01234567", 128, 60),
    ("9007199254740993", "0123456789abcdef01234567", 450, 421),
    ("18446744073709551615", "0123456789abcdef", 512, 253),
    ("18446744073709551615", "0123456789abcdef", 25, 19),
    ("18446744073709551615", "0123456789abcdef", 20, 9),
    ("18446744073709551615", "0123456789abcdef", 64, 31),
    ("18446744073709551615", "0123456789abcdef", 128, 63),
    ("18446744073709551615", "0123456789abcdef", 450, 19),
    ("18446744073709551615", "123456789012345678901", 512, 384),
    ("18446744073709551615", "123456789012345678901", 25, 9),
    ("18446744073709551615", "123456789012345678901", 20, 14),
    ("18446744073709551615", "123456789012345678901", 64, 48),
    ("18446744073709551615", "123456789012345678901", 128, 96),
    ("18446744073709551615", "123456789012345678901", 450, 334),
    ("18446744073709551615", "0123456789abcdef01234567", 512, 255),
    ("18446744073709551615", "0123456789abcdef01234567", 25, 17),
    ("18446744073709551615", "0123456789abcdef01234567", 20, 12),
    ("18446744073709551615", "0123456789abcdef01234567", 64, 31),
    ("18446744073709551615", "0123456789abcdef01234567", 128, 63),
    ("18446744073709551615", "0123456789abcdef01234567", 450, 92),
    ("12345678901234567890", "0123456789abcdef", 512, 78),
    ("12345678901234567890", "0123456789abcdef", 25, 2),
    ("12345678901234567890", "0123456789abcdef", 20, 12),
    ("12345678901234567890", "0123456789abcdef", 64, 9),
    ("12345678901234567890", "0123456789abcdef", 128, 19),
    ("12345678901234567890", "0123456789abcdef", 450, 52),
    ("12345678901234567890", "123456789012345678901", 512, 265),
    ("12345678901234567890", "123456789012345678901", 25, 19),
    ("12345678901234567890", "123456789012345678901", 20, 19),
    ("12345678901234567890", "123456789012345678901", 64, 33),
    ("12345678901234567890", "123456789012345678901", 128, 66),
    ("12345678901234567890", "123456789012345678901", 450, 119),
    ("12345678901234567890", "0123456789abcdef01234567", 512, 41),
    ("12345678901234567890", "0123456789abcdef01234567", 25, 3),
    ("12345678901234567890", "0123456789abcdef01234567", 20, 13),
    ("12345678901234567890", "0123456789abcdef01234567", 64, 5),
    ("12345678901234567890", "0123456789abcdef01234567", 128, 10),
    ("12345678901234567890", "0123456789abcdef01234567", 450, 203),
    ("10000000000000000168", "0123456789abcdef", 512, 0),
    ("10000000000000000168", "0123456789abcdef", 25, 18),
    ("10000000000000000168", "0123456789abcdef", 20, 3),
    ("10000000000000000168", "0123456789abcdef", 64, 0),
    ("10000000000000000168", "0123456789abcdef", 128, 0),
    ("10000000000000000168", "0123456789abcdef", 450, 143),
    ("10000000000000000168", "123456789012345678901", 512, 118),
    ("10000000000000000168", "123456789012345678901", 25, 23),
    ("10000000000000000168", "123456789012345678901", 20, 13),
    ("10000000000000000168", "123456789012345678901", 64, 14),
    ("10000000000000000168", "123456789012345678901", 128, 29),
    ("10000000000000000168", "123456789012345678901", 450, 73),
    ("10000000000000000168", "0123456789abcdef01234567", 512, 364),
    ("10000000000000000168", "0123456789abcdef01234567", 25, 13),
    ("10000000000000000168", "0123456789abcdef01234567", 20, 3),
    ("10000000000000000168", "0123456789abcdef01234567", 64, 45),
    ("10000000000000000168", "0123456789abcdef01234567", 128, 91),
    ("10000000000000000168", "0123456789abcdef01234567", 450, 113),
    ("7000000000000000028", "0123456789abcdef", 512, 386),
    ("7000000000000000028", "0123456789abcdef", 25, 5),
    ("7000000000000000028", "0123456789abcdef", 20, 15),
    ("7000000000000000028", "0123456789abcdef", 64, 48),
    ("7000000000000000028", "0123456789abcdef", 128, 96),
    ("7000000000000000028", "0123456789abcdef", 450, 305),
    ("7000000000000000028", "123456789012345678901", 512, 0),
    ("7000000000000000028", "123456789012345678901", 25, 16),
    ("7000000000000000028", "123456789012345678901", 20, 1),
    ("7000000000000000028", "123456789012345678901", 64, 0),
    ("7000000000000000028", "123456789012345678901", 128, 0),
    ("7000000000000000028", "123456789012345678901", 450, 291),
    ("7000000000000000028", "0123456789abcdef01234567", 512, 491),
    ("7000000000000000028", "0123456789abcdef01234567", 25, 16),
    ("7000000000000000028", "0123456789abcdef01234567", 20, 16),
    ("7000000000000000028", "0123456789abcdef01234567", 64, 61),
    ("7000000000000000028", "0123456789abcdef01234567", 128, 122),
    ("7000000000000000028", "0123456789abcdef01234567", 450, 16),
    ("5000000000000000040", "0123456789abcdef", 25, 0),
    ("5000000000000000040", "0123456789abcdef", 512, 308),
    ("5000000000000000059", "0123456789abcdef", 25, 0),
    ("5000000000000000059", "0123456789abcdef", 512, 340),
    ("5000000000000000069", "0123456789abcdef", 25, 0),
    ("5000000000000000069", "0123456789abcdef", 512, 64),
]


@pytest.mark.parametrize("encounter_id, player_id, bound, roll", JAVA_ROLLS)
def test_next_int_matches_java_for_every_bound(encounter_id, player_id, bound, roll):
    assert next_int(encounter_id, player_id, bound) == roll


def test_the_base_roll_is_next_int_512():
    for encounter_id, player_id, bound, roll in JAVA_ROLLS:
        if bound == 512:
            assert shiny_die(encounter_id, player_id) == roll


def test_the_draw_scaled_to_a_power_of_two_is_javas_next_int():
    from modules.shiny_roll import shiny_draw

    for encounter_id, player_id, bound, roll in JAVA_ROLLS:
        if bound in (512, 128, 64):
            assert int(shiny_draw(encounter_id, player_id) * bound) == roll


def test_shiny_is_the_draw_falling_under_one_in_odds():
    # Measured on the 2026-10-10 Community Day (about 1 in 25) with a proven
    # scanner account: 7 shinies at draws up to 0.0344, 154 non-shinies from
    # 0.0420 up. nextInt(25) == 0 predicted 10 of the 161 and 1 was shiny.
    from modules.shiny_roll import is_shiny_at

    assert is_shiny_at(0.0344, 25) and not is_shiny_at(0.042, 25)
    assert is_shiny_at(0.0019, 512) and not is_shiny_at(0.0344, 512)


def test_at_the_base_odds_it_is_the_512_roll_of_zero():
    from modules.shiny_roll import is_shiny_at, shiny_draw

    for encounter_id, player_id, bound, roll in JAVA_ROLLS:
        if bound == 512:
            assert is_shiny_at(shiny_draw(encounter_id, player_id), 512) == (roll == 0)


# The test vectors of Golbat PR #423 (decoder/api_pokemon_shiny_test.go):
# player ID, encounter ID, and the first nextFloat of the seeded Random. The
# third has a non-ASCII character, which pins the ID's encoding as UTF-8.
GOLBAT_VECTORS = [
    ("0123456789abcdef", 1, 0.22137850522994995),
    ("Player-ID:ABC/123", 1, 0.3684830665588379),
    (" Trainer-é ", 1, 0.03772324323654175),
]


@pytest.mark.parametrize("player_id, encounter_id, next_float", GOLBAT_VECTORS)
def test_the_draw_is_javas_next_float_as_golbat_computes_it(
    player_id, encounter_id, next_float
):
    from modules.shiny_roll import shiny_draw

    assert shiny_draw(encounter_id, player_id) == next_float


def test_the_cutoff_is_golbats_ceiling_on_a_24_bit_roll():
    from modules.shiny_roll import is_shiny_at

    scale = 1 << 24
    # 1/25: Golbat's cutoff is ceil(0.04 * 2**24) = 671089, so roll 671088 is
    # shiny and 671089 is not.
    assert is_shiny_at(671088 / scale, 25) and not is_shiny_at(671089 / scale, 25)
    # 1/512: exactly 2**24 / 512 = 32768.
    assert is_shiny_at(32767 / scale, 512) and not is_shiny_at(32768 / scale, 512)
