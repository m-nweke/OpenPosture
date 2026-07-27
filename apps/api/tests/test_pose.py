"""One backend per process, loaded at startup, and honest about it when it fails.

`RUNDOWN.md`'s Open Items flagged per-request model loading on the original project and never
resolved it. These tests are the proof it is resolved: the backend is built once, before the
first request, behind a dependency a test can replace — and a failure to build leaves a
diagnosable container rather than a crash loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from openposture_api.config import Settings
from openposture_api.main import create_app
from openposture_api.pose import (
    PROBE_NAME,
    PoseBackendState,
    PoseBackendStatus,
    close_pose_backend,
    get_pose_backend,
    load_pose_backend,
)
from pose_backends.base import PoseBackend
from pose_backends.errors import ModelNotFoundError
from pose_backends.fake import FakePoseBackend, PosePreset


def _readiness_check(body: dict[str, object], name: str) -> dict[str, object]:
    checks = body["checks"]
    assert isinstance(checks, list)
    matching = [check for check in checks if check["name"] == name]
    assert matching, f"no readiness check named {name!r} in {checks}"
    return dict(matching[0])


class TestLoading:
    def test_a_configured_backend_loads_and_warms(self) -> None:
        state = load_pose_backend(Settings(environment="test", pose_backend="fake"))

        assert state.status is PoseBackendStatus.READY
        assert state.is_ready
        assert state.backend is not None
        assert state.backend.name == "fake"

    def test_the_configured_preset_reaches_the_backend(self) -> None:
        """Otherwise `POSE_BACKEND_PRESET` would be accepted, validated, and then ignored."""
        state = load_pose_backend(
            Settings(
                environment="test",
                pose_backend="fake",
                pose_backend_preset=PosePreset.HUNCHBACK,
            )
        )

        assert isinstance(state.backend, FakePoseBackend)
        assert state.backend.preset is PosePreset.HUNCHBACK

    def test_a_missing_model_does_not_raise(self) -> None:
        """A container that exits on a missing model restarts forever and explains nothing.

        Deterministic with or without the `mediapipe` extra installed: the adapter checks the
        path before importing the library, so this is `ModelNotFoundError` either way — which is
        what lets it run in CI, where the extra is deliberately absent.
        """
        state = load_pose_backend(
            Settings(
                environment="test",
                pose_backend="mediapipe",
                model_path=Path("/nonexistent/pose_landmarker_full.task"),
            )
        )

        assert state.status is PoseBackendStatus.FAILED
        assert not state.is_ready
        assert state.backend is None

    def test_a_load_failure_records_the_cause_by_name(self) -> None:
        """`curl /health/ready` should be the whole diagnosis, not the first step of one."""
        state = load_pose_backend(
            Settings(
                environment="test",
                pose_backend="mediapipe",
                model_path=Path("/nonexistent/pose_landmarker_full.task"),
            )
        )

        assert isinstance(state.error, ModelNotFoundError)
        assert state.detail is not None
        assert "ModelNotFoundError" in state.detail
        # The path it looked for is what makes the message actionable.
        assert "/nonexistent/pose_landmarker_full.task" in state.detail


class TestLifespan:
    def test_the_backend_is_built_once_per_process(self, settings: Settings) -> None:
        """The acceptance criterion, and the defect the original never fixed: many requests, one
        model. A backend rebuilt per request would make every response pay the load."""
        app = create_app(settings)

        with TestClient(app) as client:
            seen = []
            for _ in range(3):
                client.get("/health/ready")
                seen.append(id(app.state.pose_backend_state.backend))

        assert len(set(seen)) == 1

    def test_nothing_is_loaded_by_constructing_the_app(self, settings: Settings) -> None:
        """Construction must stay cheap and side-effect free — the whole reason for a factory."""
        app = create_app(settings)

        assert app.state.pose_backend_state.status is PoseBackendStatus.PENDING
        assert app.state.pose_backend_state.backend is None

    def test_the_backend_is_ready_once_startup_has_run(self, settings: Settings) -> None:
        app = create_app(settings)

        with TestClient(app):
            assert app.state.pose_backend_state.status is PoseBackendStatus.READY

    def test_two_apps_get_independent_backends(self, settings: Settings) -> None:
        """Held on `app.state`, not in a module global — the property that made the legacy
        engine's `model` untestable."""
        first = create_app(settings)
        second = create_app(settings)

        with TestClient(first), TestClient(second):
            assert (
                first.state.pose_backend_state.backend
                is not second.state.pose_backend_state.backend
            )

    def test_shutdown_closes_the_backend(self, settings: Settings) -> None:
        closed: list[bool] = []

        class _Closable:
            name = "closable"

            def close(self) -> None:
                closed.append(True)

        state = PoseBackendState(status=PoseBackendStatus.READY, backend=_Closable())  # type: ignore[arg-type]
        close_pose_backend(state)

        assert closed == [True]

    def test_a_backend_without_close_is_not_a_shutdown_error(self) -> None:
        """`close` is not in the Protocol, so a five-line test double need not implement it."""

        class _Bare:
            name = "bare"

        close_pose_backend(PoseBackendState(status=PoseBackendStatus.READY, backend=_Bare()))  # type: ignore[arg-type]

    def test_a_failing_close_does_not_break_shutdown(self) -> None:
        """A process that is stopping anyway should stop, not emit a traceback."""

        class _BadCloser:
            name = "bad"

            def close(self) -> None:
                raise RuntimeError("native handle already gone")

        close_pose_backend(PoseBackendState(status=PoseBackendStatus.READY, backend=_BadCloser()))  # type: ignore[arg-type]


class TestReadinessReporting:
    def test_readiness_is_true_once_the_backend_is_warm(self, settings: Settings) -> None:
        app = create_app(settings)

        with TestClient(app) as client:
            response = client.get("/health/ready")

        assert response.status_code == 200
        assert _readiness_check(response.json(), PROBE_NAME)["ready"] is True

    def test_a_pending_backend_is_not_ready(self) -> None:
        """Startup blocks the server from accepting connections, so this state is unreachable
        over HTTP — but it is what the probe reports before startup runs, and a probe that said
        "ready" here would be wrong the moment anything defers loading."""
        state = PoseBackendState()

        assert state.is_ready is False

    def test_a_failed_backend_makes_the_container_unready_with_a_named_cause(self) -> None:
        settings = Settings(
            environment="test",
            json_logs=True,
            pose_backend="mediapipe",
            model_path=Path("/nonexistent/model.task"),
        )
        app = create_app(settings)

        with TestClient(app) as client:
            response = client.get("/health/ready")

        assert response.status_code == 503
        check = _readiness_check(response.json(), PROBE_NAME)
        assert check["ready"] is False
        assert "ModelNotFoundError" in str(check["detail"])

    def test_a_failed_backend_still_reports_alive(self) -> None:
        """Liveness must stay green: restarting the container will not conjure a model file."""
        settings = Settings(
            environment="test",
            json_logs=True,
            pose_backend="mediapipe",
            model_path=Path("/nonexistent/model.task"),
        )

        with TestClient(create_app(settings)) as client:
            assert client.get("/health").status_code == 200


class TestDependency:
    @staticmethod
    def _app_with_route(settings: Settings, *, load_backend: bool = True) -> FastAPI:
        app = create_app(settings, load_backend=load_backend)

        @app.get("/backend-name")
        async def backend_name(
            backend: Annotated[PoseBackend, Depends(get_pose_backend)],
        ) -> dict[str, str]:
            return {"name": backend.name}

        return app

    def test_the_dependency_hands_out_the_lifespan_backend(self, settings: Settings) -> None:
        app = self._app_with_route(settings)

        with TestClient(app) as client:
            assert client.get("/backend-name").json() == {"name": "fake"}

    def test_the_dependency_can_be_overridden_without_any_model(self, settings: Settings) -> None:
        """The point of the seam: an endpoint test runs its real code path against a fake, with
        `load_backend=False` so startup touches nothing at all."""
        app = self._app_with_route(settings, load_backend=False)
        app.dependency_overrides[get_pose_backend] = lambda: FakePoseBackend(PosePreset.HUNCHBACK)

        with TestClient(app) as client:
            assert client.get("/backend-name").json() == {"name": "fake"}

    def test_a_route_needing_inference_gets_503_when_the_backend_failed(self) -> None:
        """Not a 500: the condition is the server's and may be temporary, and a 500 would suggest
        the request itself was mishandled."""
        settings = Settings(
            environment="test",
            json_logs=True,
            pose_backend="mediapipe",
            model_path=Path("/nonexistent/model.task"),
        )
        app = self._app_with_route(settings)

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/backend-name")

        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/problem+json")

    def test_the_503_names_the_cause_and_asks_for_a_retry(self) -> None:
        settings = Settings(
            environment="test",
            json_logs=True,
            pose_backend="mediapipe",
            model_path=Path("/nonexistent/model.task"),
        )
        app = self._app_with_route(settings)

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/backend-name")

        body = response.json()
        assert body["type"].endswith("/pose-backend-unavailable")
        assert body["backend_status"] == "failed"
        assert "ModelNotFoundError" in body["backend_detail"]
        assert response.headers["Retry-After"] == "30"
