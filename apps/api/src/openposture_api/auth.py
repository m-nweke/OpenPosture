"""Authentication routes and dependencies.

Four endpoints, and each exists to move one credential across one boundary:

* `register` and `login` turn an email and a password into a session.
* `refresh` turns a refresh token into a new access token — and a new refresh token.
* `logout` ends the session everywhere it exists.

**Two credentials, because one cannot do both jobs.** The access token is self-verifying and so
costs no query, but cannot be withdrawn before it expires. The refresh token is opaque and so is
revocable, but requires a lookup. Fifteen minutes and thirty days are the two halves of that
trade-off (ADR-0003).

**Rotation makes theft detectable.** Every refresh mints a new token and retires the one
presented. A retired token arriving a second time has two explanations — a client bug or a stolen
copy — and only one safe reading, so the whole family is revoked and everyone re-authenticates.
The alternative is that an attacker who has copied a token keeps refreshing it forever, silently,
alongside the legitimate user.

**Sign-in never reveals whether an account exists.** Not just an identical response body: an
identical amount of *work*. See `_verify_credentials`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Final

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from openposture_api.db import get_session
from openposture_api.repos import RefreshTokenRepository, UserRepository
from openposture_api.schemas import CredentialsRequest, TokenResponse
from openposture_api.security import (
    hash_password,
    hash_refresh_token,
    issue_access_token,
    issue_refresh_token,
    verify_password,
)

if TYPE_CHECKING:
    from openposture_api.config import Settings
    from openposture_api.db.models import User

# `AsyncSession` is imported above at runtime rather than here, and that is load-bearing rather
# than untidy. `from __future__ import annotations` turns every annotation into a string, and
# FastAPI resolves route signatures by evaluating those strings at startup. A name that exists
# only for the type checker cannot be resolved, so `Annotated[AsyncSession, Depends(...)]`
# silently stops being a dependency and becomes an unrecognised *query parameter* — every request
# then fails with "query.session: Field required" rather than anything mentioning imports.

__all__ = ["AUTH_PREFIX", "build_auth_router", "get_current_user_id"]

AUTH_PREFIX: Final = "/api/v1/auth"

_LOGGER = structlog.get_logger(__name__)

_INVALID_CREDENTIALS: Final = "Email or password is incorrect."
"""One sentence for "no such account" and for "wrong password" alike.

Two messages would be an account-existence oracle in plain text, which is the same leak the
equal-cost verification below closes in the time domain. Both halves are needed; either alone
still tells an attacker which addresses are registered.
"""

_DUMMY_HASH: Final = hash_password("a password nobody has, hashed once at import")
"""A real argon2 hash that no submitted password can match.

Verifying against this when the email is unknown is what makes a failed login cost the same
whether or not the account exists. Computed once at import, because doing it per request would
double the cost of every failed sign-in for no benefit.
"""


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


def build_auth_router() -> APIRouter:
    """The authentication routes.

    A builder rather than a module-level router, for the same reason `create_app` is a factory:
    two apps in one test session must not share mutable route state.
    """
    router = APIRouter(prefix=AUTH_PREFIX, tags=["auth"])

    @router.post(
        "/register",
        status_code=status.HTTP_201_CREATED,
        response_model=TokenResponse,
        summary="Create an account and sign in",
        responses={
            status.HTTP_409_CONFLICT: {"description": "That email is already registered."},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Password or email rejected."},
        },
    )
    async def register(
        credentials: CredentialsRequest,
        request: Request,
        response: Response,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> TokenResponse:
        """Register, then immediately issue a session — nobody wants to log in twice.

        **This route does leak whether an address is registered**, via the 409, and that is a
        known gap rather than an oversight. Closing it means always returning 201 and sending a
        "somebody tried to register your address" email, and ADR-0003 records that this project
        has no outbound mail path. The alternative — a 201 that silently creates nothing — lies
        to legitimate users about whether they have an account, which is worse.
        """
        settings = _settings_of(request)
        email = _normalise_email(credentials.email)
        users = UserRepository(session)

        try:
            # `create` flushes, so the unique constraint is enforced here rather than by a prior
            # SELECT. That ordering matters: two simultaneous registrations of one address both
            # pass a check-then-insert, and only one survives a constraint. The race is rare, the
            # database already prevents it, and a pre-check would merely make it less visible.
            user = await users.create(
                email=email, password_hash=hash_password(credentials.password)
            )
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That email is already registered.",
            ) from exc

        tokens = await _open_session_for(
            user, session=session, settings=settings, response=response
        )
        await session.commit()
        _LOGGER.info("user_registered", user_id=str(user.id))
        return tokens

    @router.post(
        "/login",
        response_model=TokenResponse,
        summary="Sign in",
        responses={status.HTTP_401_UNAUTHORIZED: {"description": "Credentials rejected."}},
    )
    async def login(
        credentials: CredentialsRequest,
        request: Request,
        response: Response,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> TokenResponse:
        settings = _settings_of(request)
        email = _normalise_email(credentials.email)

        user = await _verify_credentials(email, credentials.password, session=session)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_INVALID_CREDENTIALS,
                headers={"WWW-Authenticate": "Bearer"},
            )

        tokens = await _open_session_for(
            user, session=session, settings=settings, response=response
        )
        await session.commit()
        _LOGGER.info("user_logged_in", user_id=str(user.id))
        return tokens

    @router.post(
        "/refresh",
        response_model=TokenResponse,
        summary="Exchange a refresh token for a new access token",
        responses={
            status.HTTP_401_UNAUTHORIZED: {
                "description": "The refresh token is missing, expired, unknown or already used."
            }
        },
    )
    async def refresh(
        request: Request,
        response: Response,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> TokenResponse:
        """Rotate the refresh token and mint a new access token.

        The presented token is always retired, whatever else happens. A refresh that returned a
        new token while leaving the old one usable would make rotation decorative.
        """
        settings = _settings_of(request)
        presented = request.cookies.get(settings.refresh_cookie_name)
        if not presented:
            raise _unauthorized_refresh(response, settings)

        tokens = RefreshTokenRepository(session)
        stored = await tokens.get_by_hash(hash_refresh_token(presented))
        now = datetime.now(UTC)

        if stored is None:
            raise _unauthorized_refresh(response, settings)

        if stored.revoked_at is not None:
            # Replay. This token was already rotated, so a correct client would never present it
            # again — meaning either the legitimate client and an attacker both hold copies, or
            # something is badly confused. Both warrant ending every session in the family: the
            # attacker's branch and the user's branch are indistinguishable from here, and
            # revoking only the presented row would leave whichever of them refreshed last.
            revoked = await tokens.revoke_family(stored.family_id, now)
            await session.commit()
            _LOGGER.warning(
                "refresh_token_replayed",
                user_id=str(stored.user_id),
                family_id=str(stored.family_id),
                tokens_revoked=revoked,
            )
            raise _unauthorized_refresh(response, settings)

        if _as_utc(stored.expires_at) <= now:
            raise _unauthorized_refresh(response, settings)

        await tokens.revoke(stored, now)
        await _issue_refresh_cookie(
            response,
            settings=settings,
            session=session,
            user_id=stored.user_id,
            # Same family: this is the next link in one chain of rotations, and a replay of any
            # earlier link has to be able to revoke everything downstream of it.
            family_id=stored.family_id,
            now=now,
        )
        access = _issue_access(stored.user_id, settings)
        await session.commit()
        return access

    @router.post(
        "/logout",
        status_code=status.HTTP_204_NO_CONTENT,
        # Without this, returning `None` from a 204 route serialises to a four-byte `null` body,
        # which is a protocol violation: 204 means there is no body to send.
        response_class=Response,
        summary="End the session on every device",
    )
    async def logout(
        request: Request,
        response: Response,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> None:
        """Revoke the whole family and clear the cookie.

        Always 204, even when the cookie is absent or unrecognised. Logging out is not a place to
        report that a token was already invalid — the caller wanted no session, and afterwards
        there is no session. Reporting the difference would turn logout into a token oracle.

        The family, not just the presented token, because "log out" means the session is over. A
        rotation the client had already performed elsewhere must not survive it.
        """
        settings = _settings_of(request)
        presented = request.cookies.get(settings.refresh_cookie_name)

        if presented:
            tokens = RefreshTokenRepository(session)
            stored = await tokens.get_by_hash(hash_refresh_token(presented))
            if stored is not None:
                await tokens.revoke_family(stored.family_id, datetime.now(UTC))
                await session.commit()
                _LOGGER.info("user_logged_out", user_id=str(stored.user_id))

        _clear_refresh_cookie(response, settings)

    return router


async def _verify_credentials(email: str, password: str, *, session: AsyncSession) -> User | None:
    """Return the user if the credentials are right, `None` otherwise — at a constant cost.

    The naive version returns early when no user is found, and that early return is a timing
    oracle: a missing account answers in a fraction of a millisecond, a wrong password answers in
    roughly twenty-three. The difference is two orders of magnitude, far above network jitter, so
    an attacker with a list of addresses can simply time the responses and learn which are
    registered — the exact leak ADR-0003 forbids, arriving through the clock instead of the body.

    So the hash is verified either way, against `_DUMMY_HASH` when there is no user. It cannot
    match, and that is the point: the work is spent regardless.

    This equalises, it does not make constant-time. Argon2's own comparison is constant-time; what
    remains here is the difference between a hit and a miss on the email index, which is orders of
    magnitude below the noise floor of a network round trip.
    """
    user = await UserRepository(session).get_by_email(email)
    stored_hash = user.password_hash if user is not None else _DUMMY_HASH

    matched = verify_password(stored_hash, password)

    return user if (matched and user is not None) else None


async def _open_session_for(
    user: User, *, session: AsyncSession, settings: Settings, response: Response
) -> TokenResponse:
    """Start a brand-new rotation family and return the access token for it."""
    now = datetime.now(UTC)
    await _issue_refresh_cookie(
        response,
        settings=settings,
        session=session,
        user_id=user.id,
        family_id=uuid.uuid4(),
        now=now,
    )
    return _issue_access(user.id, settings)


async def _issue_refresh_cookie(
    response: Response,
    *,
    settings: Settings,
    session: AsyncSession,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    now: datetime,
) -> None:
    """Mint a refresh token, store only its digest, and set it as an `HttpOnly` cookie."""
    token = issue_refresh_token()
    expires_at = now + timedelta(days=settings.refresh_token_ttl_days)

    await RefreshTokenRepository(session).create(
        user_id=user_id,
        # The plaintext exists only in this function and in the client's cookie jar. Nothing
        # writes it to a row, a log, or a response body.
        token_hash=hash_refresh_token(token),
        family_id=family_id,
        issued_at=now,
        expires_at=expires_at,
    )

    response.set_cookie(
        settings.refresh_cookie_name,
        token,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        # Unreadable from JavaScript, which is what makes the "nothing in localStorage" rule
        # structural: an XSS can still *send* a request, but it cannot exfiltrate this token.
        httponly=True,
        secure=settings.use_secure_cookies,
        # Lax, not Strict: the cookie must survive a top-level navigation back into the app, and
        # not None, which would attach it to any cross-site request and reintroduce CSRF.
        samesite="lax",
        # Scoped to the routes that need it, so it is not attached to every image upload.
        path=AUTH_PREFIX,
    )


def _issue_access(user_id: uuid.UUID, settings: Settings) -> TokenResponse:
    return TokenResponse(
        access_token=issue_access_token(user_id=user_id, settings=settings),
        token_type="bearer",
        expires_in=settings.access_token_ttl_minutes * 60,
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    """Delete the cookie with the same attributes it was set with.

    A browser matches a deletion against name, path and domain. Clearing it without the `path`
    it was set under silently does nothing, and the stale cookie keeps being sent.
    """
    response.delete_cookie(
        settings.refresh_cookie_name,
        path=AUTH_PREFIX,
        httponly=True,
        secure=settings.use_secure_cookies,
        samesite="lax",
    )


def _unauthorized_refresh(response: Response, settings: Settings) -> HTTPException:
    """One 401 for every way a refresh can fail, with the dead cookie cleared on the way out.

    Missing, unknown, expired and replayed are deliberately indistinguishable to the client. A
    client that could tell "expired" from "revoked as stolen" would learn whether the token it
    holds was ever real, which is exactly what an attacker testing a captured token wants to know.
    """
    _clear_refresh_cookie(response, settings)
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Your session has expired. Please sign in again.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _as_utc(moment: datetime) -> datetime:
    """Read a stored timestamp back as timezone-aware UTC.

    `expires_at` is declared `DateTime(timezone=True)`, and Postgres honours that — asyncpg
    returns an aware datetime. SQLite has no timezone-aware storage at all and hands back a naive
    one, so comparing it against `datetime.now(UTC)` raises `TypeError` rather than returning a
    wrong answer. Everything written here is UTC, so attaching the zone is a restatement of what
    the column already means, not a conversion.

    Python is strict about this on purpose: naive and aware datetimes are different types of
    thing, and refusing to compare them prevents the far worse bug of silently comparing a local
    time to a UTC one.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _normalise_email(email: str) -> str:
    """Lowercase at the boundary, once.

    `users.email` carries a CHECK constraint requiring the stored value to already be lowercase,
    so this is not a convenience — it is the single place the invariant is established. A user
    registering as `Sam@Example.com` and signing in as `sam@example.com` is the same person.
    """
    return email.strip().lower()


def _settings_of(request: Request) -> Settings:
    """The app's settings, off `app.state` where `create_app` put them.

    Not `get_settings()`: that reads the process environment, and the whole point of the factory
    is that a test can build an app with settings that differ from it.
    """
    settings: Settings = request.app.state.settings
    return settings
