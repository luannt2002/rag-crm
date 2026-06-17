"""Safety Strategy registries — DI factories for both sanitizer + source validator.

Two complementary safety layers live behind this module:

1. **Source validator** — gates which document source URLs are allowed to
   feed the ingest pipeline. Selected via
   ``system_config.source_validator_provider``. Default ``"null"`` so
   existing tenants see byte-identical behaviour until the bot owner opts
   in by setting the provider to ``"domain_allowlist"`` AND populating
   ``bots.plan_limits.allowed_source_domains``.

2. **Sanitizer** — Tier-0 input sanitizer mirroring the PII redactor
   contract. Default ``"null"`` (passthrough). Operators flip via
   ``system_config['sanitizer_provider'] = 'tier0'`` to enable
   CleanBase Tier-0 strip-and-normalise.

Pattern mirrors :mod:`ragbot.infrastructure.pii.registry` so adding a new
adapter is a 1-file change: drop adapter under this package + add a row to
the appropriate ``_REGISTRY``. No orchestrator change.

``list_providers()`` returns the union of both registries (sorted) so
diagnostic callers see every safety strategy the platform exposes in a
single sorted list — matches the pattern existing tests rely on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from ragbot.infrastructure.safety.domain_allowlist_validator import (
    DomainAllowlistValidator,
)
from ragbot.infrastructure.safety.null_sanitizer import NullSanitizer
from ragbot.infrastructure.safety.null_source_validator import NullSourceValidator
from ragbot.infrastructure.safety.sanitizer import CleanBaseTier0Sanitizer

if TYPE_CHECKING:
    from ragbot.application.ports.sanitizer_port import SanitizerPort
    from ragbot.application.ports.source_validator_port import SourceValidatorPort

logger = structlog.get_logger(__name__)


_SOURCE_VALIDATOR_REGISTRY: dict[str, type] = {
    "null": NullSourceValidator,
    "domain_allowlist": DomainAllowlistValidator,
}

_SANITIZER_REGISTRY: dict[str, type] = {
    "null": NullSanitizer,
    "tier0": CleanBaseTier0Sanitizer,
}


def build_source_validator(
    provider: str | None = None,
    **kwargs,
) -> "SourceValidatorPort":
    """Instantiate a source-validator Strategy by provider key.

    Unknown / empty / None provider falls back to :class:`NullSourceValidator`
    so a config typo never breaks ingest. Constructor failure (ImportError
    for an optional dep) also degrades to Null — graceful degradation
    matching the PII registry contract.
    """
    key = (provider or "").strip().lower() or "null"
    cls = _SOURCE_VALIDATOR_REGISTRY.get(key)
    if cls is None:
        logger.warning(
            "source_validator_unknown_provider_fallback_null",
            requested=provider,
            registered=sorted(_SOURCE_VALIDATOR_REGISTRY.keys()),
        )
        cls = NullSourceValidator
    try:
        return cls(**kwargs)  # type: ignore[return-value]
    except (ImportError, NotImplementedError) as exc:
        logger.error(
            "source_validator_strategy_not_installed",
            requested=key,
            error=str(exc),
        )
        return NullSourceValidator(**kwargs)


def build_sanitizer(
    provider: str | None = None,
    **kwargs: object,
) -> "SanitizerPort":
    """Construct the sanitizer Strategy matching ``provider``.

    @param provider: registry key (``"null"`` | ``"tier0"``). Empty / unknown
        keys fall back to ``"null"`` with a structlog warning.
    @param kwargs: forwarded to the strategy constructor (currently no
        strategy honours kwargs; future tiers may).
    @return: :class:`SanitizerPort` instance — never raises on bad input.
    """
    key = (provider or "").strip().lower() or "null"
    cls = _SANITIZER_REGISTRY.get(key)
    if cls is None:
        logger.warning(
            "sanitizer_unknown_provider_fallback_null",
            requested=provider,
            registered=sorted(_SANITIZER_REGISTRY.keys()),
        )
        cls = NullSanitizer
    try:
        return cls(**kwargs)  # type: ignore[return-value]
    except (ImportError, NotImplementedError) as exc:
        logger.error(
            "sanitizer_strategy_not_installed",
            requested=key,
            error=str(exc),
        )
        return NullSanitizer(**kwargs)


def list_providers() -> list[str]:
    """Return the union of every safety registry's provider keys (sorted).

    Both ``test_source_allowlist`` and ``test_cleanbase_tier0`` import this
    symbol and assert on membership (``"domain_allowlist" in providers`` /
    ``"tier0" in providers``). Returning the union keeps both contracts
    happy without splitting symbol names.
    """
    return sorted(
        set(_SOURCE_VALIDATOR_REGISTRY.keys()) | set(_SANITIZER_REGISTRY.keys())
    )


def list_source_validator_providers() -> list[str]:
    """Source-validator registry keys only (sorted)."""
    return sorted(_SOURCE_VALIDATOR_REGISTRY.keys())


def list_sanitizer_providers() -> list[str]:
    """Sanitizer registry keys only (sorted)."""
    return sorted(_SANITIZER_REGISTRY.keys())


__all__ = [
    "build_sanitizer",
    "build_source_validator",
    "list_providers",
    "list_sanitizer_providers",
    "list_source_validator_providers",
]
