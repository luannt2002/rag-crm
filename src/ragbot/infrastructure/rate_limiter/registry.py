"""Rate-limiter strategy registry.

Pattern mirrors ``infrastructure/reranker/registry.py``: caller asks for
the implementation by string key; the registry returns the constructed
adapter. Adding a new variant (token-bucket, leaky-bucket) means one
new file + one registry line.
"""

from __future__ import annotations

import inspect
from typing import Any

import structlog

from ragbot.application.ports.rate_limiter_port import RateLimiterPort
from ragbot.infrastructure.rate_limiter.in_memory import InMemorySlidingWindow
from ragbot.infrastructure.rate_limiter.sliding_window import SlidingWindowRateLimiter

logger = structlog.get_logger(__name__)


_REGISTRY: dict[str, type] = {
    "redis_sliding": SlidingWindowRateLimiter,
    "in_memory": InMemorySlidingWindow,
}


def build_rate_limiter(
    provider: str | None = None, **kwargs: Any,
) -> RateLimiterPort:
    """Construct the rate-limiter matching ``provider``.

    Defaults to ``"redis_sliding"`` because production wiring always
    holds a Redis client; tests pass ``"in_memory"`` to avoid the
    network dep.

    Unknown / empty provider falls back to ``in_memory`` with a warn
    log so a config typo cannot crash boot. Surfacing in
    ``list_providers()`` keeps ops aware.
    """
    key = (provider or "redis_sliding").strip().lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        logger.warning(
            "rate_limiter_unknown_provider_fallback_in_memory",
            requested=provider,
            registered=sorted(_REGISTRY.keys()),
        )
        cls = InMemorySlidingWindow
    sig_params = set(inspect.signature(cls.__init__).parameters)
    filtered = {k: v for k, v in kwargs.items() if k in sig_params}
    return cls(**filtered)  # type: ignore[return-value]


def list_providers() -> list[str]:
    return sorted(_REGISTRY.keys())


__all__ = ("build_rate_limiter", "list_providers")
