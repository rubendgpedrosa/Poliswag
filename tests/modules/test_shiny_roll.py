"""The roll must match the community's JavaScript bit for bit: the vectors
below were produced by that snippet under Node, not by this module."""

import pytest

from modules.shiny_roll import is_player_id, normalise_friend_code, shiny_die

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


@pytest.mark.parametrize("value", [HEX, GOOGLE, "fedcba9876543210"])
def test_player_ids_in_the_two_known_shapes(value):
    assert is_player_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        None,
        "0123456789ABCDEF",  # upper case
        "0123456789abcde",  # 15
        "0123456789abcdef0",  # 17
        "12345678901234567890",  # 20 digits
        "E:abc+/=",  # the viewer-specific social ID
        HEX + "\n",
    ],
)
def test_anything_else_is_not_a_player_id(value):
    assert not is_player_id(value)


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
