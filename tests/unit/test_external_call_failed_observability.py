"""P4 — external-call failure observability.

Every external integration (embedding / LLM / reranker) must emit a
structured ``external_call_failed`` log on a non-2xx / provider failure so an
operator can see EXACTLY why a call failed — status code, error-body snippet,
model, provider, latency — instead of a silent re-raise.

These tests assert the structured event is emitted (captured via
``structlog.testing.capture_logs``) with the diagnostic fields present, for
the embedder + the LLM router. Pure observability: the existing raise/return
behaviour is unchanged (asserted by the ``pytest.raises`` wrappers).

Domain-neutral. No brand / industry literals.
"""

from __future__ import annotations

from typing import Any

import httpx
import litellm
import pytest
from structlog.testing import capture_logs

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.infrastructure.embedding.zeroentropy_embedder import ZeroEntropyEmbedder
from ragbot.infrastructure.llm.dynamic_litellm_router import DynamicLiteLLMRouter
from ragbot.shared.constants import DEFAULT_EXTERNAL_CALL_ERROR_SNIPPET_CHARS
from ragbot.shared.errors import ExternalServiceError, LLMError

_EXTERNAL_CALL_FAILED_EVENT = "external_call_failed"


# --------------------------------------------------------------------------- #
# Embedder — non-2xx HTTP response from ZeroEntropy embed endpoint.
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text
        self.request = httpx.Request("POST", "https://example.test/embed")

    def json(self) -> dict[str, Any]:  # pragma: no cover — non-2xx never parses
        return {}


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def post(self, *args: Any, **kwargs: Any) -> _FakeResponse:
        return self._response


@pytest.mark.asyncio
async def test_embedder_non_2xx_emits_external_call_failed(monkeypatch) -> None:
    """A non-retryable 4xx from the embed endpoint logs status + provider + model."""
    monkeypatch.setenv("ZEROENTROPY_EMBEDDING_API_KEY", "k-test")
    embedder = ZeroEntropyEmbedder(model="zembed-1")

    body = "x" * (DEFAULT_EXTERNAL_CALL_ERROR_SNIPPET_CHARS + 500)  # over the cap → truncated
    fake = _FakeClient(_FakeResponse(401, body))

    async def _fake_get_client() -> _FakeClient:
        return fake

    monkeypatch.setattr(embedder, "_get_client", _fake_get_client)

    spec = EmbeddingSpec(
        binding_id="00000000-0000-0000-0000-000000000001",
        model_name="zembed-1",
        provider="zeroentropy",
        dimension=1280,
        model_version="1",
    )

    with capture_logs() as caps:
        with pytest.raises(ExternalServiceError):  # behaviour unchanged
            await embedder.embed_batch(
                ["hello"], spec=spec, record_tenant_id="t-1",
            )

    failed = [c for c in caps if c.get("event") == _EXTERNAL_CALL_FAILED_EVENT]
    assert failed, f"no external_call_failed event; got {[c.get('event') for c in caps]}"
    evt = failed[0]
    assert evt["log_level"] in {"warning", "error"}
    assert evt["status_code"] == 401
    assert evt["provider"] == "zeroentropy"
    assert evt["integration"] == "embed"
    assert "zembed-1" in str(evt["model"])
    # Body snippet present AND truncated to the shared cap (no unbounded dump).
    assert evt["error"]
    assert len(str(evt["error"])) <= DEFAULT_EXTERNAL_CALL_ERROR_SNIPPET_CHARS
    assert "duration_ms" in evt


# --------------------------------------------------------------------------- #
# LLM router — provider raises a litellm exception (auth / 5xx) on completion.
# --------------------------------------------------------------------------- #


class _Provider:
    code = "innocom-litellm"
    api_key = "k"
    base_url = "https://example.test"
    timeout_ms = 1000
    max_concurrent = 4


class _Params:
    temperature = 0.0
    max_tokens = 16


class _Cfg:
    provider = _Provider()
    params = _Params()
    litellm_name = "openai/some-model"
    pricing = None
    # failover disabled (no fallback fields populated)
    fallback_model_row_id = None
    fallback_wire_model_id = None
    fallback_provider = None


@pytest.mark.asyncio
async def test_llm_router_provider_failure_emits_external_call_failed(monkeypatch) -> None:
    """A litellm ServiceUnavailableError logs status + provider + model + latency."""
    router = DynamicLiteLLMRouter(ai_config_repo=object())

    err = litellm.exceptions.ServiceUnavailableError(
        message="upstream 503 body snippet",
        model="some-model",
        llm_provider="innocom-litellm",
    )

    async def _boom(**kwargs: Any) -> Any:
        raise err

    monkeypatch.setattr(litellm, "acompletion", _boom)

    with capture_logs() as caps:
        with pytest.raises(LLMError):  # behaviour unchanged — still surfaces as LLMError
            await router._complete_runtime_one(
                _Cfg(),
                [{"role": "user", "content": "hi"}],
                purpose="answer",
            )

    failed = [c for c in caps if c.get("event") == _EXTERNAL_CALL_FAILED_EVENT]
    assert failed, f"no external_call_failed event; got {[c.get('event') for c in caps]}"
    evt = failed[0]
    assert evt["log_level"] in {"warning", "error"}
    assert evt["integration"] == "llm"
    assert evt["provider"] == "innocom-litellm"
    assert "some-model" in str(evt["model"])
    # litellm exceptions carry a status_code (503 here).
    assert evt["status_code"] == 503
    assert evt["error"]
    assert len(str(evt["error"])) <= DEFAULT_EXTERNAL_CALL_ERROR_SNIPPET_CHARS
    assert "duration_ms" in evt
