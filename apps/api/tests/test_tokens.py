"""Token abuse cases, built as attacks rather than asserted as intentions.

Each test here forges something. That is deliberate: a comment claiming `alg: none` is rejected
is a wish, and a test that constructs an `alg: none` token and watches it bounce is a guarantee.
These run against the functions directly — no client, no database, no network — which is what
lets the whole suite stay inside `pr.yml`'s no-service, no-secret rule.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr

from openposture_api.config import Settings
from openposture_api.security import (
    ALGORITHM,
    REFRESH_TOKEN_BYTES,
    InvalidAccessTokenError,
    decode_access_token,
    hash_refresh_token,
    issue_access_token,
    issue_refresh_token,
)

SECRET = "test-only-signing-key-long-enough-for-hs512-and-hs256-alike-0123456789"
USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="test", jwt_secret=SecretStr(SECRET))


class TestRoundTrip:
    def test_a_freshly_issued_token_identifies_its_user(self, settings: Settings) -> None:
        token = issue_access_token(user_id=USER_ID, settings=settings)

        assert decode_access_token(token, settings=settings) == USER_ID

    def test_the_token_carries_the_configured_lifetime(self, settings: Settings) -> None:
        """15 minutes is a security parameter, not a default — worth pinning to the setting."""
        issued = datetime.now(UTC)
        token = issue_access_token(user_id=USER_ID, settings=settings, issued_at=issued)

        claims = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
        lifetime = claims["exp"] - claims["iat"]

        assert lifetime == settings.access_token_ttl_minutes * 60

    def test_the_token_body_is_readable_by_anyone(self, settings: Settings) -> None:
        """A JWT is signed, not encrypted. Pinned so nobody ever puts a secret in a claim."""
        token = issue_access_token(user_id=USER_ID, settings=settings)

        unverified = jwt.decode(token, options={"verify_signature": False})

        assert unverified["sub"] == str(USER_ID)


class TestExpiry:
    def test_an_expired_token_is_rejected(self, settings: Settings) -> None:
        """Injecting `issued_at` beats sleeping for fifteen minutes or freezing the clock."""
        long_ago = datetime.now(UTC) - timedelta(hours=1)
        token = issue_access_token(user_id=USER_ID, settings=settings, issued_at=long_ago)

        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(token, settings=settings)

    def test_a_token_with_no_expiry_is_rejected(self, settings: Settings) -> None:
        """The claim that cannot be checked if it is absent.

        Without `require`, expiry validation on a token carrying no `exp` finds nothing to
        object to and passes — which produces a token that never expires.
        """
        forged = jwt.encode({"sub": str(USER_ID), "iat": datetime.now(UTC)}, SECRET, ALGORITHM)

        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(forged, settings=settings)


class TestTampering:
    def test_a_token_signed_with_another_key_is_rejected(self, settings: Settings) -> None:
        """The attacker who guessed the claim structure but not the secret."""
        forged = jwt.encode(
            {"sub": str(USER_ID), "iat": datetime.now(UTC), "exp": _soon()},
            "not-our-signing-key-but-equally-long-0123456789012345678901234567890",
            ALGORITHM,
        )

        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(forged, settings=settings)

    def test_an_edited_payload_is_rejected(self, settings: Settings) -> None:
        """Swap the subject for someone else's and the signature no longer covers the body."""
        victim = uuid.UUID("22222222-2222-2222-2222-222222222222")
        header, _, signature = issue_access_token(user_id=USER_ID, settings=settings).split(".")
        substitute = jwt.encode(
            {"sub": str(victim), "iat": datetime.now(UTC), "exp": _soon()}, SECRET, ALGORITHM
        ).split(".")[1]

        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(f"{header}.{substitute}.{signature}", settings=settings)

    @pytest.mark.parametrize(
        "garbage",
        ["", "not-a-token", "a.b.c", "....", "eyJhbGciOiJIUzI1NiJ9"],
        ids=["empty", "no-dots", "three-junk-segments", "only-dots", "header-only"],
    )
    def test_a_malformed_token_is_rejected_rather_than_raising(
        self, garbage: str, settings: Settings
    ) -> None:
        """Every shape of nonsense arrives as the same domain error, so the route has one path."""
        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(garbage, settings=settings)

    def test_a_valid_signature_over_a_nonsense_subject_is_rejected(
        self, settings: Settings
    ) -> None:
        """We signed it, so it verifies — and it still must not reach the repository layer."""
        forged = jwt.encode(
            {"sub": "not-a-uuid", "iat": datetime.now(UTC), "exp": _soon()}, SECRET, ALGORITHM
        )

        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(forged, settings=settings)


class TestAlgorithmConfusion:
    """The attacks where the *attacker* picks the verification algorithm.

    Both work against a decoder that reads `alg` from the token's own header and believes it.
    The header is attacker-supplied data; treating it as instructions is the bug.
    """

    def test_an_unsigned_token_is_rejected(self, settings: Settings) -> None:
        """`alg: none` — the classic. The token declares it needs no signature, and a trusting
        decoder agrees with it."""
        unsigned = jwt.encode(
            {"sub": str(USER_ID), "iat": datetime.now(UTC), "exp": _soon()},
            key="",
            algorithm="none",
        )

        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(unsigned, settings=settings)

    def test_a_token_declaring_a_different_hmac_is_rejected(self, settings: Settings) -> None:
        """HS512 is signed with our real secret, and is still not the algorithm we accept."""
        forged = jwt.encode(
            {"sub": str(USER_ID), "iat": datetime.now(UTC), "exp": _soon()}, SECRET, "HS512"
        )

        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(forged, settings=settings)

    def test_the_decoder_does_not_consult_the_token_header(self, settings: Settings) -> None:
        """The property underneath both attacks above, asserted directly.

        An `alg: none` token *says* it is unsigned. If the header were consulted, that claim
        would be honoured. The allowlist is what makes the header advisory rather than binding.
        """
        unsigned = jwt.encode({"sub": str(USER_ID)}, key="", algorithm="none")

        assert jwt.get_unverified_header(unsigned)["alg"] == "none"
        with pytest.raises(InvalidAccessTokenError):
            decode_access_token(unsigned, settings=settings)


class TestRefreshTokens:
    def test_every_token_is_different(self) -> None:
        assert len({issue_refresh_token() for _ in range(100)}) == 100

    def test_the_token_carries_the_intended_entropy(self) -> None:
        """`token_urlsafe` returns base64url, so the string is longer than the byte count.

        Asserted as a lower bound on length rather than an equality, because the encoding's
        padding behaviour is not the property that matters — the 256 bits underneath it is.
        """
        assert len(issue_refresh_token()) >= REFRESH_TOKEN_BYTES

    def test_the_token_survives_a_cookie_unencoded(self) -> None:
        """`+` and `/` from standard base64 would need escaping in a Set-Cookie header."""
        token = issue_refresh_token()

        assert "+" not in token
        assert "/" not in token
        assert "=" not in token

    def test_the_hash_does_not_contain_the_token(self) -> None:
        """The database-dump property: nothing stored is directly replayable."""
        token = issue_refresh_token()

        assert token not in hash_refresh_token(token)

    def test_hashing_is_deterministic(self) -> None:
        """Required, not incidental: the refresh flow finds the row *by* this value."""
        token = issue_refresh_token()

        assert hash_refresh_token(token) == hash_refresh_token(token)

    def test_different_tokens_hash_differently(self) -> None:
        assert hash_refresh_token(issue_refresh_token()) != hash_refresh_token(
            issue_refresh_token()
        )


def _soon() -> datetime:
    """An expiry comfortably in the future, so a forged token fails on its signature or its
    algorithm rather than incidentally on its age."""
    return datetime.now(UTC) + timedelta(minutes=5)
