"""Access tokens (signed JWTs) and refresh tokens (opaque random strings).

Two credentials, deliberately different in kind, because they answer different questions.

**The access token is a signed claim, verified without a database.** That is its whole value —
every request checks it in-process, so authentication costs no query. The price is that it cannot
be withdrawn: a signed token stays valid until `exp`, whatever happens to the account meanwhile.
Fifteen minutes is that window, and it is why the number in :class:`Settings` is small.

**The refresh token is a random string with no meaning at all.** It says nothing; it is merely
looked up. That makes it revocable — deleting the row ends it — which is exactly the property the
access token trades away. It is stored hashed, so a database disclosure yields nothing usable.

**Decoding always names the algorithm it will accept.** The header of a JWT is attacker-supplied
data, and a decoder that trusts it can be handed `{"alg": "none"}` or an RS256 token whose
"signature" is an HMAC keyed with the public key. `algorithms=[ALGORITHM]` is the one line that
makes both unrepresentable rather than merely untested.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import jwt

if TYPE_CHECKING:
    from openposture_api.config import Settings

__all__ = [
    "ALGORITHM",
    "REFRESH_TOKEN_BYTES",
    "InvalidAccessTokenError",
    "decode_access_token",
    "hash_refresh_token",
    "issue_access_token",
    "issue_refresh_token",
]

ALGORITHM: Final = "HS256"
"""The only algorithm this service signs or accepts.

Symmetric, because the API is both issuer and verifier. An asymmetric algorithm exists to let a
party verify without being able to sign, and there is no such party here — RS256 would add key
management to buy a separation nothing in the system needs.
"""

REFRESH_TOKEN_BYTES: Final = 32
"""256 bits from the OS random source.

Large enough that the token has no guessable structure, which is the premise that lets
:func:`hash_refresh_token` use a fast digest instead of a password hash.
"""

_REQUIRED_CLAIMS: Final = ["exp", "iat", "sub"]
"""Claims that must be present, not merely valid if present.

Without `require`, a token carrying no `exp` at all passes expiry validation — there is nothing
to find expired. An absent claim and a satisfied claim must not look the same.
"""


class InvalidAccessTokenError(Exception):
    """An access token that cannot be trusted, for any reason.

    One exception for expired, tampered, malformed, wrongly-signed and wrong-algorithm tokens.
    The caller returns 401 for all of them, and a client that could tell "expired" from "forged"
    would learn which of its guesses were structurally correct.
    """


def issue_access_token(
    *,
    user_id: uuid.UUID,
    settings: Settings,
    issued_at: datetime | None = None,
) -> str:
    """Sign a short-lived access token identifying `user_id`.

    `issued_at` is injectable so the suite can mint a token that expired an hour ago without
    freezing the clock or sleeping. Production never passes it.

    `sub` is the registered claim for "who this token is about", and it is a string by
    specification — a UUID object is not JSON, and PyJWT would reject it rather than coerce.
    """
    now = issued_at if issued_at is not None else datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)


def decode_access_token(token: str, *, settings: Settings) -> uuid.UUID:
    """Verify a token and return the user it identifies.

    Raises :class:`InvalidAccessTokenError` for every failure, including a well-signed token
    whose `sub` is not a UUID — a valid signature over a nonsense subject is still nonsense, and
    letting it through would hand a malformed identifier to the repository layer.
    """
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            # The allowlist. Not derived from the token's own header, which is the entire point:
            # `alg: none` and HS/RS confusion are both attacks in which the attacker chooses the
            # verification algorithm, and here the attacker has no say in it.
            algorithms=[ALGORITHM],
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError as exc:
        raise InvalidAccessTokenError(str(exc)) from exc

    try:
        return uuid.UUID(claims["sub"])
    except (KeyError, ValueError, AttributeError, TypeError) as exc:
        raise InvalidAccessTokenError("subject is not a user identifier") from exc


def issue_refresh_token() -> str:
    """A new opaque refresh token, URL-safe so it survives a cookie unencoded.

    `secrets`, never `random`: the latter is a Mersenne Twister seeded for reproducibility, and
    observing a few of its outputs is enough to predict the rest. That is a fine property for a
    simulation and a disqualifying one for a credential.
    """
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """The digest stored in `refresh_tokens.token_hash`.

    SHA-256 rather than argon2, and the reason is the input, not the speed. Argon2's cost exists
    to make *guessing* expensive, which presupposes a search space small enough to guess —
    human-chosen passwords. A 256-bit random token has no such space, so slowness would buy no
    security while putting a deliberately expensive function on the path every client hits on a
    timer.

    A plain digest is also deterministic, which is required here: the refresh flow finds the row
    *by* this value. A per-row salt would make lookup impossible without reading every row first.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
