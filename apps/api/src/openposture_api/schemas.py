"""The response contract, as Pydantic models.

These exist so the OpenAPI document describes the analysis response in full, which is what lets
OP-45 generate the frontend's TypeScript from it. A route annotated `-> dict[str, Any]` produces a
schema saying "an object", and types generated from that are worth nothing: the frontend would
still be hand-writing its own shapes, and a renamed field in Python would still surface months
later as an `undefined` in a browser.

**These are not a second serialiser.** `PostureReport.to_dict()` remains the only code that turns
a report into JSON — shared with the CLI and the golden corpus, and depended on by the
cross-language parity check in Epic G. These models *validate* that dictionary rather than
rebuilding it from the dataclass, so there is exactly one place where a field name is chosen and
these models fail loudly if it changes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AnalysisResponse",
    "ContractModel",
    "DetectedLandmark",
    "Finding",
    "Gap",
    "ImageSize",
    "Metric",
    "PostureReportModel",
    "Quality",
]


class ContractModel(BaseModel):
    """Base for every model here, carrying the strictness the contract depends on.

    ``extra="forbid"`` because the default is to *ignore* unknown fields. A key added to
    ``PostureReport.to_dict()`` would then be silently dropped on its way out — a change to what
    the API returns, invisible in the schema, and a second serialiser in practice. Forbidding it
    turns that into an exception on the first request instead.

    The complementary half is ``strict=True`` at the `model_validate` call site: without it
    Pydantic coerces, so a field that became a string containing a number would validate happily
    and the declared schema would quietly stop describing the response.
    """

    model_config = ConfigDict(extra="forbid")


MetricStatus = Literal["ok", "insufficient_keypoints", "low_confidence"]
KeypointStatus = Literal["ok", "low_confidence", "not_detected", "out_of_frame"]
Severity = Literal["major", "minor", "info"]


class ImageSize(ContractModel):
    width: int
    height: int


class Metric(ContractModel):
    """One measurement, or an honest absence of one."""

    value: float | None = Field(
        description="`null` whenever `status` is not `ok`. Never a guess — that is the point."
    )
    unit: str
    status: MetricStatus
    detail: str = Field(description="Phrased for display by the engine, not by the client.")
    confidence: float | None


class Finding(ContractModel):
    """Something the engine noticed, already worded for a person."""

    code: str = Field(description="Stable identifier. Branch on this, not on `message`.")
    severity: Severity
    message: str
    metric: str
    value: float
    confidence: float


class Gap(ContractModel):
    """A metric that could not be assessed, and what stopped it."""

    metric: str
    status: MetricStatus
    detail: str
    keypoints: dict[str, KeypointStatus]


class Quality(ContractModel):
    """How much of the body the engine could actually see."""

    assessed: int
    total: int
    coverage: float
    gaps: list[Gap]
    keypoints: dict[str, KeypointStatus]


class PostureReportModel(ContractModel):
    """One assessment of one photograph. Mirrors `PostureReport.to_dict()` exactly."""

    schema_version: str
    rules_version: str
    backend: str
    inference_ms: float
    image: ImageSize
    overall_score: float | None = Field(
        description=(
            "0 to 100, or `null` when nothing could be measured. Null rather than 0 or 100: both "
            "would be confident claims about a photograph the engine could not assess."
        )
    )
    findings: list[Finding]
    metrics: dict[str, Metric]
    quality: Quality


class DetectedLandmark(ContractModel):
    """One measured point, in the form a client needs to draw it.

    **Normalised to the image, not in pixels.** The frontend renders the photo at whatever size
    the viewport allows, so a pixel coordinate would be wrong the moment the layout changed. `x`
    and `y` are fractions of width and height, which stay correct under any scale — the same
    reasoning as the world-space metrics, applied to presentation.

    World coordinates are deliberately absent: they are metres from the hip and mean nothing on
    a 2D canvas.
    """

    name: str
    x: float = Field(description="Fraction of image width. May fall outside [0, 1].")
    y: float = Field(description="Fraction of image height, origin top-left.")
    status: KeypointStatus = Field(
        description=(
            "How usable this point is. A client must render anything other than `ok` distinctly "
            "or not at all — drawing a guessed position as though it were measured is the "
            "confident-wrong-answer failure this project exists to remove."
        )
    )
    visibility: float = Field(description="[0, 1] — confidence the point is not occluded.")
    presence: float = Field(description="[0, 1] — confidence the point is in frame at all.")


class AnalysisResponse(ContractModel):
    """What `POST /api/v1/analyses` returns on success."""

    object_key: str = Field(
        description=(
            "Where the uploaded image was stored. A key, never a URL — a URL embeds a host and "
            "an expiry, and persisting one means a hostname change invalidates it."
        )
    )
    pose_detected: bool
    report: PostureReportModel | None = Field(
        description="`null` exactly when `pose_detected` is false."
    )
    # Required rather than defaulted. A field with a default is *optional* in the OpenAPI
    # document, so the generated TypeScript types it `| undefined` and every consumer has to
    # narrow a case the server never produces. Always sending it — empty when there is no pose —
    # is both the simpler contract and the more honest one.
    landmarks: list[DetectedLandmark] = Field(
        description=(
            "Every landmark the backend returned, for drawing a skeleton over the photo. Empty "
            "when no pose was detected. The overlay is drawn client-side from these — the server "
            "never writes an annotated image, which would cost a round trip, storage for a "
            "derived artifact, and a cache-invalidation question every time the rules change."
        ),
    )
    image: ImageSize
