"""Liveness and readiness, which are different questions.

**`/health` — liveness.** Is this process running and able to serve a response at all? It touches
nothing external and cannot fail for a reason outside the process. An orchestrator restarts a
container that fails this, so anything that can fail transiently must stay out of it.

**`/health/ready` — readiness.** Can this instance serve real traffic *right now*? An orchestrator
removes a failing instance from the load balancer without killing it, which is the correct
response to "still warming up" or "the database is briefly unreachable".

Conflating them is the standard mistake and it is expensive in exactly this application. OP-40
loads the pose model at startup and warms it up, which takes tens of seconds; a single endpoint
doing both jobs reports failure during that window, and the orchestrator kills the container for
being slow to start — repeatedly, since the restart takes just as long.

Right now the readiness check has nothing to check, and says so honestly rather than hardcoding
``true``: the ``checks`` map is empty and the registration seam is here, so OP-40 adds the pose
backend by appending to a list.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final, Literal, TypeAlias

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from starlette import status

__all__ = ["Health", "Readiness", "ReadinessCheck", "ReadinessProbe", "build_health_router"]


class Health(BaseModel):
    """Liveness. Deliberately has no field that could ever be false."""

    status: Literal["ok"] = "ok"
    version: str = Field(description="Version of the running service.")


class ReadinessCheck(BaseModel):
    """One subsystem's answer."""

    name: str
    ready: bool
    detail: str | None = Field(
        default=None,
        description="Why it is not ready. Omitted when it is.",
    )


ReadinessProbe: TypeAlias = Callable[[], Awaitable[ReadinessCheck]]
"""A named async check contributed by a subsystem. See :func:`build_health_router`.

Defined after :class:`ReadinessCheck` because it names it: an alias is evaluated when the module
loads, so a forward reference here would only fail later and further away.
"""


class Readiness(BaseModel):
    """Aggregate readiness: ready only when every registered check is."""

    status: Literal["ready", "not_ready"]
    version: str
    checks: list[ReadinessCheck] = Field(
        default_factory=list,
        description="One entry per registered subsystem, in registration order.",
    )


def build_health_router(
    *,
    version: str,
    probes: list[ReadinessProbe] | None = None,
) -> APIRouter:
    """Health routes bound to a specific set of readiness probes.

    Probes are injected rather than imported so that a test can build an app whose database is
    unreachable without needing an unreachable database. It is also what keeps this module free
    of any dependency on the subsystems it reports on — health checking the pose backend must not
    make `health.py` import `pose_backends`.
    """
    router = APIRouter(tags=["health"])
    registered: Final = probes if probes is not None else []

    @router.get(
        "/health",
        response_model=Health,
        summary="Liveness probe",
        # Documented explicitly: a 200 here means the process is up, nothing more. Callers that
        # want "can it work" must ask the other endpoint.
        description="Whether the process is alive. Checks nothing external and never fails "
        "for an external reason.",
    )
    async def liveness() -> Health:
        return Health(version=version)

    @router.get(
        "/health/ready",
        response_model=Readiness,
        summary="Readiness probe",
        description="Whether this instance can serve traffic. Returns 503 when any registered "
        "subsystem is not ready.",
        responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
    )
    async def readiness(response: Response) -> Readiness:
        checks = [await probe() for probe in registered]
        ready = all(check.ready for check in checks)
        if not ready:
            # 503 rather than a 200 carrying `status: "not_ready"`. Orchestrators and load
            # balancers route on the status code; a body they do not parse saying "not ready"
            # keeps traffic arriving at an instance that cannot serve it.
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Readiness(
            status="ready" if ready else "not_ready",
            version=version,
            checks=checks,
        )

    return router
