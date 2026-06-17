"""DocumentProfileAnalyzer Port — contract for AdapChunk Layer 3 profilers.

Inspired by the internal AdapChunk blueprint + Ekimetrics LREC 2026 proven
adaptive-chunking metrics. The Port exposes ONE method that turns a raw
document body into a populated ``DocumentProfile`` entity — 10 quantitative
features rule-based, no LLM, no external lang-detect dependency.

Owner-opt-in via feature flag ``adapchunk_layer3_doc_profile_enabled`` in
``system_config``. While the flag is OFF the platform uses the legacy
``analyze_document() -> dict`` path for select_strategy; flipping the flag
ON lights up the entity refine + enriched structlog event so operators can
A/B-test the refined profile without redeploy.

Implementations:
    - ``NullDocumentProfileAnalyzer`` — returns a zero-valued
      ``DocumentProfile`` (default-OFF baseline).
    - ``RuleBasedDocumentProfileAnalyzer`` — pure regex/heuristic count
      of headings, tables, formulas, images, code blocks + VN-diacritic
      language detection.

The Port deliberately does NOT carry tenant or trace identifiers — the
analyzer is stateless and side-effect free; observability lives at the
call site.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ragbot.domain.entities.document_profile import DocumentProfile


@runtime_checkable
class DocumentProfileAnalyzerPort(Protocol):
    """Analyze a raw document body into a populated ``DocumentProfile``.

    Implementations MUST be deterministic and side-effect free: the same
    input text MUST yield the same profile. Empty / whitespace-only input
    MUST return a well-formed zero-valued profile (no exceptions) so the
    ingest hot path degrades gracefully on edge-case documents.
    """

    def analyze(self, text: str) -> DocumentProfile:
        """Return the populated ``DocumentProfile`` for ``text``.

        @param text: raw document body (post-parser, post-normalisation).
        @return: ``DocumentProfile`` entity with all 10 quantitative
            features populated. Null implementations return a zero-valued
            profile; rule-based implementations populate every field.
        """
        ...


__all__ = ["DocumentProfileAnalyzerPort"]
