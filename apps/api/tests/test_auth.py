"""The auth routes, exercised as a client sees them.

These drive the real application over ASGI against a real (SQLite) database, because the
properties under test are properties of the *endpoint*: what status came back, what the body
said, what cookie was set and with which attributes. A unit test of the handler function would
assert none of that.

The abuse cases live here rather than in `test_tokens.py` because they are about the flow, not
the primitive: replay, family revocation, and the account-existence oracle only exist once there
is state to replay against.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from openposture_api.auth import AUTH_PREFIX
from openposture_api.config import Settings
from openposture_api.db import Base
from openposture_api.db.models import RefreshToken, User
from openposture_api.main import create_app
from openposture_api.security import hash_refresh_token

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from fastapi import FastAPI

EMAIL = "sam@example.com"
PASSWORD = "a-sufficiently-long-password"

REGISTER = f"{AUTH_PREFIX}/register"
LOGIN = f"{AUTH_PREFIX}/login"
REFRESH = f"{AUTH_PREFIX}/refresh"
LOGOUT = f"{AUTH_PREFIX}/logout"


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A fresh in-memory database with the real schema.

    `StaticPool`-free: the URL is shared via a named in-memory database so the app's sessions and
    the test's assertions see the same data, which two independent `:memory:` connections would
    not.
    """
    engine = create_async_engine("sqlite+aiosqlite:///file::memory:?cache=shared&uri=true")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def auth_app(session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    app = create_app(
        Settings(
            environment="test",
            log_level="warning",
            json_logs=True,
            pose_backend="fake",
            # `test` is not `development`, so the refresh cookie would be marked `Secure` — and a
            # `Secure` cookie is never returned over the plain http `TestClient` speaks, which
            # shows up as every refresh failing with 401 rather than as anything about transport.
            # Overridden rather than relaxing the default: shipping `Secure` everywhere but a
            # developer's machine is the behaviour worth keeping.
            refresh_cookie_secure=False,
        )
    )
    app.state.session_factory = session_factory
    return app


@pytest.fixture
def client(
    auth_app: FastAPI, session_factory: async_sessionmaker[AsyncSession]
) -> Iterator[TestClient]:
    """A client whose requests reach SQLite rather than the configured Postgres.

    The reinstatement inside the context manager is the load-bearing part. `lifespan` builds an
    engine from `settings.database_url` and overwrites whatever factory was on `app.state`, so
    assigning before startup is silently undone — and the symptom is a DNS failure resolving the
    Compose hostname, which points nowhere near this fixture.
    """
    with TestClient(auth_app, raise_server_exceptions=False) as test_client:
        auth_app.state.session_factory = session_factory
        yield test_client


def _credentials(**overrides: str) -> dict[str, str]:
    return {"email": EMAIL, "password": PASSWORD, **overrides}


class TestRegistration:
    def test_registering_returns_a_session(self, client: TestClient) -> None:
        response = client.post(REGISTER, json=_credentials())

        assert response.status_code == 201
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"]
        assert body["expires_in"] == 15 * 60

    def test_registering_sets_an_httponly_refresh_cookie(self, client: TestClient) -> None:
        """The criterion that makes "no token in localStorage" structural rather than a habit."""
        response = client.post(REGISTER, json=_credentials())

        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=lax" in cookie
        assert f"path={AUTH_PREFIX}" in cookie

    def test_the_refresh_token_is_not_in_the_response_body(self, client: TestClient) -> None:
        """A refresh token in the body is a refresh token the frontend can persist."""
        body = client.post(REGISTER, json=_credentials()).json()

        assert set(body) == {"access_token", "token_type", "expires_in"}

    def test_a_duplicate_email_is_rejected(self, client: TestClient) -> None:
        client.post(REGISTER, json=_credentials())

        assert client.post(REGISTER, json=_credentials()).status_code == 409

    def test_email_case_does_not_create_a_second_account(self, client: TestClient) -> None:
        """`Sam@Example.com` and `sam@example.com` are one person, decided at the boundary."""
        client.post(REGISTER, json=_credentials())

        assert client.post(REGISTER, json=_credentials(email="SAM@EXAMPLE.COM")).status_code == 409

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("password", "short"),
            ("password", "x" * 129),
            ("email", "not-an-email"),
            ("email", ""),
        ],
    )
    def test_invalid_credentials_are_rejected_before_any_hashing(
        self, client: TestClient, field: str, value: str
    ) -> None:
        response = client.post(REGISTER, json=_credentials(**{field: value}))

        assert response.status_code == 422

    async def test_the_password_is_not_stored(
        self, client: TestClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        client.post(REGISTER, json=_credentials())

        async with session_factory() as session:
            user = (await session.execute(select(User))).scalar_one()

        assert PASSWORD not in user.password_hash
        assert user.password_hash.startswith("$argon2id$")
        assert user.email == EMAIL


class TestLogin:
    def test_the_right_password_signs_in(self, client: TestClient) -> None:
        client.post(REGISTER, json=_credentials())
        client.cookies.clear()

        assert client.post(LOGIN, json=_credentials()).status_code == 200

    def test_email_case_does_not_prevent_signing_in(self, client: TestClient) -> None:
        client.post(REGISTER, json=_credentials())

        response = client.post(LOGIN, json=_credentials(email="Sam@Example.COM"))

        assert response.status_code == 200

    def test_the_wrong_password_is_rejected(self, client: TestClient) -> None:
        client.post(REGISTER, json=_credentials())

        response = client.post(LOGIN, json=_credentials(password="the-wrong-password"))

        assert response.status_code == 401


class TestAccountExistenceOracle:
    """A failed sign-in must not reveal whether the address is registered.

    ADR-0003 requires this, and it has to hold in both channels — what the response *says* and
    how long it *takes*. Closing one and leaving the other open closes nothing.
    """

    def test_an_unknown_email_and_a_wrong_password_are_byte_identical(
        self, client: TestClient
    ) -> None:
        client.post(REGISTER, json=_credentials())
        client.cookies.clear()

        wrong_password = client.post(LOGIN, json=_credentials(password="the-wrong-password"))
        no_such_user = client.post(LOGIN, json=_credentials(email="nobody@example.com"))

        assert wrong_password.status_code == no_such_user.status_code == 401
        assert _without_request_id(wrong_password.json()) == _without_request_id(
            no_such_user.json()
        )

    def test_neither_response_names_the_field_that_was_wrong(self, client: TestClient) -> None:
        """The message must blame both fields or neither, never one.

        Naming a single field is an oracle in either direction, and the asymmetry is easy to
        miss. "Email not found" obviously reveals that the address is unregistered. "Password
        incorrect" reveals the opposite and is just as bad: it confirms the address *is*
        registered, which is precisely what an attacker enumerating a list wants to learn.

        Hence an equality between two membership tests rather than an implication. The earlier
        version of this assertion read `"email" not in detail or "password" in detail`, which is
        satisfied by "Password incorrect" — it passes on exactly the message it exists to catch.
        """
        client.post(REGISTER, json=_credentials())

        # Long enough to clear `MIN_PASSWORD_LENGTH`, and that is not incidental. An obviously
        # bad password like "nope" is rejected by the schema with a 422 whose detail mentions
        # neither field, so the assertion below would hold without the login route ever running.
        # The earlier version did exactly that and was therefore vacuous.
        rejected = client.post(LOGIN, json=_credentials(password="the-wrong-password"))
        assert rejected.status_code == 401, "the request must reach the credential check"

        detail = rejected.json()["detail"].lower()

        assert ("email" in detail) == ("password" in detail), (
            f"{detail!r} singles out one credential, which tells the caller which half was wrong"
        )

    def test_an_unknown_email_still_costs_a_password_hash(self, client: TestClient) -> None:
        """The timing half, asserted through the clock.

        A missing user that returned early would answer in well under a millisecond, against the
        ~23 ms an argon2 verification costs. The bound here is deliberately loose — 5 ms — because
        this asserts "work was done", not a precise duration, and a tight bound would make the
        test fail on a slow CI runner for no security reason.
        """
        elapsed = _time_of(lambda: client.post(LOGIN, json=_credentials(email="ghost@example.com")))

        assert elapsed > 0.005, (
            f"an unknown email answered in {elapsed * 1000:.1f} ms, which is too fast to have "
            "hashed anything — the early return is a timing oracle for account existence"
        )

    def test_a_known_and_unknown_email_take_comparable_time(self, client: TestClient) -> None:
        """Not constant-time, but within the same order of magnitude.

        The bound is deliberately loose. The bug this catches is a ~200x difference — the early
        return that skips hashing entirely — so a 5x threshold detects it with enormous margin
        while leaving room for a shared CI runner to be descheduled mid-request. A tighter bound
        would buy no additional detection and would fail on load, and a timing test that flakes
        gets marked skip, which is worse than a loose one that does not.
        """
        client.post(REGISTER, json=_credentials())
        client.cookies.clear()

        known = _time_of(lambda: client.post(LOGIN, json=_credentials(password="wrong-password")))
        unknown = _time_of(lambda: client.post(LOGIN, json=_credentials(email="ghost@here.com")))

        ratio = max(known, unknown) / min(known, unknown)
        assert ratio < 5.0, f"login timing differs by {ratio:.1f}x, which is an existence oracle"


class TestRefreshRotation:
    def test_refreshing_returns_a_new_access_token(self, client: TestClient) -> None:
        client.post(REGISTER, json=_credentials())

        response = client.post(REFRESH)

        assert response.status_code == 200
        assert response.json()["access_token"]

    def test_refreshing_replaces_the_refresh_cookie(self, client: TestClient) -> None:
        """Rotation. A refresh that returned a new access token and left the old refresh token
        usable would make the whole mechanism decorative."""
        client.post(REGISTER, json=_credentials())
        original = client.cookies["openposture_refresh"]

        client.post(REFRESH)

        assert client.cookies["openposture_refresh"] != original

    def test_refreshing_without_a_cookie_is_rejected(self, client: TestClient) -> None:
        assert client.post(REFRESH).status_code == 401

    def test_an_unknown_refresh_token_is_rejected(self, client: TestClient) -> None:
        client.cookies.set("openposture_refresh", "a-token-nobody-ever-issued")

        assert client.post(REFRESH).status_code == 401

    def test_a_rejected_refresh_clears_the_dead_cookie(self, client: TestClient) -> None:
        """Regression: the 401 used to keep the browser resending a token it can never use.

        The first implementation `raise`d an `HTTPException` after writing the deletion to the
        injected `Response`. Raising hands control to the error handler, which builds its own
        response — so the `Set-Cookie` was written to an object that was then discarded, and the
        clearing the code documented never reached the client.
        """
        client.cookies.set("openposture_refresh", "a-token-nobody-ever-issued")

        response = client.post(REFRESH)

        assert response.status_code == 401
        assert "set-cookie" in response.headers, (
            "the 401 sent no Set-Cookie, so the dead token stays in the browser and is resent "
            "on every subsequent refresh"
        )
        assert 'openposture_refresh=""' in response.headers["set-cookie"]

    def test_a_rejected_refresh_still_returns_an_rfc_9457_body(self, client: TestClient) -> None:
        """Building the response by hand must not lose the error envelope the rest of the API
        uses — that is the risk taken on by returning instead of raising."""
        client.cookies.set("openposture_refresh", "a-token-nobody-ever-issued")

        response = client.post(REFRESH)

        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["status"] == 401
        assert response.json()["type"].endswith("/unauthorized")

    async def test_only_the_hash_of_the_token_is_stored(
        self, client: TestClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """The database-dump property: nothing stored is directly replayable."""
        client.post(REGISTER, json=_credentials())
        plaintext = client.cookies["openposture_refresh"]

        async with session_factory() as session:
            stored = (await session.execute(select(RefreshToken))).scalars().all()

        assert len(stored) == 1
        assert stored[0].token_hash != plaintext
        assert stored[0].token_hash == hash_refresh_token(plaintext)

    async def test_an_expired_refresh_token_is_rejected(
        self, client: TestClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        client.post(REGISTER, json=_credentials())

        async with session_factory() as session:
            token = (await session.execute(select(RefreshToken))).scalar_one()
            token.expires_at = datetime.now(UTC) - timedelta(days=1)
            await session.commit()

        assert client.post(REFRESH).status_code == 401


class TestReplayDetection:
    """The property the `family_id` column exists for.

    A rotated token presented a second time means two parties hold it. Which of them is the
    attacker is unknowable from here, so the safe reading is that the session is compromised and
    both are logged out.
    """

    def test_reusing_a_rotated_token_is_rejected(self, client: TestClient) -> None:
        client.post(REGISTER, json=_credentials())
        stolen = client.cookies["openposture_refresh"]
        client.post(REFRESH)  # rotates; `stolen` is now retired

        client.cookies.set("openposture_refresh", stolen)

        assert client.post(REFRESH).status_code == 401

    def test_reusing_a_rotated_token_revokes_the_whole_family(self, client: TestClient) -> None:
        """The legitimate client's *current* token dies too, and that is the point.

        Revoking only the replayed row would leave whichever party refreshed most recently in
        possession of a working session — and there is no way to know that it is the victim.
        """
        client.post(REGISTER, json=_credentials())
        stolen = client.cookies["openposture_refresh"]
        client.post(REFRESH)
        legitimate = client.cookies["openposture_refresh"]

        client.cookies.set("openposture_refresh", stolen)
        client.post(REFRESH)

        client.cookies.set("openposture_refresh", legitimate)
        assert client.post(REFRESH).status_code == 401, (
            "the legitimate token survived a replay of its family — the attacker's copy and the "
            "victim's copy are indistinguishable, so both must be revoked"
        )

    async def test_a_replay_revokes_every_token_in_the_family(
        self, client: TestClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        client.post(REGISTER, json=_credentials())
        stolen = client.cookies["openposture_refresh"]
        client.post(REFRESH)
        client.post(REFRESH)

        client.cookies.set("openposture_refresh", stolen)
        client.post(REFRESH)

        async with session_factory() as session:
            tokens = (await session.execute(select(RefreshToken))).scalars().all()

        assert all(token.revoked_at is not None for token in tokens)

    def test_a_replay_leaves_signing_in_again_possible(self, client: TestClient) -> None:
        """Revocation must end sessions, not the account. The user re-authenticates."""
        client.post(REGISTER, json=_credentials())
        stolen = client.cookies["openposture_refresh"]
        client.post(REFRESH)
        client.cookies.set("openposture_refresh", stolen)
        client.post(REFRESH)

        client.cookies.clear()
        assert client.post(LOGIN, json=_credentials()).status_code == 200


class TestLogout:
    def test_logging_out_succeeds(self, client: TestClient) -> None:
        client.post(REGISTER, json=_credentials())

        assert client.post(LOGOUT).status_code == 204

    def test_logging_out_invalidates_the_refresh_token(self, client: TestClient) -> None:
        client.post(REGISTER, json=_credentials())
        held = client.cookies["openposture_refresh"]

        client.post(LOGOUT)

        client.cookies.set("openposture_refresh", held)
        assert client.post(REFRESH).status_code == 401

    def test_logging_out_invalidates_the_whole_family(self, client: TestClient) -> None:
        """ "Log out" means the session is over, including rotations performed elsewhere."""
        client.post(REGISTER, json=_credentials())
        first = client.cookies["openposture_refresh"]
        client.post(REFRESH)

        client.post(LOGOUT)

        client.cookies.set("openposture_refresh", first)
        assert client.post(REFRESH).status_code == 401

    def test_logging_out_without_a_session_still_succeeds(self, client: TestClient) -> None:
        """Idempotent, and deliberately not a token oracle. The caller wanted no session; there
        is no session. Reporting that the token was already invalid would be information."""
        assert client.post(LOGOUT).status_code == 204

    def test_logging_out_clears_the_cookie(self, client: TestClient) -> None:
        client.post(REGISTER, json=_credentials())

        response = client.post(LOGOUT)

        assert "openposture_refresh" in response.headers["set-cookie"]
        assert 'openposture_refresh=""' in response.headers["set-cookie"]


def _without_request_id(problem: dict[str, Any]) -> dict[str, Any]:
    """Drop the correlation ID before comparing two error bodies.

    `request_id` differs on every response by design and says nothing about the account — it is
    the one field that is *supposed* to vary. Everything else must be identical, which is the
    property being asserted.
    """
    return {key: value for key, value in problem.items() if key != "request_id"}


def _time_of(call: Any) -> float:
    """Wall-clock duration of one request, in seconds.

    `perf_counter` rather than `time`, because it is monotonic and has far finer resolution —
    the differences being measured here are milliseconds.
    """
    import time

    started = time.perf_counter()
    call()
    return time.perf_counter() - started
