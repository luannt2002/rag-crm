"""Re-enable narrate-then-embed as the (now-safe) per-table description path.

0230 disabled narrate because it was the unbounded spreadsheet ingest storm.
Since then the embedder/LLM paths gained: per-model TPM limiter (paces nano so a
table burst queues instead of 429-storming) + the circuit-breaker 429-carve-out
(a 429 no longer trips the shared provider breaker, so ingest narrate can never
fast-fail the live query path). narrate is now O(tables) bounded + paced =
RAG-Anything "Technique 1" (one LLM description per table block, embedded
alongside the rows) — the measured-correct fix for aggregation/numeric questions
("đắt nhất", "dưới 500k") that raw-CSV chunks can't satisfy.

Re-enables the DB flag only (constant default stays False = safe cold-start).
Reversible: downgrade turns it back off. Effect measured by re-load-test
(faithfulness HALLU=0 must hold; table-question coverage should rise).
"""
from alembic import op

revision = "0234"
down_revision = "0233"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE system_config SET value = 'true'::jsonb WHERE key = 'narrate_then_embed_enabled'")


def downgrade() -> None:
    op.execute("UPDATE system_config SET value = 'false'::jsonb WHERE key = 'narrate_then_embed_enabled'")
