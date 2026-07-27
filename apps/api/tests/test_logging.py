"""Every response carries a request ID, and every log line for that request carries the same one.

That correspondence is the whole feature. An ID on the response that does not appear in the logs
is decoration; an ID in the logs that the client never sees cannot be quoted in a bug report.
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openposture_api.config import Settings
from openposture_api.logging import configure_logging, current_request_id
from openposture_api.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def log_stream(settings: Settings) -> Iterator[io.StringIO]:
    """A buffer standing in for stderr, restored afterwards.

    Restoring matters: the root logger is process-wide, so a test that left its buffer
    installed would silently swallow every later test's log output.
    """
    buffer = io.StringIO()
    yield buffer
    configure_logging(settings)


@pytest.fixture
def logging_app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/logs")
    async def logs() -> dict[str, str | None]:
        structlog.get_logger("test").warning("handler_ran")
        return {"seen_by_handler": current_request_id()}

    return app


@pytest.fixture
def logging_client(logging_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(logging_app, raise_server_exceptions=False) as client:
        yield client


class TestResponseHeader:
    def test_every_response_carries_a_request_id(self, client: TestClient) -> None:
        assert client.get("/health").headers["X-Request-ID"]

    def test_two_requests_get_different_ids(self, client: TestClient) -> None:
        first = client.get("/health").headers["X-Request-ID"]
        second = client.get("/health").headers["X-Request-ID"]

        assert first != second

    def test_a_caller_supplied_id_is_honoured(self, client: TestClient) -> None:
        """A browser-side error report and the server logs can only be joined if the ID the
        client generated is the one the server uses."""
        response = client.get("/health", headers={"X-Request-ID": "trace-from-the-frontend"})

        assert response.headers["X-Request-ID"] == "trace-from-the-frontend"

    def test_an_absurdly_long_inbound_id_is_replaced_rather_than_echoed(
        self, client: TestClient
    ) -> None:
        """The header is attacker-controlled and lands in every log line for the request, so an
        unbounded one is a cheap way to write arbitrary volume into the logs."""
        response = client.get("/health", headers={"X-Request-ID": "x" * 5000})

        assert response.status_code == 200
        assert response.headers["X-Request-ID"] != "x" * 5000

    def test_a_bad_correlation_header_does_not_fail_the_request(self, client: TestClient) -> None:
        """It is a diagnostic aid. Rejecting the request would make observability a liability."""
        assert client.get("/health", headers={"X-Request-ID": ""}).status_code == 200

    def test_the_header_name_is_configurable(self) -> None:
        settings = Settings(environment="test", request_id_header="X-Trace-Id", json_logs=True)
        with TestClient(create_app(settings)) as client:
            response = client.get("/health", headers={"X-Trace-Id": "abc123"})

        assert response.headers["X-Trace-Id"] == "abc123"

    def test_an_error_response_carries_the_id_too(self, client: TestClient) -> None:
        """Errors are precisely when a correlation ID is worth having."""
        assert client.get("/no-such-route").headers["X-Request-ID"]


class TestLogCorrelation:
    def test_the_handler_sees_the_same_id_the_client_receives(
        self, logging_client: TestClient
    ) -> None:
        response = logging_client.get("/logs", headers={"X-Request-ID": "correlate-me"})

        assert response.json()["seen_by_handler"] == "correlate-me"
        assert response.headers["X-Request-ID"] == "correlate-me"

    def test_the_id_appears_in_the_emitted_log_line(
        self, settings: Settings, log_stream: io.StringIO
    ) -> None:
        """Bound into a context variable, so a log call deep in a service picks it up without
        anything having to thread it through the call chain.

        Asserted against the real rendered output rather than a mocked logger: the thing that can
        break is the processor chain, and a mock would not exercise it.
        """
        app = create_app(settings)

        @app.get("/emit")
        async def emit() -> dict[str, bool]:
            structlog.get_logger("test").warning("handler_ran")
            return {"ok": True}

        configure_logging(settings, stream=log_stream)
        with TestClient(app) as client:
            client.get("/emit", headers={"X-Request-ID": "find-this-line"})

        lines = [json.loads(line) for line in log_stream.getvalue().splitlines() if line]
        handler_lines = [line for line in lines if line.get("event") == "handler_ran"]

        assert handler_lines, "the handler's log line was not emitted"
        assert handler_lines[0]["request_id"] == "find-this-line"

    def test_an_unhandled_exception_logs_with_the_request_id(
        self, settings: Settings, log_stream: io.StringIO
    ) -> None:
        """The failing request is exactly the one whose log line must be findable, and it is the
        line most easily left uncorrelated — Starlette's own error middleware sits outside the
        scope where the ID exists.
        """
        app = create_app(settings)

        @app.get("/explode")
        async def explode() -> None:
            raise RuntimeError("boom")

        configure_logging(settings, stream=log_stream)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/explode", headers={"X-Request-ID": "failed-request"})

        assert response.status_code == 500
        lines = [json.loads(line) for line in log_stream.getvalue().splitlines() if line]
        failures = [line for line in lines if line.get("event") == "unhandled_exception"]

        assert failures, "the failure was not logged"
        assert failures[0]["request_id"] == "failed-request"

    def test_no_id_is_bound_outside_a_request(self) -> None:
        """Better than inventing one that correlates with nothing."""
        structlog.contextvars.clear_contextvars()

        assert current_request_id() is None

    def test_the_binding_does_not_leak_into_the_next_request(
        self, logging_client: TestClient
    ) -> None:
        """Context variables are per-task and tasks are reused; without an explicit clear, one
        request's ID can appear on the next request's log lines."""
        logging_client.get("/logs", headers={"X-Request-ID": "first"})
        second = logging_client.get("/logs", headers={"X-Request-ID": "second"})

        assert second.json()["seen_by_handler"] == "second"
