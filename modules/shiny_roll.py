"""Will this wild spawn be shiny for this player?

The game decides it from two values: the spawn's encounter ID and the
player's internal ID (not the public trainer identifiers, which differ per
viewer). SHA-1 over the encounter ID as eight little-endian bytes
followed by the ID text seeds a `java.util.Random`; its `nextInt(512)` is the
roll, and 0 is shiny at the base 1/512 rate. More generally the spawn is
shiny when the first draw, as a fraction, is under 1/odds (`shiny_draw`,
`is_shiny_at`), so a base-rate shiny is shiny at any better odds too.

Confirmed on 2026-10-10 against three scanner accounts (3 of 3 recorded
shinies roll 0, 0 of 201 non-shinies do): /root/unonwhash/CLAUDE-HANDOVER.md.

Standard library only, so it can be imported outside the bot's venv.
"""

import hashlib
import math
import re

_MASK = (1 << 48) - 1
_MULTIPLIER = 0x5DEECE66D
_UINT64 = (1 << 64) - 1
# Java's nextFloat is a 24-bit draw; odds are held against it at that scale.
_FLOAT_SCALE = 1 << 24
# Seen so far: 16 lowercase hex (Pokémon Trainer Club accounts), 24 lowercase
# hex (the owner's account), and, by report, 21 digits for Google sign-ups.
# Kept loose within that family, because a shape nobody has reported yet
# should link rather than fail; what must never pass is the viewer-specific
# social ID ("E:" + base64 + "="), which is not what the game rolls with.
_PLAYER_ID = re.compile(r"(?:[0-9a-f]{16,32}|[0-9]{15,24})\Z")
_FRIEND_CODE = re.compile(r"[0-9]{12}\Z")


def _random(encounter_id, player_id):
    """The 31-bit draws of the java.util.Random the game seeds for this pair."""
    encounter = (int(encounter_id) & _UINT64).to_bytes(8, "little")
    seed = hashlib.sha1(encounter + player_id.encode("utf-8")).digest()[:8]
    state = (int.from_bytes(seed, "little") ^ _MULTIPLIER) & _MASK
    while True:
        state = (state * _MULTIPLIER + 11) & _MASK
        yield state >> 17


def next_int(encounter_id, player_id, bound):
    """`new java.util.Random(seed).nextInt(bound)`: 0 is shiny at 1-in-`bound`
    odds. Checked against OpenJDK for powers of two and for other bounds
    (which Java draws again for, to stay unbiased)."""
    draws = _random(encounter_id, player_id)
    value = next(draws)
    if bound & (bound - 1) == 0:
        return (bound * value) >> 31
    while value - value % bound + bound - 1 >= 1 << 31:
        value = next(draws)
    return value % bound


def shiny_draw(encounter_id, player_id):
    """The first `nextFloat` of the game's Random for this pair: a 24-bit
    draw as a fraction in [0, 1). This one number decides shininess at any
    odds."""
    return (next(_random(encounter_id, player_id)) >> 7) / _FLOAT_SCALE


def personal_draws(encounter_id, player_id):
    """First and second nextFloat fractions, matching Golbat PR #423.

    The second draw is the experimental background candidate. No extra draw
    is skipped, and changing a shiny cutoff never changes its position.
    """
    draws = _random(encounter_id, player_id)
    return tuple((next(draws) >> 7) / _FLOAT_SCALE for _ in range(2))


def is_background_at(draw, probability):
    """A configured background cutoff; unknown/invalid rates cannot qualify."""
    return (
        math.isfinite(probability)
        and 0 < probability <= 1
        and math.isfinite(draw)
        and 0 <= draw < 1
        and round(draw * _FLOAT_SCALE) < math.ceil(probability * _FLOAT_SCALE)
    )


def is_shiny_at(draw, odds):
    """Shiny at 1-in-`odds`: `nextFloat() < 1/odds`.

    Measured before it was read anywhere (2026-10-10, a Community Day at about
    1 in 25, a scanner account with a proven ID, 161 encounters: the 7 shinies
    had the 7 lowest draws), then found to be what Golbat PR #423 implements.
    The arithmetic is that PR's, a ceiling on the 24-bit roll, so the two agree
    on every spawn. `next_int(odds) == 0`, the rule first reported for event
    odds, only agrees with this at powers of two.
    """
    return round(draw * _FLOAT_SCALE) < math.ceil(_FLOAT_SCALE / odds)


def shiny_die(encounter_id, player_id):
    """The 512-sided roll (0-511) for one spawn and one player."""
    return next_int(encounter_id, player_id, 512)


def is_player_id(value):
    return bool(value) and _PLAYER_ID.match(value) is not None


def normalise_friend_code(raw):
    """Twelve digits, however they were typed; None if that is not what it is."""
    digits = re.sub(r"[\s-]", "", raw or "")
    return digits if _FRIEND_CODE.match(digits) else None
