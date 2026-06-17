"""Unit tests — BKAI Vietnamese Bi-Encoder embedder (T3-Refactor).

Verifies the adapter:
1. Implements ``EmbeddingPort`` (Protocol runtime check).
2. Reports the documented dimension (768 — PhoBERT-base hidden size).
3. Refuses to embed when no endpoint URL is configured (zero-hardcode
   discipline: no default public URL, must be set per-deployment).
4. Parses all three TEI-compatible response shapes correctly:
   bare list-of-lists, ``{"embeddings": [...]}``, OpenAI-style
   ``{"data": [{"embedding": ...}]}``.
5. Has a CircuitBreaker attached (parity with LiteLLM/ZE adapters).
6. Registers under provider key ``"bkai_vn"`` in the registry.
7. Honours the ``bkai_vn_embedder_enabled`` feature flag — falls back to
   default when OFF.
8. Telemetry: emits structlog event ``bkai_vn_embed_done`` with
   ``step_name`` + ``feature_flag`` keys (OBSERVABILITY-MATRIX contract).

Domain-neutral. No brand / customer literals.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.application.ports.embedding_port import EmbeddingPort
from ragbot.application.services.retry_policy import CBState, CircuitBreaker
from ragbot.infrastructure.embedding.bkai_vn_embedder import BkaiVnEmbedder
from ragbot.infrastructure.embedding.litellm_embedder import LiteLLMEmbedder
from ragbot.infrastructure.embedding.registry import (
    DEFAULT_EMBEDDING_PROVIDER,
    _REGISTRY,
    build_embedder,
)
from ragbot.shared.constants import (
    DEFAULT_BKAI_VN_EMBEDDING_DIM,
    DEFAULT_BKAI_VN_EMBEDDING_ENDPOINT_PATH,
    DEFAULT_BKAI_VN_EMBEDDING_MODEL,
)
from ragbot.shared.errors import ExternalServiceError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _spec(model: str = DEFAULT_BKAI_VN_EMBEDDING_MODEL) -> EmbeddingSpec:
    return EmbeddingSpec(
        binding_id=uuid4(),
        model_name=model,
        provider="bkai_vn",
        dimension=DEFAULT_BKAI_VN_EMBEDDING_DIM,
        model_version="bkai-vn-bi-encoder",
    )


class _MockTransport(httpx.AsyncBaseTransport):
    """Tiny in-memory transport so we can assert on the wire payload."""

    def __init__(self, payload: Any, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.captured_requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Drain the request body so .content is populated for assertions.
        await request.aread()
        self.captured_requests.append(request)
        import json as _json

        return httpx.Response(
            status_code=self.status,
            content=_json.dumps(self.payload).encode("utf-8"),
            headers={"content-type": "application/json"},
            request=request,
        )


def _patched_embedder(transport: _MockTransport, **kwargs: Any) -> BkaiVnEmbedder:
    e = BkaiVnEmbedder(api_url="http://embed.test", **kwargs)
    # Inject pre-built client so the mock transport intercepts requests.
    e._client = httpx.AsyncClient(transport=transport, timeout=5)
    return e


# ---------------------------------------------------------------------------
# Constants — anchor the proof citation
# ---------------------------------------------------------------------------


def test_constants_match_phobert_base_dimension() -> None:
    """PhoBERT-base hidden size = 768 (proof: HuggingFace model card)."""
    assert DEFAULT_BKAI_VN_EMBEDDING_DIM == 768
    assert DEFAULT_BKAI_VN_EMBEDDING_MODEL == (
        "bkai-foundation-models/vietnamese-bi-encoder"
    )
    assert DEFAULT_BKAI_VN_EMBEDDING_ENDPOINT_PATH.startswith("/")


# ---------------------------------------------------------------------------
# Port conformance + structural invariants
# ---------------------------------------------------------------------------


def test_embedder_implements_embedding_port() -> None:
    e = BkaiVnEmbedder(api_url="http://embed.test")
    assert isinstance(e, EmbeddingPort)


def test_embedder_reports_documented_dimension() -> None:
    e = BkaiVnEmbedder(api_url="http://embed.test")
    assert e.dimension == DEFAULT_BKAI_VN_EMBEDDING_DIM == 768


def test_embedder_model_id_defaults_to_hf_model() -> None:
    e = BkaiVnEmbedder(api_url="http://embed.test")
    assert e.model_id == DEFAULT_BKAI_VN_EMBEDDING_MODEL


def test_embedder_has_circuit_breaker_attached() -> None:
    """CB parity with LiteLLM/ZE — embedder outage must not starve workers."""
    e = BkaiVnEmbedder(api_url="http://embed.test")
    assert hasattr(e, "_cb")
    assert isinstance(e._cb, CircuitBreaker)
    assert e._cb.state == CBState.CLOSED


def test_endpoint_url_built_from_base_and_path() -> None:
    """Adapter appends /embed to operator-supplied base URL."""
    e = BkaiVnEmbedder(api_url="http://embed.test:8080/")
    assert e._api_url.endswith(DEFAULT_BKAI_VN_EMBEDDING_ENDPOINT_PATH)
    # Trailing slash must be stripped — no doubled slash between host and path.
    assert e._api_url == "http://embed.test:8080" + DEFAULT_BKAI_VN_EMBEDDING_ENDPOINT_PATH


def test_endpoint_url_empty_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """No public default URL — adapter constructs but cannot embed."""
    monkeypatch.delenv("BKAI_VN_EMBEDDING_URL", raising=False)
    e = BkaiVnEmbedder()
    assert e._api_url == ""


# ---------------------------------------------------------------------------
# Registry — provider key + flag gating
# ---------------------------------------------------------------------------


def test_registry_includes_bkai_vn() -> None:
    assert "bkai_vn" in _REGISTRY
    assert _REGISTRY["bkai_vn"] is BkaiVnEmbedder


def test_build_embedder_returns_bkai_vn_when_flag_on() -> None:
    """provider=bkai_vn + flag ON → returns BkaiVnEmbedder instance."""
    with patch(
        "ragbot.infrastructure.embedding.registry.get_boot_config",
        return_value=True,
    ):
        adapter = build_embedder(provider="bkai_vn")
    assert isinstance(adapter, BkaiVnEmbedder)


def test_build_embedder_falls_back_when_flag_off() -> None:
    """provider=bkai_vn + flag OFF → falls back to default (LiteLLM)."""
    with patch(
        "ragbot.infrastructure.embedding.registry.get_boot_config",
        return_value=False,
    ):
        adapter = build_embedder(provider="bkai_vn")
    assert isinstance(adapter, LiteLLMEmbedder)
    assert not isinstance(adapter, BkaiVnEmbedder)


def test_build_embedder_flag_string_truthy_coercion() -> None:
    """Flag values from jsonb may arrive as string 'true' / 'on' / '1'."""
    for truthy in ("true", "True", "yes", "on", "1"):
        with patch(
            "ragbot.infrastructure.embedding.registry.get_boot_config",
            return_value=truthy,
        ):
            adapter = build_embedder(provider="bkai_vn")
            assert isinstance(adapter, BkaiVnEmbedder), (
                f"truthy string {truthy!r} should enable bkai_vn"
            )


def test_build_embedder_flag_string_falsy_falls_back() -> None:
    for falsy in ("false", "0", "off", "no", ""):
        with patch(
            "ragbot.infrastructure.embedding.registry.get_boot_config",
            return_value=falsy,
        ):
            adapter = build_embedder(provider="bkai_vn")
            assert not isinstance(adapter, BkaiVnEmbedder), (
                f"falsy string {falsy!r} should fall back to default"
            )


def test_build_embedder_strips_cross_provider_prefix() -> None:
    """Caller passing ``openai/foo`` to bkai_vn falls back to native model id."""
    with patch(
        "ragbot.infrastructure.embedding.registry.get_boot_config",
        return_value=True,
    ):
        adapter = build_embedder(provider="bkai_vn", model="openai/foo")
    assert isinstance(adapter, BkaiVnEmbedder)
    assert adapter.model_id == DEFAULT_BKAI_VN_EMBEDDING_MODEL


def test_default_provider_still_litellm() -> None:
    """Adding bkai_vn must NOT shift platform default away from LiteLLM."""
    assert DEFAULT_EMBEDDING_PROVIDER == "litellm"


# ---------------------------------------------------------------------------
# Behavioural — embed_batch happy / sad paths (async)
# ---------------------------------------------------------------------------


async def test_embed_batch_empty_returns_empty() -> None:
    e = BkaiVnEmbedder(api_url="http://embed.test")
    result = await e.embed_batch([], spec=_spec(), record_tenant_id=uuid4())
    assert result == []


async def test_embed_batch_raises_when_endpoint_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BKAI_VN_EMBEDDING_URL", raising=False)
    e = BkaiVnEmbedder()  # no api_url, no env
    with pytest.raises(ExternalServiceError, match="endpoint not configured"):
        await e.embed_batch(["xin chào"], spec=_spec(), record_tenant_id=uuid4())


async def test_embed_batch_parses_bare_list_of_lists() -> None:
    """TEI native response shape: ``[[float, ...], ...]``."""
    vec_a = [0.1] * DEFAULT_BKAI_VN_EMBEDDING_DIM
    vec_b = [0.2] * DEFAULT_BKAI_VN_EMBEDDING_DIM
    transport = _MockTransport([vec_a, vec_b])
    e = _patched_embedder(transport)
    out = await e.embed_batch(
        ["xin chào", "việt nam"], spec=_spec(), record_tenant_id=uuid4(),
    )
    assert len(out) == 2
    assert len(out[0]) == DEFAULT_BKAI_VN_EMBEDDING_DIM
    assert out[0][0] == pytest.approx(0.1)
    assert out[1][0] == pytest.approx(0.2)
    # Verify the wire payload contains the inputs.
    req = transport.captured_requests[0]
    import json

    body = json.loads(req.content)
    assert body["inputs"] == ["xin chào", "việt nam"]
    assert body["truncate"] is True


async def test_embed_batch_parses_embeddings_dict_shape() -> None:
    """Proxy response shape: ``{"embeddings": [[...], ...]}``."""
    vec = [0.5] * DEFAULT_BKAI_VN_EMBEDDING_DIM
    transport = _MockTransport({"embeddings": [vec]})
    e = _patched_embedder(transport)
    out = await e.embed_batch(["test"], spec=_spec(), record_tenant_id=uuid4())
    assert len(out) == 1
    assert out[0][0] == pytest.approx(0.5)


async def test_embed_batch_parses_openai_style_data_shape() -> None:
    """LiteLLM-proxy shape: ``{"data": [{"embedding": [...]}]}``."""
    vec = [0.9] * DEFAULT_BKAI_VN_EMBEDDING_DIM
    transport = _MockTransport({"data": [{"embedding": vec}]})
    e = _patched_embedder(transport)
    out = await e.embed_batch(["q"], spec=_spec(), record_tenant_id=uuid4())
    assert len(out) == 1
    assert out[0][0] == pytest.approx(0.9)


async def test_embed_batch_unknown_shape_raises() -> None:
    transport = _MockTransport({"unexpected_key": "noise"})
    e = _patched_embedder(transport)
    with pytest.raises(ExternalServiceError, match="unexpected response shape"):
        await e.embed_batch(["q"], spec=_spec(), record_tenant_id=uuid4())


async def test_embed_batch_non_retryable_http_error_raises_external_service() -> None:
    """HTTP 400 → ExternalServiceError (not retried)."""
    transport = _MockTransport({"error": "bad request"}, status=400)
    e = _patched_embedder(transport)
    with pytest.raises(ExternalServiceError, match="HTTP 400"):
        await e.embed_batch(["q"], spec=_spec(), record_tenant_id=uuid4())


async def test_embed_one_delegates_to_batch() -> None:
    vec = [0.42] * DEFAULT_BKAI_VN_EMBEDDING_DIM
    transport = _MockTransport([vec])
    e = _patched_embedder(transport)
    out = await e.embed_one(
        "xin chào việt nam", spec=_spec(), record_tenant_id=uuid4(),
    )
    assert len(out) == DEFAULT_BKAI_VN_EMBEDDING_DIM
    assert out[0] == pytest.approx(0.42)


# ---------------------------------------------------------------------------
# Auth — bearer token propagation
# ---------------------------------------------------------------------------


async def test_bearer_token_attached_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When BKAI_VN_EMBEDDING_TOKEN is set, Authorization header is sent."""
    monkeypatch.setenv("BKAI_VN_EMBEDDING_TOKEN", "secret-token-123")
    transport = _MockTransport([[0.0] * DEFAULT_BKAI_VN_EMBEDDING_DIM])
    e = _patched_embedder(transport)
    await e.embed_batch(["q"], spec=_spec(), record_tenant_id=uuid4())
    req = transport.captured_requests[0]
    assert req.headers.get("Authorization") == "Bearer secret-token-123"


async def test_no_auth_header_when_token_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token-less TEI deployments (in-VPC) must work without auth header."""
    monkeypatch.delenv("BKAI_VN_EMBEDDING_TOKEN", raising=False)
    transport = _MockTransport([[0.0] * DEFAULT_BKAI_VN_EMBEDDING_DIM])
    e = _patched_embedder(transport)
    await e.embed_batch(["q"], spec=_spec(), record_tenant_id=uuid4())
    req = transport.captured_requests[0]
    assert "Authorization" not in req.headers


# ---------------------------------------------------------------------------
# Telemetry — structlog contract for OBSERVABILITY-MATRIX
# ---------------------------------------------------------------------------


def test_telemetry_event_has_step_name_and_feature_flag() -> None:
    """OBSERVABILITY-MATRIX: every feature MUST emit step_name + feature_flag."""
    import inspect

    src = inspect.getsource(BkaiVnEmbedder.embed_batch)
    assert "bkai_vn_embed_done" in src, "missing structlog event"
    assert 'step_name="bkai_vn_embed"' in src, "missing step_name"
    assert 'feature_flag="bkai_vn_embedder_enabled"' in src, "missing feature_flag"


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


async def test_health_check_returns_false_when_endpoint_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BKAI_VN_EMBEDDING_URL", raising=False)
    e = BkaiVnEmbedder()
    assert await e.health_check() is False


async def test_health_check_returns_true_on_200() -> None:
    transport = _MockTransport([[0.0] * DEFAULT_BKAI_VN_EMBEDDING_DIM])
    e = _patched_embedder(transport)
    assert await e.health_check() is True
