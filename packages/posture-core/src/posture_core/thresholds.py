"""Every tunable number in the rules engine, in one frozen, injected object.

## Why this exists at all

The inherited engine scattered its constants through the code it used them in — a `±100` pixel
literal for arm crossing whose own inline comment admitted it "shall be replaced with a
calculation which can adjust to different sizes of people", a hardcoded angle for spine
classification, a foot check that was a tautology. Retuning any of them meant finding them,
understanding the surrounding code, and hoping there was not a second copy. There usually was:
about 250 lines were duplicated between the two posture scripts.

Collecting them here changes three things:

* **Tuning is a config change.** One object, one place, no code touched.
* **Every rule test is parameterisable.** A boundary test can construct `Thresholds(slouch_deg=30)`
  and assert behaviour at exactly 29.9 and 30.1 without monkeypatching a module global.
* **There is no module global to patch.** Thresholds are passed in. A test that mutated a global
  would leak into every test that ran after it, and the failure would land somewhere else
  entirely.

## Why injected rather than loaded here

This package reads no files and no environment variables — that is the constraint that lets its
suite run in seconds anywhere. The API layer loads its own values from the environment (OP-39) and
passes the resulting object down. In OP-36 the defaults below move to
`packages/posture-spec/rules.json`, so the Python engine and its TypeScript mirror load *the same*
numbers and cannot drift; this dataclass then becomes the parsed shape of that file rather than
the source of the values.
"""

from __future__ import annotations

import dataclasses
from typing import Final

__all__ = ["DEFAULT_THRESHOLDS", "RULES_VERSION", "Thresholds"]

RULES_VERSION: Final = "1.0.0"
"""Stamped onto every report (OP-32).

Two reports are only comparable if they were produced by the same rules at the same settings. A
stored report with no version becomes uninterpretable the first time a threshold moves — you can
no longer tell whether the user's posture changed or the yardstick did.
"""


@dataclasses.dataclass(frozen=True, slots=True)
class Thresholds:
    """Every number a rule is allowed to compare against.

    Defaults are starting points chosen from the clinical literature where one exists and from the
    fixture set otherwise. They are not sacred, and the point of this class is that changing them
    is cheap.
    """

    version: str = RULES_VERSION

    # -- confidence ----------------------------------------------------------------------------
    min_visibility: float = 0.5
    """Below this, a landmark is reported but not trusted for measurement.

    Matches MediaPipe's own default detection confidence. Raising it makes the engine abstain more
    often, which is the safe direction: an abstention says "I couldn't assess this", a bad
    measurement says something false with confidence.
    """

    min_presence: float = 0.5
    """Below this, the landmark is treated as outside the frame rather than merely occluded.

    The two signals are independent, and keeping their thresholds separate is what lets the API
    tell a user "your knees are out of shot" rather than "your knees are unclear".
    """

    # -- trunk inclination (OP-26) -------------------------------------------------------------
    trunk_upright_deg: float = 10.0
    """Forward lean at or below this is neutral sitting, not a finding."""

    trunk_slouch_deg: float = 20.0
    """Forward lean beyond this is a slouch worth reporting."""

    trunk_recline_deg: float = -20.0
    """Backward lean beyond this (i.e. more negative) is reclining.

    A separate threshold rather than a mirror of the slouch value, because the two are not
    symmetric problems: reclining into a supportive backrest is mostly benign, slouching forward
    unsupported is not.
    """

    # -- craniovertebral angle (OP-27) ---------------------------------------------------------
    cva_forward_head_deg: float = 50.0
    """Below this, forward head posture. The standard clinical cutoff.

    Note the direction: a *smaller* craniovertebral angle is worse, which is the opposite of every
    other angular threshold here and is worth stating twice.
    """

    cva_borderline_deg: float = 55.0
    """Between this and the cutoff, worth mentioning but not calling a fault."""

    # -- arms and elbows (OP-28) ---------------------------------------------------------------
    arms_crossed_ratio: float = 0.70
    """Wrist-to-opposite-elbow distance over torso length, below which the arms read as folded.

    Normalised by torso length, which is the entire fix for the legacy `±100` pixel literal: the
    same folded arms at twice the camera distance produced half the pixel separation and a
    different verdict. Dividing by a length measured on the same body cancels the scale.

    **The value was measured, not assumed.** `docs/V2-PLAN.md` proposed 0.15 against a different
    formula — `|forearm - upper_arm| / torso` — which compares two segments of the *same* arm and
    evaluates to ~0.06 for typical adult proportions in any posture whatsoever; it would have
    reported folded arms for everybody. Running the real backend over the eight fixtures gives a
    clean separation on the wrist-to-opposite-elbow measure:

        straight_armsfolded.jpg   0.53   <- the one fixture with folded arms
        bench_feet_dangling.jpg   0.91
        hunchback_right.jpg       1.00
        desk_lean_exif.jpeg       1.04
        hunchback_left.jpg        1.05
        reclined_right.jpg        1.04
        kneeling_right.jpg        1.07
        desk_hunch.jpeg           1.20

    0.70 sits in the gap with room on both sides. Eight photographs is a small sample and this
    should be revisited against the evaluation set in Epic H.
    """

    elbow_flexed_deg: float = 120.0
    """Elbow angle below this counts as flexed."""

    # -- knees (OP-29) -------------------------------------------------------------------------
    knee_seated_min_deg: float = 70.0
    knee_seated_max_deg: float = 120.0
    """A hip-knee-ankle angle in this band is ordinary seated posture."""

    knee_kneeling_max_deg: float = 60.0
    """Below this the subject is kneeling rather than sitting."""

    # -- heel contact (OP-30) ------------------------------------------------------------------
    heel_contact_tolerance_m: float = 0.05
    """How far above the lowest foot point a heel may sit and still count as grounded.

    In metres, from world landmarks — which is why this metric is possible at all. The 18-point
    COCO schema the legacy engine used had no foot landmarks, so its feet check was a tautology
    that returned "on the floor" for a fixture whose subject's feet visibly dangle.
    """

    # -- view confidence (OP-31) ---------------------------------------------------------------
    lateral_view_max_ratio: float = 0.35
    """``shoulder_width / torso_length`` in image space, at or below which the view is lateral.

    In a true side view the shoulders overlap and this approaches zero; face-on it approaches the
    subject's real proportions. The original app *told* users to photograph themselves from the
    side and then assessed whatever arrived — so a 30° slump shot head-on, which projects to
    almost no apparent lean, was reported as good posture with full confidence.
    """

    frontal_view_min_ratio: float = 0.55
    """At or above this the view is frontal and sagittal metrics must abstain."""

    # -- scoring (OP-32) -----------------------------------------------------------------------
    score_penalty_per_finding: float = 15.0
    """Points deducted from 100 for each fault found.

    Deliberately crude and deliberately documented as such: an overall score is a summary for the
    user, not a measurement. The findings are the output that means something.
    """

    def __post_init__(self) -> None:
        # Ordering constraints, checked because a mis-ordered pair does not raise anywhere else —
        # it just produces a band that no value can fall into, so a rule silently never fires.
        if not 0.0 <= self.min_visibility <= 1.0:
            raise ValueError(f"min_visibility must be a probability, got {self.min_visibility}")
        if not 0.0 <= self.min_presence <= 1.0:
            raise ValueError(f"min_presence must be a probability, got {self.min_presence}")
        if self.trunk_upright_deg > self.trunk_slouch_deg:
            raise ValueError(
                f"trunk_upright_deg ({self.trunk_upright_deg}) must not exceed "
                f"trunk_slouch_deg ({self.trunk_slouch_deg}); nothing could fall between them"
            )
        if self.trunk_recline_deg >= 0.0:
            raise ValueError(
                f"trunk_recline_deg is a backward lean and must be negative, "
                f"got {self.trunk_recline_deg}"
            )
        if self.cva_forward_head_deg >= self.cva_borderline_deg:
            raise ValueError(
                f"cva_forward_head_deg ({self.cva_forward_head_deg}) must be strictly below "
                f"cva_borderline_deg ({self.cva_borderline_deg}) — a smaller craniovertebral "
                "angle is worse, not better. Equal values leave the borderline band empty, so "
                "that finding could never fire"
            )
        if self.knee_seated_min_deg >= self.knee_seated_max_deg:
            raise ValueError("knee_seated_min_deg must be below knee_seated_max_deg")
        if self.lateral_view_max_ratio >= self.frontal_view_min_ratio:
            raise ValueError(
                "lateral_view_max_ratio must be below frontal_view_min_ratio; the gap between "
                "them is the ambiguous-view band, and it cannot be negative"
            )
        if self.score_penalty_per_finding < 0.0:
            raise ValueError("score_penalty_per_finding must not be negative")


DEFAULT_THRESHOLDS: Final = Thresholds()
"""A shared instance of the defaults.

Safe to share because the class is frozen — this is a constant, not a mutable global. Callers that
want different values construct their own with :func:`dataclasses.replace`, which is what every
boundary test does.
"""
