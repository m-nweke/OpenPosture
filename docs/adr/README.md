# Architecture Decision Records

Each file records one decision that is expected to outlive the ticket that prompted it: the context
it was made in, what was chosen, what was rejected and why, and what it costs.

The point is not documentation for its own sake. It is the difference between *"I used MediaPipe"*
and *"I evaluated three pose backends against six criteria, gated the one real portability risk
with a spike before writing any rules code, and here is the tradeoff I accepted."* The second is a
reasoned decision; the first is a coincidence.

## Conventions

**ADRs are append-only.** An ADR is never edited to change its conclusion, because its value is
being an honest record of what was believed at the time. When a decision changes, add a new ADR
that supersedes the old one and mark the old one `Superseded by ADR-XXXX`. Corrections of fact are
fine — and should be marked as corrections, with the original claim left visible.

**Status** is one of `Accepted`, `Superseded by ADR-XXXX`, or `Proposed`.

## Index

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-fastapi-over-flask.md) | FastAPI, not a modernized Flask | Accepted |
| [0002](0002-mediapipe-pose.md) | MediaPipe Pose Landmarker, not the CMU Keras model | Accepted |
| [0003](0003-self-hosted-jwt.md) | Self-hosted JWT, not Firebase Auth | Accepted |
| [0004](0004-postgres-and-minio.md) | Postgres and MinIO, not Firebase | Accepted |
| [0005](0005-scale-invariant-metrics.md) | World-space angles and normalized ratios, not pixel thresholds | Accepted |
| [0006](0006-retain-git-history.md) | Retain git history rather than rewrite it | Accepted |

[0002](0002-mediapipe-pose.md) and [0005](0005-scale-invariant-metrics.md) carry the most weight:
the first is the platform bet the whole pipeline rests on, and the second is the one that fixes the
original project's central correctness defect rather than merely restating it.
