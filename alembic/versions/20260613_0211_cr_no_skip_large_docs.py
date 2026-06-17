"""Stop skipping Contextual Retrieval on large customer documents.

Root cause (2026-06-13): ``contextual_retrieval_max_doc_chars`` was seeded at
50_000 — a band-aid that DROPPED CR enrichment for any file over ~50K chars.
Real customer corpora average ~200K chars/file (≈50K tokens) and ~1M chars/bot
across 5 files, so the 50K cap silently discarded ~75% of a bot's contextual
enrichment. That is a quality regression masquerading as a cost guard.

Expert fix (research-backed — Anthropic CR blog + arxiv ablations):
  * TABULAR docs (table_csv / table_dual_index) skip per-chunk LLM enrichment
    via the ROW GATE (``enrich_row_gate_enabled``, code default ON) — rows are
    self-describing (header + key:value), 0 LLM cost regardless of size.
  * PROSE docs keep WHOLE-DOCUMENT context (NOT a local window — windowing
    fails cross-section coreference, e.g. VN legal "điều khoản chung", and it
    defeats prompt caching). Cost is bounded by prompt caching the doc prefix
    (OpenAI auto-prefix cache on the gpt-4.1 family; Anthropic ephemeral cache
    if the CR model is switched to Haiku). With caching, a 200K-char prose
    file enriches for cents, so the size cap is no longer a cost lever.

This migration lifts the operational gate 50_000 → 300_000 (covers the stated
~200K avg file with headroom). The constant ``DEFAULT_CR_MAX_DOC_CHARS`` =
5_000_000 remains the hard runaway ceiling for pathological single files.
Behaviour-neutral for tabular bots (row-gated); enables full CR for prose.
"""
import sqlalchemy as sa
from alembic import op

revision = "0211"
down_revision = "0210"
branch_labels = None
depends_on = None

_KEY = "contextual_retrieval_max_doc_chars"
_NEW = 300_000
_OLD = 50_000


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO system_config (key, value) "
        "VALUES (:k, CAST(:v AS jsonb)) "
        "ON CONFLICT (key) DO UPDATE SET value = CAST(:v AS jsonb)"
    ), {"k": _KEY, "v": str(_NEW)})


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO system_config (key, value) "
        "VALUES (:k, CAST(:v AS jsonb)) "
        "ON CONFLICT (key) DO UPDATE SET value = CAST(:v AS jsonb)"
    ), {"k": _KEY, "v": str(_OLD)})
