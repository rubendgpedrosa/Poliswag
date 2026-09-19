"""Login codes for the trades tool.

A code is a password Poliswag DMs to a player; `apps/trades/lib/code.ts` is
the same three functions in TypeScript, pinned to the same test vector. Only
the SHA-256 hash is ever stored (pogoleiria.trade_player.code_hash), so a
leaked database row doesn't hand out logins.

The alphabet is Crockford base32 without I, L, O and U, so nothing in a code
can be misread when someone types it off their phone; normalise() folds the
lookalikes back in.
"""

import hashlib
import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
LENGTH = 16
GROUP = 4

_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})


def generate():
    """A fresh code, unformatted: 16 chars, 80 bits of entropy."""
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def format_code(code):
    """`K7MQ4XPD92HTB3WN` -> `K7MQ-4XPD-92HT-B3WN`, for the DM."""
    code = normalise(code)
    return "-".join(code[i : i + GROUP] for i in range(0, len(code), GROUP))


def normalise(raw):
    """Anything a human typed -> the canonical 16-char form."""
    text = "".join(c for c in (raw or "").upper() if c.isalnum())
    return text.translate(_LOOKALIKES)


def is_valid(raw):
    code = normalise(raw)
    return len(code) == LENGTH and set(code) <= set(ALPHABET)


def hash_code(raw):
    """SHA-256 of the normalised code, hex. Raises ValueError if malformed."""
    if not is_valid(raw):
        raise ValueError("malformed trade code")
    return hashlib.sha256(normalise(raw).encode()).hexdigest()
