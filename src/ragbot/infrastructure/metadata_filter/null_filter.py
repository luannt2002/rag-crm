"""NullFilter — Null Object pattern for query-side metadata pre-filter.

Default selection when ``system_config.metadata_filter_provider`` is
missing or set to ``"null"``. Returns an empty dict so the caller
(retrieve node in :mod:`ragbot.orchestration.query_graph`) skips the
JSONB containment WHERE clause and runs hybrid_search unmodified.

Selecting Null is the operator-OFF baseline — keeps the article-aware
pre-filter strictly opt-in per-bot to preserve backward compatibility
for tenants whose corpus is not structurally segmented.
"""

from __future__ import annotations


class NullFilter:
    """No-op metadata filter — :meth:`extract` returns ``{}`` for any input."""

    def __init__(self, **_: object) -> None:
        # Accept and ignore any kwargs so the registry can build NullFilter
        # with the same signature as a real provider (e.g. ``patterns=[...]``
        # passed unconditionally from the DI container).
        return

    @staticmethod
    def get_provider_name() -> str:
        return "null"

    def extract(self, query: str) -> dict[str, str]:
        # ``query`` is intentionally ignored — Null is content-agnostic.
        return {}


__all__ = ["NullFilter"]
