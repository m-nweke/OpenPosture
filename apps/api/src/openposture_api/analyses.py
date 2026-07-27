"""`POST /api/v1/analyses` — the endpoint that makes the application real.

Until this exists the project is scaffolding: the frontend fakes a five-second wait and renders
two hardcoded strings, and `API/app.py` never imports a model at all. This route is the missing
wire. A photograph goes in, and a report derived from that specific body comes out.

The flow is deliberately linear: read → validate and decode → detect → build report → persist the
original → 201.

**An abstaining report is a success.** A report that is entirely gaps returns 201, not an error.
The request was well-formed and the service did its job; the answer is "I could not see enough of
you to say", which is information the user needs. Turning that into a 4xx would push the frontend
into an error state and lose the gap detail that C3's status model exists to produce — which is
the original's central defect (`None` → "Straight back position") arriving by a different route.

Only genuine failures are non-2xx: an undecodable file, an oversized one, a format outside the
allowlist, or a backend that is not there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Final

import structlog
from fastapi import APIRouter, Depends, File, Request, UploadFile
from starlette import status

from openposture_api.errors import PROBLEM_TYPE_BASE, problem_response
from openposture_api.images import (
    MAX_IMAGE_BYTES,
    ImageTooLargeError,
    InvalidImageError,
    UnsupportedImageTypeError,
    decode_upload,
)
from openposture_api.pose import get_pose_backend
from openposture_api.schemas import AnalysisResponse, ImageSize, PostureReportModel
from openposture_api.storage import StorageBackend, StorageError, get_storage
from pose_backends.base import PoseBackend
from pose_backends.errors import PoseBackendError
from posture_core import build_report

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.responses import JSONResponse

__all__ = ["API_PREFIX", "build_analyses_router", "register_analysis_error_handlers"]

API_PREFIX: Final = "/api/v1"

_LOGGER = structlog.get_logger(__name__)


def build_analyses_router() -> APIRouter:
    """The analyses routes.

    A builder rather than a module-level router for the same reason `create_app` is a factory:
    two apps in one test session must not share mutable route state.
    """
    router = APIRouter(prefix=API_PREFIX, tags=["analyses"])

    @router.post(
        "/analyses",
        status_code=status.HTTP_201_CREATED,
        response_model=AnalysisResponse,
        summary="Analyse posture in a photograph",
        description=(
            "Upload a lateral photograph and receive a posture report. A report that could not "
            "assess some metrics still returns 201 with those metrics listed under "
            "`quality.gaps` — abstention is an answer, not an error."
        ),
        responses={
            status.HTTP_400_BAD_REQUEST: {"description": "The file could not be decoded."},
            status.HTTP_413_CONTENT_TOO_LARGE: {"description": "Over the size limit."},
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {"description": "Format not supported."},
            # 502 is returned by the backend- and storage-failure handlers registered below.
            # Listed here because OP-45 generates the frontend's types from this schema, so
            # an unlisted status is a response the client is not typed to handle.
            status.HTTP_502_BAD_GATEWAY: {"description": "Inference or storage failed downstream."},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Inference is unavailable."},
        },
    )
    async def create_analysis(
        request: Request,
        backend: Annotated[PoseBackend, Depends(get_pose_backend)],
        storage: Annotated[StorageBackend, Depends(get_storage)],
        image: Annotated[UploadFile, File(description="The photograph to analyse.")],
    ) -> AnalysisResponse:
        raw = await _read_within_limit(image)
        decoded = decode_upload(raw)

        # Blocking, CPU-bound, and on the event loop — deliberately, for now. `detect` takes
        # ~25 ms with the pinned model, which is short enough that moving it to a thread costs
        # more in context switching than it saves. The moment that stops being true (a heavier
        # model, or real concurrency) this becomes `run_in_threadpool`, which is one line and one
        # of the four reasons ADR-0001 chose FastAPI.
        frame = backend.detect(decoded.array)

        stored = storage.put(decoded.data, content_type=decoded.content_type)

        if frame is None:
            # An ordinary outcome — the user photographed their desk. 201, because the request
            # succeeded and "nobody in this image" is the finding.
            _LOGGER.info("analysis_no_pose", backend=backend.name, object_key=stored.key)
            return AnalysisResponse(
                object_key=stored.key,
                pose_detected=False,
                report=None,
                image=ImageSize(width=decoded.width, height=decoded.height),
            )

        report = build_report(frame)

        _LOGGER.info(
            "analysis_complete",
            backend=backend.name,
            object_key=stored.key,
            assessed=report.quality.assessed,
            total=report.quality.total,
            findings=len(report.findings),
            score=report.overall_score,
        )

        return AnalysisResponse(
            object_key=stored.key,
            pose_detected=True,
            # Validated from `to_dict()` rather than rebuilt from the dataclass. `to_dict` stays
            # the engine's only serialiser — shared with the CLI and the golden corpus, and
            # depended on by the cross-language parity check in Epic G — while the model gives
            # the OpenAPI document a real shape. If the two ever disagree this raises here,
            # loudly, rather than shipping a schema that lies about the response.
            report=PostureReportModel.model_validate(report.to_dict()),
            image=ImageSize(width=decoded.width, height=decoded.height),
        )

    return router


async def _read_within_limit(upload: UploadFile) -> bytes:
    """Read the upload, refusing anything over the limit *before* it is all in memory.

    Read in chunks and abandoned as soon as the running total crosses the limit, rather than
    `await upload.read()` followed by a length check. The difference matters: the naive version
    has already materialised the whole payload by the time it decides to reject it, so a 2 GB
    upload is a 2 GB allocation regardless of the limit being 10 MB.

    Starlette spools large uploads to disk, which bounds memory but not disk, and neither bounds
    the time spent reading. This bounds all three.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(64 * 1024):
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            raise ImageTooLargeError(
                f"upload exceeds the {MAX_IMAGE_BYTES} byte limit",
                limit=MAX_IMAGE_BYTES,
                actual=total,
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _handle_too_large(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ImageTooLargeError)
    return problem_response(
        request,
        status.HTTP_413_CONTENT_TOO_LARGE,
        str(exc),
        problem_type=f"{PROBLEM_TYPE_BASE}/image-too-large",
        limit=exc.limit,
        actual=exc.actual,
    )


async def _handle_unsupported_type(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, UnsupportedImageTypeError)
    return problem_response(
        request,
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        str(exc),
        problem_type=f"{PROBLEM_TYPE_BASE}/unsupported-image-type",
        detected_format=exc.detected,
    )


async def _handle_invalid_image(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, InvalidImageError)
    return problem_response(
        request,
        status.HTTP_400_BAD_REQUEST,
        str(exc),
        problem_type=f"{PROBLEM_TYPE_BASE}/invalid-image",
    )


async def _handle_backend_failure(request: Request, exc: Exception) -> JSONResponse:
    """Inference broke. Distinct from "no pose detected", which is a 201.

    502 rather than 500: the fault is in a component this service depends on, and the distinction
    is what tells an operator whether to look at the API or at the model.
    """
    assert isinstance(exc, PoseBackendError)
    _LOGGER.exception("analysis_backend_failed", error_type=type(exc).__name__)
    return problem_response(
        request,
        status.HTTP_502_BAD_GATEWAY,
        "The pose backend failed while analysing this image.",
        problem_type=f"{PROBLEM_TYPE_BASE}/pose-backend-failed",
    )


async def _handle_storage_failure(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StorageError)
    _LOGGER.exception("analysis_storage_failed", error_type=type(exc).__name__)
    return problem_response(
        request,
        status.HTTP_502_BAD_GATEWAY,
        "The image could not be stored.",
        problem_type=f"{PROBLEM_TYPE_BASE}/storage-failed",
    )


def register_analysis_error_handlers(app: FastAPI) -> None:
    """Map this module's exceptions onto problem documents.

    Registered on the app rather than caught in the route, so the route reads as the happy path
    and every failure produces the same envelope without a `try` around each step.
    """
    app.add_exception_handler(ImageTooLargeError, _handle_too_large)
    app.add_exception_handler(UnsupportedImageTypeError, _handle_unsupported_type)
    app.add_exception_handler(InvalidImageError, _handle_invalid_image)
    app.add_exception_handler(PoseBackendError, _handle_backend_failure)
    app.add_exception_handler(StorageError, _handle_storage_failure)
