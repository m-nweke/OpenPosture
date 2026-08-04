"""Settings validate at startup, and say which field was wrong.

The acceptance criterion for OP-39 is specifically that a bad value *prevents startup and names
the offending field*. A configuration error that surfaces as a 500 on the first request that
happens to read the setting is the failure mode this layer exists to remove.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from openposture_api.config import DEV_JWT_SECRET, ENV_PREFIX, Settings, get_settings

A_REAL_SECRET = "a-secret-that-is-not-the-one-in-the-repository"
"""Stands in for what a real deployment would set. Any value but :data:`DEV_JWT_SECRET` will do —
which is precisely the property the production guard checks."""


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
        settings = Settings(
            environment="production", jwt_secret=SecretStr(A_REAL_SECRET), _env_file=None
        )  # type: ignore[call-arg]

        assert settings.emit_json_logs is True
        assert settings.docs_url is None
        assert settings.is_production is True

    def test_an_explicit_log_format_beats_the_environment_default(self) -> None:
        """The derived default must remain overridable, or it is a hardcoded rule."""
        settings = Settings(  # type: ignore[call-arg]
            environment="production",
            json_logs=False,
            jwt_secret=SecretStr(A_REAL_SECRET),
            _env_file=None,
        )

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


class TestJwtSecret:
    """OP-55's boot guard: the dev signing key must never reach production.

    The key is in the repository, so a production process running on it can have its access
    tokens forged by anyone who can `git clone`. Nothing about the running service looks wrong
    when this happens — the tokens verify — so startup is the only place it can be caught.
    """

    def test_development_boots_on_the_default_key(self) -> None:
        """The convenience half of the trade-off: `docker compose up` needs no secret."""
        settings = Settings(environment="development", _env_file=None)  # type: ignore[call-arg]

        assert settings.jwt_secret.get_secret_value() == DEV_JWT_SECRET

    def test_production_refuses_to_boot_on_the_default_key(self) -> None:
        with pytest.raises(ValidationError) as caught:
            Settings(environment="production", _env_file=None)  # type: ignore[call-arg]

        assert f"{ENV_PREFIX}JWT_SECRET" in str(caught.value)

    def test_production_boots_once_it_has_its_own_key(self) -> None:
        """The guard must reject *the* default, not every production configuration."""
        settings = Settings(
            environment="production", jwt_secret=SecretStr(A_REAL_SECRET), _env_file=None
        )  # type: ignore[call-arg]

        assert settings.jwt_secret.get_secret_value() == A_REAL_SECRET

    @pytest.mark.parametrize("short", ["", "hunter2", "x" * 31])
    def test_a_key_shorter_than_the_hash_is_rejected(self, short: str) -> None:
        """RFC 7518 §3.2: an HMAC key must be at least the hash's output size — 32 bytes here.

        A shorter key is the weakest part of the scheme rather than the algorithm, and it is
        brute-forceable offline from one captured token, after which every token is forgeable.
        PyJWT emits a warning about this; a warning that nobody is watching is not a control.
        """
        with pytest.raises(ValidationError) as caught:
            Settings(jwt_secret=SecretStr(short), _env_file=None)  # type: ignore[call-arg]

        assert "jwt_secret" in str(caught.value)

    def test_the_shipped_default_clears_the_length_bar(self) -> None:
        """Otherwise development would be unable to boot on its own default."""
        assert len(DEV_JWT_SECRET) >= 32

    def test_the_secret_does_not_appear_in_the_settings_repr(self) -> None:
        """`SecretStr` is why a logged or traceback-printed Settings cannot leak the key."""
        settings = Settings(jwt_secret=SecretStr(A_REAL_SECRET), _env_file=None)  # type: ignore[call-arg]

        assert A_REAL_SECRET not in repr(settings)


class TestCookiePolicy:
    def test_development_serves_the_cookie_over_plain_http(self) -> None:
        """`Secure` on a localhost http origin means the browser silently drops the cookie."""
        settings = Settings(environment="development", _env_file=None)  # type: ignore[call-arg]

        assert settings.use_secure_cookies is False

    def test_production_requires_https_for_the_cookie(self) -> None:
        settings = Settings(
            environment="production", jwt_secret=SecretStr(A_REAL_SECRET), _env_file=None
        )  # type: ignore[call-arg]

        assert settings.use_secure_cookies is True

    def test_an_explicit_cookie_policy_beats_the_environment_default(self) -> None:
        settings = Settings(  # type: ignore[call-arg]
            environment="development", refresh_cookie_secure=True, _env_file=None
        )

        assert settings.use_secure_cookies is True


class TestEnvironmentReading:
    def test_settings_come_from_the_prefixed_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(f"{ENV_PREFIX}ENVIRONMENT", "production")
        monkeypatch.setenv(f"{ENV_PREFIX}LOG_LEVEL", "error")
        monkeypatch.setenv(f"{ENV_PREFIX}JWT_SECRET", A_REAL_SECRET)

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


class TestRateLimiting:
    """OP-59: the two limits and the proxy-trust knob are all tunable, not hardcoded."""

    def test_the_documented_defaults_are_what_ships(self) -> None:
        settings = Settings(_env_file=None)  # type: ignore[call-arg]

        assert settings.login_rate_limit == "5/minute"
        assert settings.analyses_rate_limit == "10/minute"
        assert settings.trusted_proxy_hops == 0

    def test_both_limits_are_overridable_without_a_deploy(self) -> None:
        settings = Settings(  # type: ignore[call-arg]
            login_rate_limit="3/second",
            analyses_rate_limit="1/hour",
            trusted_proxy_hops=2,
            _env_file=None,
        )

        assert settings.login_rate_limit == "3/second"
        assert settings.analyses_rate_limit == "1/hour"
        assert settings.trusted_proxy_hops == 2

    @pytest.mark.parametrize("field", ["login_rate_limit", "analyses_rate_limit"])
    def test_a_malformed_limit_names_the_field_at_startup(self, field: str) -> None:
        """The same principle as the log-level and header checks above: a typo here must stop
        the process, not silently leave the route unlimited on the first request that reads it."""
        with pytest.raises(ValidationError) as caught:
            Settings(**{field: "five per minuet"}, _env_file=None)  # type: ignore[arg-type, call-arg]

        assert field in str(caught.value)

    def test_a_negative_proxy_hop_count_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as caught:
            Settings(trusted_proxy_hops=-1, _env_file=None)  # type: ignore[call-arg]

        assert "trusted_proxy_hops" in str(caught.value)
