"""Property-based tests. **This file is the proof that the redesign fixed the original defect.**

The inherited engine compared raw pixel distances against literal thresholds (FINDINGS §2.6), so
identical posture photographed at twice the distance, or on a person of a different size, produced
a different verdict. Every example-based test in this package agrees the new engine does not do
that — but examples only ever show that the cases someone thought of happen to work.

Hypothesis generates the cases nobody thought of. For *any* posture, *any* body size in a 10x
range, and *any* position in the frame, every angular metric must agree to within 1e-6 degrees.
That is a statement about the whole input space, and it is the one that would fail catastrophically
against `posture_image.py`.

The second property here is quieter and just as important: **nothing raises**. For any pose with
any subset of its landmarks removed, `build_report` returns a report. Not a plausible one
necessarily — a report full of gaps is the correct answer to a photograph of an empty chair — but
a report, never a traceback and never a `None` the caller has to interpret.
"""

from __future__ import annotations

from typing import Any, Final

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from posture_core import KeypointName as K
from posture_core import Landmark, PoseFrame, build_report
from posture_core.metrics import (
    craniovertebral_angle_deg,
    elbow_flexion_deg,
    knee_flexion_deg,
    trunk_inclination_deg,
    view_confidence,
)
from posture_core.resolver import KeypointResolver
from posture_core.synthetic import Anthropometry, Facing, View, make_pose
from posture_core.thresholds import DEFAULT_THRESHOLDS as T

# Angular metrics only. `heel_contact_m` is a *length* in metres and is supposed to scale with the
# body — asserting it invariant would be asserting the wrong thing. `arms_crossed` is a ratio and
# gets its own check below.
ANGULAR = (
    trunk_inclination_deg,
    craniovertebral_angle_deg,
    elbow_flexion_deg,
    knee_flexion_deg,
)

# Physically plausible joint angles. Bounded rather than unbounded because a figure with a 400°
# knee is not a posture the engine will ever see, and testing it would only pin down the behaviour
# of arithmetic on nonsense.
poses = st.fixed_dictionaries(
    {
        "trunk_deg": st.floats(-60, 70),
        "neck_deg": st.floats(-30, 60),
        "thigh_deg": st.floats(0, 110),
        "shank_deg": st.floats(-20, 180),
        "upper_arm_deg": st.floats(-40, 100),
        "forearm_deg": st.floats(-20, 170),
        "forearm_cross_deg": st.floats(0, 80),
        "facing": st.sampled_from(list(Facing)),
        "view": st.sampled_from(list(View)),
    }
)

# 0.3x to 3.0x: a small child through to a very large adult, which is a wider range than the
# application will meet and therefore a stronger claim than it needs.
scales = st.floats(0.3, 3.0)

frame_sizes = st.tuples(st.integers(240, 4000), st.integers(240, 4000))

# Derived, not written down. `KeypointName` is the source of truth for how many landmarks exist,
# and a literal here would silently stop covering the new one the day a keypoint is added — the
# visibility strategy below zips against every name with `strict=True`, so it would fail loudly,
# but the drop strategy would just quietly stop being able to drop everything.
KEYPOINT_COUNT: Final = len(K)

SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def scaled_body(scale: float) -> Anthropometry:
    default = Anthropometry()
    return Anthropometry(
        **{
            field: getattr(default, field) * scale
            for field in (
                "torso",
                "neck",
                "shoulder_width",
                "hip_width",
                "head_width",
                "upper_arm",
                "forearm",
                "hand",
                "thigh",
                "shank",
                "foot",
                "heel_drop",
            )
        }
    )


def resolver_for(
    landmarks: dict[K, Landmark], width: int = 640, height: int = 480
) -> KeypointResolver:
    return KeypointResolver(
        PoseFrame(
            landmarks=landmarks,
            image_width=width,
            image_height=height,
            backend="synthetic",
            inference_ms=0.0,
        ),
        T,
    )


def angular_values(landmarks: dict[K, Landmark], **frame_kwargs: int) -> dict[str, float | None]:
    resolver = resolver_for(landmarks, **frame_kwargs)
    return {metric.__name__: metric(resolver, T).value for metric in ANGULAR}


# ---------------------------------------------------------------------------------------------
# The headline property
# ---------------------------------------------------------------------------------------------


@SETTINGS
@given(pose=poses, scale=scales)
def test_every_angular_metric_is_invariant_to_body_size(pose: dict[str, Any], scale: float) -> None:
    """The defect the whole rebuild exists to fix, stated over the entire input space.

    The legacy engine's thresholds were absolute pixel counts, so this property fails
    catastrophically against it — a person twice as far from the camera got a different verdict for
    the same posture. Here the angle is computed in metric world space, where scale never enters.

    1e-6 degrees is not a tolerance for a normalisation that nearly works; it is floating-point
    noise. The invariance is exact by construction.
    """
    baseline = angular_values(make_pose(**pose))
    scaled = angular_values(make_pose(**pose, body=scaled_body(scale)))

    for name, value in baseline.items():
        other = scaled[name]
        assert (value is None) == (other is None), name
        if value is not None and other is not None:
            assert other == pytest.approx(value, abs=1e-6), f"{name} moved under {scale}x scaling"


@SETTINGS
@given(pose=poses, size=frame_sizes)
def test_every_angular_metric_is_invariant_to_frame_size_and_aspect_ratio(
    pose: dict[str, Any], size: tuple[int, int]
) -> None:
    """A 4:3 photo and a 21:9 one of the same posture must agree.

    Worth its own property because the obvious implementation — computing angles straight from
    normalised coordinates — silently measures in a space stretched by the aspect ratio, and the
    error it produces is small enough to look like model noise.
    """
    width, height = size
    baseline = angular_values(make_pose(**pose))
    resized = angular_values(
        make_pose(**pose, image_width=width, image_height=height), width=width, height=height
    )

    for name, value in baseline.items():
        other = resized[name]
        assert (value is None) == (other is None), name
        if value is not None and other is not None:
            assert other == pytest.approx(value, abs=1e-6), f"{name} moved at {width}x{height}"


@SETTINGS
@given(
    pose=poses,
    offset=st.tuples(
        st.floats(-2.0, 2.0),
        st.floats(-2.0, 2.0),
        # Depth too, not just the image plane. Checked rather than assumed: perturbing `z_world`
        # alone moves `craniovertebral_angle_deg` (50.0° to 66.3°) and `knee_flexion_deg` (100.0°
        # to 96.4°), so an x/y-only offset was exercising two axes out of three on metrics that
        # genuinely read the third. `trunk_inclination_deg` is the exception and correctly so —
        # depth is its *lateral* axis once the facing direction is resolved, so a sideways shift
        # should not change a forward lean.
        st.floats(-2.0, 2.0),
    ),
)
def test_every_angular_metric_is_invariant_to_where_the_subject_stands(
    pose: dict[str, Any], offset: tuple[float, float, float]
) -> None:
    """Translation invariance, applied in world space where the metrics actually work.

    MediaPipe's world landmarks are hip-origin, so a real translation is invisible to them by
    construction — which is exactly why this must be checked rather than assumed. Shifting every
    world coordinate by a constant simulates a backend that did *not* re-origin, and the angles
    must not care.
    """
    dx, dy, dz = offset
    original = make_pose(**pose)
    shifted = {
        name: Landmark(
            x=landmark.x,
            y=landmark.y,
            visibility=landmark.visibility,
            presence=landmark.presence,
            x_world=None if landmark.x_world is None else landmark.x_world + dx,
            y_world=None if landmark.y_world is None else landmark.y_world + dy,
            z_world=None if landmark.z_world is None else landmark.z_world + dz,
        )
        for name, landmark in original.items()
    }

    baseline = angular_values(original)
    moved = angular_values(shifted)
    for name, value in baseline.items():
        other = moved[name]
        # Availability first, as the scale and frame-size properties already do. Comparing only
        # when both sides produced a number would pass a translation that changed whether a metric
        # can be computed at all — a bigger regression than one that changes its value, and the
        # one this loop was silently exempting.
        assert (value is None) == (other is None), f"{name} changed availability under translation"
        if value is not None and other is not None:
            assert other == pytest.approx(value, abs=1e-6), f"{name} moved under translation"


@SETTINGS
@given(pose=poses, scale=scales)
def test_the_view_ratio_is_invariant_to_body_size(pose: dict[str, Any], scale: float) -> None:
    """A ratio of two image-space lengths, so the projection cancels.

    Separated from the angular metrics because it is measured in image space on purpose — the
    question it answers is what the *camera* could see.
    """
    baseline = view_confidence(resolver_for(make_pose(**pose)), T).value
    scaled = view_confidence(resolver_for(make_pose(**pose, body=scaled_body(scale))), T).value
    assert (baseline is None) == (scaled is None), "resizing the body changed availability"
    if baseline is not None and scaled is not None:
        assert scaled == pytest.approx(baseline, abs=1e-6)


# ---------------------------------------------------------------------------------------------
# Nothing raises, ever
# ---------------------------------------------------------------------------------------------


@SETTINGS
@given(pose=poses, dropped=st.sets(st.sampled_from(list(K)), max_size=KEYPOINT_COUNT))
def test_a_report_can_always_be_built_however_much_is_missing(
    pose: dict[str, Any], dropped: set[K]
) -> None:
    """Degradation must be quiet at the boundary and loud in the report, never an exception.

    The legacy engine's answer to missing data was a swallowed exception and a `None` that its
    caller rendered as good posture. The answer here is a report full of gaps — which is the
    correct assessment of a photograph of an empty chair, and is not a crash.
    """
    landmarks = make_pose(**pose, omit=tuple(dropped))
    report = build_report(
        PoseFrame(
            landmarks=landmarks,
            image_width=640,
            image_height=480,
            backend="synthetic",
            inference_ms=0.0,
        ),
        T,
    )

    assert report.quality.assessed + len(report.quality.gaps) == report.quality.total
    # A metric never claims a value it could not compute, whatever was thrown away.
    for metric in report.metrics.values():
        assert metric.is_ok == (metric.value is not None)


@SETTINGS
@given(
    pose=poses,
    visibilities=st.lists(st.floats(0.0, 1.0), min_size=KEYPOINT_COUNT, max_size=KEYPOINT_COUNT),
)
def test_a_report_can_always_be_built_however_unconfident_the_landmarks(
    pose: dict[str, Any], visibilities: list[float]
) -> None:
    """The other half of degradation: everything present, nothing trustworthy."""
    confidence = {
        name: (visibility, 0.9)
        for name, visibility in zip(sorted(K, key=str), visibilities, strict=True)
    }
    report = build_report(
        PoseFrame(
            landmarks=make_pose(**pose, confidence=confidence),
            image_width=640,
            image_height=480,
            backend="synthetic",
            inference_ms=0.0,
        ),
        T,
    )
    for finding in report.findings:
        assert 0.0 <= finding.confidence <= 1.0
    assert report.overall_score is None or 0.0 <= report.overall_score <= 100.0


@SETTINGS
@given(pose=poses)
def test_the_same_pose_always_produces_the_same_report(pose: dict[str, Any]) -> None:
    """Determinism over the whole input space, not just the examples someone chose.

    Everything downstream that compares two reports — the history trend, the golden corpus, the
    cross-language check against the TypeScript mirror — is meaningless without this.
    """
    landmarks = make_pose(**pose)
    first = build_report(
        PoseFrame(
            landmarks=landmarks,
            image_width=640,
            image_height=480,
            backend="synthetic",
            inference_ms=0.0,
        ),
        T,
    ).to_dict()
    second = build_report(
        PoseFrame(
            landmarks=landmarks,
            image_width=640,
            image_height=480,
            backend="synthetic",
            inference_ms=0.0,
        ),
        T,
    ).to_dict()
    assert first == second
