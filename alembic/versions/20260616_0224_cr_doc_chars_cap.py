"""Cap contextual_retrieval_max_doc_chars 300000 → 24000 (per-call token throttle).

Root of the ingest TPM stall: the CR enrichment prompt embeds the WHOLE document
(`Document: {doc}`) with EVERY chunk. At max_doc_chars=300000 each call is
~25k tokens (observed `Requested 25297` in the 429s). A large doc (legal Thông
tư) × N chunks cold-fans-out far past the 200k per-model TPM even on nano's
separate bucket — so lowering concurrency does not help (per-call tokens ×
throughput still exceeds TPM).

Capping the document context to 24000 chars (~6k tokens) drops the per-call
payload ~4×, so the same fan-out fits the budget and ingest completes. 24k of
leading document context is ample to *situate* a chunk (the CR task) — full-doc
context was overkill. Pairs with the warm-then-fan-out prompt-cache change and
the enrichment→nano routing (0223) for the full fast+cost-controlled ingest.

Follow-up (not in this migration): wire DEFAULT_CR_CONTEXT_WINDOW_CHARS so each
chunk gets its LOCAL neighbourhood as context instead of a doc prefix — the
proper Anthropic-CR-at-scale pattern. See
reports/CASE_STUDY_INGEST_TPM_COST_20260616.md.

Governed via alembic (system_config in no-psql-hotfix set). Reversible.
"""
import sqlalchemy as sa
from alembic import op

revision = "0224"
down_revision = "0223"
branch_labels = None
depends_on = None

_SC = sa.table("system_config", sa.column("key", sa.String), sa.column("value", sa.Text))
_KEY = "contextual_retrieval_max_doc_chars"


def _set(value: str) -> None:
    op.execute(_SC.update().where(_SC.c.key == op.inline_literal(_KEY)).values(value=value))


def upgrade() -> None:
    _set("24000")


def downgrade() -> None:
    _set("300000")
