"""Pin DEFAULT_CR_ENHANCED_ENABLED at the value LEGAL-RETRIEVAL-FIX Phase 3
introduced.

Why a dedicated pin test:
- The constant is the **last-resort fallback** for the 3-tier resolve chain
  (per-bot column > plan_limits > system_config > constants). A silent
  flip back to ``False`` would re-create the "0/80 chunks enriched" gap
  for any tenant whose ``system_config`` row gets deleted by an operator
  cleanup script.
- The companion alembic migration (010r) seeds ``cr_enhanced_enabled=true``
  into ``system_config``; pairing the migration default with the constant
  default keeps the two SSoT lined up if the seed is ever dropped without
  also flipping the constant.
"""

from __future__ import annotations

from ragbot.shared.constants import DEFAULT_CR_ENHANCED_ENABLED


def test_default_cr_enhanced_enabled_is_false() -> None:
    """2026-06-17 Jina-migration mandate: ingest is pure-Jina by default.

    Enhanced CR is a per-chunk nano path (the O(n^2) ingest storm). Jina
    late_chunking now supplies cross-chunk context inside the embed pass (0 LLM),
    so the platform DEFAULT is OFF — safe-by-default so a cold-start before
    system_config loads never re-triggers the storm. Owners opt IN per-bot via
    plan_limits.cr_enhanced_enabled. (Reversed the Phase-3 True default.)
    """
    assert DEFAULT_CR_ENHANCED_ENABLED is False, (
        "Jina late_chunking supersedes per-chunk nano CR. A regression to True "
        "re-introduces the O(n^2) ingest storm at cold-start."
    )


def test_default_cr_enhanced_enabled_exported() -> None:
    """Constant must be in ``__all__`` so import-from-package consumers
    (currently ``DocumentService``) keep working after refactor.
    """
    from ragbot.shared import constants

    assert "DEFAULT_CR_ENHANCED_ENABLED" in constants.__all__
