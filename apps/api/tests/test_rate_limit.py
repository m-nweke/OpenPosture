"""OP-59: 5/minute on login, a configurable cap on analyses, both as RFC 9457 429s.

Two layers under test. `TestClientIpResolution` is a direct, no-HTTP unit test of
`resolve_client_ip` — the trust-boundary decision the ticket calls out as the part most likely to
be subtly wrong. `TestLoginRateLimit`, `TestAnalysesRateLimit` and
`TestClientIpResolutionEndToEnd` drive the real routes over ASGI, because "the sixth request in a
minute is refused" and "a spoofed header does not buy a fresh bucket" are properties of the wired
system, not of any one function.
"""

from __future__ import annotations

import io
import time
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from openposture_api.auth import AUTH_PREFIX, get_current_user_id
from openposture_api.config import Settings
from openposture_api.db import Base, get_session
from openposture_api.main import create_app
from openposture_api.pose import get_pose_backend
from openposture_api.rate_limit import _retry_after_seconds, resolve_client_ip
from openposture_api.storage import LocalDiskStorage, get_storage
from pose_backends.fake import FakePoseBackend

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

    from fastapi import FastAPI

LOGIN = f"{AUTH_PREFIX}/login"
ANALYSES = "/api/v1/analyses"

_CREDENTIALS = {"email": "sam@example.com", "password": "the-wrong-password-every-time"}
"""Deliberately wrong. The rate limit is enforced before the route body runs a query, so a login
attempt that will 401 anyway still counts against the bucket — using real credentials would make
these tests also depend on registration succeeding, which is not what is under test here."""


def _settings(**overrides: Any) -> Settings:
    return Settings(
        environment="test",
        log_level="warning",
        json_logs=True,
        pose_backend="fake",
        refresh_cookie_secure=False,
        **overrides,
    )


class TestClientIpResolution:
    """`resolve_client_ip` in isolation — no ASGI, no TestClient, just scopes."""

    @staticmethod
    def _request(
        *,
        settings: Settings,
        client_host: str = "203.0.113.9",
        forwarded_for: str | None = None,
    ) -> Request:
        headers = [(b"x-forwarded-for", forwarded_for.encode())] if forwarded_for else []
        scope: dict[str, Any] = {
            "type": "http",
            "headers": headers,
            "client": (client_host, 12345),
            "app": create_app(settings, load_backend=False),
        }
        return Request(scope)

    def test_by_default_the_header_is_not_read_at_all(self) -> None:
        """`trusted_proxy_hops=0` — today's topology: nothing sits in front of this service."""
        request = self._request(
            settings=_settings(trusted_proxy_hops=0),
            client_host="203.0.113.9",
            forwarded_for="6.6.6.6",
        )

        assert resolve_client_ip(request) == "203.0.113.9"

    def test_no_header_falls_back_to_the_direct_peer_regardless_of_hop_count(self) -> None:
        request = self._request(settings=_settings(trusted_proxy_hops=1), client_host="203.0.113.9")

        assert resolve_client_ip(request) == "203.0.113.9"

    def test_one_trusted_hop_reads_the_proxy_appended_entry(self) -> None:
        """`"attacker-claim, 9.9.9.9"` — a real proxy appends what it saw, at the *end*."""
        request = self._request(
            settings=_settings(trusted_proxy_hops=1),
            forwarded_for="attacker-claim, 9.9.9.9",
        )

        assert resolve_client_ip(request) == "9.9.9.9"

    def test_the_client_supplied_entry_is_never_trusted_however_it_is_spelled(self) -> None:
        """The whole point: only the position counts. Whatever the attacker writes in the
        untrusted slot, the resolved key is unchanged as long as the trusted slot is unchanged."""
        settings = _settings(trusted_proxy_hops=1)
        first = self._request(settings=settings, forwarded_for="1.1.1.1, 9.9.9.9")
        second = self._request(settings=settings, forwarded_for="totally-different-lie, 9.9.9.9")

        assert resolve_client_ip(first) == resolve_client_ip(second) == "9.9.9.9"

    def test_two_trusted_hops_reads_the_outermost_trusted_entry(self) -> None:
        """A chain of two trusted proxies: the untrusted, attacker-supplied prefix is discarded
        no matter how many fake entries it contains, by counting from the right."""
        request = self._request(
            settings=_settings(trusted_proxy_hops=2),
            forwarded_for="attacker-lie, real-browser-ip, proxy1-ip",
        )

        assert resolve_client_ip(request) == "real-browser-ip"

    def test_fewer_entries_than_configured_hops_falls_back_rather_than_crashing(self) -> None:
        """A misconfigured or lying proxy must not turn every request into a 500."""
        request = self._request(
            settings=_settings(trusted_proxy_hops=3),
            forwarded_for="only-one-entry",
        )

        assert resolve_client_ip(request) == "only-one-entry"


class TestRetryAfterRounding:
    """`Retry-After` must never advertise a shorter wait than the window actually has left —
    flooring a fractional reset (e.g. 10.9s -> 10) would let a client retry before the window
    resets and land right back on a 429."""

    def test_a_fractional_reset_rounds_up_not_down(self) -> None:
        stats = MagicMock()
        stats.reset_time = time.time() + 10.1
        limiter = MagicMock()
        limiter.limiter.get_window_stats.return_value = stats

        request = MagicMock()
        request.app.state.limiter = limiter
        request.state.view_rate_limit = (MagicMock(), MagicMock())

        assert _retry_after_seconds(request) == 11


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A fresh in-memory database, matching `tests/test_auth.py`'s fixture of the same name."""
    engine = create_async_engine("sqlite+aiosqlite:///file::memory:?cache=shared&uri=true")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@contextmanager
def _login_client(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> Iterator[TestClient]:
    app: FastAPI = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        # `lifespan` overwrites `app.state.session_factory` from `settings.database_url`, so this
        # has to happen after the context manager has entered, not before — see `test_auth.py`.
        app.state.session_factory = session_factory
        yield client


class TestLoginRateLimit:
    """5 per minute per IP, the credential-stuffing control (OP-59)."""

    def test_a_sixth_attempt_within_the_window_is_refused(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        with _login_client(_settings(), session_factory) as client:
            for _ in range(5):
                response = client.post(LOGIN, json=_CREDENTIALS)
                assert response.status_code == 401

            sixth = client.post(LOGIN, json=_CREDENTIALS)

        assert sixth.status_code == 429

    def test_the_429_carries_retry_after_and_the_problem_envelope(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        with _login_client(_settings(), session_factory) as client:
            for _ in range(5):
                client.post(LOGIN, json=_CREDENTIALS)
            response = client.post(LOGIN, json=_CREDENTIALS)

        assert response.status_code == 429
        assert response.headers["content-type"].startswith("application/problem+json")
        retry_after = response.headers["retry-after"]
        assert retry_after.isdigit()
        assert int(retry_after) > 0

        body = response.json()
        assert body["status"] == 429
        assert body["type"].endswith("/too-many-requests")
        assert body["instance"] == LOGIN
        assert "detail" in body

    def test_the_limit_is_configurable(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A tighter limit than the 5/minute default must take effect without touching code."""
        with _login_client(_settings(login_rate_limit="1/minute"), session_factory) as client:
            first = client.post(LOGIN, json=_CREDENTIALS)
            second = client.post(LOGIN, json=_CREDENTIALS)

        assert first.status_code == 401
        assert second.status_code == 429


class TestClientIpResolutionEndToEnd:
    """The trust boundary again, but through the real route rather than a bare `Request`."""

    def test_spoofing_the_forwarded_header_does_not_buy_a_fresh_bucket_by_default(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """`trusted_proxy_hops=0` is today's default. A different `X-Forwarded-For` on every
        request must not look like five different attackers to the limiter."""
        with _login_client(_settings(), session_factory) as client:
            for i in range(5):
                client.post(LOGIN, json=_CREDENTIALS, headers={"X-Forwarded-For": f"6.6.6.{i}"})
            sixth = client.post(LOGIN, json=_CREDENTIALS, headers={"X-Forwarded-For": "6.6.6.99"})

        assert sixth.status_code == 429

    def test_two_real_clients_behind_one_trusted_proxy_get_independent_buckets(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Once a proxy hop is trusted, users sharing it must not be collapsed onto one bucket —
        the "punishes users behind shared NAT" failure the ticket names."""
        settings = _settings(trusted_proxy_hops=1, login_rate_limit="1/minute")
        with _login_client(settings, session_factory) as client:
            user_a = client.post(
                LOGIN, json=_CREDENTIALS, headers={"X-Forwarded-For": "claim, 10.0.0.1"}
            )
            user_b = client.post(
                LOGIN, json=_CREDENTIALS, headers={"X-Forwarded-For": "claim, 10.0.0.2"}
            )

        assert user_a.status_code == 401
        assert user_b.status_code == 401

    def test_rotating_the_spoofable_prefix_does_not_evade_a_trusted_hop_either(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Even with a hop trusted, only the proxy-appended entry counts — the client's own
        claimed identity, wherever it sits, is never load-bearing."""
        settings = _settings(trusted_proxy_hops=1, login_rate_limit="1/minute")
        with _login_client(settings, session_factory) as client:
            first = client.post(
                LOGIN, json=_CREDENTIALS, headers={"X-Forwarded-For": "lie-one, 10.0.0.1"}
            )
            second = client.post(
                LOGIN, json=_CREDENTIALS, headers={"X-Forwarded-For": "lie-two, 10.0.0.1"}
            )

        assert first.status_code == 401
        assert second.status_code == 429


def _image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color=(120, 130, 140)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def storage(tmp_path: Path) -> LocalDiskStorage:
    return LocalDiskStorage(tmp_path / "objects")


async def _fake_session() -> AsyncIterator[Any]:
    """Enough of a session for `AnalysisRepository.create` to look successful, matching the mock
    in `tests/test_analyses.py`."""
    pending: list[Any] = []

    async def _flush() -> None:
        for obj in pending:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = None
    execute_result.scalars.return_value.all.return_value = []

    mock = MagicMock()
    mock.add = MagicMock(side_effect=pending.append)
    mock.add_all = MagicMock(side_effect=pending.extend)
    mock.get = AsyncMock(return_value=None)
    mock.flush = AsyncMock(side_effect=_flush)
    mock.commit = AsyncMock()
    mock.close = AsyncMock()
    mock.execute = AsyncMock(return_value=execute_result)
    yield mock


@contextmanager
def _analyses_client(settings: Settings, storage: LocalDiskStorage) -> Iterator[TestClient]:
    app: FastAPI = create_app(settings, load_backend=False)
    app.dependency_overrides[get_pose_backend] = lambda: FakePoseBackend()
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_session] = _fake_session
    app.dependency_overrides[get_current_user_id] = lambda: uuid.uuid4()
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestAnalysesRateLimit:
    """A resource control, not a credential one — a small limit here to keep the test fast."""

    def test_a_request_past_the_limit_is_refused(self, storage: LocalDiskStorage) -> None:
        settings = _settings(analyses_rate_limit="2/minute")
        with _analyses_client(settings, storage) as client:
            for _ in range(2):
                response = client.post(
                    ANALYSES, files={"image": ("photo.jpg", _image_bytes(), "image/jpeg")}
                )
                assert response.status_code == 201

            third = client.post(
                ANALYSES, files={"image": ("photo.jpg", _image_bytes(), "image/jpeg")}
            )

        assert third.status_code == 429
        assert third.headers["retry-after"].isdigit()
        body = third.json()
        assert body["type"].endswith("/too-many-requests")
        assert body["instance"] == ANALYSES

    def test_the_analyses_limit_is_independent_of_the_login_limit(
        self, storage: LocalDiskStorage
    ) -> None:
        """The two are configured, and so enforced, separately — exhausting one must not touch
        the other."""
        settings = _settings(analyses_rate_limit="1/minute", login_rate_limit="5/minute")
        with _analyses_client(settings, storage) as client:
            client.post(ANALYSES, files={"image": ("photo.jpg", _image_bytes(), "image/jpeg")})
            blocked = client.post(
                ANALYSES, files={"image": ("photo.jpg", _image_bytes(), "image/jpeg")}
            )
            login_attempt = client.post(LOGIN, json=_CREDENTIALS)

        assert blocked.status_code == 429
        assert login_attempt.status_code in (401, 422)
