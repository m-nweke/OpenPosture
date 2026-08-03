"""Cryptographic primitives, kept away from the routes that use them.

The split is deliberate: this package knows how to hash and how to sign, and nothing about HTTP,
sessions or users. That is what lets the abuse-case suite test algorithm confusion and replay
directly against these functions, with no client, no database and no request.
"""

from __future__ import annotations

from openposture_api.security.passwords import hash_password, verify_password

__all__ = ["hash_password", "verify_password"]
