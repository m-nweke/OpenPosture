"""Settings as one typed, validated object, read once at startup.

The legacy service had no configuration layer at all: `API/app.py` hardcoded its Firebase bucket
and its CORS policy (`origins: '*'`) as literals, so changing either meant editing code, and an
invalid value could not be detected because nothing ever validated one.

Two properties matter here and both are about *when* a mistake surfaces.

**Validation happens at startup, not at first use.** A misspelled log level should stop the
process immediately with the field name in the message, not raise from inside a request handler
an hour later. pydantic-settings does that by construction, which is most of why it is worth a
dependency over `os.environ.get`.

**Nothing reads the environment except this module.** Scattered `os.environ` lookups are how a
setting ends up with two different defaults in two different places. Everything else takes a
:class:`Settings` instance as an argument.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pose_backends.fake import BACKEND_NAME as FAKE_BACKEND
from pose_backends.fake import PosePreset
from pose_backends.mediapipe_backend import BACKEND_NAME as MEDIAPIPE_BACKEND

__all__ = ["ENV_PREFIX", "Environment", "PoseBackendName", "Settings", "get_settings"]

ENV_PREFIX: Final = "OPENPOSTURE_"
"""Namespaced so the container's environment cannot collide with ours.

``LOG_LEVEL`` is a name half the ecosystem uses; ``OPENPOSTURE_LOG_LEVEL`` is ours alone.
"""

Environment = Literal["development", "test", "production"]

LogLevel = Literal["debug", "info", "warning", "error", "critical"]

PoseBackendName = Literal["mediapipe", "fake"]
"""Which inference adapter to build.

Spelled out as a literal rather than derived from the registry's names so that an unknown value
is rejected by *configuration* validation, with the field named, instead of raising from inside
`create_backend` during startup. The assertion below keeps the two in step.
"""

# The literal above duplicates names the registry owns. Cheap insurance that a rename there is a
# failed import here rather than a config field that silently no longer matches anything.
assert (MEDIAPIPE_BACKEND, FAKE_BACKEND) == ("mediapipe", "fake")


class Settings(BaseSettings):
    """Everything the service reads from its environment.

    Deliberately small. Fields arrive with the tickets that need them — the database URL in
    OP-50, the storage credentials in OP-41, the Anthropic key in Epic F — rather than being
    declared speculatively now and defaulted to something meaningless in the meantime.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        # An unrecognised OPENPOSTURE_* variable is a typo, and a typo in configuration is
        # exactly the failure this class exists to catch. Silently ignoring it means the
        # deployment runs with the default while its operator believes otherwise.
        extra="forbid",
        # Environments are conventionally shouted (`OPENPOSTURE_LOG_LEVEL`); fields are not.
        case_sensitive=False,
    )

    environment: Environment = Field(
        default="development",
        description="Which deployment this is. Governs documentation exposure and log format.",
    )

    log_level: LogLevel = Field(
        default="info",
        description="Minimum severity emitted by structlog.",
    )

    json_logs: bool | None = Field(
        default=None,
        description=(
            "Emit JSON log lines instead of the human-readable console renderer. "
            "Defaults to true outside development, where something is collecting them."
        ),
    )

    request_id_header: str = Field(
        default="X-Request-ID",
        min_length=1,
        description=(
            "Inbound header carrying a caller-supplied correlation ID, echoed on every response."
        ),
    )

    pose_backend: PoseBackendName = Field(
        default="mediapipe",
        description=(
            "Which inference adapter to load at startup. `fake` runs the whole application with "
            "no model at all, which is what lets CI and the container smoke test work offline."
        ),
    )

    pose_backend_preset: PosePreset = Field(
        default=PosePreset.STRAIGHT,
        description="Scenario the fake backend returns. Ignored by `mediapipe`.",
    )

    model_path: Path | None = Field(
        default=None,
        description=(
            "Override the model location. `None` defers to the backend's own default, which is "
            "`MODEL_PATH` and then `models/pose_landmarker_full.task`."
        ),
    )

    @field_validator("request_id_header")
    @classmethod
    def _reject_blank_header(cls, value: str) -> str:
        """`min_length` alone accepts `"   "`, which produces an unusable header name.

        A whitespace header would be rejected by the ASGI server at response time — one failure
        per request, far from its cause — rather than at startup, which is the whole point of
        validating here.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def emit_json_logs(self) -> bool:
        """Explicit setting if given, otherwise structured everywhere but a developer's terminal."""
        if self.json_logs is not None:
            return self.json_logs
        return self.environment != "development"

    @property
    def docs_url(self) -> str | None:
        """`/docs` is a development affordance, not a production endpoint.

        Returning ``None`` is how FastAPI is told to omit the route entirely. The OpenAPI schema
        the frontend generates its types from (OP-45) is produced from the app object at build
        time, so nothing downstream depends on the route being served in production.
        """
        return None if self.is_production else "/docs"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, constructed once.

    Cached because reading and validating the environment on every request would be waste, and
    because two `Settings()` calls disagreeing after someone mutated `os.environ` mid-run is a
    debugging session nobody needs.

    Tests do not use this. They construct `Settings(...)` directly and hand it to
    :func:`~openposture_api.main.create_app`, which is the entire reason the factory takes one.
    """
    return Settings()
