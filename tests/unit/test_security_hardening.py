"""Tests for P0 security hardening."""

import pytest


class TestJwtSecretValidation:
    def test_dev_env_allows_empty_api_token(self):
        """Development env can work without APP_API_TOKEN."""
        from ragbot.config.settings import AppSettings

        s = AppSettings(env="development", api_token="")
        assert s.api_token == ""

    def test_settings_loads_api_token_from_env(self, monkeypatch):
        monkeypatch.setenv("APP_API_TOKEN", "my-secure-token-123")
        monkeypatch.setenv("APP_ENV", "development")
        from ragbot.config.settings import AppSettings

        s = AppSettings()
        assert s.api_token == "my-secure-token-123"


class TestHmacSecretValidation:
    def test_weak_hmac_secret_rejected_in_production(self):
        """Weak HMAC secrets must be rejected in production."""
        from ragbot.config.settings import SecretSettings

        with pytest.MonkeyPatch.context() as m:
            m.setenv("APP_ENV", "production")
            m.setenv("TENANT_HMAC_SECRET", "change-me-in-prod")
            with pytest.raises(Exception):
                SecretSettings()

    def test_weak_hmac_secret_allowed_in_dev(self):
        """Weak HMAC secrets are OK in development."""
        from ragbot.config.settings import SecretSettings

        with pytest.MonkeyPatch.context() as m:
            m.setenv("APP_ENV", "development")
            m.setenv("TENANT_HMAC_SECRET", "change-me-in-prod")
            s = SecretSettings()
            assert s.tenant_hmac_secret == "change-me-in-prod"

    def test_strong_hmac_secret_accepted_everywhere(self):
        from ragbot.config.settings import SecretSettings

        with pytest.MonkeyPatch.context() as m:
            m.setenv("APP_ENV", "production")
            m.setenv("TENANT_HMAC_SECRET", "a-very-strong-secret-abc123xyz-OK")
            s = SecretSettings()
            assert s.tenant_hmac_secret == "a-very-strong-secret-abc123xyz-OK"


class TestAuthBypass:
    def test_ragbot_prefix_not_in_public_bypass(self):
        """The /ragbot prefix must NOT bypass auth."""
        import inspect

        from ragbot.interfaces.http.middlewares.tenant_context import TenantContextMiddleware

        source = inspect.getsource(TenantContextMiddleware.dispatch)
        assert 'startswith("/ragbot")' not in source, (
            "CRITICAL: /ragbot prefix bypasses auth for all API endpoints"
        )


class TestAuthBypassRobust:
    def test_public_paths_are_explicit(self):
        """Only specific known paths bypass auth — no wildcards."""
        import inspect

        from ragbot.interfaces.http.middlewares.tenant_context import TenantContextMiddleware

        source = inspect.getsource(TenantContextMiddleware.dispatch)
        # These dangerous patterns must NOT exist:
        dangerous = [
            'endswith(".html")',
            'startswith("/ragbot")',
            'endswith("/tokens/self")',
        ]
        for pattern in dangerous:
            assert pattern not in source, (
                f"Dangerous auth bypass pattern found: {pattern}"
            )
