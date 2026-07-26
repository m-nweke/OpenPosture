# ADR-0005 — World-space angles and normalized ratios, not pixel thresholds

**Status:** Accepted
**Date:** 2026-07-26
**Ticket:** OP-15 (implemented across Epic C: OP-31, OP-34, OP-37, OP-38, OP-41)

This is the decision that fixes the inherited project's central correctness defect rather than
restating it in a nicer framework. It is the ADR to read if only one is read.

## Context

Every threshold in the legacy engine was a raw pixel count, tuned against one camera setup. The
original author knew, and wrote it down in the code (`posture_image.py:144`):

```python
# this value 100 is arbitary [sic]. this shall be replaced with a calculation
# which can adjust to different sizes of people.
```

That comment describes the whole class of defect. Consequences measured on the eight curated
fixtures and recorded in `docs/archive/legacy-baseline.json` (captured in OP-11, before the legacy
environment was removed):

**The neck metric carried no information at all** (FINDINGS §2.2). It compared the neck's *y*
against the shoulder midpoint's *y* with a 10 px threshold — but in the COCO-18 schema the model
emits, keypoint 1 (`neck`) **is** the shoulder midpoint, synthesized from the two shoulders rather
than observed. So it compared a point against itself. Across all eight fixtures the difference never
exceeded 4 px against a 10 px threshold, and the verdict was **"Neck is Straight" 8 times out of
8** — a constant masquerading as a measurement. Forward-head posture is a sagittal offset (ear ahead
of shoulder), not a vertical one, so even the axis was wrong.

**Resolution changed the answer.** Those fixtures are ≤1280 px. On the 5712 px originals the same
physical offsets scale past the fixed 10 px threshold, so the verdict would begin flipping on image
resolution alone. Identical posture, different picture, different diagnosis.

**The stated precondition was never enforced** (FINDINGS §2.7). Both dashboards instructed "this
image must be taken from a side angle" and nothing checked it, so a frontal photo produced confident
and meaningless spine angles.

## Decision

Three layers, in order of preference. A metric uses the highest one it can.

**1. Compute angles in world space.** MediaPipe's `pose_world_landmarks` are in metres with the hip
midpoint as origin ([ADR-0002](0002-mediapipe-pose.md)), so they are already independent of image
resolution and subject distance. An angle between two world-space vectors is scale-invariant by
construction — there is no normalization step to get wrong, because there is no pixel quantity in
the calculation. This is the layer that makes the original's core problem disappear rather than
solving it.

**2. Where a distance is unavoidable, express it as a ratio against an anatomical reference.**
Never an absolute length. Torso length (hip midpoint → shoulder midpoint) and shoulder width are the
two references; both scale with the subject, so `ear_forward_offset / torso_length` means the same
thing for a child two metres away and an adult at one metre.

**3. Reject inputs the metrics cannot honestly serve.** A `view_confidence` heuristic from the
shoulder-width-to-torso ratio detects frontal images, which are refused up front instead of being
answered confidently (OP-38). A precondition that is stated must be checked.

Concretely, replacing the three information-free checks:

| Legacy check | Why it failed | Replacement | Ticket |
| --- | --- | --- | --- |
| `neck_y` vs `shoulder_center_y`, 10 px | Point compared against itself; wrong axis; resolution-dependent | **Craniovertebral angle** — angle at C7 between the ear→C7 vector and horizontal, in world space. `< 50°` indicates forward head. | OP-34 |
| `checkHandFold`, ±100 px | Arbitrary absolute pixels, author-acknowledged | Ratio against shoulder width | OP-31 |
| `ankle_y > knee_y` | Tautology — holds in nearly every seated pose | **Heel contact** from `HEEL`(29/30) and `FOOT_INDEX`(31/32), landmarks COCO-18 did not have | OP-37 |
| *(none)* | Side-angle precondition unenforced | `view_confidence` from shoulder-width : torso ratio; frontal images rejected | OP-38 |

**No threshold is a literal in code.** All of them belong in `packages/posture-spec/rules.json`,
loaded by both the Python engine and its TypeScript mirror, so retuning is one change in one place
and the two implementations cannot drift apart.

> Forward references, stated plainly: `packages/posture-spec/rules.json` and
> `docs/evaluation.md` do not exist yet either — they arrive with Epic C and OP-115. This ADR
> describes the decision, not the current state of the tree.

## The property test is the point

A comment promising scale invariance is worth nothing; the original had one. So invariance is
asserted as an executable property, not a claim:

> **`packages/posture-core/tests/test_scale_invariance.py`** — a Hypothesis property test asserting
> that every metric's output is unchanged under uniform scaling `s ∈ [0.3, 3.0]` and arbitrary
> translation of the input landmarks. (OP-31 for the metrics, OP-41 for the full property suite.)

Two things make this more than a formality. First, it is a **property** test rather than an example
test: Hypothesis searches the input space and shrinks counterexamples, so it fails on inputs nobody
thought to write down. Second, and more usefully, **it would fail catastrophically against the
legacy implementation** — a pixel threshold cannot survive a 3× scale, so this single test is a
mechanical, reproducible demonstration that the defect is fixed rather than relocated.

> **Status note (2026-07-26):** this test does not exist yet. It lands with the metrics in Epic C;
> this ADR is written in OP-15 to fix the decision *before* the code, which is the point of writing
> it now. If Epic C completes without this test, the decision has not actually been implemented —
> the test is the deliverable, not documentation of it.

## Alternatives considered

**Normalize pixel thresholds by torso length and keep working in 2D.** This is what the original
author's comment proposed and it is a genuine fix for the scale problem — layer 2 of the decision
above, used wherever world landmarks are unavailable. Rejected as the *primary* approach because
world landmarks make it unnecessary for angles, and because 2D projection still loses the sagittal
information that forward-head posture consists of. Retained as the fallback rather than discarded.

**Calibrate against a known reference object or a stated subject height.** Would give true metric
scale from 2D. Rejected as user-hostile: it requires the person to measure something or place an
object in frame, for information MediaPipe already provides free.

**Train a classifier on posture labels and skip explicit geometry.** Rejected on two grounds. There
is no labelled dataset of sufficient size, and — decisively — the product's value is the
explanation. "Your craniovertebral angle is 44°, below the 50° threshold" is actionable in a way
"posture class 3, confidence 0.71" is not. An explicit geometric metric can be argued with; a
learned score cannot.

**Keep absolute thresholds but require a fixed camera setup.** Rejected: it converts a software
defect into a documentation burden users will not honour, and the original already demonstrated that
an unenforced instruction is not a constraint.

## Consequences

- **A hard dependency on world landmarks.** Layer 1 assumes `pose_world_landmarks`. A backend
  without them — the MoveNet fallback in [ADR-0002](0002-mediapipe-pose.md) — forces every metric
  down to layer 2 and loses the sagittal component. That is a real cost of switching backends and is
  why the fallback is a fallback.
- **Thresholds now need clinical justification, not tuning.** `< 50°` for craniovertebral angle is a
  published clinical figure, and every value in `rules.json` should cite where it came from.
  "It worked on our photos" is how the original got its numbers.
- **`rules.json` becomes a cross-language contract.** Both engines load it, and `golden/*.json`
  fixtures are run by both pytest and Vitest with CI failing if the two disagree on any fixture.
  Without that, the TypeScript mirror silently drifts and live mode contradicts the API.
- Rejecting frontal images means the product will sometimes decline to answer. That is the correct
  behaviour and it must be presented as a clear instruction rather than an error.
- Real-world scale invariance is bounded by the pose model's own accuracy at distance. The property
  test proves the *arithmetic* is scale-free; it does not prove landmark detection is equally good at
  0.3× — that is an evaluation question for `docs/evaluation.md` (OP-115), not a property.
