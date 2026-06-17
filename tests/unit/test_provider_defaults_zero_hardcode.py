"""Y2-P1 + zero-hardcode regression: ai_providers + bot_model_bindings ORM
defaults must come from `shared.constants`, not inline literals.

Audit `AUDIT_DEEPDIVE_INFRA_LAYER_20260429_120000.md` (P1) and
`AUDIT_DEEPDIVE_MIGRATIONS_DB_20260429_142848.md` (P1-3) flagged:
  - timeout_ms=30000 inline (should be DEFAULT_PROVIDER_TIMEOUT_MS)
  - connect_timeout_ms=5000 inline (should be DEFAULT_PROVIDER_CONNECT_TIMEOUT_MS)
  - max_retries=2 inline, contradicts DEFAULT_RETRY_MAX_ATTEMPTS=3
  - max_concurrent=16 inline (should be DEFAULT_PROVIDER_MAX_CONCURRENT)
  - bot_model_bindings.max_tokens=1000 inline (should be DEFAULT_LLM_MAX_TOKENS)

Tests bind the ORM column-default to the constants via SQLAlchemy
introspection.
"""
from __future__ import annotations

from ragbot.infrastructure.db.models import (
    AIProviderModel,
    BotModelBindingModel,
)
from ragbot.shared.constants import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_PROVIDER_CONNECT_TIMEOUT_MS,
    DEFAULT_PROVIDER_MAX_CONCURRENT,
    DEFAULT_PROVIDER_TIMEOUT_MS,
    DEFAULT_RETRY_MAX_ATTEMPTS,
)


def _column_default(model: type, col_name: str) -> object:
    col = model.__table__.c[col_name]
    return col.default.arg if col.default is not None else None


def test_provider_timeout_ms_uses_constant() -> None:
    assert _column_default(AIProviderModel, "timeout_ms") == DEFAULT_PROVIDER_TIMEOUT_MS
    assert DEFAULT_PROVIDER_TIMEOUT_MS == 30_000


def test_provider_connect_timeout_ms_uses_constant() -> None:
    assert (
        _column_default(AIProviderModel, "connect_timeout_ms")
        == DEFAULT_PROVIDER_CONNECT_TIMEOUT_MS
    )
    assert DEFAULT_PROVIDER_CONNECT_TIMEOUT_MS == 5_000


def test_provider_max_retries_aligns_with_global_retry_constant() -> None:
    """Audit P1-3: ai_providers.max_retries=2 contradicted DEFAULT_RETRY_MAX_ATTEMPTS=3.
    After fix, both default to the SAME constant — single source of truth."""
    assert (
        _column_default(AIProviderModel, "max_retries")
        == DEFAULT_RETRY_MAX_ATTEMPTS
    )


def test_provider_max_concurrent_uses_constant() -> None:
    assert (
        _column_default(AIProviderModel, "max_concurrent")
        == DEFAULT_PROVIDER_MAX_CONCURRENT
    )


def test_binding_max_tokens_uses_constant() -> None:
    """Audit P1-1 (Migrations): inline 1000 → DEFAULT_LLM_MAX_TOKENS."""
    assert (
        _column_default(BotModelBindingModel, "max_tokens")
        == DEFAULT_LLM_MAX_TOKENS
    )
    assert DEFAULT_LLM_MAX_TOKENS == 1_000


def test_constants_are_positive_int() -> None:
    """Sanity guard against accidental zero/negative defaults."""
    for v in (
        DEFAULT_PROVIDER_TIMEOUT_MS,
        DEFAULT_PROVIDER_CONNECT_TIMEOUT_MS,
        DEFAULT_PROVIDER_MAX_CONCURRENT,
        DEFAULT_LLM_MAX_TOKENS,
        DEFAULT_RETRY_MAX_ATTEMPTS,
    ):
        assert isinstance(v, int) and v > 0
