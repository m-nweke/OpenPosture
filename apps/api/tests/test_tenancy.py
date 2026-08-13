"""E8's three guarantees, asserted rather than documented.

A comment describing a security property is a wish; a test asserting it is a guarantee. The
properties this file exists to hold down:

1. **The access token is the only way in.** `get_current_user_id` accepts a token this service
   signed and nothing else — not an expired one, not a re-signed one, not one whose header asks
   for a weaker algorithm.
2. **No route can forget it.** The route table is enumerated and every entry must either be on
   the public list below or depend on the dependency. A route added in six months by someone who
   never read this ticket fails this test.
3. **Another user's analysis is indistinguishable from one that does not exist.** Same status,
   same body. A 403 would confirm the row is real, which is the whole prize in id enumeration.

These run against real SQLite and real signed tokens rather than dependency overrides. An
override would make the suite assert its own fixture: the point here is precisely the wiring that
`app.dependency_overrides` replaces.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from openposture_api.analyses import API_PREFIX
from openposture_api.auth import AUTH_PREFIX, get_current_user_id
from openposture_api.config import Settings
from openposture_api.db import Base
from openposture_api.repos import AnalysisRepository
from openposture_api.security import issue_access_token

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from fastapi import FastAPI

ANALYSES = f"{API_PREFIX}/analyses"

_PUBLIC_PATHS = frozenset(
    {
        # Liveness and readiness: consulted by the orchestrator, which holds no session. A probe
        # that required a token would report the service unhealthy exactly when auth broke, which
        # is the moment the difference matters most.
        "/health",
        "/health/ready",
        # The four ways to obtain a session. Requiring one to get one is the obvious circularity.
        f"{AUTH_PREFIX}/register",
        f"{AUTH_PREFIX}/login",
        f"{AUTH_PREFIX}/refresh",
        f"{AUTH_PREFIX}/logout",
        # The schema and its viewer. Public because OP-45 generates the frontend's types from it
        # in CI, where no account exists. It describes the shape of every endpoint and the
        # contents of none.
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
    }
)
"""Every route allowed to answer without a token, named one at a time.

An allowlist rather than a rule about path prefixes, because a prefix rule silently adopts
whatever is added under it later. Adding a genuinely public route means adding a line here, in a
diff a reviewer reads.
"""


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A fresh in-memory database with the real schema, shared by name.

    Two plain `:memory:` connections are two separate databases; the shared-cache URL is what
    lets the app's sessions and this file's fixtures see the same rows.
    """
    engine = create_async_engine("sqlite+aiosqlite:///file::memory:?cache=shared&uri=true")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        log_level="warning",
        json_logs=True,
        pose_backend="fake",
        refresh_cookie_secure=False,
    )


@pytest.fixture
def app(settings: Settings, session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    from openposture_api.main import create_app

    built = create_app(settings)
    built.state.session_factory = session_factory
    return built


@pytest.fixture
def client(app: FastAPI, session_factory: async_sessionmaker[AsyncSession]) -> Iterator[TestClient]:
    """Reinstates the factory *inside* the context manager.

    `lifespan` builds an engine from `settings.database_url` and overwrites whatever was on
    `app.state`, so assigning before startup is silently undone — and the symptom is a DNS
    failure resolving a Compose hostname, which points nowhere near this fixture.
    """
    with TestClient(app, raise_server_exceptions=False) as test_client:
        app.state.session_factory = session_factory
        yield test_client


def _register(client: TestClient, email: str) -> tuple[uuid.UUID, str]:
    """Create an account and return its id and a usable access token."""
    response = client.post(
        f"{AUTH_PREFIX}/register",
        json={"email": email, "password": "a-sufficiently-long-password"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    # The subject is the user id, and reading it back here means the tests never need a second
    # source of truth for who they just created.
    payload = json.loads(_b64url_decode(token.split(".")[1]))
    return uuid.UUID(payload["sub"]), token


def _b64url_decode(segment: str) -> bytes:
    """JWT segments drop base64 padding; `urlsafe_b64decode` insists on it."""
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _store_analysis(
    factory: async_sessionmaker[AsyncSession], owner: uuid.UUID, key: str = "analyses/x.jpg"
) -> uuid.UUID:
    """Persist one analysis owned by `owner`, bypassing the upload route."""
    async with factory() as session:
        analysis = await AnalysisRepository(session).create(
            user_id=owner,
            object_key=key,
            pose_detected=True,
            image_width=640,
            image_height=480,
            overall_score=70.0,
            assessed=7,
            total=7,
            inference_ms=12.5,
            pose_backend="fake",
            rules_version="1.0.0",
            schema_version="1.0",
        )
        await session.commit()
        return analysis.id


def _comparable(response: Any) -> tuple[int, str, dict[str, Any]]:
    """A response reduced to the parts that must not differ between two 404s.

    `instance` and `request_id` are dropped, and neither is a leak. `instance` echoes the path the
    caller just sent, so it tells them only the id they already chose; `request_id` is random per
    request. Everything else — status, media type, and every remaining member — has to match
    exactly, because anything that varied with whether the row existed would be the oracle this
    rule closes.

    The media type is included because a body comparison alone cannot see it: two responses can
    carry identical JSON and still be told apart by one arriving as `application/problem+json` and
    the other as `application/json`.
    """
    body = dict(response.json())
    body.pop("instance", None)
    body.pop("request_id", None)
    return response.status_code, response.headers.get("content-type", ""), body


class TestAccessTokenDependency:
    """Only a token this service signed, and still valid, gets through."""

    def test_a_valid_token_is_accepted(self, client: TestClient) -> None:
        _, token = _register(client, "sam@example.com")

        response = client.get(ANALYSES, headers=_auth(token))

        assert response.status_code == 200

    def test_a_missing_header_is_401(self, client: TestClient) -> None:
        assert client.get(ANALYSES).status_code == 401

    def test_the_401_is_a_problem_document(self, client: TestClient) -> None:
        """RFC 9457, and the header RFC 9110 requires on a 401."""
        response = client.get(ANALYSES)

        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.headers["www-authenticate"] == "Bearer"
        body = response.json()
        assert body["status"] == 401
        assert body["type"].endswith("/unauthorized")
        assert body["title"] == "Unauthorized"
        assert body["instance"] == ANALYSES

    def test_a_non_bearer_scheme_is_401(self, client: TestClient) -> None:
        response = client.get(ANALYSES, headers={"Authorization": "Basic c2FtOnB3"})

        assert response.status_code == 401

    def test_a_malformed_token_is_401(self, client: TestClient) -> None:
        assert client.get(ANALYSES, headers=_auth("not-a-jwt")).status_code == 401

    def test_an_expired_token_is_401(self, client: TestClient, settings: Settings) -> None:
        """Issued two hours ago against a fifteen-minute TTL.

        `issued_at` is injectable precisely so this test needs neither a frozen clock nor a sleep.
        """
        token = issue_access_token(
            user_id=uuid.uuid4(),
            settings=settings,
            issued_at=datetime.now(UTC) - timedelta(hours=2),
        )

        assert client.get(ANALYSES, headers=_auth(token)).status_code == 401

    def test_a_tampered_payload_is_401(self, client: TestClient) -> None:
        """Re-point `sub` at another user, keeping the original signature.

        This is the attack the signature exists to stop: the payload is readable and editable by
        anyone holding the token, and only the HMAC makes editing it useless.
        """
        _, token = _register(client, "sam@example.com")
        header, payload, signature = token.split(".")

        claims = json.loads(_b64url_decode(payload))
        claims["sub"] = str(uuid.uuid4())
        forged_payload = (
            base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("ascii").rstrip("=")
        )

        response = client.get(ANALYSES, headers=_auth(f"{header}.{forged_payload}.{signature}"))

        assert response.status_code == 401

    def test_a_token_signed_with_another_secret_is_401(self, client: TestClient) -> None:
        """A well-formed, unexpired, correctly-structured token — signed by the wrong issuer."""
        foreign = Settings(
            environment="test",
            log_level="warning",
            pose_backend="fake",
            # `SecretStr` explicitly: pydantic would coerce the bare string at runtime, but the
            # field is declared `SecretStr` and mypy checks the declaration, not the coercion.
            jwt_secret=SecretStr("a-different-secret-entirely-long-enough"),
        )
        token = issue_access_token(user_id=uuid.uuid4(), settings=foreign)

        assert client.get(ANALYSES, headers=_auth(token)).status_code == 401

    def test_an_alg_none_token_is_401(self, client: TestClient) -> None:
        """The classic JWT forgery: declare no algorithm and supply no signature.

        Constructed by hand because PyJWT will not encode `alg: none` without being forced. It is
        rejected because `decode_access_token` passes `algorithms=[ALGORITHM]` — the verifier
        names what it accepts instead of reading it out of attacker-supplied header bytes.
        """

        def _segment(data: dict[str, Any]) -> str:
            raw = json.dumps(data).encode("utf-8")
            return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

        now = datetime.now(UTC)
        forged = "{}.{}.".format(
            _segment({"alg": "none", "typ": "JWT"}),
            _segment(
                {
                    "sub": str(uuid.uuid4()),
                    "iat": int(now.timestamp()),
                    "exp": int((now + timedelta(hours=1)).timestamp()),
                }
            ),
        )

        assert client.get(ANALYSES, headers=_auth(forged)).status_code == 401

    def test_every_rejection_reads_the_same(self, client: TestClient, settings: Settings) -> None:
        """Expired, forged and absent are one message.

        A client that could tell "expired" from "bad signature" would learn which of its guesses
        were structurally correct — the same oracle `_verify_credentials` closes at sign-in.
        """
        expired = issue_access_token(
            user_id=uuid.uuid4(),
            settings=settings,
            issued_at=datetime.now(UTC) - timedelta(hours=2),
        )

        bodies = [
            _comparable(client.get(ANALYSES)),
            _comparable(client.get(ANALYSES, headers=_auth("not-a-jwt"))),
            _comparable(client.get(ANALYSES, headers=_auth(expired))),
        ]

        assert bodies[0] == bodies[1] == bodies[2]


class TestRouteTable:
    """The rule that cannot be forgotten, because forgetting it fails a test."""

    def test_every_route_is_public_by_name_or_authenticated(self, app: FastAPI) -> None:
        """Enumerate the registered routes; each must be listed public or carry the dependency.

        This is the test the ticket exists for. Route-level auth is a convention someone has to
        remember on every new endpoint, and conventions are not enforceable. Enumerating the real
        route table turns "we always add the dependency" into something CI can check.
        """
        unprotected = [
            f"{','.join(sorted(route.methods or set()))} {route.path}"
            for route in _iter_api_routes(app)
            if route.path not in _PUBLIC_PATHS and not _depends_on_current_user(route)
        ]

        assert not unprotected, (
            "These routes neither require authentication nor appear in _PUBLIC_PATHS: "
            f"{unprotected}. Add the `get_current_user_id` dependency, or — if the route really "
            "is public — add its path to _PUBLIC_PATHS so the decision is visible in review."
        )

    def test_the_public_list_has_no_stale_entries(self, app: FastAPI) -> None:
        """An allowlist that outlives the routes it names quietly stops meaning anything.

        Without this, deleting a public route leaves its path behind, and a later route reusing
        that path is exempted by an entry nobody chose for it.
        """
        registered = _all_paths(app)

        assert not _PUBLIC_PATHS - registered, (
            f"_PUBLIC_PATHS names routes that no longer exist: {sorted(_PUBLIC_PATHS - registered)}"
        )

    def test_the_analyses_routes_are_all_protected(self, app: FastAPI) -> None:
        """The specific claim, spelled out, so the general test above cannot pass vacuously."""
        analyses_routes = [
            route for route in _iter_api_routes(app) if route.path.startswith(ANALYSES)
        ]

        # 5 as of E10's trend endpoint: create, list, get, delete, trunk-inclination trend.
        assert len(analyses_routes) == 5
        assert all(_depends_on_current_user(route) for route in analyses_routes)


def _walk_routes(app: FastAPI) -> Iterator[Any]:
    """Every route object in the app, descending into included routers.

    `app.routes` is not a flat list. This FastAPI version keeps an included router as a single
    wrapper entry (`_IncludedRouter`) holding the real routes on `original_router`, so a loop over
    `app.routes` alone sees three wrappers and none of the ten endpoints — and a protection test
    written that way passes by checking nothing.

    One traversal, used by both callers below. Two separate walks drifted apart in review: the
    path collector had a descent condition the route collector did not, so the two could disagree
    about what was registered, and the allowlist would be validated against a different set of
    routes than the one being protected.
    """
    stack: list[Any] = list(app.routes)
    while stack:
        route = stack.pop()
        yield route
        # An `APIRoute` has no `routes`, so this is a no-op for leaves; a wrapper exposes the real
        # router on `original_router`, and a plain `Mount` holds its children on `routes`.
        container = getattr(route, "original_router", route)
        stack.extend(getattr(container, "routes", []))


def _iter_api_routes(app: FastAPI) -> list[APIRoute]:
    """The endpoints, as opposed to routers, mounts and the framework's own entries.

    `test_the_analyses_routes_are_all_protected` pins the count, so a future framework change that
    breaks this walk fails loudly rather than quietly protecting nothing.
    """
    return [route for route in _walk_routes(app) if isinstance(route, APIRoute)]


def _all_paths(app: FastAPI) -> set[str]:
    """Every registered path, including the framework's own non-`APIRoute` entries."""
    return {
        path for route in _walk_routes(app) if isinstance(path := getattr(route, "path", None), str)
    }


def _depends_on_current_user(route: APIRoute) -> bool:
    """Whether `get_current_user_id` appears anywhere in a route's dependency tree.

    Recursive rather than a scan of the route's own parameters, because a dependency can be
    nested — a route depending on something that itself depends on the current user is just as
    protected, and a flat check would report it as a hole.
    """
    stack = list(route.dependant.dependencies)
    while stack:
        dependency = stack.pop()
        if dependency.call is get_current_user_id:
            return True
        stack.extend(dependency.dependencies)
    return False


class TestCrossTenantIsolation:
    """Another user's analysis is indistinguishable from one that was never there.

    The ticket names read, update and delete. There is no update route — an analysis is a
    derived record, and E6 gave it no mutating endpoint — so read and delete are the complete set.
    """

    async def test_reading_another_users_analysis_is_404(
        self, client: TestClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        owner, _ = _register(client, "owner@example.com")
        _, intruder_token = _register(client, "intruder@example.com")
        analysis_id = await _store_analysis(session_factory, owner)

        response = client.get(f"{ANALYSES}/{analysis_id}", headers=_auth(intruder_token))

        assert response.status_code == 404

    async def test_deleting_another_users_analysis_is_404(
        self, client: TestClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        owner, _ = _register(client, "owner@example.com")
        _, intruder_token = _register(client, "intruder@example.com")
        analysis_id = await _store_analysis(session_factory, owner)

        response = client.delete(f"{ANALYSES}/{analysis_id}", headers=_auth(intruder_token))

        assert response.status_code == 404

    async def test_a_refused_delete_does_not_delete(
        self, client: TestClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """The 404 has to mean "nothing happened", not "it happened and we said no".

        A delete scoped by `user_id` matches no row, so there is nothing to undo — but that is a
        claim about the SQL, and this asserts the consequence: the owner still has their analysis.
        """
        owner, owner_token = _register(client, "owner@example.com")
        _, intruder_token = _register(client, "intruder@example.com")
        analysis_id = await _store_analysis(session_factory, owner)

        client.delete(f"{ANALYSES}/{analysis_id}", headers=_auth(intruder_token))

        still_there = client.get(f"{ANALYSES}/{analysis_id}", headers=_auth(owner_token))
        assert still_there.status_code == 200

    async def test_another_users_analysis_is_not_listed(
        self, client: TestClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        owner, _ = _register(client, "owner@example.com")
        _, intruder_token = _register(client, "intruder@example.com")
        await _store_analysis(session_factory, owner)

        body = client.get(ANALYSES, headers=_auth(intruder_token)).json()

        assert body["items"] == []

    async def test_the_two_404s_are_indistinguishable(
        self, client: TestClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """The acceptance criterion, and the reason this rule is 404 rather than 403.

        A 403 would confirm the row exists, which is exactly what makes walking a space of
        identifiers worth an attacker's time. Here the response to someone else's real analysis
        and to an id that was never issued are the same bytes, so enumeration returns no signal
        at all.
        """
        owner, _ = _register(client, "owner@example.com")
        _, intruder_token = _register(client, "intruder@example.com")
        real_but_not_theirs = await _store_analysis(session_factory, owner)
        never_existed = uuid.uuid4()

        theirs = client.get(f"{ANALYSES}/{real_but_not_theirs}", headers=_auth(intruder_token))
        ghost = client.get(f"{ANALYSES}/{never_existed}", headers=_auth(intruder_token))

        assert _comparable(theirs) == _comparable(ghost)

    async def test_the_owner_can_still_read_their_own(
        self, client: TestClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """The control. Without it, a repository that returned None for everyone would pass.

        Every assertion above is satisfied by an API that serves nobody, so one of them has to
        show the door opens for the person holding the key.
        """
        owner, owner_token = _register(client, "owner@example.com")
        analysis_id = await _store_analysis(session_factory, owner)

        response = client.get(f"{ANALYSES}/{analysis_id}", headers=_auth(owner_token))

        assert response.status_code == 200
        assert response.json()["id"] == str(analysis_id)
