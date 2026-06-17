"""Metadata Filter Protocol — Strategy Pattern for query-side pre-filter.

A MetadataFilter inspects the raw user query BEFORE embed + retrieve and
returns a JSONB-containment-ready dict that the vector store uses to
pre-filter chunks via ``WHERE metadata_json @> ...``. This sidesteps the
dense encoder's weakness on short keyword + number queries (e.g. legal
article references) by narrowing the candidate pool to chunks whose
ingest-side metadata already matched the structural anchor in the query.

This Port is distinct from the LLM-based query intent extractor
(``application/services/query_intent_extractor.py``): a MetadataFilter
implementation is fast (regex / pure lookup) and side-effect free, so the
orchestrator can call it in the hot path without an LLM round-trip.

Default implementation is :class:`NullFilter` (returns ``{}``).
Strategy adapters are opt-in via the registry +
``system_config.metadata_filter_provider`` (master) or per-bot
``pipeline_config.metadata_filter_provider`` (override).

Vertical-agnostic: the strategy must NOT bake in domain keywords
(industry, brand, product). Pattern lists belong in
``system_config.article_ref_patterns`` (operator-supplied) — strategies
return *generic* structural keys (``article_no``, ``chapter_no``, ...)
which the hybrid_search WHERE clause matches against the per-bot corpus
regardless of vertical.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MetadataFilterPort(Protocol):
    """Strategy interface for query-side metadata pre-filter extractors.

    Implementations:
      - :class:`NullFilter` — default OFF, returns ``{}`` for every query.
      - :class:`ArticleAwareFilter` — regex-driven structural-reference
        detector seeded from operator config.

    Contract:
      - MUST NOT raise on empty / whitespace input — return ``{}``.
      - MUST NOT raise on pattern mismatch — return ``{}``.
      - MUST return only ``str`` values (JSONB persistence + index lookup
        require homogeneous types).
      - Result keys SHOULD match the ingest-side metadata schema so the
        downstream ``WHERE metadata_json @> :filter`` query lands hits.
    """

    def extract(self, query: str) -> dict[str, str]:
        """Return a JSONB-containment-ready filter dict for ``query``.

        @param query: raw user query string (PII-redacted upstream).
        @return: dict with structural keys (e.g. ``article_no``) or ``{}``
            when no structural anchor is detected. Empty dict ==
            "skip the filter — run unmodified hybrid_search".
        """
        ...


__all__ = ["MetadataFilterPort"]
