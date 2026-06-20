"""Embedder strategy registry — Port + Strategy + Registry per CLAUDE.md.

Maps the system_config key ``embedding_provider`` (string) to an
``EmbeddingPort`` adapter. Default ``"litellm"`` preserves legacy LiteLLM
behaviour; flip to ``"zeroentropy"`` to use the direct-HTTP zembed-1
adapter (1280-dim, multilingual matryoshka), or to ``"bkai_vn"`` to use
the self-hosted BKAI Vietnamese Bi-Encoder (PhoBERT, 768-dim).

CLAUDE.md compliance:
* Add a provider = one new file in this directory + one row in
  ``_REGISTRY`` below. Orchestrator / business logic untouched.
* No ``if provider == "..."`` in callers — they go through
  ``build_embedder(...)``.
* Per-provider feature flags gated here (not in caller business logic).
"""

from __future__ import annotations

import inspect
from typing import Final

from ragbot.application.ports.embedding_port import EmbeddingPort
from ragbot.infrastructure.embedding.bkai_vn_embedder import BkaiVnEmbedder
from ragbot.infrastructure.embedding.jina_embedder import JinaEmbedder
from ragbot.infrastructure.embedding.litellm_embedder import LiteLLMEmbedder
from ragbot.infrastructure.embedding.zeroentropy_embedder import ZeroEntropyEmbedder
from ragbot.shared.api_key_pool import ApiKeyPoolFactory
from ragbot.shared.bootstrap_config import get_boot_config
from ragbot.shared.constants import (
    DEFAULT_JINA_EMBEDDING_TPM_LIMIT,
    DEFAULT_JINA_EMBEDDING_TPM_SAFETY_FRACTION,
)

_REGISTRY: Final[dict[str, type[EmbeddingPort]]] = {
    "litellm": LiteLLMEmbedder,
    "jina": JinaEmbedder,      # direct-HTTP jina-embeddings-v3 (1024-dim, late_chunking)
    "jina_ai": JinaEmbedder,   # alias matching ai_providers.code (LiteLLM convention)
    "zeroentropy": ZeroEntropyEmbedder,
    "bkai_vn": BkaiVnEmbedder,
}

DEFAULT_EMBEDDING_PROVIDER: Final[str] = "litellm"

# Provider keys gated by a system_config feature flag. When ``embedding_provider``
# selects one of these but the flag is False, ``build_embedder`` falls back to
# the default. Keeps the rollout discipline single-source-of-truth in DB.
_FLAG_GATED_PROVIDERS: Final[dict[str, str]] = {
    "bkai_vn": "bkai_vn_embedder_enabled",
}


def _is_provider_flag_enabled(provider_key: str) -> bool:
    """Resolve the feature flag for a gated provider; True if no flag exists."""
    flag_key = _FLAG_GATED_PROVIDERS.get(provider_key)
    if flag_key is None:
        return True
    # ``get_boot_config`` returns the raw jsonb value; coerce truthy strings.
    raw = get_boot_config(flag_key, False)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)


def build_embedder(
    *,
    provider: str | None = None,
    model: str | None = None,
    key_pool_factory: ApiKeyPoolFactory | None = None,
    ledger: object | None = None,
) -> EmbeddingPort:
    """Construct an embedder for ``provider`` (defaults to LiteLLM).

    Unknown provider falls back to the default so a stale DB row never
    crashes worker boot — caller can recover by editing system_config.

    Flag-gated providers (e.g. ``bkai_vn``): if the system_config flag is
    OFF, falls back to the default. Lets ops stage a provider switch in
    two steps (set provider key, then flip the flag).

    Per-provider model override: when ``provider="zeroentropy"`` the caller-
    supplied ``model`` is ignored if it carries a LiteLLM prefix
    (``jina_ai/...``, ``openai/...``) — the adapter's own default (zembed-1)
    is used instead. Same logic for ``bkai_vn``: a cross-provider prefixed
    name falls back to the adapter's native HF model id.
    """
    key = (provider or DEFAULT_EMBEDDING_PROVIDER).strip().lower()
    if key in _FLAG_GATED_PROVIDERS and not _is_provider_flag_enabled(key):
        # Flag OFF — degrade to default rather than raising; mirrors
        # "unknown provider" fallback semantics.
        key = DEFAULT_EMBEDDING_PROVIDER
    cls = _REGISTRY.get(key, _REGISTRY[DEFAULT_EMBEDDING_PROVIDER])
    if key in {"zeroentropy", "bkai_vn"} and model and "/" in model:
        # Provider mismatch — fall back to adapter's native default model id.
        model = None
    # Filter kwargs to the constructor signature — adapters vary (only the
    # log-center-aware ones accept ``ledger``; LiteLLM/ZeroEntropy ignore it).
    sig = set(inspect.signature(cls.__init__).parameters)
    kwargs: dict[str, object] = {"key_pool_factory": key_pool_factory}
    if model is not None:
        kwargs["model"] = model
    if ledger is not None:
        kwargs["ledger"] = ledger
    # Config-driven per-key Jina TPM (leader scales free→pro without deploy).
    # Only JinaEmbedder's ctor accepts these; the sig filter below drops them
    # for providers that don't (LiteLLM / ZeroEntropy / bkai). get_boot_config
    # returns the constant default when no system_config row is set.
    if "tpm_per_key" in sig:
        try:
            kwargs["tpm_per_key"] = int(get_boot_config(
                "jina_embedding_tpm_per_key", DEFAULT_JINA_EMBEDDING_TPM_LIMIT))
        except (ValueError, TypeError):
            kwargs["tpm_per_key"] = DEFAULT_JINA_EMBEDDING_TPM_LIMIT
    if "tpm_safety_fraction" in sig:
        try:
            kwargs["tpm_safety_fraction"] = float(get_boot_config(
                "jina_embedding_tpm_safety_fraction",
                DEFAULT_JINA_EMBEDDING_TPM_SAFETY_FRACTION))
        except (ValueError, TypeError):
            kwargs["tpm_safety_fraction"] = DEFAULT_JINA_EMBEDDING_TPM_SAFETY_FRACTION
    return cls(**{k: v for k, v in kwargs.items() if k in sig})  # type: ignore[arg-type]


__all__ = ["DEFAULT_EMBEDDING_PROVIDER", "build_embedder"]
