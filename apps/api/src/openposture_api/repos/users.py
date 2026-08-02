"""Repository for user records."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from openposture_api.db.models import User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["UserRepository"]


class UserRepository:
    """Read and write operations on the user aggregate.

    User lookups (`get_by_email`, `get_by_id`) are not scoped by a second user_id — they are
    used in the auth flow, where the caller is establishing who the user is rather than acting on
    behalf of one already known. The tenancy guarantee lives on :class:`AnalysisRepository`, where
    the resource being protected has an owner distinct from the requester.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, email: str, password_hash: str) -> User:
        """Persist a new user.

        The email must be already lowercased — the database CHECK constraint will reject it
        otherwise, and the error message points here rather than to the right normalisation site.
        """
        user = User(email=email, password_hash=password_hash)
        self._session.add(user)
        await self._session.flush()
        return user

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Look up a user by their lowercased email address.

        Returns None if no account exists. Used by the login flow to retrieve the user
        before verifying their password — the only place in the codebase where a user
        is retrieved by email rather than by session identity.
        """
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()
