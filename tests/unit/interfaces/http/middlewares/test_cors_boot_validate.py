"""Boot-time CORS allow_origins validator.

SEC-13 in security audit 20260516: literal ``"*"`` CORS pattern allowed
through ``APP_CORS_ALLOWED_ORIGINS='["*"]'`` in production tenants would
emit ``Access-Control-Allow-Origin: *`` together with
``Access-Control-Allow-Credentials: true`` — CSRF amplifier.

The settings validator must:

* Reject ``"*"`` when ``APP_ENV`` is one of {uat, staging, production}.
* Tolerate ``"*"`` in ``development`` (operator's local box) but emit a
  log warning so it surfaces at boot.
* Reject empty origin list in non-dev (services that publish CORS need
  at least one origin).
* Leave the in-dev empty case alone — dev may run same-origin only.

Tests load ``AppSettings`` fresh with patched env vars so each case is
hermetic; pydantic-settings caches via ``BaseSettings`` constructor only.
"""

from __future__ import annotations

import importlib
import logging
import os

import pytest

from ragbot.config import settings as settings_mod


def _reload_settings_module() -> object:
    """Reload the settings module so env-var changes take effect.

    pydantic-settings reads env at ``BaseSettings()`` construction time
    and the module-level ``@lru_cache`` cache holds the first call. We
    force a fresh import + cache clear so ``APP_ENV`` / ``APP_CORS_*``
    overrides applied via monkeypatch land cleanly.
    """
    importlib.reload(settings_mod)
    return settings_mod


@pytest.mark.parametrize("env", ["production", "staging", "uat"])
def test_cors_rejects_wildcard_in_non_dev(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    """APP_ENV=non-dev + allow_origins=['*'] → ValueError at settings load."""
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("APP_CORS_ALLOWED_ORIGINS", '["*"]')
    mod = _reload_settings_module()
    with pytest.raises(ValueError, match="wildcard"):
        mod.AppSettings()


def test_cors_warns_wildcard_in_dev(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """APP_ENV=development + '*' → log warning, allow boot."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_CORS_ALLOWED_ORIGINS", '["*"]')
    mod = _reload_settings_module()
    with caplog.at_level(logging.WARNING, logger="ragbot.config.settings"):
        app = mod.AppSettings()
    assert app.cors_allowed_origins == '["*"]'
    # The warning is surfaced at logger boot — checking caplog records is
    # the only contract that doesn't tie us to a specific log line.
    assert any(
        "wildcard" in (r.getMessage() or "").lower() for r in caplog.records
    ), f"expected wildcard warning in dev; got records: {[r.getMessage() for r in caplog.records]}"


@pytest.mark.parametrize("env", ["production", "staging", "uat"])
def test_cors_empty_origins_rejected_in_non_dev(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    """APP_ENV=non-dev + allow_origins=[] → ValueError (need at least one origin)."""
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("APP_CORS_ALLOWED_ORIGINS", "[]")
    mod = _reload_settings_module()
    with pytest.raises(ValueError, match="empty|at least one"):
        mod.AppSettings()


def test_cors_empty_origins_allowed_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty origin list is OK in dev (same-origin only setups)."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_CORS_ALLOWED_ORIGINS", "[]")
    mod = _reload_settings_module()
    app = mod.AppSettings()
    assert app.cors_allowed_origins == "[]"


def test_cors_explicit_origins_accepted_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """APP_ENV=production + concrete origin list → boot OK."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "APP_CORS_ALLOWED_ORIGINS", '["https://app.example.com", "https://*.example.com"]',
    )
    mod = _reload_settings_module()
    app = mod.AppSettings()
    assert "example.com" in app.cors_allowed_origins
    assert "*" not in app.cors_allowed_origins.replace("*.example.com", "")


def test_cors_invalid_json_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed JSON in APP_CORS_ALLOWED_ORIGINS → ValueError."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_CORS_ALLOWED_ORIGINS", "not-json")
    mod = _reload_settings_module()
    with pytest.raises(ValueError, match="json|list|origin"):
        mod.AppSettings()


@pytest.fixture(autouse=True)
def _restore_settings_module() -> object:
    """Restore the settings module after each test (other suites import it)."""
    yield
    # Drop test env vars so fresh import sees the real .env defaults.
    for key in ("APP_ENV", "APP_CORS_ALLOWED_ORIGINS"):
        os.environ.pop(key, None)
    importlib.reload(settings_mod)
