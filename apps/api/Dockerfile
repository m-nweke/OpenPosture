# The API image.
#
# Two stages: one that resolves and installs the workspace, one that runs it. The build stage
# keeps uv, the compiler toolchain and the lockfile out of the final image, which is both smaller
# and a smaller attack surface.
#
# Everything here is pinned. A `latest` base or an unpinned model download would mean two builds
# of the same commit produce different images, which makes "it worked yesterday" unanswerable.

# ---------------------------------------------------------------------------
# Stage 1 — dependencies
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# uv from its own distroless image rather than `pip install uv`: one fewer resolution step, and
# the version is pinned by the tag instead of by whatever pip finds today.
COPY --from=ghcr.io/astral-sh/uv:0.9.29 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Manifests first, sources second. Docker caches by layer, so an edit to a `.py` file re-runs
# only the second install — the ~857 MB mediapipe download is not repeated for a one-line change.
COPY pyproject.toml uv.lock ./
COPY packages/posture-core/pyproject.toml packages/posture-core/
COPY packages/posture-spec/pyproject.toml packages/posture-spec/
COPY packages/pose-backends/pyproject.toml packages/pose-backends/
COPY apps/api/pyproject.toml apps/api/

# `--frozen` refuses to update the lockfile. A build that silently re-resolves is a build whose
# dependency set is decided at build time rather than at review time.
RUN --mount=type=cache,target=/root/.cache/uv \
    mkdir -p packages/posture-core/src/posture_core \
             packages/posture-spec/src/posture_spec \
             packages/pose-backends/src/pose_backends \
             apps/api/src/openposture_api \
    && touch packages/posture-core/src/posture_core/__init__.py \
             packages/posture-spec/src/posture_spec/__init__.py \
             packages/pose-backends/src/pose_backends/__init__.py \
             apps/api/src/openposture_api/__init__.py \
    && uv sync --frozen --no-dev --extra serve --extra inference --package openposture-api

COPY packages/ packages/
COPY apps/api/ apps/api/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra serve --extra inference --package openposture-api

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# libGL and libglib are cv2's shared-library dependencies. Their absence is the single most
# common "works locally, ImportError in Docker" failure with OpenCV, and it surfaces as
# `libGL.so.1: cannot open shared object file` at import time rather than at install time.
# `opencv-python-headless` avoids most of it; mediapipe pulls the non-headless build, so the
# libraries are installed rather than hoped for.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Non-root. A container that runs as root shares that uid with the host through any bind mount,
# and there is nothing in this service that needs it.
RUN useradd --create-home --uid 10001 openposture

WORKDIR /app

COPY --from=builder --chown=openposture:openposture /app/.venv /app/.venv
COPY --from=builder --chown=openposture:openposture /app/packages /app/packages
COPY --from=builder --chown=openposture:openposture /app/apps/api /app/apps/api

# The model, fetched at build time with the hash pinned in models/checksums.txt. `ADD --checksum`
# fails the build if the bytes differ, so a compromised or truncated download cannot produce a
# working image — the legacy project's weights came from a bare Dropbox link with no checksum at
# all, which is part of why ADR-0002 moved to MediaPipe.
#
# Keep this hash in step with models/checksums.txt. It is duplicated because Dockerfiles cannot
# read a file at build time, and the containers workflow greps both to prove they agree.
ADD --checksum=sha256:5134a3aad27a58b93da0088d431f366da362b44e3ccfbe3462b3827a839011b1 \
    --chown=openposture:openposture \
    https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task \
    /app/models/pose_landmarker_full.task

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODEL_PATH=/app/models/pose_landmarker_full.task \
    OPENPOSTURE_STORAGE_ROOT=/app/var/storage

RUN mkdir -p /app/var/storage && chown -R openposture:openposture /app/var

USER openposture

EXPOSE 8000

# `/health` rather than `/health/ready`: this answers "is the process alive", which is the
# question a container healthcheck asks. Readiness is the orchestrator's separate concern, and
# wiring it here would restart a container that is merely still loading its model.
HEALTHCHECK --interval=10s --timeout=3s --start-period=40s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()"

CMD ["uvicorn", "openposture_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
