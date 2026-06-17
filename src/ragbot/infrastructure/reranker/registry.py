"""Reranker strategy registry — DI factory based on config provider name.

Pattern: caller (``bootstrap.Container``) reads ``reranker_provider`` from
``system_config`` (Redis-cached) and asks the registry for the matching
``RerankerPort`` implementation. Adding a new provider = drop a new file in
this package and register it here; **no edits to query_graph or bootstrap**.

Default = ``"null"`` (NullReranker) — operator-OFF baseline. The strategy is
deliberately fail-soft on unknown provider strings so a typo in
``system_config`` cannot crash boot; instead we log + fall back to null and
the reranker node continues to emit ``mode="rerank"`` (provider == null) so
the misconfig is observable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from ragbot.infrastructure.reranker.jina_reranker import JinaReranker
from ragbot.infrastructure.reranker.litellm_reranker import LiteLLMReranker
from ragbot.infrastructure.reranker.null_reranker import NullReranker
from ragbot.infrastructure.reranker.viranker_local_reranker import ViRankerLocalReranker
from ragbot.infrastructure.reranker.voyage_reranker import VoyageReranker
from ragbot.infrastructure.reranker.zeroentropy_reranker import ZeroEntropyReranker

if TYPE_CHECKING:
    from ragbot.application.ports.reranker_port import RerankerPort

logger = structlog.get_logger(__name__)


# Registered providers. Keep the values as classes (not instances) so each
# call to ``build_reranker`` returns a fresh adapter — callers may stash a
# Singleton wrapper in the DI container if they want one process-wide.
#
# ``viranker_local`` is registered as a STUB only — its ``__init__`` raises
# ``NotImplementedError`` until the operator installs the heavy cross-encoder
# dep (``pip install sentence-transformers`` + downloads the model weights)
# and replaces the body of ``rerank``. Until then, the registry's fail-soft
# path falls back to ``NullReranker`` and surfaces the install hint in logs,
# which is exactly what we want for an opt-in provider — visible in
# ``list_providers()`` so ops can plan, but not silently default-on.
_REGISTRY: dict[str, type] = {
    "jina": JinaReranker,      # NEW — provision RERANKER_JINA_API_KEY + flip system_config
    "jina_ai": JinaReranker,   # alias matching ai_providers.code (LiteLLM convention)
    "litellm": LiteLLMReranker,
    "null": NullReranker,
    "viranker_local": ViRankerLocalReranker,  # opt-in — `pip install` heavy
                                              # cross-encoder dep before
                                              # flipping system_config.reranker_provider.
    "voyage": VoyageReranker,  # hosted multilingual cross-encoder reranker;
                               # provision RERANKER_VOYAGE_API_KEY or
                               # PROVIDER_API_KEYS_JSON before flip.
    "zeroentropy": ZeroEntropyReranker,  # hosted multilingual reranker;
                                         # provision RERANKER_ZEROENTROPY_API_KEY
                                         # or PROVIDER_API_KEYS_JSON before flip.
}


def build_reranker(provider: str | None = None, **kwargs) -> "RerankerPort":
    """Construct the reranker matching ``provider``.

    @param provider: registry key (``"null"`` | ``"litellm"`` | ...).
        ``None`` / unknown / empty falls back to ``NullReranker`` (warned).
    @param kwargs: forwarded to the strategy constructor (e.g. ``model=``).
    @return: ``RerankerPort`` instance.
    """
    key = (provider or "").strip().lower() or "null"
    cls = _REGISTRY.get(key)
    if cls is None:
        logger.warning(
            "reranker_unknown_provider_fallback_null",
            requested=provider,
            registered=sorted(_REGISTRY.keys()),
        )
        cls = NullReranker
    try:
        # Strategies vary in accepted kwargs (Jina needs api_key, Null
        # ignores everything). Filter to what the constructor signature
        # actually accepts so a globally-passed api_key= doesn't blow up
        # NullReranker / ViRanker constructors.
        import inspect
        sig_params = set(inspect.signature(cls.__init__).parameters)
        filtered = {k: v for k, v in kwargs.items() if k in sig_params}
        return cls(**filtered)  # type: ignore[return-value]
    except (NotImplementedError, TypeError, ValueError) as exc:
        logger.error(
            "reranker_strategy_init_failed",
            requested=key,
            error=str(exc),
        )
        return NullReranker()


def list_providers() -> list[str]:
    """Return registered provider keys (sorted, for stable test asserts)."""
    return sorted(_REGISTRY.keys())


__all__ = ["build_reranker", "list_providers"]
