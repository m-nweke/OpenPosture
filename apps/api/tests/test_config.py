"""Settings validate at startup, and say which field was wrong.

The acceptance criterion for OP-39 is specifically that a bad value *prevents startup and names
the offending field*. A configuration error that surfaces as a 500 on the first request that
happens to read the setting is the failure mode this layer exists to remove.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openposture_api.config import ENV_PREFIX, Settings, get_settings


class TestDefaults:
    def test_a_bare_environment_produces_a_usable_development_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No environment variables at all must still boot — that is `docker compose up`."""
        monkeypatch.delenv(f"{ENV_PREFIX}ENVIRONMENT", raising=False)
        settings = Settings(_env_file=None)  # type: ignore[call-arg]

        assert settings.environment == "development"
        assert settings.log_level == "info"
        assert settings.request_id_header == "X-Request-ID"

    def test_development_gets_readable_logs_and_docs(self) -> None:
        settings = Settings(environment="development", _env_file=None)  # type: ignore[call-arg]

        assert settings.emit_json_logs is False
        assert settings.docs_url == "/docs"
        assert settings.is_production is False

    def test_production_gets_json_logs_and_no_docs(self) -> None:
        settings = Settings(environment="production", _env_file=None)  # type: ignore[call-arg]

        assert settings.emit_json_logs is True
        assert settings.docs_url is None
        assert settings.is_production is True

    def test_an_explicit_log_format_beats_the_environment_default(self) -> None:
        """The derived default must remain overridable, or it is a hardcoded rule."""
        settings = Settings(environment="production", json_logs=False, _env_file=None)  # type: ignore[call-arg]

        assert settings.emit_json_logs is False


class TestValidation:
    def test_an_unknown_environment_names_the_field(self) -> None:
        with pytest.raises(ValidationError) as caught:
            Settings(environment="staging", _env_file=None)  # type: ignore[arg-type, call-arg]

        assert "environment" in str(caught.value)

    def test_a_misspelled_log_level_names_the_field(self) -> None:
        """`warn` is the plausible typo — every other logging library accepts it."""
        with pytest.raises(ValidationError) as caught:
            Settings(log_level="warn", _env_file=None)  # type: ignore[arg-type, call-arg]

        assert "log_level" in str(caught.value)

    def test_an_unrecognised_setting_is_rejected_rather_than_ignored(self) -> None:
        """A typo'd variable must not leave the service running on the default silently."""
        with pytest.raises(ValidationError) as caught:
            Settings(loglevel="debug", _env_file=None)  # type: ignore[call-arg]

        assert "loglevel" in str(caught.value)

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_a_blank_request_id_header_is_rejected(self, blank: str) -> None:
        """An unusable header name must fail here, not once per request at response time."""
        with pytest.raises(ValidationError) as caught:
            Settings(request_id_header=blank, _env_file=None)  # type: ignore[call-arg]

        assert "request_id_header" in str(caught.value)

    def test_a_padded_header_name_is_trimmed_rather_than_rejected(self) -> None:
        settings = Settings(request_id_header="  X-Trace-Id  ", _env_file=None)  # type: ignore[call-arg]

        assert settings.request_id_header == "X-Trace-Id"


class TestEnvironmentReading:
    def test_settings_come_from_the_prefixed_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(f"{ENV_PREFIX}ENVIRONMENT", "production")
        monkeypatch.setenv(f"{ENV_PREFIX}LOG_LEVEL", "error")

        settings = Settings(_env_file=None)  # type: ignore[call-arg]

        assert settings.environment == "production"
        assert settings.log_level == "error"

    def test_an_unprefixed_variable_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`LOG_LEVEL` belongs to half the ecosystem; the prefix is what stops a collision."""
        monkeypatch.setenv("LOG_LEVEL", "critical")
        monkeypatch.delenv(f"{ENV_PREFIX}LOG_LEVEL", raising=False)

        settings = Settings(_env_file=None)  # type: ignore[call-arg]

        assert settings.log_level == "info"

    def test_the_process_settings_are_read_once(self) -> None:
        """Cached, so two callers cannot disagree after someone mutates the environment."""
        get_settings.cache_clear()
        try:
            assert get_settings() is get_settings()
        finally:
            get_settings.cache_clear()
