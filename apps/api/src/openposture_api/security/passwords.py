"""Password hashing with argon2id.

`argon2-cffi`'s defaults are used as they ship — 64 MiB, three passes, four lanes — rather than
hand-tuned here. They track the RFC 9106 recommendations and are revised by people who follow the
cryptanalysis; a number invented in this file would be a number nobody revisits.

**What argon2 buys is cost per guess, not entropy.** Hashing adds no randomness: a weak password
is weak after hashing too. What the parameters do is make each attempt expensive — measured at
roughly 23 ms on a development laptop, against the ~10⁹/s a GPU manages on a bare SHA-256 — and
the 64 MiB memory requirement is what stops an attacker buying that back with parallel hardware.
That memory-hardness is why argon2id, rather than bcrypt.

Contrast :mod:`openposture_api.security.tokens`, which hashes refresh tokens with plain SHA-256.
Opposite choice, same reasoning applied to a different input: a 256-bit random token has nothing
to guess, so a slow hash there would buy no security and would put a deliberately expensive
function on the refresh path.
"""

from __future__ import annotations

from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

__all__ = ["hash_password", "verify_password"]

_HASHER: Final = PasswordHasher()
"""One hasher for the process.

`PasswordHasher` holds only its parameters — it carries no per-password state, so a single
instance is safe to share across concurrent requests. Constructing one per call would be waste
without being safer.
"""


def hash_password(password: str) -> str:
    """Hash a plaintext password for storage.

    Returns argon2's encoded form — algorithm, version, parameters and salt, all in the one
    string::

        $argon2id$v=19$m=65536,t=3,p=4$<salt>$<digest>

    That self-describing format is why there is no separate salt column and no parameter column
    in `users`: everything needed to verify against this hash later, including the parameters it
    was created under, travels with it. Retuning the parameters therefore does not invalidate
    existing hashes.

    Unlike bcrypt, argon2 does not silently truncate long inputs, so there is no 72-byte cliff to
    work around here. The length cap lives at the request schema, where it belongs — it is there
    to bound the work per request, not to protect the algorithm.
    """
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Check a password against a stored hash. Never raises.

    A boolean, not an exception, and deliberately one boolean for every way this can fail —
    wrong password, malformed hash, unknown algorithm. ADR-0003 requires that sign-in not reveal
    whether an email is registered, and a caller that has to distinguish `VerifyMismatchError`
    from `InvalidHashError` is a caller with two code paths that can diverge into an oracle.

    The comparison inside argon2 is constant-time, so a wrong password costs the same as a right
    one. That property only survives to the endpoint if the *caller* also spends the same work
    when no user exists at all — see the login route, which hashes against a dummy for exactly
    that reason. A missing user is otherwise the fast path, and a fast path is a signal.
    """
    try:
        return _HASHER.verify(password_hash, password)
    except (Argon2Error, InvalidHashError):
        # Argon2Error covers the mismatch and verification failures; InvalidHashError covers a
        # stored value that is not an argon2 hash at all. Both mean "not authenticated", and
        # neither is worth telling the client apart from the other.
        return False
