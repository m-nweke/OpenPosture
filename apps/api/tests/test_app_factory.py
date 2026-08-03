"""The factory is the seam the legacy service never had.

`API/app.py` built its Flask app at import time against real credentials, so nothing in it could
be tested and no two configurations could coexist. These tests assert the property that fixes
that: an app is a value, built from settings passed in, and two of them are independent.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import NoSuchModuleError

from openposture_api import __version__, main
from openposture_api.config import Settings
from openposture_api.main import create_app
from openposture_api.pose import PoseBackendState, PoseBackendStatus


def _production_settings() -> Settings:
    """Production settings that get past OP-55's boot guard.

    Production refuses to start on the repository's default signing key, so every production
    `Settings` now has to supply one. These tests are about the factory, not about auth — the
    secret is scaffolding, and naming it once keeps that obvious.
    """
    return Settings(
        environment="production", jwt_secret=SecretStr("test-only-signing-key-of-sufficient-length")
    )


class _RecordingBackend:
    """A backend whose only job is to record that `close` was called."""

    def __init__(self, log: list[str]) -> None:
        self._log = log

    def close(self) -> None:
        self._log.append("closed")


def _recording_state(log: list[str]) -> PoseBackendState:
    """A *real* `PoseBackendState` holding a backend that records its own closure.

    The real dataclass rather than a mock, because `close_pose_backend` calls `close` only when
    the attribute is present and callable — a bare mock satisfies that check for reasons the
    production path would not, so it would pass against a backend that has no `close` at all.
    """
    state = PoseBackendState(status=PoseBackendStatus.READY)
    state.backend = _RecordingBackend(log)  # type: ignore[assignment]
    return state


class TestConstruction:
    def test_two_apps_built_from_different_settings_are_independent(self) -> None:
        """The property that makes the suite order-independent: no shared module-level state."""
        development = create_app(Settings(environment="development", json_logs=True))
        production = create_app(_production_settings())

        assert development.state.settings.environment == "development"
        assert production.state.settings.environment == "production"

    def test_the_app_is_stamped_with_the_package_version(self) -> None:
        app = create_app(Settings(environment="test", json_logs=True))

        assert app.version == __version__

    def test_construction_starts_no_connections(self) -> None:
        """Building an app must stay cheap and side-effect free — OP-40 puts the model load in
        `lifespan`, where it can be skipped by a test that does not need it."""
        app = create_app(Settings(environment="test", json_logs=True))

        assert not hasattr(app.state, "pose_backend")


class TestDocumentation:
    def test_interactive_docs_are_served_outside_production(self) -> None:
        app = create_app(Settings(environment="development", json_logs=True))
        with TestClient(app) as client:
            assert client.get("/docs").status_code == 200

    def test_interactive_docs_are_absent_in_production(self) -> None:
        app = create_app(_production_settings())
        with TestClient(app) as client:
            assert client.get("/docs").status_code == 404

    def test_the_openapi_schema_is_served_regardless(self) -> None:
        """OP-45 generates the frontend's TypeScript types from this document, so it is a build
        input rather than a convenience route."""
        app = create_app(_production_settings())
        with TestClient(app) as client:
            schema = client.get("/openapi.json")

        assert schema.status_code == 200
        assert schema.json()["info"]["version"] == __version__

    def test_both_health_endpoints_are_documented(self) -> None:
        """An undocumented health endpoint is one an orchestrator's author has to guess at."""
        app = create_app(Settings(environment="test", json_logs=True))
        with TestClient(app) as client:
            paths = client.get("/openapi.json").json()["paths"]

        assert "/health" in paths
        assert "/health/ready" in paths


class TestStartupFailure:
    """What happens when startup gets part-way and then fails.

    The interesting case is not the failure itself — a bad configuration should stop the process
    — but what is still holding resources afterwards, and whether the error a person sees is the
    real one.
    """

    #: A driver that is not installed. `create_engine` raises on this immediately, because the
    #: URL is parsed and the dialect imported eagerly even though *connecting* is lazy. That
    #: happens after the pose backend has loaded, which makes it the realistic partial startup.
    BROKEN_URL = "postgresql+nosuchdriver://someone@nowhere/nothing"

    def _app(self, monkeypatch: pytest.MonkeyPatch, log: list[str]) -> FastAPI:
        """An app whose pose backend records that it was closed.

        `load_pose_backend` is patched rather than `app.state` being overwritten after the fact:
        `lifespan` calls it and binds the result to the local it later closes, so assigning to
        `app.state.pose_backend_state` from a test is discarded on startup.
        """
        monkeypatch.setattr(main, "load_pose_backend", lambda _settings: _recording_state(log))
        return create_app(
            Settings(
                environment="test",
                json_logs=True,
                pose_backend="fake",
                database_url=self.BROKEN_URL,
            )
        )

    def test_a_failed_engine_still_releases_the_pose_backend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pose model loaded and then leaked is tens of seconds of work and a live native
        handle, dropped on every failed boot."""
        closed: list[str] = []
        app = self._app(monkeypatch, closed)

        with pytest.raises(NoSuchModuleError), TestClient(app):
            pass  # pragma: no cover - startup raises before the body runs

        assert closed == ["closed"], "the pose backend was not released on a failed startup"

    def test_the_real_error_is_not_replaced_by_a_teardown_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Teardown runs against attributes that may never have been assigned.

        Reaching for `app.state.db_engine` unguarded would surface an `AttributeError` from the
        shutdown path instead of the configuration error that actually stopped startup — the more
        confusing of the two, and the only one a reader would see.
        """
        app = self._app(monkeypatch, [])

        with pytest.raises(NoSuchModuleError) as raised, TestClient(app):
            pass  # pragma: no cover - startup raises before the body runs

        assert "nosuchdriver" in str(raised.value)
