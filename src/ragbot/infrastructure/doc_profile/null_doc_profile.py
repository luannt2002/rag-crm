"""NullDocumentProfileAnalyzer — Null Object for AdapChunk Layer 3.

Default-OFF baseline. ``analyze(text)`` returns a zero-valued
``DocumentProfile`` so the call site can wire the Port unconditionally
and still preserve the dict-path baseline semantics (no entity-based
strategy switch, no enriched profile event) until the operator flips
``adapchunk_layer3_doc_profile_enabled`` to True.
"""

from __future__ import annotations

import structlog

from ragbot.domain.entities.document_profile import DocumentProfile, HeadingCounts
from ragbot.shared.constants import DEFAULT_LANG_DETECT_FALLBACK

logger = structlog.get_logger(__name__)


class NullDocumentProfileAnalyzer:
    """No-op profiler — always returns a zero-valued ``DocumentProfile``."""

    @staticmethod
    def get_provider_name() -> str:
        return "null"

    def analyze(self, text: str) -> DocumentProfile:
        """Return a zero-valued ``DocumentProfile`` independent of ``text``.

        Logged at debug so an operator can confirm the Null branch is in
        effect without spamming the hot-path logs.
        """
        logger.debug("null_doc_profile_bypass", text_chars=len(text or ""))
        return DocumentProfile(
            heading_counts=HeadingCounts(),
            has_toc=False,
            table_count=0,
            table_avg_rows=0.0,
            formula_count=0,
            image_count=0,
            code_block_count=0,
            avg_text_block_length=0.0,
            heading_ratio=0.0,
            mixed_content_score=0.0,
            detected_language=DEFAULT_LANG_DETECT_FALLBACK,
            total_blocks=0,
            total_words=0,
        )


__all__ = ["NullDocumentProfileAnalyzer"]
