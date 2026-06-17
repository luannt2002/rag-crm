"""DocumentProfileAnalyzer strategy registry — DI factory keyed on provider.

AdapChunk Layer 3 refine. Pattern mirrors
``infrastructure.hyde.registry``: the DI container reads
``doc_profile_analyzer_provider`` (default ``"rule_based"``) from
``system_config`` and asks the registry for the matching
``DocumentProfileAnalyzerPort``. Adding a new provider = drop a new file
in this package and register it here — no edits to ``document_service``
or ``chunking`` required.

Unknown provider strings raise ``ValueError`` so a typo at the DB
config layer surfaces loudly rather than silently degrading the ingest
profiler.
"""

from __future__ import annotations

from typing import Any

from ragbot.application.ports.doc_profile_port import DocumentProfileAnalyzerPort
from ragbot.infrastructure.doc_profile.null_doc_profile import NullDocumentProfileAnalyzer
from ragbot.infrastructure.doc_profile.rule_based_doc_profile import (
    RuleBasedDocumentProfileAnalyzer,
)

_REGISTRY: dict[str, type[DocumentProfileAnalyzerPort]] = {
    "null": NullDocumentProfileAnalyzer,
    "rule_based": RuleBasedDocumentProfileAnalyzer,
}


def build_doc_profile_analyzer(
    provider: str, **kwargs: Any
) -> DocumentProfileAnalyzerPort:
    """Construct the document-profile analyzer matching ``provider``.

    @param provider: registry key (``"null"`` | ``"rule_based"``).
    @param kwargs: forwarded to the strategy constructor (both built-in
        strategies are stateless / kw-less today, but the contract is
        preserved for future LLM-backed analyzers).
    @return: ``DocumentProfileAnalyzerPort`` instance.
    @raise ValueError: unknown provider key — flag a typo loudly.
    """
    key = (provider or "").strip().lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        raise ValueError(
            f"unknown doc_profile provider: {provider!r}; "
            f"registered={sorted(_REGISTRY.keys())}"
        )
    instance: DocumentProfileAnalyzerPort = cls(**kwargs)
    return instance


def list_providers() -> list[str]:
    """Return registered provider keys (sorted, for stable test asserts)."""
    return sorted(_REGISTRY.keys())


__all__ = ["build_doc_profile_analyzer", "list_providers"]
