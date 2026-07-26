# ADR-0002 — MediaPipe Pose Landmarker, not the CMU Keras model

**Status:** Accepted
**Date:** 2026-07-26
**Ticket:** OP-15, recording the OP-16 (B1) spike results
**Supersedes nothing. Descopes OP-22 (B7, ONNX MoveNet fallback) — see "The gated risk".**

This is the platform bet the whole pipeline rests on, so it is recorded in more detail than the
other ADRs.

## Context

The original project used a Keras reimplementation of CMU OpenPose: a 209 MB `model.h5` with
~52.3 M parameters, distributed as a bare Dropbox link. The code around it has three separate
disqualifying problems (FINDINGS §3):

- Heatmaps come out at a quarter of the expected magnitude, so peak detection is tuned against an
  artefact.
- The multi-person association stage is absent entirely.
- Neither `process()` function is importable — both are defined inside `if __name__ == "__main__"`
  scope in the sense that matters: they depend on module-level globals built at import time from a
  cwd-relative config.

Replacing the model was therefore not optional. The question was what to replace it with.

## Decision

**MediaPipe Pose Landmarker**, pinned to **`mediapipe==0.10.18`**, behind the `PoseBackend`
Protocol in `packages/pose-backends`.

| | Keras OpenPose (legacy) | MediaPipe Pose Landmarker |
| --- | --- | --- |
| Model size | 209 MB, Dropbox-hosted | 9 MB (`pose_landmarker_full.task`) |
| Parameters | 52.3 M | ~3 M |
| CPU latency | seconds | ~20–35 ms |
| Python support | 3.11 only, no arm64 wheel | 3.9–3.12 |
| Licence | code MIT; **weights unattributed** | Apache-2.0 |
| Keypoints | 18 (COCO), x/y | 33, x/y/z, incl. heel and foot index |
| Per-point confidence | none | `visibility` **and** `presence`, both [0,1] |
| Metric 3D | none | `pose_world_landmarks`, metres, hip-origin |

Three wins decided it, in order of weight.

**1. World landmarks give scale invariance for free.** `pose_world_landmarks` are in metres with
the hip midpoint as origin, independent of image resolution and subject distance. The original
code's central defect — thresholds in raw pixels, so identical posture at twice the distance gets a
different verdict (FINDINGS §2.6) — stops being a normalization problem to solve and becomes
*"compute the angle in world space."* No competing backend offers this. See
[ADR-0005](0005-scale-invariant-metrics.md).

**2. `visibility` and `presence` make missing data expressible.** Two independent signals
distinguish *occluded* (low visibility, high presence) from *out of frame* (low presence). That is
what lets the API say "I couldn't assess your knees" instead of the original's silent
"Straight back position" whenever assessment failed (FINDINGS §2.5) — the single most damaging
behaviour in the inherited system.

**3. Feet exist.** `LEFT_HEEL(29)`, `RIGHT_HEEL(30)`, `LEFT_FOOT_INDEX(31)`, `RIGHT_FOOT_INDEX(32)`
make heel contact a real measurement. The 18-point COCO schema simply had no foot landmarks, which
is why the original's feet check was a tautology that returned "on the floor" for a fixture whose
subject's feet are visibly dangling (FINDINGS §2.3). The project's own README listed this as a
goal and never delivered it.

On licensing, precisely: the legacy *code* is MIT (Copyright (c) 2020 Vinay Varma, retained at
`docs/archive/legacy-openpose/LICENSE`). It is the *weights* whose provenance cannot be
established — a Dropbox URL with no licence, no checksum and no stated origin
(`docs/archive/legacy-openpose/model-weights-readme.md`). For a public MIT repository that is
disqualifying on its own.

## Keypoint mapping, legacy COCO-18 → MediaPipe 33

The legacy authoritative order, from `API/config`:

```
part_str = [nose, neck, Rsho, Relb, Rwri, Lsho, Lelb, Lwri, Rhip, Rkne,
            Rank, Lhip, Lkne, Lank, Leye, Reye, Lear, Rear, pt19]
```

| Legacy | Idx | MediaPipe | Idx | Note |
| --- | --- | --- | --- | --- |
| `nose` | 0 | `NOSE` | 0 | |
| `neck` | 1 | *(derived)* | — | **See below — the one real schema change.** |
| `Rsho` | 2 | `RIGHT_SHOULDER` | 12 | |
| `Lsho` | 5 | `LEFT_SHOULDER` | 11 | |
| `Relb` | 3 | `RIGHT_ELBOW` | 14 | |
| `Lelb` | 6 | `LEFT_ELBOW` | 13 | |
| `Rwri` | 4 | `RIGHT_WRIST` | 16 | |
| `Lwri` | 7 | `LEFT_WRIST` | 15 | |
| `Rhip` | 8 | `RIGHT_HIP` | 24 | |
| `Lhip` | 11 | `LEFT_HIP` | 23 | Legacy code comments 11 as "Hip"; it is the *left* hip. |
| `Rkne` | 9 | `RIGHT_KNEE` | 26 | |
| `Lkne` | 12 | `LEFT_KNEE` | 25 | |
| `Rank` | 10 | `RIGHT_ANKLE` | 28 | |
| `Lank` | 13 | `LEFT_ANKLE` | 27 | |
| `Leye` | 14 | `LEFT_EYE` | 2 | MediaPipe also has inner/outer eye points (1,3,4,5,6). |
| `Reye` | 15 | `RIGHT_EYE` | 5 | |
| `Lear` | 16 | `LEFT_EAR` | 7 | **Legacy code had 16/17 inverted — see below.** |
| `Rear` | 17 | `RIGHT_EAR` | 8 | |
| `pt19` | 18 | — | — | Background channel. No equivalent; not a body point. |
| — | — | `LEFT_HEEL` | 29 | **New capability.** No legacy equivalent. |
| — | — | `RIGHT_HEEL` | 30 | **New capability.** |
| — | — | `LEFT_FOOT_INDEX` | 31 | **New capability.** |
| — | — | `RIGHT_FOOT_INDEX` | 32 | **New capability.** |

**`NECK` must be derived** as `midpoint(LEFT_SHOULDER, RIGHT_SHOULDER)`. MediaPipe has no neck
landmark, and this is the single schema change that touches the most rule code. Worth noting it
changes nothing semantically: in the legacy COCO-18 output, keypoint 1 was *already* synthesized
from the two shoulders rather than observed. Making that derivation explicit is what exposes
FINDINGS §2.2 for what it was — a comparison of the shoulder midpoint's *y* against the shoulder
midpoint's *y*, a point against itself, which could only ever return one answer.

**The ear-inversion bug disappears by construction.** `API/config` declares 16 = left ear and
17 = right ear, but `posture_image.py:103-107` comments them the other way round, and the
laterality flag `f` — which decides whether to apply `degrees = 180 - degrees` — is keyed off that
misreading (FINDINGS §2.1). MediaPipe returns unambiguous named landmarks, so there is no
hand-rolled laterality flag to get backwards. This is the clearest case in the project of a class
of bug being removed by schema choice rather than by fixing code.

Everything except `NECK` is a rename inside the adapter, invisible to the rules engine.

## The gated risk, and the spike that settled it

MediaPipe historically shipped `manylinux_x86_64` wheels only; linux `aarch64` arrived late and —
as it turns out — left again. Because an Apple Silicon dev machine and arm64 containers are both in
scope, this was the one risk capable of invalidating the decision, so OP-16 gated it *before* any
rules code was written.

**Findings, verified against PyPI on 2026-07-26:**

- **Linux aarch64 wheels were dropped after `0.10.18`.** `0.10.18` publishes
  `manylinux_2_17_aarch64` for cp39–cp312. There is no `0.10.19`; **`0.10.20` is the first release
  with zero linux-aarch64 wheels**, and the current release `0.10.35` publishes only
  `manylinux_2_28_x86_64`, `win_amd64`, `win_arm64` and `macosx_11_0_arm64`. macOS arm64 survives;
  Linux arm64 does not.
- Both acceptance commands pass under `python:3.12-slim` on `linux/arm64` and `linux/amd64`, with
  `--only-binary=:all:`, importing `mediapipe.tasks.python.vision.PoseLandmarker`.
- **Therefore no ONNX MoveNet fallback is needed, and OP-22 (B7) is descoped.**
- Transitive ceilings `0.10.18` imposes on the entire workspace: **`numpy<2`** and
  **`protobuf<5,>=4.25.3`**. Co-installation with FastAPI 0.140, SQLAlchemy 2.0.51 and Pydantic
  2.13 verified clean; `pip check` reports no broken requirements.
- **Image size.** A default install is **857 MB** — `jaxlib` alone is 299 MB, `scipy` 146 MB, plus
  `matplotlib` and `sentencepiece`, none of which pose inference uses. Installing `--no-deps` with
  a hand-picked runtime set plus `opencv-python-headless` yields **311 MB** with `PoseLandmarker`
  working. This matters against the <600 MB API image budget in OP-112.
- **Correction to the plan's stated "container gotcha".** `0.10.18` hard-depends on
  `opencv-contrib-python` — the *non-headless* build — so a default install genuinely needs
  `libgl1` and `libglib2.0-0` in the image. Swapping to `opencv-python-headless` requires the
  `--no-deps` route above; it is not a matter of overriding one requirement. Both options are
  recorded because the choice is a size/complexity tradeoff, not a correctness one.

## Alternatives considered

**Keep the Keras OpenPose model.** Rejected: quarter-magnitude heatmaps, no multi-person stage, no
importable entry point, 209 MB of weights with no provenance, no arm64 wheel for its TensorFlow
pin, and no confidence signal with which to express missing data.

**YOLOv8-pose (Ultralytics).** Strong accuracy and actively maintained. Rejected on licence:
AGPL-3.0 is disqualifying for a public MIT portfolio repository, and no technical merit outweighs
shipping a licence that contradicts the project's own.

**MoveNet Thunder via ONNX Runtime.** Reliably multi-arch — its main advantage, and the reason it
was the designated fallback. Rejected as the primary because it is 2D-only with a single confidence
score per point: no world landmarks (so scale invariance goes back to being a problem to solve) and
no way to separate occluded from out-of-frame. Kept as the documented escape hatch; because
everything sits behind the Protocol, implementing it is ~150 lines and an adapter swap, and nothing
in `posture-core` or `apps/api` moves.

## Consequences

- **`mediapipe==0.10.18` is a hard pin, not a floor.** Any bump breaks arm64 outright. Dependabot
  is configured to ignore this dependency, and `numpy>=2` / `protobuf>=5` are ignored too, because
  a routine-looking bump to either would break inference. Revisiting the pin requires rerunning the
  spike, not reading a changelog.
- **The workspace cannot move to Python 3.13 while pinned.** `0.10.18` publishes cp39–cp312 only.
  `requires-python = ">=3.11"` and the 3.11/3.12 CI matrix are consistent with that; 3.13 would
  need this decision reopened.
- `numpy<2` propagates to every workspace member, including `posture-core` — the one dependency the
  pure package has, now with a ceiling set by a package it must never import. An irony worth
  noticing, but not a violation: the constraint is on the resolved environment, not on
  `posture-core`'s own declared dependencies.
- The adapter owns the `NECK` derivation and all landmark renaming. Rules code sees canonical named
  keypoints and never an integer index.
- Choosing a 3 M-parameter model over 52.3 M is a deliberate accuracy-for-latency trade. It is the
  right one here — the target is interactive posture feedback on a CPU, not a pose-estimation
  benchmark — but it should be stated rather than implied.
