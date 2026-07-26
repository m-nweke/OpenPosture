# OpenPosture - Sitting Posture Feedback System

> **⚠️ This README describes the original v1 capstone and is being replaced.**
> The project is mid-rewrite. Sections below still describe the Vue frontend and the
> TensorFlow/Keras OpenPose model, both of which have been removed. See
> [`docs/V2-PLAN.md`](docs/V2-PLAN.md) for what is being built and
> [`docs/FINDINGS.md`](docs/FINDINGS.md) for the audit of what was here before.
> A full rewrite lands in OP-113.

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
1. [Michael Nweke](https://github.com/m-nweke)
2. [Ally Ryan](https://github.com/aerc4d)
3. [Parisha Rathod](https://github.com/parisha8994)

---

## Development (v2)

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

### Layout

```
packages/posture-core    pure rules engine — numpy only, no I/O, no globals, no frameworks
packages/pose-backends   inference adapters behind a Protocol (the heavy, fragile dependency)
apps/api                 FastAPI service — depends on both
apps/web                 React + TypeScript frontend (own npm toolchain, not in the uv workspace)
docs/archive             the original capstone, preserved as audit evidence
fixtures/images          8 curated test images
```

The dependency direction is one-way — `posture-core` ← `pose-backends` ← `apps/api` — and
nothing depends on `apps/api`. That is what lets the rules-engine suite run in well under a
second with no model, no Docker and no database. It is enforced by
`packages/posture-core/tests/test_dependency_isolation.py`, not just by convention.
