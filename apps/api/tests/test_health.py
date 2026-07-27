"""Liveness and readiness answer different questions, and readiness can fail.

The tests that matter here are the ones proving the two endpoints *diverge*: a readiness probe
reporting failure must not make liveness fail too, because an orchestrator that sees liveness
fail restarts the container instead of routing around it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openposture_api import __version__
from openposture_api.config import Settings
from openposture_api.health import ReadinessCheck, ReadinessProbe, build_health_router
from openposture_api.main import create_app

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


def _ready(name: str) -> ReadinessCheck:
    return ReadinessCheck(name=name, ready=True)


def _not_ready(name: str, detail: str) -> ReadinessCheck:
    return ReadinessCheck(name=name, ready=False, detail=detail)


@pytest.fixture
def probe_client() -> Iterator[Callable[[list[ReadinessProbe]], TestClient]]:
    """Builds a bare app carrying only the probes a test names.

    The router is exercised directly rather than through `create_app`, which registers the
    pose backend's probe and will register storage's and the database's in later tickets.
    Aggregation semantics are the router's business, and asserting them through the factory
    makes every one of these tests fail the day a subsystem is added.

    Every client it hands out is entered and exited by the fixture. That closes the underlying
    transport rather than leaving it to the garbage collector, and it means these apps run their
    lifespan — so if the bare app ever gains startup or shutdown hooks, they execute here too
    instead of being silently skipped.
    """
    clients: list[TestClient] = []

    def build(probes: list[ReadinessProbe]) -> TestClient:
        app = FastAPI()
        app.include_router(build_health_router(version=__version__, probes=probes))
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield build

    for client in clients:
        client.__exit__(None, None, None)


class TestLiveness:
    def test_liveness_reports_ok_with_the_running_version(self, client: TestClient) -> None:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "version": __version__}

    def test_liveness_does_not_depend_on_readiness(self, settings: Settings) -> None:
        """The whole reason the endpoints are separate: a dead dependency must not look like a
        dead process, or the orchestrator kills a container that only needed its database back."""

        async def failing() -> ReadinessCheck:
            return _not_ready("database", "connection refused")

        app = create_app(settings, readiness_probes=[failing])
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/health/ready").status_code == 503


class TestReadiness:
    def test_readiness_with_no_registered_probes_is_ready_and_says_so_honestly(
        self, probe_client: Callable[[list[ReadinessProbe]], TestClient]
    ) -> None:
        """`checks` is present and empty rather than absent, so a consumer written against a
        subsystem-free build does not break when subsystems start registering."""
        response = probe_client([]).get("/health/ready")

        assert response.status_code == 200
        assert response.json() == {"status": "ready", "version": __version__, "checks": []}

    def test_every_probe_passing_is_ready(
        self, probe_client: Callable[[list[ReadinessProbe]], TestClient]
    ) -> None:
        async def backend() -> ReadinessCheck:
            return _ready("pose_backend")

        async def storage() -> ReadinessCheck:
            return _ready("storage")

        response = probe_client([backend, storage]).get("/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert [check["name"] for check in body["checks"]] == ["pose_backend", "storage"]

    def test_one_failing_probe_fails_the_whole_check(
        self, probe_client: Callable[[list[ReadinessProbe]], TestClient]
    ) -> None:
        async def backend() -> ReadinessCheck:
            return _ready("pose_backend")

        async def storage() -> ReadinessCheck:
            return _not_ready("storage", "bucket unreachable")

        response = probe_client([backend, storage]).get("/health/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert body["checks"][1] == {
            "name": "storage",
            "ready": False,
            "detail": "bucket unreachable",
        }

    def test_a_failing_probe_reports_503_not_a_200_carrying_bad_news(
        self, settings: Settings
    ) -> None:
        """Load balancers route on the status code and never read the body."""

        async def failing() -> ReadinessCheck:
            return _not_ready("database", "connection pool exhausted")

        app = create_app(settings, readiness_probes=[failing])
        with TestClient(app) as client:
            assert client.get("/health/ready").status_code == 503

    def test_probes_report_in_registration_order(
        self, probe_client: Callable[[list[ReadinessProbe]], TestClient]
    ) -> None:
        """Stable ordering keeps a health payload diffable between two deploys."""

        async def first() -> ReadinessCheck:
            return _ready("a")

        async def second() -> ReadinessCheck:
            return _ready("b")

        async def third() -> ReadinessCheck:
            return _ready("c")

        body = probe_client([first, second, third]).get("/health/ready").json()

        assert [check["name"] for check in body["checks"]] == ["a", "b", "c"]
