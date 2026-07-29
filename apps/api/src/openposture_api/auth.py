"""Authentication dependencies.

E7 (OP-55) adds the full auth stack: argon2id password hashing, JWT access tokens, and rotating
opaque refresh tokens. E8 (OP-56) implements `get_current_user_id` for real.

Until then this module exists so that E6's routes compile and tests can override the dependency.
Every route that needs the current user depends on `get_current_user_id`, which raises 401 today
and will return the authenticated user's UUID after E8 lands.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from starlette import status

__all__ = ["get_current_user_id"]


async def get_current_user_id() -> uuid.UUID:
    """Placeholder — implemented in E8 (OP-56).

    Routes depend on this rather than on a hard-coded user so that swapping the implementation
    is one change in one place. Override in tests with `app.dependency_overrides`.
    """
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required.",
        headers={"WWW-Authenticate": "Bearer"},
    )
