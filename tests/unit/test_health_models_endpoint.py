"""Unit tests for the /health/models endpoint.

Coverage (10 tests, all must STAY GREEN):
1.  test_endpoint_returns_200_when_db_empty — fail-soft baseline
2.  test_endpoint_returns_200_when_db_query_raises — outer wrap survives
3.  test_endpoint_groups_purposes_correctly — embedding/rerank/llm_primary
4.  test_backcompat_purpose_alias_emits_drift_warning — 'reranker' → drift
5.  test_skip_smoke_short_circuits_probes — query-only mode
6.  test_probe_dim_mismatch_marks_unhealthy — embedding dim guard
7.  test_probe_timeout_classified_unhealthy — outer fail-soft
8.  test_unexpected_exception_classified_unhealthy — broad-except wrapper
9.  test_missing_env_api_key_emits_drift_warning — config drift detect
10. test_summary_counts_match_models_array — invariant

Tests exercise the helper functions directly + a thin TestClient harness so
we never need a real DB / Redis / Jina API.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ragbot.interfaces.http.routes.health_models import (
    STATUS_CONFIG_DRIFT,
    STATUS_DEGRADED,
    STATUS_HEALTHY,
    STATUS_UNHEALTHY,
    _classify_status,
    _detect_config_drift,
    _safe_probe,
)
from ragbot.shared.constants import (
    DEFAULT_HEALTH_MODELS_DEGRADED_LATENCY_MS,
    DEFAULT_HEALTH_MODELS_PROBE_TIMEOUT_S,
)


# ---------------------------------------------------------------------------
# Harness — boots the real app with a no-op lifespan + mocked container.
# ---------------------------------------------------------------------------


def _make_session_factory(rows: list[dict[str, Any]] | Exception) -> MagicMock:
    """Build a session_factory mock that returns ``rows`` on .execute().

    Pass a list of dicts to simulate a successful query; pass an Exception to
    simulate DB failure.
    """
    sf = MagicMock()

    @asynccontextmanager
    async def _ctx() -> AsyncIterator[Any]:
        session = MagicMock()
        if isinstance(rows, Exception):
            session.execute = AsyncMock(side_effect=rows)
        else:
            mappings_obj = MagicMock()
            mappings_obj.all.return_value = rows
            result = MagicMock()
            result.mappings.return_value = mappings_obj
            session.execute = AsyncMock(return_value=result)
        yield session

    sf.return_value = _ctx()

    def _factory_call() -> Any:  # pragma: no cover - trivial
        return _ctx()

    sf.side_effect = lambda: _ctx()
    return sf


@asynccontextmanager
async def _noop_lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Wires a fully mocked container so route handlers run without I/O."""
    mock_container = MagicMock()
    mock_settings = MagicMock()
    mock_settings.app.env = "development"
    mock_settings.app.api_token = ""
    mock_settings.app.version = "0.0.0-test"
    mock_settings.observability.prometheus_path = "/metrics"
    mock_settings.observability.log_level = "WARNING"
    mock_settings.observability.log_format = "text"
    application.state.container = mock_container
    application.state.settings = mock_settings
    application.state.dev_jwt_secret = "test-secret-for-health-models"
    yield


@pytest.fixture()
def client_factory():
    """Return a callable building a TestClient with custom container behaviour."""
    import importlib

    app_mod = importlib.import_module("ragbot.interfaces.http.app")
    original_lifespan = app_mod.lifespan  # type: ignore[attr-defined]
    app_mod.lifespan = _noop_lifespan  # type: ignore[attr-defined]

    created_clients: list[TestClient] = []

    def _make(rows: list[dict[str, Any]] | Exception) -> TestClient:
        application = app_mod.create_app()  # type: ignore[attr-defined]
        c = TestClient(application, raise_server_exceptions=False)
        c.__enter__()  # triggers lifespan → wires mock_container into state
        sf = _make_session_factory(rows)
        application.state.container.session_factory = MagicMock(return_value=sf)
        # Stub LLM router to avoid container.llm() raising.
        application.state.container.llm = MagicMock(return_value=None)
        created_clients.append(c)
        return c

    try:
        yield _make
    finally:
        for c in created_clients:
            c.__exit__(None, None, None)
        app_mod.lifespan = original_lifespan  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 1. Empty DB
# ---------------------------------------------------------------------------


def test_endpoint_returns_200_when_db_empty(client_factory) -> None:
    client = client_factory([])
    resp = client.get("/health/models?skip_smoke=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["summary"]["total_models"] == 0
    assert body["models"] == {"embedding": [], "rerank": [], "llm_primary": []}


# ---------------------------------------------------------------------------
# 2. DB query raises
# ---------------------------------------------------------------------------


def test_endpoint_returns_200_when_db_query_raises(client_factory) -> None:
    from sqlalchemy.exc import OperationalError

    err = OperationalError("stmt", {}, Exception("connection refused"))
    client = client_factory(err)
    resp = client.get("/health/models?skip_smoke=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    warnings = body["summary"]["config_drift_warnings"]
    assert any("DB query failed" in w["issue"] for w in warnings)


# ---------------------------------------------------------------------------
# 3. Purpose grouping
# ---------------------------------------------------------------------------


def test_endpoint_groups_purposes_correctly(client_factory) -> None:
    rows = [
        {
            "purpose": "embedding",
            "model_name": "text-embedding-3-small",
            "model_dim": 1536,
            "provider_code": "openai",
            "provider_name": "OpenAI",
            "api_key_ref": "OPENAI_API_KEY",
            "api_key_encrypted": None,
            "base_url": "https://api.openai.com",
            "bot_count": 3,
        },
        {
            "purpose": "rerank",
            "model_name": "jina-reranker-v3",
            "model_dim": None,
            "provider_code": "jina",
            "provider_name": "Jina AI",
            "api_key_ref": "RERANKER_JINA_API_KEY",
            "api_key_encrypted": None,
            "base_url": "https://api.jina.ai",
            "bot_count": 4,
        },
        {
            "purpose": "llm_primary",
            "model_name": "gpt-4.1-mini",
            "model_dim": None,
            "provider_code": "openai",
            "provider_name": "OpenAI",
            "api_key_ref": "OPENAI_API_KEY",
            "api_key_encrypted": None,
            "base_url": "https://api.openai.com",
            "bot_count": 4,
        },
    ]
    client = client_factory(rows)
    resp = client.get("/health/models?skip_smoke=true")
    body = resp.json()
    assert len(body["models"]["embedding"]) == 1
    assert len(body["models"]["rerank"]) == 1
    assert len(body["models"]["llm_primary"]) == 1
    assert body["models"]["embedding"][0]["model_name"] == "text-embedding-3-small"
    assert body["summary"]["total_bot_bindings"] == 11


# ---------------------------------------------------------------------------
# 4. Legacy purpose alias → drift warning
# ---------------------------------------------------------------------------


def test_backcompat_purpose_alias_emits_drift_warning(client_factory) -> None:
    rows = [
        {
            "purpose": "reranker",  # legacy
            "model_name": "jina-reranker-v3",
            "model_dim": None,
            "provider_code": "jina",
            "provider_name": "Jina AI",
            "api_key_ref": None,
            "api_key_encrypted": None,
            "base_url": "https://api.jina.ai",
            "bot_count": 1,
        },
    ]
    client = client_factory(rows)
    resp = client.get("/health/models?skip_smoke=true")
    body = resp.json()
    warnings = body["summary"]["config_drift_warnings"]
    assert any("legacy" in w["issue"].lower() for w in warnings)
    # And the row should still flow into rerank bucket via alias.
    assert len(body["models"]["rerank"]) == 1


# ---------------------------------------------------------------------------
# 5. skip_smoke short-circuits probes
# ---------------------------------------------------------------------------


def test_skip_smoke_short_circuits_probes(client_factory) -> None:
    rows = [
        {
            "purpose": "embedding",
            "model_name": "text-embedding-3-small",
            "model_dim": 1536,
            "provider_code": "openai",
            "provider_name": "OpenAI",
            "api_key_ref": "OPENAI_API_KEY",
            "api_key_encrypted": None,
            "base_url": "https://api.openai.com",
            "bot_count": 1,
        },
    ]
    client = client_factory(rows)
    resp = client.get("/health/models?skip_smoke=true")
    body = resp.json()
    entry = body["models"]["embedding"][0]
    # When skip_smoke=true we must not have called the probe — status reflects that.
    assert entry["status"] == "not_configured"
    assert entry["error"] == "skip_smoke=true"
    assert entry["latency_ms"] == 0


# ---------------------------------------------------------------------------
# 6. Embedding dim mismatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_dim_mismatch_marks_unhealthy() -> None:
    from ragbot.interfaces.http.routes.health_models import _probe_embedding

    fake_embedder = AsyncMock()
    fake_embedder.embed_batch = AsyncMock(return_value=[[0.0] * 768])  # got 768
    fake_embedder.close = AsyncMock()

    with patch(
        "ragbot.interfaces.http.routes.health_models.build_embedder",
        return_value=fake_embedder,
    ):
        result = await _probe_embedding(
            model_name="text-embedding-3-small",
            provider_code="openai",
            expected_dim=1536,  # DB says 1536
        )

    assert result["status"] == STATUS_UNHEALTHY
    assert result["dim_match_db"] is False
    assert "dim_mismatch" in (result.get("error") or "")


# ---------------------------------------------------------------------------
# 7. Timeout classified as unhealthy via outer wrap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_timeout_classified_unhealthy() -> None:
    async def _slow():
        raise asyncio.TimeoutError()

    out = await _safe_probe(_slow, provider_name="test:provider")
    assert out["status"] == STATUS_UNHEALTHY
    assert "timeout" in out["error"]
    assert out["latency_ms"] == int(DEFAULT_HEALTH_MODELS_PROBE_TIMEOUT_S * 1000)


# ---------------------------------------------------------------------------
# 8. Unexpected exception caught by broad-except wrapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_exception_classified_unhealthy() -> None:
    class CustomBoom(RuntimeError):
        pass

    async def _bad():
        raise CustomBoom("kaboom!")

    out = await _safe_probe(_bad, provider_name="test:bad")
    assert out["status"] == STATUS_UNHEALTHY
    assert "CustomBoom" in out["error"]


# ---------------------------------------------------------------------------
# 9. Missing API key env emits drift warning
# ---------------------------------------------------------------------------


def test_missing_env_api_key_emits_drift_warning() -> None:
    rows = [
        {
            "purpose": "rerank",
            "model_name": "jina-reranker-v3",
            "model_dim": None,
            "provider_code": "jina",
            "provider_name": "Jina AI",
            "api_key_ref": "DEFINITELY_NOT_SET_ENV_VAR_HEALTH_TEST",
            "api_key_encrypted": None,
            "base_url": "https://api.jina.ai",
            "bot_count": 1,
        },
    ]
    # Ensure the env var really is unset.
    os.environ.pop("DEFINITELY_NOT_SET_ENV_VAR_HEALTH_TEST", None)
    warnings = _detect_config_drift(rows)
    assert any("env var" in w["issue"] for w in warnings)


# ---------------------------------------------------------------------------
# 10. Summary counts match model array
# ---------------------------------------------------------------------------


def test_summary_counts_match_models_array(client_factory) -> None:
    rows = [
        {
            "purpose": "embedding",
            "model_name": "m1",
            "model_dim": 1024,
            "provider_code": "p1",
            "provider_name": "P1",
            "api_key_ref": None,
            "api_key_encrypted": None,
            "base_url": "x",
            "bot_count": 2,
        },
        {
            "purpose": "rerank",
            "model_name": "m2",
            "model_dim": None,
            "provider_code": "p2",
            "provider_name": "P2",
            "api_key_ref": None,
            "api_key_encrypted": None,
            "base_url": "y",
            "bot_count": 1,
        },
    ]
    client = client_factory(rows)
    resp = client.get("/health/models?skip_smoke=true")
    body = resp.json()
    flat = sum(len(arr) for arr in body["models"].values())
    s = body["summary"]
    assert s["total_models"] == flat == 2
    # Sum of every status bucket equals total_models.
    bucket_sum = s["healthy"] + s["degraded"] + s["unhealthy"] + s["config_drift"] + s["not_configured"]
    assert bucket_sum == s["total_models"]


# ---------------------------------------------------------------------------
# Bonus: latency classifier is pure + correct
# ---------------------------------------------------------------------------


def test_classify_status_pure_function() -> None:
    # Failure dominates everything.
    assert _classify_status(50, is_ok=False) == STATUS_UNHEALTHY
    # Below threshold = healthy.
    assert _classify_status(50, is_ok=True) == STATUS_HEALTHY
    # Above threshold = degraded.
    high = DEFAULT_HEALTH_MODELS_DEGRADED_LATENCY_MS + 1
    assert _classify_status(high, is_ok=True) == STATUS_DEGRADED
