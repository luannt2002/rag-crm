"""Legacy ``"{prefix}\\n\\n{raw}"`` embedding-text strategy.

Mirrors the historical behaviour of ``DocumentService.ingest`` (pre-fix):
the LLM-generated enriched prefix is prepended to the raw chunk before the
dense encoder sees it. Kept as the default for backward compatibility with
already-ingested corpora — re-embedding under a different strategy would
silently break retrieval until the bot is re-indexed.
"""

from __future__ import annotations

STRATEGY_NAME = "prefix_plus_raw"


class PrefixPlusRawStrategy:
    """Concatenate ``enriched_prefix`` and ``raw_chunk`` with a blank line.

    When the prefix is empty / ``None`` the strategy degrades gracefully to
    embedding the raw chunk alone — no leading whitespace, no separator.
    """

    @property
    def name(self) -> str:
        return STRATEGY_NAME

    def build(self, *, raw_chunk: str, enriched_prefix: str | None) -> str:
        prefix = (enriched_prefix or "").strip()
        if not prefix:
            return raw_chunk
        return f"{prefix}\n\n{raw_chunk}"


__all__ = ["PrefixPlusRawStrategy", "STRATEGY_NAME"]
