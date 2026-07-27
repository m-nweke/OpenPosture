"""Golden snapshots: the whole report, for each named preset, byte for byte.

Every other test in this package asserts one property of one metric. These assert the *entire*
document — findings, wording, gaps, confidences, score, both version stamps — so a change nobody
meant to make cannot slip through by being outside the scope of any individual assertion.

## Why they are committed files rather than inline expectations

Three reasons, in order of weight:

1. **The diff is the review.** Retuning a threshold is supposed to change these files. What
   matters is that the change is *visible* — a reviewer reads "confidence 0.95 -> 0.61 on eleven
   findings" and asks why, which is not a question that gets asked about a number buried in an
   assertion.
2. **The same corpus will be run by the TypeScript mirror** (OP-36). Both engines load these files
   and CI fails if they disagree on any fixture. That is only possible if the expectation lives in
   a language-neutral file.
3. **They are the versioned record of what the engine said.** `docs/archive/legacy-baseline.json`
   is the old engine's half of the same comparison, captured before its weights and its
   TensorFlow 2.12 environment were removed and impossible to regenerate. This is the new half.

## Updating them

`uv run python packages/posture-core/tests/regenerate_golden.py`, from the repository root — the
same command the failure message prints, so there is one instruction rather than two. (`python -m
tests.regenerate_golden` also works, but only from `packages/posture-core`, and a command that
silently depends on the working directory is not the one to put in a docstring.)

Or delete a file and run the suite, which writes it and fails once with a message saying so. Never
edit one by hand: a golden file that was reasoned about rather than generated is a golden file
that asserts what someone hoped for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from builders import frame

from posture_core import build_report
from posture_core.synthetic import View
from posture_core.thresholds import DEFAULT_THRESHOLDS

GOLDEN = Path(__file__).parent / "golden"

# The corpus. Named to match `FakePoseBackend`'s presets where they overlap, so a fixture that
# breaks here and a fake-backed end-to-end test that breaks downstream are visibly the same case.
CASES: dict[str, dict[str, Any]] = {
    "straight": {"trunk_deg": 3.0, "neck_deg": 5.0, "upper_arm_deg": 10.0, "forearm_deg": 75.0},
    "hunchback": {"trunk_deg": 32.0, "neck_deg": 30.0, "upper_arm_deg": 15.0, "forearm_deg": 80.0},
    "reclined": {"trunk_deg": -25.0, "neck_deg": 10.0, "upper_arm_deg": -5.0, "forearm_deg": 60.0},
    "kneeling": {
        "trunk_deg": 5.0,
        "neck_deg": 6.0,
        "thigh_deg": 15.0,
        "shank_deg": 165.0,
        "upper_arm_deg": 8.0,
        "forearm_deg": 20.0,
    },
    "arms_folded": {
        "trunk_deg": 8.0,
        "neck_deg": 8.0,
        "upper_arm_deg": 15.0,
        "forearm_deg": 80.0,
        "forearm_cross_deg": 55.0,
    },
    "frontal_view": {
        "trunk_deg": 30.0,
        "neck_deg": 25.0,
        "upper_arm_deg": 10.0,
        "forearm_deg": 75.0,
        "view": View.FRONTAL,
    },
    # No case may sit on a threshold. `partial_occlusion` was originally built at trunk_deg=10.0,
    # exactly `trunk_upright_deg`, and CI caught the consequence: the measured angle is bit-exact
    # 10.0 on arm64 and a hair above it on x86_64, so `value > threshold` fired on one
    # architecture and not the other. The snapshot then differed by a whole finding and a 7.5
    # point score while the *displayed* value still read 10.0, which is a genuinely confusing
    # diff to be handed. Values reaching a comparison have been through a trig pipeline; their
    # last bits are not portable.
    "partial_occlusion": {
        "trunk_deg": 8.0,
        "neck_deg": 12.0,
        "upper_arm_deg": 12.0,
        "forearm_deg": 78.0,
        "omit": (
            "left_knee",
            "right_knee",
            "left_ankle",
            "right_ankle",
            "left_heel",
            "right_heel",
            "left_foot_index",
            "right_foot_index",
        ),
    },
}


def report_for(name: str) -> dict[str, Any]:
    from posture_core import KeypointName

    pose = dict(CASES[name])
    if "omit" in pose:
        pose["omit"] = tuple(KeypointName(value) for value in pose["omit"])
    return build_report(frame(**pose), DEFAULT_THRESHOLDS).to_dict()


def serialise(payload: dict[str, Any]) -> str:
    """Stable formatting, so a diff shows a changed verdict rather than a reordered dictionary."""
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_report_matches_its_golden_snapshot(name: str) -> None:
    path = GOLDEN / f"{name}.json"
    actual = serialise(report_for(name))

    if not path.exists():
        # Writing it and failing once, rather than passing silently. A snapshot test that creates
        # its own expectation on first run and then passes has asserted nothing, and the run where
        # that happened is the one nobody looks at.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        pytest.fail(f"golden snapshot {path.name} did not exist and has been written — review it")

    expected = path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"the report for {name!r} changed. If that was intended, regenerate with "
        f"`uv run python packages/posture-core/tests/regenerate_golden.py` and review the diff."
    )


def test_every_snapshot_on_disk_still_has_a_case() -> None:
    """An orphaned file would keep passing review as evidence of behaviour nothing produces."""
    on_disk = {path.stem for path in GOLDEN.glob("*.json")}
    assert on_disk <= set(CASES), f"golden files with no case: {sorted(on_disk - set(CASES))}"


def test_the_snapshots_record_the_versions_that_produced_them() -> None:
    """Without both stamps a snapshot cannot be interpreted after a threshold moves — you cannot
    tell whether the engine changed or the yardstick did.

    The existence check is not redundant with the test above. That one writes a missing snapshot
    and fails with an explanation; this one only reads, so on a deleted file — the documented way
    to regenerate one — it raised `FileNotFoundError` and reported a missing path rather than the
    thing to do about it. Under a full run the parametrised test happens to write the file first
    and this never surfaces; run in isolation, or with `-k`, it is the only failure you see.
    """
    for name in CASES:
        path = GOLDEN / f"{name}.json"
        assert path.exists(), (
            f"golden snapshot {path.name} is missing. Run the whole file to have it written for "
            f"review, or regenerate the corpus with "
            f"`uv run python packages/posture-core/tests/regenerate_golden.py`."
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema_version"]
        assert payload["rules_version"]
