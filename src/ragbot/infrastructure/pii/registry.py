"""PII redactor strategy registry — DI factory based on provider key.

Exposes :class:`PiiRedactorPort` (sync + ``(text, list[dict])``).
``bootstrap.Container.pii`` currently binds ``RegexPIIRedactor`` directly;
once that Factory is replaced with ``build_pii_redactor`` the legacy
adapter can be retired.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from ragbot.infrastructure.pii.null_pii_redactor import NullPiiRedactor
from ragbot.infrastructure.pii.presidio_pii_redactor import PresidioPiiRedactor
from ragbot.infrastructure.pii.vn_regex_pii_redactor import VnRegexPiiRedactor

if TYPE_CHECKING:
    from ragbot.application.ports.pii_redactor_port import PiiRedactorPort

logger = structlog.get_logger(__name__)


_REGISTRY: dict[str, type] = {
    "null": NullPiiRedactor,
    "vn_regex": VnRegexPiiRedactor,
    "presidio": PresidioPiiRedactor,
}


def build_pii_redactor(
    provider: str | None = None,
    **kwargs,
) -> "PiiRedactorPort":
    key = (provider or "").strip().lower() or "null"
    cls = _REGISTRY.get(key)
    if cls is None:
        logger.warning(
            "pii_redactor_unknown_provider_fallback_null",
            requested=provider,
            registered=sorted(_REGISTRY.keys()),
        )
        cls = NullPiiRedactor
    try:
        return cls(**kwargs)  # type: ignore[return-value]
    except (ImportError, NotImplementedError) as exc:
        logger.error(
            "pii_redactor_strategy_not_installed",
            requested=key,
            error=str(exc),
        )
        return NullPiiRedactor(**kwargs)


def list_providers() -> list[str]:
    return sorted(_REGISTRY.keys())


__all__ = ["build_pii_redactor", "list_providers"]
