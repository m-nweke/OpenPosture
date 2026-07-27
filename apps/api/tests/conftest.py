"""Shared fixtures.

Every test builds its own app from explicit settings. Nothing here reads or mutates the process
environment: a test that sets `os.environ` leaks into every test that runs after it, and the
ordering dependency that creates is only ever discovered on the day CI shuffles the order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openposture_api.config import Settings
from openposture_api.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI


@pytest.fixture
def settings() -> Settings:
    """Settings for a test run: quiet, structured, and explicitly not production.

    `pose_backend="fake"` rather than disabling the backend entirely. The suite then exercises
    the real lifespan — construct, warm, register the probe, close — and still touches no model
    file, which is what keeps required CI free of a download.
    """
    return Settings(
        environment="test",
        log_level="warning",
        json_logs=True,
        pose_backend="fake",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """A client that lets server exceptions reach the error handler.

    `raise_server_exceptions=False` is the non-obvious half. TestClient's default re-raises an
    unhandled exception into the test, which is convenient for debugging and useless for
    asserting what the *client* sees — and what the client sees is the entire point of the
    RFC 9457 handler.
    """
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
