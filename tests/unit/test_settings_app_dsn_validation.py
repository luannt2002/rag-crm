"""Validate the runtime DSN split (DATABASE_URL vs DATABASE_URL_APP).

Mock-only — no live database. Each case scrubs the ``DATABASE_*`` and
escape envs first so the host environment cannot leak into the test.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from ragbot.config.settings import DatabaseSettings, Settings
from ragbot.infrastructure.db.engine import create_engine_app
from ragbot.shared.constants import (
    RAGBOT_ALLOW_SUPERUSER_RUNTIME_ENV,
    RAGBOT_ALLOW_SUPERUSER_RUNTIME_VALUE,
)

_ADMIN_DSN = "postgresql+asyncpg://postgres:adminpw@localhost:5432/ragbot"
_APP_DSN = "postgresql+asyncpg://ragbot_app:apppw@localhost:5432/ragbot"


@pytest.fixture(autouse=True)
def _scrub_dsn_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every env var that could change the validator outcome.

    Beyond deleting the process-env vars, neutralise the dotenv source on
    both settings classes: pydantic-settings re-reads ``.env`` from disk at
    instantiation (``env_file=".env"``), so without this the host ``.env``
    (which sets ``DATABASE_URL_APP`` and the escape env) would leak straight
    back in and mask the values each case explicitly controls.
    """
    for var in (
        "DATABASE_URL",
        "DATABASE_URL_APP",
        "DATABASE_URL_SYNC",
        RAGBOT_ALLOW_SUPERUSER_RUNTIME_ENV,
    ):
        monkeypatch.delenv(var, raising=False)
    # ``Settings.database`` is built via ``default_factory=DatabaseSettings``,
    # so patching the nested class's config covers the ``Settings()`` paths too.
    monkeypatch.setitem(DatabaseSettings.model_config, "env_file", None)
    monkeypatch.setitem(Settings.model_config, "env_file", None)


def test_settings_with_url_app_set_instantiates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: explicit DATABASE_URL_APP wires url_app on DatabaseSettings."""
    monkeypatch.setenv("DATABASE_URL", _ADMIN_DSN)
    monkeypatch.setenv("DATABASE_URL_APP", _APP_DSN)

    db = DatabaseSettings()
    rendered_app = str(db.url_app)
    assert rendered_app.startswith("postgresql+asyncpg://ragbot_app")
    assert "ragbot_app:apppw" in rendered_app
    # Admin DSN remains accessible for alembic/ops paths — neither field
    # should mask the other.
    assert "postgres:adminpw" in str(db.url)


def test_url_app_missing_without_escape_env_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-loud when DATABASE_URL_APP is unset and the operator did not opt in."""
    monkeypatch.setenv("DATABASE_URL", _ADMIN_DSN)
    # DATABASE_URL_APP and the escape env are both absent (autouse scrub).

    with pytest.raises(RuntimeError) as excinfo:
        DatabaseSettings()
    err = str(excinfo.value)
    assert "DATABASE_URL_APP" in err
    assert RAGBOT_ALLOW_SUPERUSER_RUNTIME_ENV in err


def test_url_app_missing_with_escape_env_instantiates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escape env lets boot succeed; url_app stays None so engine factory falls back."""
    monkeypatch.setenv("DATABASE_URL", _ADMIN_DSN)
    monkeypatch.setenv(
        RAGBOT_ALLOW_SUPERUSER_RUNTIME_ENV,
        RAGBOT_ALLOW_SUPERUSER_RUNTIME_VALUE,
    )

    db = DatabaseSettings()
    # url_app stays unset under escape mode so the engine factory can branch
    # to the admin DSN with a structured warning at build time.
    assert db.url_app is None
    assert "postgres:adminpw" in str(db.url)


def test_create_engine_app_uses_url_app_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_engine_app must bind to url_app when the operator configured it."""
    monkeypatch.setenv("DATABASE_URL", _ADMIN_DSN)
    monkeypatch.setenv("DATABASE_URL_APP", _APP_DSN)

    settings = Settings()
    engine = create_engine_app(settings)
    try:
        rendered = engine.url.render_as_string(hide_password=False)
        assert "ragbot_app:apppw" in rendered
        assert "postgres:adminpw" not in rendered
    finally:
        engine.sync_engine.dispose()


def test_create_engine_app_falls_back_with_escape_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escape env path: engine binds to admin DSN AND emits structured warning."""
    monkeypatch.setenv("DATABASE_URL", _ADMIN_DSN)
    monkeypatch.setenv(
        RAGBOT_ALLOW_SUPERUSER_RUNTIME_ENV,
        RAGBOT_ALLOW_SUPERUSER_RUNTIME_VALUE,
    )

    settings = Settings()
    # capture_logs reroutes structlog's processor chain into a list so
    # test code sees the same event dict the runtime would emit.
    with capture_logs() as cap:
        engine = create_engine_app(settings)
    try:
        rendered = engine.url.render_as_string(hide_password=False)
        assert "postgres:adminpw" in rendered
        assert "ragbot_app" not in rendered
        fallback_events = [
            e for e in cap if e.get("event") == "engine.app_dsn_superuser_fallback"
        ]
        assert len(fallback_events) == 1, (
            f"expected exactly one fallback warning, got {cap!r}"
        )
        evt = fallback_events[0]
        assert evt["log_level"] == "warning"
        assert evt["escape_env"] == RAGBOT_ALLOW_SUPERUSER_RUNTIME_ENV
    finally:
        engine.sync_engine.dispose()
