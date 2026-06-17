"""Text Normalizer Protocol — Strategy Pattern for VN accent restore + i18n.

Strategy port for swap-able text normalisers (VN tone restoration,
case-folding, NFC/NFD normalisation, future JP / EN variants).

Default implementation is :class:`NullNormalizer` (passthrough). Heavy
ML adapters (e.g. BARTpho) are opt-in via system_config provider key.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TextNormalizerPort(Protocol):
    """Strategy interface for text normalisers."""

    async def normalize(self, text: str) -> str:
        """Return the normalised text. Implementations must NOT raise on
        empty input — passthrough an empty string."""
        ...

    def get_provider_name(self) -> str:
        ...


__all__ = ["TextNormalizerPort"]
