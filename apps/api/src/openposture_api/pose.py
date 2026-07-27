"""One pose backend per process, loaded at startup and reached through a dependency.

`RUNDOWN.md`'s Open Items flagged this on the original project, in the team's own words: a cold
load is slow, and per-request loading would be unusable. They shipped the version that reloads.
The reason they could not fix it is structural — the model was a module-global assigned under a
``if __name__ == '__main__'`` guard (FINDINGS §3.3), so it did not exist when the Flask app
imported the module, and there was no startup hook to move it to.

The fix has three parts, and each one is doing work:

**Loaded in `lifespan`.** Once per process, before the first request, with a defined shutdown.
Not at import, so nothing is constructed by the act of importing a module.

**Held in application state, not a module global.** Two apps in one test session then have two
independent backends, and neither can see the other's.

**Reached through a dependency.** `app.dependency_overrides[get_pose_backend]` is how an endpoint
test runs its real code path against `FakePoseBackend` — the answer to "how do I test an endpoint
that runs a model without running the model", and the reason FastAPI was chosen (ADR-0001).

A failed load is not a crash. A container that exits on a missing model file restarts forever and
tells an operator nothing; one that stays up and reports *why* it is unready is diagnosable with
`curl`. So the error is captured, readiness reports it by name, and requests needing inference
get a 503 rather than a 500.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import structlog
from fastapi import Request
from starlette import status
from starlette.responses import JSONResponse

from openposture_api.errors import PROBLEM_TYPE_BASE, problem_response
from openposture_api.health import ReadinessCheck
from pose_backends.errors import PoseBackendError
from pose_backends.registry import create_backend

if TYPE_CHECKING:
    from openposture_api.config import Settings
    from pose_backends.base import PoseBackend

__all__ = [
    "PROBE_NAME",
    "PoseBackendState",
    "PoseBackendStatus",
    "PoseBackendUnavailableError",
    "get_pose_backend",
    "load_pose_backend",
]

PROBE_NAME: Final = "pose_backend"

_LOGGER = structlog.get_logger(__name__)


class PoseBackendStatus(StrEnum):
    """Where the one backend is in its lifecycle."""

    PENDING = "pending"
    """Constructed but not yet warmed, or not yet attempted. Never ready."""

    READY = "ready"
    """Loaded and warmed. The only state that serves traffic."""

    FAILED = "failed"
    """The load or the warmup raised. `error` says which and why."""


class PoseBackendUnavailableError(Exception):
    """Raised by the dependency when a route needs inference and there is none.

    Deliberately not an `HTTPException`: routes should not have to know that the failure becomes a
    503, and the problem document is built in one place rather than at each call site.
    """

    def __init__(self, state: PoseBackendState) -> None:
        super().__init__(state.detail or "the pose backend is unavailable")
        self.state = state


@dataclass
class PoseBackendState:
    """The backend and how it got that way, held on `app.state`.

    A dataclass rather than a bare backend reference because "not loaded" and "failed to load,
    for this reason" are states the readiness probe has to be able to describe. A `None` backend
    alone cannot distinguish *still starting* from *broken*, and those want different operator
    responses.
    """

    status: PoseBackendStatus = PoseBackendStatus.PENDING
    backend: PoseBackend | None = None
    error: PoseBackendError | None = field(default=None, repr=False)
    detail: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.status is PoseBackendStatus.READY and self.backend is not None

    async def probe(self) -> ReadinessCheck:
        """This subsystem's answer for `/health/ready`.

        The failure detail is the exception's own message, which names the path it looked for or
        the library it could not import. "not ready" without a cause makes an operator read logs
        they may not have; with one, `curl /health/ready` is the whole diagnosis.
        """
        return ReadinessCheck(name=PROBE_NAME, ready=self.is_ready, detail=self.detail)


def load_pose_backend(settings: Settings) -> PoseBackendState:
    """Construct the configured backend and warm it, returning the outcome either way.

    Blocking, and called from `lifespan` where blocking is correct: the ASGI server does not
    accept connections until startup finishes, so an instance that is still loading is not
    serving anything. A readiness probe against it gets a refused connection rather than a 503 —
    different symptom, same conclusion for an orchestrator, and it costs no background-task
    machinery to get there.

    Never raises. Every failure the backends define is a `PoseBackendError`, and the point of
    catching them is that the process stays up and stays diagnosable.
    """
    state = PoseBackendState()

    try:
        backend = create_backend(
            settings.pose_backend,
            model_path=settings.model_path,
            preset=settings.pose_backend_preset,
        )
        # Before readiness flips, so no user request is the one that pays for initialisation.
        # `MediaPipeBackend.warmup` documents that on the pinned version this currently saves
        # nothing measurable — the guarantee is what matters, not today's timing.
        backend.warmup()
    except PoseBackendError as exc:
        state.status = PoseBackendStatus.FAILED
        state.error = exc
        state.detail = f"{type(exc).__name__}: {exc}"
        # `.exception` rather than `.error`: the readiness endpoint carries the message, so what
        # the log adds is the traceback — which is the part that matters when the failure is a
        # `ModelLoadError` wrapping something the native runtime said.
        _LOGGER.exception(
            "pose_backend_unavailable",
            backend=settings.pose_backend,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return state

    state.status = PoseBackendStatus.READY
    state.backend = backend
    _LOGGER.info("pose_backend_ready", backend=backend.name)
    return state


def close_pose_backend(state: PoseBackendState) -> None:
    """Release native resources on shutdown, if the backend holds any.

    `close` is not part of the `PoseBackend` Protocol — a five-line test double should not have to
    implement it — so it is called only when present. Failures are logged and swallowed: a
    process that is stopping anyway should not turn an orderly shutdown into a stack trace.
    """
    closer = getattr(state.backend, "close", None)
    if not callable(closer):
        return
    try:
        closer()
    except Exception as exc:  # noqa: BLE001
        # BLE001 is enabled because swallowed exceptions were the legacy engine's defining
        # failure mode. This one is logged in full and reached only while shutting down, where
        # re-raising would replace a clean stop with a traceback and change nothing else.
        _LOGGER.warning("pose_backend_close_failed", error_type=type(exc).__name__, error=str(exc))


def get_pose_backend(request: Request) -> PoseBackend:
    """FastAPI dependency: the process's one backend.

    Override this in tests — `app.dependency_overrides[get_pose_backend] = lambda: fake` — and an
    endpoint test exercises its real code path with no model on disk.

    Consuming it looks like::

        async def route(backend: Annotated[PoseBackend, Depends(get_pose_backend)]) -> ...:

    and `PoseBackend` must be imported at *runtime* in that module, not under `TYPE_CHECKING`.
    Every module here uses `from __future__ import annotations`, so FastAPI resolves the
    annotation from module globals when it builds the route — and if the name is not there, the
    parameter is silently treated as a **query parameter** rather than a dependency. The symptom
    is a 422 complaining about a missing `backend` query string, which points nowhere near the
    cause.

    :raises PoseBackendUnavailableError: if the backend never loaded. Becomes a 503, because the
        condition is the server's and it may be temporary; a 500 would suggest a bug in handling
        the request, which it is not.
    """
    state: PoseBackendState | None = getattr(request.app.state, "pose_backend_state", None)
    if state is None or not state.is_ready:
        raise PoseBackendUnavailableError(state or PoseBackendState())
    # `is_ready` already established this is not None; the assert is for the type checker.
    assert state.backend is not None
    return state.backend


async def handle_pose_backend_unavailable(request: Request, exc: Exception) -> JSONResponse:
    """Render :class:`PoseBackendUnavailableError` as a 503 problem document.

    `Retry-After` is set because the honest answer is "try again shortly": the usual causes are a
    model still downloading or a volume not yet mounted, both of which resolve without a client
    changing anything about its request.
    """
    assert isinstance(exc, PoseBackendUnavailableError)
    return problem_response(
        request,
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Pose analysis is unavailable because the inference backend did not load.",
        problem_type=f"{PROBLEM_TYPE_BASE}/pose-backend-unavailable",
        headers={"Retry-After": "30"},
        backend_status=exc.state.status.value,
        backend_detail=exc.state.detail,
    )
