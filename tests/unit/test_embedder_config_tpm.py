"""Pin — config-driven per-key Jina TPM limit (2026-06-20).

The per-key TPM limiter (fix e17c0f4) defaulted to the constant
``DEFAULT_JINA_EMBEDDING_TPM_LIMIT × safety``. This wires ``build_embedder`` to
read ``jina_embedding_tpm_per_key`` / ``jina_embedding_tpm_safety_fraction`` from
``system_config`` (via ``get_boot_config``, allowlisted) so the leader can scale
free→pro WITHOUT a deploy. The signature filter must drop the kwargs for
embedders whose ctor doesn't accept them (LiteLLM / ZeroEntropy / bkai).
"""

from __future__ import annotations

from unittest.mock import patch

from ragbot.infrastructure.embedding.litellm_embedder import LiteLLMEmbedder
from ragbot.infrastructure.embedding.registry import build_embedder


def _boot(per_key=None, safety=None):
    def _fn(key: str, default):  # mirrors get_boot_config(key, default)
        if "tpm_per_key" in key and per_key is not None:
            return per_key
        if "safety_fraction" in key and safety is not None:
            return safety
        return default
    return _fn


def test_jina_default_tpm_when_no_config() -> None:
    """No system_config row → constant default (100k × 0.9 = 90k effective)."""
    with patch(
        "ragbot.infrastructure.embedding.registry.get_boot_config", _boot(),
    ):
        emb = build_embedder(provider="jina")
    assert emb._tpm_per_key_effective == 90_000


def test_jina_tpm_overridden_from_config() -> None:
    """Leader sets a smaller (free) or larger (pro) per-key TPM via config."""
    with patch(
        "ragbot.infrastructure.embedding.registry.get_boot_config",
        _boot(per_key=50_000, safety=0.9),
    ):
        emb = build_embedder(provider="jina")
    assert emb._tpm_per_key_effective == 45_000  # 50k × 0.9


def test_jina_pro_tier_scales_up() -> None:
    with patch(
        "ragbot.infrastructure.embedding.registry.get_boot_config",
        _boot(per_key=1_000_000, safety=0.9),
    ):
        emb = build_embedder(provider="jina")
    assert emb._tpm_per_key_effective == 900_000


def test_non_jina_embedder_ignores_tpm_kwargs() -> None:
    """The signature filter must not leak tpm_* into a ctor that rejects them."""
    with patch(
        "ragbot.infrastructure.embedding.registry.get_boot_config",
        _boot(per_key=50_000, safety=0.9),
    ):
        emb = build_embedder(provider="litellm")
    assert isinstance(emb, LiteLLMEmbedder)


def test_bad_config_value_falls_back_to_default() -> None:
    """A non-numeric system_config value must not crash embedder boot."""
    with patch(
        "ragbot.infrastructure.embedding.registry.get_boot_config",
        _boot(per_key="not-a-number", safety="bad"),
    ):
        emb = build_embedder(provider="jina")
    assert emb._tpm_per_key_effective == 90_000  # fell back to 100k × 0.9
