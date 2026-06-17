"""Unit tests for ModelRuntimeConfig DTO + compute_version_hash."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from ragbot.application.dto.model_runtime import (
    Capabilities,
    GenerationParams,
    ModelRuntimeConfig,
    Pricing,
    ProviderRuntime,
    compute_version_hash,
)


def _build_cfg(api_key: str = "sk-secret-abcd1234") -> ModelRuntimeConfig:
    return ModelRuntimeConfig(
        model_row_id=uuid4(),
        binding_id=uuid4(),
        purpose="generation",
        kind="chat",
        provider=ProviderRuntime(
            code="anthropic",
            base_url="https://api.anthropic.com",
            api_key=api_key,
            timeout_ms=30000,
            connect_timeout_ms=5000,
            max_retries=2,
            max_concurrent=16,
        ),
        wire_model_id="claude-3-5-sonnet",
        litellm_name="anthropic/claude-3-5-sonnet",
        context_window=200000,
        max_output_tokens=4096,
        embedding_dimension=None,
        params=GenerationParams(temperature=0.2, top_p=0.9, max_tokens=1024),
        pricing=Pricing(
            input_per_1k_usd=Decimal("0.003"),
            output_per_1k_usd=Decimal("0.015"),
            cached_input_per_1k_usd=None,
        ),
        capabilities=Capabilities(supports_tool_use=True),
        quality_tier="premium",
        version_hash="deadbeef",
        loaded_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )


def test_compute_version_hash_deterministic():
    payload = {
        "model": "claude-3-5-sonnet",
        "temperature": 0.2,
        "max_tokens": 1024,
        "api_key": "sk-should-be-ignored",
    }
    h1 = compute_version_hash(payload)
    h2 = compute_version_hash(dict(payload))
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_compute_version_hash_excludes_api_key():
    base = {"model": "m", "temperature": 0.1}
    h_no_key = compute_version_hash(base)
    h_with_key = compute_version_hash({**base, "api_key": "sk-anything-here"})
    assert h_no_key == h_with_key


def test_mask_redacts_api_key():
    cfg = _build_cfg(api_key="sk-abcdef1234567890WXYZ")
    masked = cfg.mask()
    api_key_masked = masked["provider"]["api_key"]
    assert "abcdef1234567890" not in api_key_masked
    assert api_key_masked.startswith("sk-***")
    assert api_key_masked.endswith("WXYZ")  # last4 visible


def test_mask_short_key_safe():
    cfg = _build_cfg(api_key="ab")
    masked = cfg.mask()
    assert "ab" not in masked["provider"]["api_key"].replace("***", "")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
