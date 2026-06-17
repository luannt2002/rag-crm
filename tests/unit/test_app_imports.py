"""Smoke test — ensure FastAPI app imports + OpenAPI generates."""

from __future__ import annotations

import pytest


def test_app_imports() -> None:
    from ragbot.interfaces.http.app import create_app

    app = create_app()
    assert app is not None
    assert app.title == "RAGbot"


def test_openapi_routes_present() -> None:
    from ragbot.interfaces.http.app import create_app

    app = create_app()
    schema = app.openapi()
    paths = set(schema["paths"].keys())
    assert "/health" in paths
    assert "/ready" not in paths  # merged into /health
    assert "/api/ragbot/chat" in paths
    assert "/api/ragbot/documents/create" in paths
    assert "/api/ragbot/documents" in paths
    assert "/api/ragbot/documents/rechunk" in paths
    assert "/api/ragbot/jobs/{job_id}" in paths
    # Admin AI endpoints
    assert "/api/ragbot/admin/ai/providers" in paths
    assert "/api/ragbot/admin/ai/models" in paths
    assert "/api/ragbot/admin/bots/{bot_id}/bindings" in paths
    # Sync endpoints
    assert "/api/ragbot/sync/bot" in paths
    assert "/api/ragbot/sync/documents" in paths


def test_settings_load() -> None:
    from ragbot.config.settings import get_settings

    s = get_settings()
    assert s.app.name == "ragbot"
    assert s.embedding.dimension > 0


# --- Reranker preflight (earlier brutal-audit) -----------------------


def test_reranker_preflight_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """enabled=False must never raise, even with no API key set."""
    from ragbot.interfaces.http.app import _check_reranker_preflight

    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    monkeypatch.delenv("CO_API_KEY", raising=False)
    # Should not raise — RRF-only mode is a valid deployment.
    _check_reranker_preflight(enabled=False, model_name="cohere/rerank-v3.5")


def test_reranker_preflight_no_provider_warns_no_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enabled=True + model_name only (no provider=) → warn + fail-soft.

    Current Strategy+DI contract: when caller passes ``model_name`` but
    omits the ``provider=`` registry key, the preflight emits the
    ``reranker_preflight_no_provider_provided`` warning and returns
    cleanly. Production call sites pass ``provider=`` explicitly; legacy
    model-only calls degrade gracefully (NullReranker downstream).
    """
    import importlib

    # ``from ragbot.interfaces.http import app`` resolves to the FastAPI
    # instance re-exported by __init__; we need the submodule itself to
    # patch its module-level structlog logger.
    app_mod = importlib.import_module("ragbot.interfaces.http.app")

    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    monkeypatch.delenv("CO_API_KEY", raising=False)
    captured: list[tuple[str, dict[str, object]]] = []

    def _capture(event: str, **kw: object) -> None:
        captured.append((event, kw))

    monkeypatch.setattr(app_mod.logger, "warning", _capture)
    # MUST NOT raise — preflight is fail-soft when no provider given.
    app_mod._check_reranker_preflight(
        enabled=True, model_name="cohere/rerank-v3.5",
    )
    events = [evt for evt, _ in captured]
    assert "reranker_preflight_no_provider_provided" in events, (
        f"expected reranker_preflight_no_provider_provided warning, got {events}"
    )


def test_reranker_preflight_cohere_with_key_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """COHERE_API_KEY present → no raise."""
    from ragbot.interfaces.http.app import _check_reranker_preflight

    monkeypatch.setenv("COHERE_API_KEY", "test-key-not-real")
    _check_reranker_preflight(enabled=True, model_name="cohere/rerank-v3.5")


def test_reranker_preflight_cohere_with_co_api_key_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy CO_API_KEY also accepted."""
    from ragbot.interfaces.http.app import _check_reranker_preflight

    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    monkeypatch.setenv("CO_API_KEY", "test-key-not-real")
    _check_reranker_preflight(enabled=True, model_name="cohere/rerank-v3.5")


def test_reranker_preflight_jina_provider_missing_key_fail_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """jina provider + no JINA_API_KEY → fail-soft via registry (NullReranker).

    Current contract: registry's ``build_reranker`` catches the strategy's
    ``ValueError`` and returns ``NullReranker``. Preflight therefore does
    NOT raise — the silent-degrade is intentional graceful degradation
    (boot continues, ops sees ``reranker_strategy_init_failed`` log).
    Caller passes ``provider=`` explicitly here so we exercise the
    registry-driven path (not the no-provider warning branch).
    """
    from ragbot.interfaces.http.app import _check_reranker_preflight

    monkeypatch.delenv("JINA_API_KEY", raising=False)
    monkeypatch.delenv("RERANKER_JINA_API_KEY", raising=False)
    monkeypatch.delenv("PROVIDER_API_KEYS_JSON", raising=False)
    # MUST NOT raise — registry swallows missing-key ValueError into NullReranker.
    _check_reranker_preflight(
        enabled=True,
        model_name="jina_ai/jina-reranker-v2",
        provider="jina",
    )


def test_reranker_preflight_viranker_local_no_key_required() -> None:
    """ViRanker is local; no remote credential check."""
    from ragbot.interfaces.http.app import _check_reranker_preflight

    _check_reranker_preflight(enabled=True, model_name="viranker/vi-rerank")
