# OpenPosture - Sitting Posture Feedback System

[![PR](https://github.com/m-nweke/OpenPosture/actions/workflows/pr.yml/badge.svg?branch=main)](https://github.com/m-nweke/OpenPosture/actions/workflows/pr.yml)

> **⚠️ The narrative sections below describe the original v1 capstone and are being replaced.**
> The project is mid-rewrite. Overview, Model Architecture and Technologies Used still describe
> the Vue frontend and the TensorFlow/Keras OpenPose model, both of which have been removed. See
> [`docs/V2-PLAN.md`](docs/V2-PLAN.md) for what is being built and
> [`docs/FINDINGS.md`](docs/FINDINGS.md) for the audit of what was here before.
> [Development (v2)](#development-v2) is current. A rewritten README ships with the walking
> skeleton (OP-48).

### [Run Guide (archived)](docs/archive/RUNNING.md)
### [Model Download Link (archived)](docs/archive/ModelReadME.md)
### [Project Proposal Slide deck](docs/archive/Presentations/PostureCapstone.pptx)
### [Statement of Work](docs/archive/Misc/SoW_Posture.docx)

## Overview
This MIT-licensed software, hosted on GitHub, is a posture assessment tool that determines the sitting position of a person when given a lateral view as input. The output includes details such as the position of the back (straight, hunchback, reclined), position of the hands (folded vs not folded), and kneeling (i.e., feet curled behind the knees). The project employs a trained Keras model based on OpenPose to detect keypoints on the human body, providing valuable insights into sitting posture.

## Importance
Poor sitting posture can lead to lower back and neck pain, as well as various adverse health effects, including musculoskeletal imbalances, balance issues, impaired digestion, and reduced flexibility. Good posture, such as keeping feet flat on the floor, avoiding crossing knees or ankles, and sitting up straight, is crucial for overall well-being.

## Project Goals
### New Features:
Determine the position of the neck.
Identify if feet are on the ground or dangling.
Detect if legs are crossed.
Provide recommendations for posture improvement to reduce the risk of back and neck pain.
### User Interface:
Develop an easy-to-use UI for users to view their posture status and receive corresponding recommendations.

## Expected Outcomes
Design a seated posture assessment interface that evaluates the alignment of the back, feet, knees, and neck. Provide personalized recommendations to minimize the risk of neck and back discomfort.

## Model Architecture
The project uses a VGG-like architecture with a multi-stage approach (stages 1 to 6) to progressively refine predictions. The model focuses on detecting keypoints on the human body, including joints like the head, shoulders, elbows, wrists, hips, knees, and ankles. It incorporates branches for both Part Affinity Fields (PAF) and confidence maps. Predictions from prior stages are concatenated with the input for iterative refinement. The model is designed for training with additional inputs such as vector weights and heat weights, utilizing ReLU activation, concatenation, and multiplication operations.

## Technologies Used
Frontend: Vue.js

Database: Firebase

Backend: Python API via OpenPose

Model: Keras/TensorFlow

Computer Vision: OpenCV

Development Environment: Jupyter Notebook

## Contributors

The original capstone was built by:

1. [Michael Nweke](https://github.com/m-nweke)
2. [Ally Ryan](https://github.com/aerc4d)
3. [Parisha Rathod](https://github.com/parisha8994)

**Ally and Parisha contributed to v1 only.** Their work is preserved in the git history and in
`docs/archive/`, and their copyright stands, but they are not involved in the v2 rewrite — it is
maintained solely by Michael Nweke. Please do not direct v2 issues, questions, or review requests
to them.

---

## Development (v2)

New here? [`CONTRIBUTING.md`](CONTRIBUTING.md) covers setup, the architectural rule and the quality
gates. [`docs/adr/`](docs/adr/) records why the stack is what it is — start with
[ADR-0002](docs/adr/0002-mediapipe-pose.md) (the pose backend) and
[ADR-0005](docs/adr/0005-scale-invariant-metrics.md) (how the original's central correctness defect
is fixed).

### Where the rewrite is

Epics are tracked in Jira project `OP`; the plan they came from is
[`docs/V2-PLAN.md`](docs/V2-PLAN.md).

- **A — Foundation** · done. Workspace, tooling, CI, ADRs, archive.
- **B — Pose backend** · done. MediaPipe adapter, fake backend, checksum-pinned weights, landmark
  CLI.
- **C — Rules engine** · nearly done. Seven metrics, the report, the shared threshold spec and the
  property/boundary/golden suites are merged. Outstanding: the extended scientific property suite
  (mirror consistency, physical domains, confidence monotonicity) and the evaluation-data
  contract.
- **D — Walking skeleton** · not started. `apps/api` is a stub with no routes, and the React
  dashboard still renders placeholder results behind a `setTimeout`.
- **E–H** · not started.

The MediaPipe portability spike passed on both `linux/amd64` and `linux/arm64`, so the ONNX
MoveNet fallback the plan held in reserve was cancelled rather than built
([ADR-0002](docs/adr/0002-mediapipe-pose.md)).

Python packaging is a [`uv`](https://docs.astral.sh/uv/) workspace — one lockfile, editable
local packages, no `requirements.txt`.

```bash
# 1. Install uv (https://docs.astral.sh/uv/getting-started/installation/)
brew install uv

# 2. The workspace targets Python 3.12. Recent macOS ships 3.14, which has no
#    TensorFlow/MediaPipe wheels, so install the pinned interpreter explicitly:
uv python install 3.12

# 3. Install every workspace member, editable, into one environment
uv sync --all-packages
```

Then:

```bash
uv run ruff check .              # lint
uv run ruff format .             # format
uv run mypy packages apps        # strict type check
uv run pytest -m "not model"     # tests, skipping those needing real model weights
```

The rules-engine and adapter suites — 516 tests — run in about a second and a half, with no model
download, no container and no database.

### Seeing it work

There is no web stack yet, but the engine is demoable from the command line. The fake backend
needs nothing installed beyond the workspace:

```bash
uv run python -m pose_backends.cli --backend fake --preset hunchback --report
```

```
score          70/100
assessed       7 of 7 metrics

Findings
  [major] Your torso is leaning 32° forward. Try bringing your hips back into the chair so your
          back is supported.
          confidence 0.95  (trunk_inclination_deg)
  ...
```

`--preset` takes `straight`, `hunchback`, `reclined`, `kneeling` or `partial_occlusion`. Drop
`--report` for the raw landmark table, add `--json` for machine-readable output, and note that
`partial_occlusion` reports honest *gaps* rather than a verdict — the behaviour the original
engine got wrong.

For a real photograph you need the weights and the inference stack:

```bash
make fetch-model                                       # pinned SHA256, writes to models/
uv pip install "mediapipe==0.10.18"                    # optional extra, ~857 MB
uv run python -m pose_backends.cli fixtures/images/desk_hunch.jpeg --report
```

Exit codes are distinct on purpose: `0` a report was printed, `1` no pose was detected in the
image, `2` the backend could not run.

### Frontend

The React app lives in `apps/web` and has its own npm toolchain — it is not part of the uv
workspace.

```bash
cd apps/web
npm ci                    # exact lockfile install, same as CI

npm run dev               # dev server on :5173
npm run lint              # oxlint
npm run format:check      # prettier
npm run typecheck         # tsc, strict
npm run test              # vitest
npm run test:coverage     # vitest with the 70% floor CI enforces
npm run test:e2e          # playwright, against a production build
npm run build             # production build
```

Authentication is a placeholder. `apps/web/src/auth` exports an in-memory implementation that
really registers accounts and really rejects wrong passwords, but stores everything in the tab
and nothing on a server. Epic E replaces it with an API-backed provider satisfying the same
`AuthContextValue` interface; no component should need to change.

Optionally mirror the CI lint jobs on every commit:

```bash
uv tool install pre-commit && pre-commit install
```

### Continuous integration

`.github/workflows/pr.yml` runs on every pull request and on every push to `main`. Toolchain
setup lives in one composite action, `.github/actions/setup-project`, so the workflows planned
for this repo cannot drift apart on versions or cache keys.

| Job                            | Gate                                              |
| ------------------------------ | ------------------------------------------------- |
| `changes`                      | Decides which areas a change touched              |
| `lint`                         | `ruff check` + `ruff format --check`              |
| `typecheck`                    | `mypy --strict`                                   |
| `test-python (3.11 \| 3.12)`   | `pytest`, with a 95% floor on `posture-core`      |
| `web-lint`                     | oxlint + Prettier                                 |
| `web-typecheck`                | `tsc`, strict                                     |
| `web-test`                     | Vitest, 70% floor                                 |
| `web-build`                    | Production build must succeed                     |
| `web-e2e`                      | Playwright against that production build          |
| **`ci-ok`**                    | **Aggregates all of the above**                   |

**`ci-ok` is the only check the `main` ruleset should require** (OP-15). The jobs above it skip
routinely — a Python-only change skips all five `web-*` jobs, and vice versa — and a ruleset
naming them individually would depend on how GitHub treats skipped checks. Requiring the
aggregator also means a job can be renamed or split without silently dropping protection.

Path filtering is per job, never at the workflow level: a workflow that never runs never reports,
so a required check would sit pending forever and a docs-only pull request could not merge.
Changes to `pr.yml` or the composite action run *everything*, and so does every push to `main`.

Two further workflows exist.

**`scientific-validation.yml`** runs on pull requests, pushes to `main` and on demand, in three
jobs aggregated by `scientific-ok`: the invariance and degradation properties, boundary behaviour
at every threshold, the golden report corpus (regenerated and diffed, so stale snapshots cannot
ride along), and a drift check that `rules.json` and the engine's `Thresholds` still describe the
same numbers. `pr.yml` asks *did this change break the software*; this asks *is the engine still
measuring the same thing*. They fail for different reasons, which is why they are separate
workflows with separate aggregators. `scientific-ok` is **not** currently in the `main` ruleset's
required checks — only `ci-ok` is.

**`model-validation.yml`** runs on `workflow_dispatch` only. It verifies the pinned SHA256, then
runs the real MediaPipe weights over the fixture images and uploads landmark and latency
diagnostics. Deliberately unscheduled, so required CI never downloads a model.

### Layout

```
packages/posture-core    pure rules engine — numpy only, no I/O, no globals, no frameworks
packages/posture-spec    rules.json — every threshold as data, plus the loader that parses it
packages/pose-backends   inference adapters behind a Protocol (the heavy, fragile dependency)
apps/api                 FastAPI service — a stub until Epic D
apps/web                 React + TypeScript frontend (own npm toolchain, not in the uv workspace)
docs/adr                 architecture decision records
docs/archive             the original capstone, preserved as audit evidence
fixtures/images          8 curated test images
models                   downloaded weights (gitignored); only checksums.txt is version controlled
```

The dependency direction is one-way — `posture-core` ← `posture-spec` ← `pose-backends` ←
`apps/api` — and nothing depends on `apps/api`. That is what lets the rules-engine suite run in
well under a second with no model, no Docker and no database. It is enforced by
`packages/posture-core/tests/test_dependency_isolation.py`, not just by convention.

`posture-spec` exists because reading a file is I/O and `posture-core` is not allowed to do any.
It also carries the drift test: a threshold that exists in `rules.json` but not in the engine's
`Thresholds` dataclass, or vice versa, is a red build rather than a discovery six weeks later
when the TypeScript mirror (Epic G) turns out to be using a stale default.

## License

[MIT](LICENSE), © 2024-2026 Michael Nweke, Ally Ryan, Parisha Rathod. Ally Ryan and Parisha Rathod
hold copyright for their v1 contributions; v2 is authored by Michael Nweke ([Contributors](#contributors)).

`docs/archive/legacy-openpose/` vendors a third-party Keras implementation of CMU OpenPose under its
own MIT licence (© 2020 Vinay Varma), preserved as audit evidence and imported by nothing. The Keras
weights that code depended on are **not** redistributed here — they were a bare Dropbox link with no
licence or checksum, which is part of why v2 uses MediaPipe instead
([ADR-0002](docs/adr/0002-mediapipe-pose.md)).
