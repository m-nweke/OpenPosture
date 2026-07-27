"""The factory is the seam the legacy service never had.

`API/app.py` built its Flask app at import time against real credentials, so nothing in it could
be tested and no two configurations could coexist. These tests assert the property that fixes
that: an app is a value, built from settings passed in, and two of them are independent.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from openposture_api import __version__
from openposture_api.config import Settings
from openposture_api.main import create_app


class TestConstruction:
    def test_two_apps_built_from_different_settings_are_independent(self) -> None:
        """The property that makes the suite order-independent: no shared module-level state."""
        development = create_app(Settings(environment="development", json_logs=True))
        production = create_app(Settings(environment="production"))

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
        app = create_app(Settings(environment="production"))
        with TestClient(app) as client:
            assert client.get("/docs").status_code == 404

    def test_the_openapi_schema_is_served_regardless(self) -> None:
        """OP-45 generates the frontend's TypeScript types from this document, so it is a build
        input rather than a convenience route."""
        app = create_app(Settings(environment="production"))
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
