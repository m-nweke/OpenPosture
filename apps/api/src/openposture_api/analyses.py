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

import base64
import json
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Final

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from openposture_api.auth import get_current_user_id
from openposture_api.db import get_session
from openposture_api.errors import PROBLEM_TYPE_BASE, problem_response
from openposture_api.images import (
    MAX_IMAGE_BYTES,
    ImageTooLargeError,
    InvalidImageError,
    UnsupportedImageTypeError,
    decode_upload,
)
from openposture_api.pose import get_pose_backend
from openposture_api.repos import AnalysisRepository, FindingRecord, KeypointRecord, MetricRecord
from openposture_api.schemas import (
    AnalysisDetail,
    AnalysisListItem,
    AnalysisPage,
    AnalysisResponse,
    DetectedLandmark,
    ImageSize,
    PostureReportModel,
    StoredFinding,
    StoredMetric,
)
from openposture_api.storage import StorageBackend, StorageError, get_storage
from pose_backends.base import PoseBackend
from pose_backends.errors import PoseBackendError
from posture_core import build_report
from posture_core.report import SCHEMA_VERSION as _SCHEMA_VERSION
from posture_core.thresholds import DEFAULT_THRESHOLDS as _DEFAULT_THRESHOLDS

if TYPE_CHECKING:
    from fastapi import FastAPI
    from slowapi import Limiter
    from starlette.responses import JSONResponse

    from openposture_api.config import Settings
    from openposture_api.db.models import Analysis
    from posture_core import PoseFrame, PostureReport

__all__ = ["API_PREFIX", "build_analyses_router", "register_analysis_error_handlers"]

API_PREFIX: Final = "/api/v1"

_LOGGER = structlog.get_logger(__name__)

_UNAUTHORIZED: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid access token."}
}
"""Declared on every route in this module, because every one of them requires a token.

Written once and spread into each `responses` map rather than repeated: OP-45 generates the
frontend's types from this schema, and a 401 that is reachable but undeclared is a response the
client is not typed to handle (the same argument as the 502 below).
"""


def build_analyses_router(limiter: Limiter, settings: Settings) -> APIRouter:
    """The analyses routes.

    A builder rather than a module-level router for the same reason `create_app` is a factory:
    two apps in one test session must not share mutable route state. `limiter` and `settings`
    arrive as arguments for the same reason `auth.build_auth_router` takes them: the upload
    route's rate limit (OP-59, `Settings.analyses_rate_limit`) has to be a concrete string at
    `@limiter.limit(...)` decoration time, before any request — and so before `settings` could
    be read off `request.app.state` the way the route body reads it for everything else.
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
            **_UNAUTHORIZED,
            status.HTTP_400_BAD_REQUEST: {"description": "The file could not be decoded."},
            status.HTTP_413_CONTENT_TOO_LARGE: {"description": "Over the size limit."},
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {"description": "Format not supported."},
            # 502 is returned by the backend- and storage-failure handlers registered below.
            # Listed here because OP-45 generates the frontend's types from this schema, so
            # an unlisted status is a response the client is not typed to handle.
            status.HTTP_502_BAD_GATEWAY: {"description": "Inference or storage failed downstream."},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Inference is unavailable."},
            status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many uploads."},
        },
    )
    # A resource control, not a credential one (OP-59, `Settings.analyses_rate_limit`): pose
    # inference is the most expensive thing this service does, so this caps how much of the
    # worker pool one client can occupy rather than guarding against guessing anything.
    @limiter.limit(settings.analyses_rate_limit)
    async def create_analysis(
        request: Request,
        user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
        backend: Annotated[PoseBackend, Depends(get_pose_backend)],
        storage: Annotated[StorageBackend, Depends(get_storage)],
        session: Annotated[AsyncSession, Depends(get_session)],
        image: Annotated[UploadFile, File(description="The photograph to analyse.")],
    ) -> AnalysisResponse:
        raw = await _read_within_limit(image)
        decoded = decode_upload(raw)

        # Blocking, CPU-bound, and on the event loop — deliberately, for now. `detect` takes
        # ~25 ms with the pinned model, which is short enough that moving it to a thread costs
        # more in context switching than it saves. The moment that stops being true (a heavier
        # model, or real concurrency) this becomes `run_in_threadpool`, which is one line and one
        # of the four reasons ADR-0001 chose FastAPI.
        _t = time.perf_counter()
        frame = backend.detect(decoded.array)
        measured_ms = (time.perf_counter() - _t) * 1000.0

        stored = storage.put(decoded.data, content_type=decoded.content_type)

        repo = AnalysisRepository(session)

        # The object is already written, and the row that will point at it is not. If the DB work
        # below fails, that object becomes garbage no code path can ever reach — its key exists
        # only in the row that was rolled back. Storage generates keys internally and never
        # reveals them anywhere else, so nothing can reconcile it later; an orphan here is an
        # orphan permanently.
        #
        # Hence the compensating delete. This is not a transaction — the object store has no part
        # in the database's — it is the cheap approximation: on failure, undo the side effect we
        # can still name. Reordering to write the row first is not available, because the key is
        # generated by `put`, which is the property that makes caller-supplied paths
        # unrepresentable (FINDINGS §5.1).
        try:
            if frame is None:
                # An ordinary outcome — the user photographed their desk. 201, because the
                # request succeeded and "nobody in this image" is the finding.
                analysis = await repo.create(
                    user_id=user_id,
                    object_key=stored.key,
                    pose_detected=False,
                    image_width=decoded.width,
                    image_height=decoded.height,
                    overall_score=None,
                    assessed=0,
                    total=0,
                    inference_ms=measured_ms,
                    pose_backend=backend.name,
                    rules_version=_DEFAULT_THRESHOLDS.version,
                    schema_version=_SCHEMA_VERSION,
                )
                await session.commit()

                _LOGGER.info("analysis_no_pose", backend=backend.name, object_key=stored.key)
                return AnalysisResponse(
                    id=analysis.id,
                    object_key=stored.key,
                    pose_detected=False,
                    report=None,
                    landmarks=[],
                    image=ImageSize(width=decoded.width, height=decoded.height),
                )

            report = build_report(frame)

            analysis = await repo.create(
                user_id=user_id,
                object_key=stored.key,
                pose_detected=True,
                image_width=decoded.width,
                image_height=decoded.height,
                overall_score=report.overall_score,
                assessed=report.quality.assessed,
                total=report.quality.total,
                # `measured_ms` in both branches, never `report.inference_ms`. They are different
                # measurements: `report.inference_ms` is the backend's own timing of its detector
                # call, while this is wall-clock around `backend.detect` including the backend's
                # own overhead. The no-pose branch has no report and so can only supply the
                # latter, and a column holding whichever one happened to be available is a column
                # whose rows cannot be compared to each other. The backend-reported figure is
                # still carried through to the API response inside `report`.
                inference_ms=measured_ms,
                pose_backend=backend.name,
                rules_version=report.rules_version,
                schema_version=report.schema_version,
                keypoints=_keypoint_records(frame, report),
                metrics=_metric_records(report),
                findings=_finding_records(report),
            )
            await session.commit()
        except Exception:
            await session.rollback()
            # Suppressed: the DB failure is the one worth propagating, and a cleanup that raises
            # would replace it with an error about the cleanup. Logged rather than silent,
            # because the object really is orphaned at that point and someone will want to know.
            try:
                storage.delete(stored.key)
            except StorageError:
                _LOGGER.warning("orphaned_object", object_key=stored.key, exc_info=True)
            raise

        _LOGGER.info(
            "analysis_complete",
            backend=backend.name,
            object_key=stored.key,
            analysis_id=str(analysis.id),
            assessed=report.quality.assessed,
            total=report.quality.total,
            findings=len(report.findings),
            score=report.overall_score,
        )

        return AnalysisResponse(
            id=analysis.id,
            object_key=stored.key,
            pose_detected=True,
            landmarks=_landmarks_for(frame, report),
            # Validated from `to_dict()` rather than rebuilt from the dataclass. `to_dict` stays
            # the engine's only serialiser — shared with the CLI and the golden corpus, and
            # depended on by the cross-language parity check in Epic G — while the model gives
            # the OpenAPI document a real shape.
            #
            # `strict=True` is what makes "they cannot disagree" true rather than aspirational.
            # Pydantic's default coerces, so a field that became a string containing a number
            # would validate happily and the schema would quietly stop describing the response.
            # Together with `extra="forbid"` on the models, a rename, an added key or a changed
            # type all raise here — on the first request, in the branch that produced them.
            report=PostureReportModel.model_validate(report.to_dict(), strict=True),
            image=ImageSize(width=decoded.width, height=decoded.height),
        )

    @router.get(
        "/analyses",
        status_code=status.HTTP_200_OK,
        response_model=AnalysisPage,
        summary="List analyses newest first",
        description=(
            "Returns a cursor-paginated list of the authenticated user's analyses. Pass the "
            "`next_cursor` from a previous response as `cursor` to advance. "
            "`next_cursor` is `null` on the last page."
        ),
        responses={**_UNAUTHORIZED},
    )
    async def list_analyses(
        user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
        session: Annotated[AsyncSession, Depends(get_session)],
        limit: int = Query(default=20, ge=1, le=100, description="Page size."),
        cursor: str | None = Query(default=None, description="Opaque cursor from `next_cursor`."),
    ) -> AnalysisPage:
        cursor_created_at: datetime | None = None
        cursor_id: uuid.UUID | None = None
        if cursor is not None:
            cursor_created_at, cursor_id = _decode_cursor(cursor)

        repo = AnalysisRepository(session)
        # Fetch one extra to know whether another page follows, without a COUNT query.
        rows = await repo.list_page(
            user_id,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]

        next_cursor = _encode_cursor(rows[-1].created_at, rows[-1].id) if has_more else None

        return AnalysisPage(
            items=[_to_list_item(a) for a in rows],
            next_cursor=next_cursor,
        )

    @router.get(
        "/analyses/{analysis_id}",
        status_code=status.HTTP_200_OK,
        response_model=AnalysisDetail,
        summary="Fetch one analysis",
        description=(
            "Returns the full analysis record including keypoints, metrics, and findings. "
            "Returns 404 whether the analysis does not exist or belongs to a different user — "
            "existence is not leaked."
        ),
        responses={
            **_UNAUTHORIZED,
            status.HTTP_404_NOT_FOUND: {"description": "Analysis not found."},
        },
    )
    async def get_analysis(
        analysis_id: uuid.UUID,
        user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> AnalysisDetail:
        repo = AnalysisRepository(session)
        analysis = await repo.get(user_id, analysis_id)
        if analysis is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return _to_detail(analysis)

    @router.delete(
        "/analyses/{analysis_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Delete one analysis",
        description=(
            "Hard-deletes the analysis and all its child rows. Returns 404 whether the "
            "analysis does not exist or belongs to a different user — the same 404-not-403 "
            "rule as the read endpoint."
        ),
        responses={
            **_UNAUTHORIZED,
            status.HTTP_404_NOT_FOUND: {"description": "Analysis not found."},
        },
    )
    async def delete_analysis(
        analysis_id: uuid.UUID,
        user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
        storage: Annotated[StorageBackend, Depends(get_storage)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> None:
        """Hard delete, rows before object.

        **Hard, not soft.** This application stores photographs of the user's body. When someone
        deletes one, the defensible reading of that request is "destroy it", not "hide it behind
        a flag" — a tombstoned row and a retained image is exactly the outcome the user believed
        they had prevented. If auditability ever argues for a tombstone, the image must still be
        hard-deleted.

        **Rows first, then the object** — the reverse of E5's write order, and for the same
        reason. The two stores cannot be committed together, so one of them fails first, and the
        choice is which residue to prefer. Deleting the object first and failing on the rows
        leaves a row pointing at nothing, which every read path then has to defend against.
        Deleting the rows first and failing on the object leaves an unreferenced object: invisible
        to the application, and reclaimable later precisely because no row claims it.
        """
        repo = AnalysisRepository(session)
        object_key = await repo.delete(user_id, analysis_id)
        if object_key is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        await session.commit()

        # After the commit, deliberately. Deleting inside the transaction would destroy the object
        # for a transaction that could still roll back.
        try:
            storage.delete(object_key)
        except StorageError:
            # Not fatal: the rows are gone, so the delete the client asked for has happened. The
            # object is now unreferenced rather than dangling — logged so it can be reclaimed.
            _LOGGER.warning("orphaned_object", object_key=object_key, exc_info=True)

    return router


def _encode_cursor(created_at: datetime, analysis_id: uuid.UUID) -> str:
    """Encode a (created_at, id) pair as an opaque URL-safe cursor."""
    payload = json.dumps({"t": created_at.isoformat(), "id": str(analysis_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Decode a cursor produced by `_encode_cursor`.

    Raises `HTTPException(400)` on any malformed input — the cursor is client-supplied and
    must be treated as untrusted.
    """
    # Narrow rather than bare `Exception`: every way a malformed cursor can fail lands in one of
    # these three. `binascii.Error` from base64 and `JSONDecodeError` are both ValueError
    # subclasses, as are `fromisoformat` and `UUID`; a payload that decodes to a non-dict raises
    # TypeError, and one missing a key raises KeyError. Catching `Exception` would also swallow a
    # genuine bug in here and report it to the client as their bad input.
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(payload["t"]), uuid.UUID(payload["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor."
        ) from exc


def _to_list_item(analysis: Analysis) -> AnalysisListItem:
    return AnalysisListItem(
        id=analysis.id,
        created_at=analysis.created_at,
        object_key=analysis.object_key,
        pose_detected=analysis.pose_detected,
        overall_score=analysis.overall_score,
    )


def _to_detail(analysis: Analysis) -> AnalysisDetail:
    return AnalysisDetail(
        id=analysis.id,
        created_at=analysis.created_at,
        object_key=analysis.object_key,
        pose_detected=analysis.pose_detected,
        image=ImageSize(width=analysis.image_width, height=analysis.image_height),
        overall_score=analysis.overall_score,
        assessed=analysis.assessed,
        total=analysis.total,
        inference_ms=analysis.inference_ms,
        pose_backend=analysis.pose_backend,
        rules_version=analysis.rules_version,
        schema_version=analysis.schema_version,
        keypoints=[
            DetectedLandmark(
                name=kp.name,
                x=kp.x,
                y=kp.y,
                status=kp.status,  # type: ignore[arg-type]
                visibility=kp.visibility,
                presence=kp.presence,
            )
            for kp in analysis.keypoints
        ],
        metrics=[
            StoredMetric(
                code=m.code,
                value=m.value,
                unit=m.unit,
                status=m.status,  # type: ignore[arg-type]
                detail=m.detail,
                confidence=m.confidence,
            )
            for m in analysis.metrics
        ],
        findings=[
            StoredFinding(
                code=f.code,
                severity=f.severity,  # type: ignore[arg-type]
                message=f.message,
                metric=f.metric,
                value=f.value,
                confidence=f.confidence,
            )
            for f in analysis.findings
        ],
    )


def _keypoint_records(frame: PoseFrame, report: PostureReport) -> list[KeypointRecord]:
    """Pair each detected landmark with its resolver status, ready for the repository."""
    statuses = report.quality.keypoints
    return [
        KeypointRecord(
            name=str(name),
            x=landmark.x,
            y=landmark.y,
            status=str(statuses[name]),
            visibility=landmark.visibility,
            presence=landmark.presence,
        )
        for name, landmark in frame.landmarks.items()
    ]


def _metric_records(report: PostureReport) -> list[MetricRecord]:
    return [
        MetricRecord(
            code=name,
            value=metric.value,
            unit=metric.unit,
            status=metric.status.value,
            detail=metric.detail,
            confidence=metric.confidence,
        )
        for name, metric in report.metrics.items()
    ]


def _finding_records(report: PostureReport) -> list[FindingRecord]:
    return [
        FindingRecord(
            code=finding.code,
            severity=finding.severity.value,
            message=finding.message,
            metric=finding.metric,
            # `finding.value` is typed `float | None` in posture_core.rules but in practice is
            # always set when a finding fires — a rule only fires when a metric is OK, and an OK
            # metric has a value. The `or 0.0` guard is defensive; it should never trigger.
            value=finding.value or 0.0,
            confidence=finding.confidence,
        )
        for finding in report.findings
    ]


def _landmarks_for(frame: PoseFrame, report: PostureReport) -> list[DetectedLandmark]:
    """Pair each measured point with the status the resolver gave it.

    The coordinates come from the frame and the status from the report, because they answer
    different questions: the frame says *where* the model thinks a point is, and the resolver says
    whether that belief is good enough to act on. A client needs both — drawing a
    `low_confidence` elbow identically to a measured one presents a guess as a measurement, which
    is the failure this project exists to remove.
    """
    # Indexed, not `.get(...)` with a default. `KeypointResolver` classifies *every* member of
    # `KeypointName`, and `frame.landmarks` is keyed by that same enum, so a miss here is not a
    # missing status — it is a broken invariant, and a default would hide it behind a plausible
    # value. `not_detected` in particular would be a lie: the resolver only assigns it when the
    # frame has no landmark at all, which cannot be true of one we are iterating over.
    statuses = report.quality.keypoints
    return [
        DetectedLandmark(
            name=str(name),
            x=landmark.x,
            y=landmark.y,
            status=str(statuses[name]),  # type: ignore[arg-type]
            visibility=landmark.visibility,
            presence=landmark.presence,
        )
        for name, landmark in frame.landmarks.items()
    ]


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
