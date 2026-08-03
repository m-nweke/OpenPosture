"""Repository for refresh token records."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from openposture_api.db.models import RefreshToken

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["RefreshTokenRepository"]


class RefreshTokenRepository:
    """Read and write operations on refresh tokens.

    Tokens are never updated in place — rotation creates a new row, then revokes the old one by
    setting `revoked_at`. This means a replay (the same token presented twice) is detectable: the
    presented hash matches a row that is already revoked, which is the signal to revoke the entire
    family rather than just the one presented.

    The family concept is why `revoke_family` exists: once a token from a family has been used
    after rotation, both the attacker's copy and the legitimate client's copy are in the same
    family, and only revoking both forces a full re-login.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        family_id: uuid.UUID,
        issued_at: datetime,
        expires_at: datetime,
    ) -> RefreshToken:
        token = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            family_id=family_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        self._session.add(token)
        await self._session.flush()
        return token

    async def get_active_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Fetch an unrevoked, unexpired token by its hash.

        Returns None for unknown hashes, revoked tokens, and expired tokens. The caller
        is responsible for distinguishing "token was rotated (revoked)" from "token
        never existed" by checking whether `get_by_hash` returns a revoked row — but
        that requires a separate method. E7 will call both as needed.
        """
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Fetch any token by hash, revoked or not.

        Used in replay detection: the caller presents a token, this method finds the row
        (possibly revoked), and `revoke_family` finishes the response if revocation is warranted.
        """
        result = await self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def revoke(self, token: RefreshToken, revoked_at: datetime) -> None:
        """Revoke one token — the normal end of a rotation.

        Separate from `revoke_family` because rotation and replay detection mean opposite things.
        Rotating retires a token that was used correctly and leaves the family alive; revoking
        the family is the response to a token used *twice*, and ends every session in it.
        """
        token.revoked_at = revoked_at
        await self._session.flush()

    async def revoke_family(self, family_id: uuid.UUID, revoked_at: datetime) -> int:
        """Revoke every active token in a family. Returns the row count."""
        result = await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
            .returning(RefreshToken.id)
        )
        await self._session.flush()
        return len(result.all())

    async def revoke_all_for_user(self, user_id: uuid.UUID, revoked_at: datetime) -> int:
        """Revoke every active token for a user. Returns the row count."""
        result = await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
            .returning(RefreshToken.id)
        )
        await self._session.flush()
        return len(result.all())
