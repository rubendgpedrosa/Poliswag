"""Will this wild spawn be shiny for this player?

The game decides it from two values: the spawn's encounter ID and the
player's internal ID (the one a raid-lobby creator sees in
`invited_player_ids`, never the friend-code or GET_PLAYER identifiers, which
differ per viewer). SHA-1 over the encounter ID as eight little-endian bytes
followed by the ID text seeds a `java.util.Random`; its `nextInt(512)` is the
roll, and 0 is shiny at the base 1/512 rate. Because 512 is a power of two,
0 is also shiny at 1/64, 1/128 and 1/256.

Confirmed on 2026-10-10 against three scanner accounts (3 of 3 recorded
shinies roll 0, 0 of 201 non-shinies do): /root/unonwhash/CLAUDE-HANDOVER.md.

Standard library only: scripts/shiny_linker imports this from its own venv.
"""

import hashlib
import re

_MASK = (1 << 48) - 1
_MULTIPLIER = 0x5DEECE66D
_UINT64 = (1 << 64) - 1
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
    seed = hashlib.sha1(encounter + player_id.encode("latin-1")).digest()[:8]
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


def shiny_die(encounter_id, player_id):
    """The 512-sided roll (0-511) for one spawn and one player."""
    return next_int(encounter_id, player_id, 512)


def is_player_id(value):
    return bool(value) and _PLAYER_ID.match(value) is not None


def normalise_friend_code(raw):
    """Twelve digits, however they were typed; None if that is not what it is."""
    digits = re.sub(r"[\s-]", "", raw or "")
    return digits if _FRIEND_CODE.match(digits) else None
