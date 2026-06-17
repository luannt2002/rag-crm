"""S1 — embedding-text strategy + hybrid RRF weights + structured-ref extraction.

Seeds platform defaults for three related retrieval-quality knobs introduced
by Stream S1 of the 2026-05-11 multi-stream ship (RAG_Master_of_Masters_
DeepDive Phase-1):

1. ``embedding_text_strategy = "prefix_plus_raw"`` — controls what text the
   dense encoder sees during ingest. Legacy default preserves backward
   compatibility with already-ingested corpora. Operators flip to
   ``"raw_only"`` once they re-ingest, fixing the short-keyword dilution
   issue documented in
   ``plans/260511-handoff-coder-master-fix/CODER_MASTER_FIX_HANDOFF.md``
   (e.g. a structured-corpus query for "Điều 3?" was picking up "Đoạn 3
   nằm trong ..." in the enriched prefix instead of literal "Điều 3.
   Nguyên tắc chung" in the raw chunk). Per-bot override via
   ``bots.plan_limits.embedding_text_strategy``.
2. ``structured_ref_extraction_enabled = true`` — ingest scans each chunk
   for Vietnamese legal anchors (Điều / Chương / Khoản / Mục / Phụ lục)
   and persists matches to ``metadata_json.article_no`` etc. The regex is
   Latin-script + Roman numerals only — domain-neutral; bots without
   structured refs simply see empty fields.
3. ``hybrid_rrf_bm25_weight = 0.5`` / ``hybrid_rrf_vector_weight = 0.5`` —
   makes the RRF fusion formula tunable. Equal weights reproduce the
   historical formula; bumping ``bm25_weight`` lifts keyword-heavy queries
   without touching the dense top-k pool.

Idempotent ``ON CONFLICT (key) DO UPDATE`` so re-running on a DB already at
the new value is a no-op. ``downgrade`` reverts the keys to their pre-S1
default (legacy ``prefix_plus_raw`` was already the implicit default; the
two RRF weights are removed so future code reads them via the constant
fallback).

Revision ID: 0078
Revises: 0077
Create Date: 2026-05-11
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0078a"
down_revision = "0078"
branch_labels = None
depends_on = None


_TUNING_ROWS: tuple[tuple[str, str, str, str], ...] = (
    (
        "embedding_text_strategy",
        "prefix_plus_raw",
        "string",
        "Embedding-text strategy provider key. 'prefix_plus_raw' (legacy "
        "default) feeds {enriched_prefix}\\n\\n{raw_chunk} to the dense "
        "encoder; 'raw_only' feeds raw_chunk only (fixes short-keyword "
        "dilution; re-embed REQUIRED after toggling). Per-bot override "
        "via bots.plan_limits.embedding_text_strategy.",
    ),
    (
        "structured_ref_extraction_enabled",
        "true",
        "bool",
        "Enable Vietnamese legal-anchor extraction at ingest (Điều / "
        "Chương / Khoản / Mục / Phụ lục → metadata_json.article_no etc.). "
        "Domain-neutral: bots without structured refs see empty fields.",
    ),
    (
        "hybrid_rrf_bm25_weight",
        "0.5",
        "float",
        "RRF fusion weight for the BM25 sub-query in hybrid_search. "
        "Equal-weighted with hybrid_rrf_vector_weight reproduces the "
        "historical 1/(rrf_k+rank) formula. Bumping lifts keyword-heavy "
        "queries (legal article refs, product SKUs).",
    ),
    (
        "hybrid_rrf_vector_weight",
        "0.5",
        "float",
        "RRF fusion weight for the dense-vector sub-query in "
        "hybrid_search. Symmetric counterpart of hybrid_rrf_bm25_weight.",
    ),
)


_DOWNGRADE_KEYS: tuple[str, ...] = (
    "embedding_text_strategy",
    "structured_ref_extraction_enabled",
    "hybrid_rrf_bm25_weight",
    "hybrid_rrf_vector_weight",
)


def upgrade() -> None:
    for key, value, value_type, description in _TUNING_ROWS:
        op.execute(
            text(
                """
                INSERT INTO system_config (key, value, value_type, description)
                VALUES (:key, :value, :value_type, :description)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    value_type = EXCLUDED.value_type,
                    description = EXCLUDED.description
                """
            ).bindparams(
                key=key,
                value=value,
                value_type=value_type,
                description=description,
            )
        )


def downgrade() -> None:
    """Remove the S1 seed rows; code falls back to constants on read."""
    for key in _DOWNGRADE_KEYS:
        op.execute(
            text("DELETE FROM system_config WHERE key = :key").bindparams(key=key)
        )
