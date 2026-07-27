"""Tests for the landmark inspection CLI.

Driven through `main(argv)` rather than by spawning a subprocess: `main` returns an exit code
instead of calling `sys.exit`, precisely so it can be called in-process. A subprocess test would
be an order of magnitude slower and would assert almost nothing extra.

Nearly all of these run on the fake backend, so the whole file needs no model and no OpenCV.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pose_backends.cli import EXIT_ERROR, EXIT_NO_POSE, EXIT_OK, OUTPUT_SCHEMA_VERSION, main
from posture_core import KeypointName


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ---------------------------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------------------------


def test_prints_a_row_for_every_canonical_keypoint(capsys: pytest.CaptureFixture[str]) -> None:
    """All 34, including any the backend did not report.

    A missing keypoint is shown as `not reported` rather than omitted, because "this backend gave
    us no left heel" is exactly the kind of thing someone runs this command to discover. A table
    that silently shortens hides its most interesting result.
    """
    code, out, _ = run(capsys, "--backend", "fake")
    assert code == EXIT_OK
    for name in KeypointName:
        assert name.value in out


def test_reports_the_backend_latency_and_landmark_count(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, out, _ = run(capsys, "--backend", "fake")
    assert "backend        fake" in out
    assert "inference      0.0 ms" in out
    assert "landmarks      34 of 34" in out
    assert "world space    yes" in out


def test_unreported_keypoints_are_shown_as_such(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = run(capsys, "--backend", "fake", "--preset", "partial_occlusion")
    assert "left_knee         not reported" in out
    assert "landmarks      26 of 34" in out


def test_occluded_keypoints_are_distinguished_from_confident_ones(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The visibility/presence split, made visible.

    This column is the human-readable face of the signal that ends the silent false negative: a
    point the model believes is in the frame but cannot see reads `occluded`, not `ok`.
    """
    _, out, _ = run(capsys, "--backend", "fake", "--preset", "partial_occlusion")
    elbow_row = next(line for line in out.splitlines() if line.startswith("left_elbow"))
    assert elbow_row.endswith("occluded")


# ---------------------------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------------------------


def test_json_output_is_valid_and_versioned(capsys: pytest.CaptureFixture[str]) -> None:
    """The schema version is not decoration.

    Epic H compares this output against `docs/archive/legacy-baseline.json`, a capture that cannot
    be regenerated — the old weights and its TensorFlow 2.12 environment are gone. Without a
    version stamp, "the numbers changed" and "the format changed" become indistinguishable after
    the fact.
    """
    code, out, _ = run(capsys, "--backend", "fake", "--json")
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["schema_version"] == OUTPUT_SCHEMA_VERSION
    assert payload["backend"] == "fake"
    assert payload["landmark_count"] == 34
    assert set(payload["landmarks"]) == {name.value for name in KeypointName}


def test_json_landmarks_are_sorted_so_two_captures_diff_cleanly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, out, _ = run(capsys, "--backend", "fake", "--json")
    names = list(json.loads(out)["landmarks"])
    assert names == sorted(names)


def test_json_output_is_stable_across_runs(capsys: pytest.CaptureFixture[str]) -> None:
    """A golden capture is worthless if re-running produces a diff for no reason."""
    _, first, _ = run(capsys, "--backend", "fake", "--json")
    _, second, _ = run(capsys, "--backend", "fake", "--json")
    assert first == second


def test_json_carries_both_coordinate_systems(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = run(capsys, "--backend", "fake", "--json")
    neck = json.loads(out)["landmarks"]["neck"]
    assert set(neck) == {
        "x",
        "y",
        "x_world",
        "y_world",
        "z_world",
        "visibility",
        "presence",
        "status",
    }


# ---------------------------------------------------------------------------------------------
# Selection and exit codes
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "preset", ["straight", "hunchback", "reclined", "kneeling", "frontal_view"]
)
def test_every_detectable_preset_can_be_selected(
    preset: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _ = run(capsys, "--backend", "fake", "--preset", preset)
    assert code == EXIT_OK


def test_the_fake_backend_needs_neither_an_image_nor_a_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The demoable path on a machine with nothing installed but the base package."""
    code, out, err = run(capsys, "--backend", "fake")
    assert code == EXIT_OK
    assert err == ""
    assert out


def test_no_pose_exits_one_with_a_clear_message(capsys: pytest.CaptureFixture[str]) -> None:
    """Distinct from a crash.

    A script batching a directory of photographs has to tell "nobody in this one" apart from "the
    model file is missing", and an exit code is the only channel it has.
    """
    code, out, err = run(capsys, "--backend", "fake", "--preset", "no_person")
    assert code == EXIT_NO_POSE
    assert out == ""
    assert "No pose detected" in err


def test_a_real_backend_without_an_image_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = run(capsys, "--backend", "mediapipe")
    assert code == EXIT_ERROR
    assert "an image is required" in err


def test_a_missing_model_reports_the_remedy_rather_than_a_traceback(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"not really a jpeg")
    code, _, err = run(
        capsys, str(photo), "--backend", "mediapipe", "--model-path", "/nonexistent/model.task"
    )
    assert code == EXIT_ERROR
    assert "make fetch-model" in err


def test_a_missing_image_is_reported_by_path(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, _, err = run(capsys, str(tmp_path / "absent.jpg"), "--backend", "fake")
    assert code == EXIT_ERROR
    assert "no such image" in err


def test_an_unknown_preset_is_rejected_by_the_parser() -> None:
    """argparse `choices`, so the error names the valid values without any code of ours."""
    with pytest.raises(SystemExit):
        main(["--backend", "fake", "--preset", "slouchy"])


# ---------------------------------------------------------------------------------------------
# Real model
# ---------------------------------------------------------------------------------------------


@pytest.mark.model
def test_a_real_photograph_produces_a_full_table(capsys: pytest.CaptureFixture[str]) -> None:
    """The acceptance criterion for the whole ticket: a real image, a readable 34-row table."""
    repo_root = Path(__file__).resolve().parents[3]
    model = repo_root / "models" / "pose_landmarker_full.task"
    photo = repo_root / "fixtures" / "images" / "hunchback_right.jpg"
    if not model.is_file():
        pytest.skip("no model — run `make fetch-model`")

    code, out, _ = run(capsys, str(photo), "--model-path", str(model))
    assert code == EXIT_OK
    assert "backend        mediapipe" in out
    assert "landmarks      34 of 34" in out
    assert "world space    yes" in out
    # Latency is reported, and it had better be a real measurement rather than the fake's 0.0.
    latency = next(line for line in out.splitlines() if line.startswith("inference "))
    assert float(latency.split()[1]) > 0.0


def test_the_fake_backend_does_not_decode_an_image_it_was_given(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--backend fake` must not import OpenCV, even when a path is supplied.

    OpenCV arrives only with the mediapipe extra, so decoding here would break the promise that
    the fake backend runs on the base package alone — and break it only when a path happened to be
    passed, which is a miserable thing to debug. Enforced by making the import itself fail.
    """
    monkeypatch.setitem(__import__("sys").modules, "cv2", None)
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"not really a jpeg")

    code, out, _ = run(capsys, str(photo), "--backend", "fake")
    assert code == EXIT_OK
    assert "backend        fake" in out


def test_a_path_given_to_the_fake_backend_is_still_checked(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Skipping the decode must not mean silently ignoring the argument."""
    code, _, err = run(capsys, str(tmp_path / "absent.jpg"), "--backend", "fake")
    assert code == EXIT_ERROR
    assert "no such image" in err


def test_json_always_carries_every_canonical_keypoint(capsys: pytest.CaptureFixture[str]) -> None:
    """A stable key set, whatever the backend reported.

    A key set that varied with detection makes two captures of the same image diff noisily, and
    makes "this backend stopped reporting a left heel" indistinguishable from "this key was never
    in the format". The table already renders all 34 rows; the machine-readable form is the one
    Epic H compares against a legacy baseline that cannot be regenerated, so it should not be the
    weaker of the two.
    """
    _, out, _ = run(capsys, "--backend", "fake", "--preset", "partial_occlusion", "--json")
    payload = json.loads(out)
    assert set(payload["landmarks"]) == {name.value for name in KeypointName}
    assert payload["landmark_count"] == 26
    assert payload["canonical_count"] == 34


def test_unreported_keypoints_are_null_filled_and_labelled(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`not_reported` is distinct from every status a returned landmark can carry."""
    _, out, _ = run(capsys, "--backend", "fake", "--preset", "partial_occlusion", "--json")
    knee = json.loads(out)["landmarks"]["left_knee"]
    assert knee["status"] == "not_reported"
    assert knee["x"] is None
    assert knee["visibility"] is None
