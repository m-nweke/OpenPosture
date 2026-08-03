"""Password hashing: the properties, not the algorithm.

These tests deliberately assert nothing about argon2's internals. They pin the four things the
rest of the system relies on — no plaintext at rest, salted, verifiable, and failing closed —
so that retuning the parameters, or one day replacing the algorithm entirely, breaks nothing here.
"""

from __future__ import annotations

import pytest

from openposture_api.security import hash_password, verify_password

PASSWORD = "correct horse battery staple"


class TestHashing:
    def test_the_hash_does_not_contain_the_password(self) -> None:
        """The property the whole layer exists for: a database dump is not a password list."""
        assert PASSWORD not in hash_password(PASSWORD)

    def test_the_same_password_hashes_differently_every_time(self) -> None:
        """A fresh random salt per hash.

        Without it, identical hashes would reveal which users share a password, and one
        precomputed table would cover every account in the database at once.
        """
        assert hash_password(PASSWORD) != hash_password(PASSWORD)

    def test_the_hash_is_self_describing(self) -> None:
        """Parameters travel with the hash, which is why retuning them is not a migration."""
        assert hash_password(PASSWORD).startswith("$argon2id$")


class TestVerification:
    def test_the_right_password_verifies(self) -> None:
        assert verify_password(hash_password(PASSWORD), PASSWORD) is True

    def test_the_wrong_password_does_not_verify(self) -> None:
        assert verify_password(hash_password(PASSWORD), "not the password") is False

    def test_verification_is_case_sensitive(self) -> None:
        assert verify_password(hash_password(PASSWORD), PASSWORD.upper()) is False

    def test_two_hashes_of_one_password_both_verify(self) -> None:
        """Different salts, same password — the salt must not make a hash unverifiable."""
        assert verify_password(hash_password(PASSWORD), PASSWORD) is True
        assert verify_password(hash_password(PASSWORD), PASSWORD) is True


class TestFailingClosed:
    """Every failure is the same failure.

    ADR-0003 requires that sign-in never reveal whether an email is registered. A caller that
    has to tell "wrong password" from "corrupt hash" is a caller with two paths that can diverge
    into an oracle, so this layer collapses them into one `False` before the route can.
    """

    @pytest.mark.parametrize(
        ("stored", "why"),
        [
            ("", "empty column"),
            ("not-a-hash-at-all", "plaintext left by a bad migration"),
            ("$argon2id$v=19$m=65536,t=3,p=4$truncated", "a hash cut short in transit"),
            ("$2b$12$abcdefghijklmnopqrstuv", "a bcrypt hash from another system"),
        ],
    )
    def test_an_unusable_stored_hash_returns_false_rather_than_raising(
        self, stored: str, why: str
    ) -> None:
        """A raise here would be a 500, and a 500 on some accounts but not others is an oracle.

        `InvalidHashError` subclasses `ValueError`, not `Argon2Error`, so this is the case a
        single `except Argon2Error` would miss — and it would miss it as a crash, not a denial.
        """
        assert verify_password(stored, PASSWORD) is False, why

    def test_an_empty_password_does_not_verify(self) -> None:
        assert verify_password(hash_password(PASSWORD), "") is False

    def test_an_empty_password_can_still_be_hashed_and_verified(self) -> None:
        """Rejecting empty passwords is the schema's job, not the hasher's.

        Pinned so that the length rule stays at the boundary where it can produce a 422 naming
        the field, rather than being silently duplicated down here.
        """
        assert verify_password(hash_password(""), "") is True
