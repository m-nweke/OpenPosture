"""Every failure leaves through the same door, in the RFC 9457 envelope.

Two properties are worth more than the rest: the media type is `application/problem+json` (a
client content-negotiating on it must actually match), and an unhandled exception discloses the
request ID and nothing else. The second is a security property — exception text carries file
paths, driver messages, and occasionally credentials.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.requests import Request

from openposture_api.config import Settings
from openposture_api.errors import PROBLEM_CONTENT_TYPE, PROBLEM_TYPE_BASE, problem_response
from openposture_api.main import create_app


class _Body(BaseModel):
    count: int


@pytest.fixture
def failing_app(settings: Settings) -> FastAPI:
    """An app with routes that fail in each of the three ways the handlers cover."""
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("database password is hunter2")

    @app.get("/teapot")
    async def teapot() -> None:
        raise HTTPException(status_code=418, detail="I am a teapot")

    @app.get("/gone")
    async def gone() -> None:
        raise HTTPException(
            status_code=401,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.post("/validate")
    async def validate(body: _Body) -> _Body:
        return body

    return app


@pytest.fixture
def failing_client(failing_app: FastAPI) -> TestClient:
    return TestClient(failing_app, raise_server_exceptions=False)


class TestEnvelope:
    def test_a_deliberate_refusal_is_a_problem_document(self, failing_client: TestClient) -> None:
        response = failing_client.get("/teapot")

        assert response.status_code == 418
        body = response.json()
        assert body["status"] == 418
        assert body["detail"] == "I am a teapot"
        assert body["instance"] == "/teapot"

    def test_the_media_type_is_problem_json(self, failing_client: TestClient) -> None:
        """`application/json` here would defeat every client that negotiates on the type."""
        response = failing_client.get("/teapot")

        assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)

    def test_a_known_status_gets_a_stable_machine_readable_type(
        self, failing_client: TestClient
    ) -> None:
        """The frontend branches on this URI, so it must not be derived from the prose title."""
        response = failing_client.get("/missing")

        assert response.status_code == 404
        assert response.json()["type"] == f"{PROBLEM_TYPE_BASE}/not-found"

    def test_a_404_from_starlette_itself_uses_the_same_envelope(
        self, failing_client: TestClient
    ) -> None:
        """An unrouted path is the most common error any API returns; it must not be the one
        response with a different shape."""
        body = failing_client.get("/no-such-route").json()

        assert set(body) >= {"type", "title", "status", "detail", "instance"}

    def test_headers_from_the_raised_exception_survive(self, failing_client: TestClient) -> None:
        """`WWW-Authenticate` is required by RFC 9110 on a 401; dropping it while rewriting the
        body would make the response non-compliant."""
        response = failing_client.get("/gone")

        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"


class TestValidation:
    def test_a_schema_violation_is_422_in_the_same_envelope(
        self, failing_client: TestClient
    ) -> None:
        response = failing_client.post("/validate", json={"count": "not a number"})

        assert response.status_code == 422
        body = response.json()
        assert body["type"] == f"{PROBLEM_TYPE_BASE}/validation-error"
        assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)

    def test_per_field_detail_survives_as_an_extension_member(
        self, failing_client: TestClient
    ) -> None:
        """Flattening this into prose would leave a form unable to mark the offending input."""
        response = failing_client.post("/validate", json={"count": "not a number"})

        errors = response.json()["errors"]
        assert len(errors) == 1
        assert errors[0]["location"] == "body.count"
        assert errors[0]["message"]
        assert errors[0]["type"]

    def test_a_missing_body_is_reported_per_field_too(self, failing_client: TestClient) -> None:
        response = failing_client.post("/validate", json={})

        assert response.status_code == 422
        assert response.json()["errors"][0]["location"] == "body.count"


class TestUnhandledExceptions:
    def test_a_bug_becomes_a_500_problem_document(self, failing_client: TestClient) -> None:
        response = failing_client.get("/boom")

        assert response.status_code == 500
        assert response.json()["type"] == f"{PROBLEM_TYPE_BASE}/internal-server-error"

    def test_the_exception_text_never_reaches_the_client(self, failing_client: TestClient) -> None:
        """The route raises a message containing a credential. None of it may be echoed."""
        response = failing_client.get("/boom")

        assert "hunter2" not in response.text
        assert "RuntimeError" not in response.text
        assert "Traceback" not in response.text

    def test_the_request_id_is_disclosed_so_the_failure_can_be_reported(
        self, failing_client: TestClient
    ) -> None:
        """The one piece of internal state that is safe to share, and the only one that lets a
        user's bug report be joined to the server logs."""
        response = failing_client.get("/boom")

        assert response.json()["request_id"] == response.headers["X-Request-ID"]


class TestProblemsWithoutARequestId:
    """The path taken when the failure happened outside the request-ID middleware.

    Starlette's `ServerErrorMiddleware` sits outside every middleware the app installs, so a
    failure in the middleware stack itself is rendered with no ID available. The document must
    still be a valid problem document rather than raising a second error while reporting the
    first.
    """

    def test_the_field_is_omitted_rather_than_null(self) -> None:
        request = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})

        body = json.loads(bytes(problem_response(request, 500, "no id here").body))

        assert "request_id" not in body
        assert body["status"] == 500
        assert body["instance"] == "/x"
