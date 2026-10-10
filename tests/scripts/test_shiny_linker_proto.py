import pytest

from scripts.shiny_linker.proto import (
    blob,
    double,
    fields,
    integer,
    one,
    packed_ints,
    real,
    text,
    uint,
)


def test_integers_strings_and_doubles_round_trip():
    message = fields(integer(1, 300) + blob(2, "olá") + real(3, 39.5) + integer(1, 7))
    assert message[1] == [300, 7]
    assert text(message, 2) == "olá"
    assert len(one(message, 3)) == 8
    assert one(message, 9, "absent") == "absent"


def test_one_returns_the_last_value():
    assert one(fields(integer(1, 1) + integer(1, 2)), 1) == 2


def test_a_64_bit_value_survives():
    seed = (1 << 64) - 5
    assert one(fields(integer(1, seed)), 1) == seed


def test_text_of_a_missing_or_numeric_field_is_empty():
    assert text(fields(integer(1, 5)), 1) == ""
    assert text(fields(b""), 4) == ""


def test_packed_and_unpacked_ints_read_the_same():
    assert packed_ints(fields(blob(1, uint(3) + uint(70000))), 1) == [3, 70000]
    assert packed_ints(fields(integer(1, 3) + integer(1, 70000)), 1) == [3, 70000]


@pytest.mark.parametrize("data", [b"\x0a\x05ab", b"\x80", b"\x00\x01", b"\x0b"])
def test_malformed_input_raises(data):
    with pytest.raises(ValueError):
        fields(data)


def test_a_double_is_read_back_as_written():
    assert double(fields(real(3, -122.0258)), 3) == -122.0258


def test_a_missing_double_is_the_default():
    assert double(fields(integer(1, 5)), 3) == 0.0
