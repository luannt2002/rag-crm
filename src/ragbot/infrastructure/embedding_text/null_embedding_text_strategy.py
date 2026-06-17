"""Null embedding-text strategy — pass-through for safe default fallback.

Used when the registry receives an unknown / empty provider key. Behaviour
matches ``PrefixPlusRawStrategy`` (legacy) so existing corpora keep working
even if the operator typos the ``system_config`` value.

NOT registered as the default strategy itself — operators should pick
``prefix_plus_raw`` or ``raw_only`` explicitly. The Null strategy is a
fail-soft floor: registry hits it only on misconfig.
"""

from __future__ import annotations

STRATEGY_NAME = "null"


class NullEmbeddingTextStrategy:
    """Pass-through: prepends prefix iff non-empty, else returns raw chunk."""

    @property
    def name(self) -> str:
        return STRATEGY_NAME

    def build(self, *, raw_chunk: str, enriched_prefix: str | None) -> str:
        prefix = (enriched_prefix or "").strip()
        if not prefix:
            return raw_chunk
        return f"{prefix}\n\n{raw_chunk}"


__all__ = ["NullEmbeddingTextStrategy", "STRATEGY_NAME"]
