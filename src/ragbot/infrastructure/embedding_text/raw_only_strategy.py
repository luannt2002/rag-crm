"""``raw_only`` embedding-text strategy — embed the un-enriched chunk.

The enriched prefix is still persisted on the ``content`` column (so BM25 +
cross-encoder rerank see it) but the dense encoder never tokenises it. This
matters for short keyword queries: a ~150-char LLM summary in front of a
chunk dilutes the cosine signal — the chunk that literally contains "Điều
3. Nguyên tắc chung" loses to a sibling whose prefix happens to say "Đoạn 3
nằm trong phần ...".

Opt-in via:
* ``bots.plan_limits.embedding_text_strategy = "raw_only"`` (per-bot), or
* ``system_config.embedding_text_strategy = "raw_only"`` (platform default).

Re-embedding is REQUIRED after toggling.
"""

from __future__ import annotations

STRATEGY_NAME = "raw_only"


class RawOnlyStrategy:
    """Embed the raw chunk text only; ignore the enriched prefix."""

    @property
    def name(self) -> str:
        return STRATEGY_NAME

    def build(self, *, raw_chunk: str, enriched_prefix: str | None) -> str:
        # `enriched_prefix` accepted for signature symmetry; intentionally
        # discarded — this strategy's whole point is to keep the dense
        # encoder clean of LLM-summary tokens.
        del enriched_prefix
        return raw_chunk


__all__ = ["RawOnlyStrategy", "STRATEGY_NAME"]
