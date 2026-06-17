"""Entity Extractor Protocol — Strategy Pattern for query-side NER.

Strategy port for swap-able entity extractors used by the multi-query
expansion service. The contract returns a *ranked list of entity strings*
extracted from the user query — rare/specific tokens (proper nouns,
numerics, brand-like ALL-CAPS, language-specific named entities) come
first so callers (e.g. ``expand_query_with_entities``) can use them as
BM25-friendly verbatim variants when the LLM rewriter drops critical
signal terms.

Default implementation is :class:`NullExtractor` (returns ``[]``).
Per-language adapters (e.g. ``vi_underthesea`` for Vietnamese,
``en_simple`` for capitalized-multi-word English) are opt-in via the
registry + ``system_config.entity_extractor_provider`` (master) or
per-bot ``pipeline_config.entity_extractor_provider`` (override).

Vertical-agnostic: the strategy must NOT bake in domain keywords
(industry, brand, product). Domain expansion belongs in
``bots.custom_vocabulary`` (per-bot) — extractors return *generic*
named-entity tokens which the BM25 query path then matches against
the per-bot corpus regardless of vertical.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EntityExtractorPort(Protocol):
    """Strategy interface for query-side named-entity extractors.

    Implementations: ``NullExtractor`` (default OFF, returns empty),
    ``ViUnderthesseaExtractor`` (Vietnamese, POS+NER hybrid),
    ``EnSimpleExtractor`` (English, capitalized-multi-word + numerics).

    Contract:
      - MUST NOT raise on empty / whitespace input — return ``[]``.
      - MUST NOT raise on language mismatch — return ``[]`` (caller
        will fall back to the language-agnostic paraphrase variants).
      - SHOULD return the *most rare/specific* entity first (so callers
        capping the list at ``max_entities`` keep the highest-signal
        tokens).
      - MUST be vertical-agnostic — no industry / brand / product
        literals baked into the strategy.
    """

    async def extract(self, query: str, *, language: str) -> list[str]:
        """Return ranked entity strings extracted from ``query``.

        @param query: raw user query (already condensed/rewritten upstream).
        @param language: bot language hint (e.g. ``"vi"``, ``"en"``).
            Strategy decides whether it can serve this language; if not,
            return ``[]`` so the caller falls back to plain paraphrase.
        @return: ranked list of entity strings (no duplicates, no empty
            strings). Empty list when no entities are found OR the
            strategy does not support the requested language.
        """
        ...

    def get_provider_name(self) -> str:
        """Identifier for observability (e.g. ``"null"``, ``"vi_underthesea"``)."""
        ...


__all__ = ["EntityExtractorPort"]
