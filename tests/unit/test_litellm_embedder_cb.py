"""Lock test — F14-HIGH-3.1 LiteLLMEmbedder gains CircuitBreaker.

Asserts CB lifecycle attached to LiteLLMEmbedder so an outage on the
embedding provider doesn't burn 90s × queue-depth seconds of retries
before each ingest task fails.

Domain-neutral. No brand / industry literals.
"""

from __future__ import annotations

import inspect

from ragbot.application.services.retry_policy import CBState, CircuitBreaker
from ragbot.infrastructure.embedding.litellm_embedder import LiteLLMEmbedder
from ragbot.shared.constants import (
    DEFAULT_EMBEDDER_CB_FAIL_MAX,
    DEFAULT_EMBEDDER_CB_RESET_S,
)


def test_constants_exist() -> None:
    assert DEFAULT_EMBEDDER_CB_FAIL_MAX >= 1
    assert DEFAULT_EMBEDDER_CB_RESET_S > 0


def test_embedder_instance_has_circuit_breaker() -> None:
    e = LiteLLMEmbedder()
    assert hasattr(e, "_cb"), "F14-HIGH-3.1 regression — CB attribute missing"
    assert isinstance(e._cb, CircuitBreaker)
    assert e._cb.state == CBState.CLOSED


def test_embed_batch_source_uses_cb_context() -> None:
    """The embed_batch loop must wrap retry_with_backoff in `with self._cb`."""
    src = inspect.getsource(LiteLLMEmbedder.embed_batch)
    assert "self._cb" in src, "F14-HIGH-3.1 regression — CB not used"
    assert "with self._cb" in src, (
        "F14-HIGH-3.1 regression — CB context-manager pattern not used"
    )


def test_circuit_opens_after_consecutive_failures() -> None:
    e = LiteLLMEmbedder()
    for _ in range(DEFAULT_EMBEDDER_CB_FAIL_MAX):
        e._cb.record_failure()
    assert e._cb.state == CBState.OPEN
