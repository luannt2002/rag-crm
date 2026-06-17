"""Query router Protocol — pre-retrieve coarse intent classifier.

A QueryRouter inspects the raw user query BEFORE embed + retrieve and
returns a coarse intent label that downstream stages use to:

* bias retrieval strategy (BM25 priority for ``structured_ref``,
  hybrid default for ``semantic``, bypass for ``smalltalk``)
* tune rerank cliff floors / top-k (tighter for ``hallu_trap``)
* select sysprompt template variant

This Port is distinct from the LLM ``UnderstandOutput`` classifier:
the router is a fast signal-cheap pre-filter (regex / small LLM),
the classifier produces the authoritative downstream label after
condensation. Both labels coexist on the graph state.

Default implementation is a Null Object that always returns the
``semantic`` label so wiring the Port is operator-OFF until a real
strategy is selected via ``system_config.query_router_provider``.

The Literal alias mirrors ``QUERY_INTENT_TYPES`` in
``shared/constants.py`` — keep both in sync (single source of truth
for the vocabulary is the constants tuple; this Literal is a typing
mirror for static analysis).
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

# Mirror of ``QUERY_INTENT_TYPES`` for static typing. The constants tuple
# remains the runtime SSoT; this alias only narrows return types so callers
# get IDE / mypy coverage without importing the tuple in type positions.
QueryIntent = Literal[
    "structured_ref",
    "comparison",
    "factoid",
    "smalltalk",
    "hallu_trap",
    "semantic",
]


@runtime_checkable
class QueryRouterPort(Protocol):
    """Pre-retrieve query routing abstraction.

    Implementations should be cheap (regex / cached LLM) and side-effect
    free so the orchestrator can call them in the hot path without
    measurable latency cost. Errors must NOT leak — fall back to
    ``"semantic"`` so the pipeline degrades to the default branch.
    """

    async def classify(self, query: str) -> QueryIntent:
        """Return the coarse query intent for ``query``.

        @param query: raw user query string (PII-redacted upstream).
        @return: one of the six ``QueryIntent`` labels.
        """
        ...


__all__ = ["QueryIntent", "QueryRouterPort"]
