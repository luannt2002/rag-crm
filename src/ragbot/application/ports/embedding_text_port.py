"""Embedding-text strategy port.

Decouples the *embedding input* from the *persisted content*. Historically,
``DocumentService.ingest`` fed ``enriched_prefix + raw_chunk`` straight into
the embedder. For short keyword queries (e.g. "Điều 3?") the ~150-char LLM
summary prefix dilutes the cosine signal — the chunk that literally contains
"Điều 3. Nguyên tắc chung" loses to a sibling whose prefix happens to say
"Đoạn 3 nằm trong phần ...".

The strategy port lets operators choose how the *embedded text* is built per
bot / per platform default. Persisted ``content`` column and citation paths
remain unchanged — only the bytes passed to ``embedder.embed_batch`` differ.

Implementations:
    * ``PrefixPlusRawStrategy`` — legacy ``"{prefix}\\n\\n{raw}"`` (default
      for backward compat with already-ingested corpora).
    * ``RawOnlyStrategy`` — embed ``raw_chunk`` only; the prefix is kept on
      the persisted ``content`` for BM25 + rerank but never fed to the dense
      encoder. Opt-in via ``embedding_text_strategy="raw_only"``.

Wiring lives in ``infrastructure/embedding_text/registry.py``. Bot-owner
override goes through ``bots.plan_limits.embedding_text_strategy``; the
platform default is read from ``system_config.embedding_text_strategy``;
final fallback = ``DEFAULT_EMBEDDING_TEXT_STRATEGY`` constant.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingTextStrategyPort(Protocol):
    """Build the text string handed to the embedder for a single chunk.

    Pure function: implementations MUST NOT do I/O or mutate state. The two
    inputs are the raw chunk text (split output, pre-enrichment) and the
    LLM-generated enriched prefix (may be empty). Implementations decide
    which (or both) feed the dense embedder.
    """

    @property
    def name(self) -> str:
        """Stable identifier used in logs / metrics (e.g. ``"raw_only"``)."""
        ...

    def build(self, *, raw_chunk: str, enriched_prefix: str | None) -> str:
        """Return the text to embed.

        @param raw_chunk: chunk text post-split, pre-enrichment.
        @param enriched_prefix: LLM-generated context prefix (may be empty
            string or ``None`` when enrichment was skipped).
        @return: the string the embedder will tokenize.
        """
        ...


__all__ = ["EmbeddingTextStrategyPort"]
