"""RuleChunkingStrategyResolver — deterministic resolver (default + fallback).

The Null-object / default implementation of ``ChunkingStrategyResolverPort``:
wraps the pure-rule ``select_strategy`` (no LLM, no I/O) so behaviour is
byte-identical to today when ``chunking_strategy_provider="rule"`` AND so the
LLM resolver has a safe path to degrade to on any failure.

The deterministic ``apply_cross_check`` guard (spec Tầng 5) is applied by the
CALLER on whatever decision a resolver returns — both resolvers therefore
return the RAW selector pick, keeping selection (Tầng 3/4) and validation
(Tầng 5) as separate layers per the AdapChunk spec.

By the time a resolver runs in the hybrid flow the CSV / legal fast-paths
have already been taken, so ``is_csv_format`` / ``vn_hierarchical_markers``
are not needed here — the weighted scorer decides among the prose strategies.
"""
from __future__ import annotations

from typing import cast

from ragbot.application.ports.strategy_ports import ChunkingDecision
from ragbot.domain.entities.document_profile import DocumentProfile
from ragbot.shared.chunking.analyze import select_strategy
from ragbot.shared.types import BotId, ChunkingStrategyName, TenantId


def profile_to_dict(dp: DocumentProfile) -> dict:
    """Map the DocumentProfile entity onto the dict shape ``select_strategy`` reads."""
    return {
        "total_headings": dp.heading_counts.total,
        "total_words": dp.total_words,
        "heading_counts": {"h2": dp.heading_counts.h2},
        "table_count": dp.table_count,
        "avg_text_length": dp.avg_text_block_length,
        "mixed_content_score": dp.mixed_content_score,
        "has_toc": dp.has_toc,
        # CSV / legal fast-paths are decided BEFORE the resolver in the flow.
        "is_csv_format": False,
        "vn_hierarchical_markers": 0,
    }


class RuleChunkingStrategyResolver:
    """Deterministic rule-based resolver — no LLM, no I/O."""

    @staticmethod
    def get_provider_name() -> str:
        return "rule"

    async def resolve_strategy(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
        document_profile: DocumentProfile,
        blocks: list | None = None,  # noqa: ARG002 — rule path scores on profile only
    ) -> ChunkingDecision:
        strategy, confidence = select_strategy(profile_to_dict(document_profile))
        return ChunkingDecision(
            strategy=cast("ChunkingStrategyName", strategy),
            forced=False,
            confidence=round(confidence, 2),
            reasoning="rule-based weighted scorer (no LLM)",
        )


__all__ = ["RuleChunkingStrategyResolver", "profile_to_dict"]
