import re

import pytest

from modules.trade_codes import (
    ALPHABET,
    format_code,
    generate,
    hash_code,
    is_valid,
    normalise,
)

VECTOR_CODE = "K7MQ-4XPD-92HT-B3WN"
VECTOR_HASH = "7534c390651746172b7a96aa4a5f2d1a0c1c4d573a97866cf220c005ac060a4c"


def test_generate_is_16_chars_from_the_alphabet():
    code = generate()
    assert len(code) == 16
    assert set(code) <= set(ALPHABET)


def test_generate_does_not_repeat():
    assert len({generate() for _ in range(50)}) == 50


def test_format_code_groups_in_fours():
    assert format_code("K7MQ4XPD92HTB3WN") == "K7MQ-4XPD-92HT-B3WN"


def test_normalise_strips_separators_and_case():
    assert normalise(" k7mq-4xpd 92ht_b3wn ") == "K7MQ4XPD92HTB3WN"


def test_normalise_maps_lookalike_characters():
    # O/o -> 0, I/i/L/l -> 1: the alphabet has none of them, so a hand-typed
    # code that used them is still the code its owner was sent.
    assert normalise("OIL0000000000000") == "0110000000000000"


def test_is_valid_rejects_wrong_length_and_unknown_characters():
    assert is_valid(VECTOR_CODE)
    assert not is_valid("K7MQ4XPD92HTB3W")
    assert not is_valid("K7MQ4XPD92HTB3WU")


def test_hash_code_matches_the_shared_vector():
    assert hash_code(VECTOR_CODE) == VECTOR_HASH
    assert hash_code("k7mq4xpd92htb3wn") == VECTOR_HASH


def test_hash_code_rejects_an_invalid_code():
    with pytest.raises(ValueError):
        hash_code("nope")


def test_generated_codes_are_valid_and_hashable():
    code = generate()
    assert is_valid(code)
    assert re.fullmatch(r"[0-9a-f]{64}", hash_code(format_code(code)))
