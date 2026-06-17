"""Disable the two remaining nano-in-ingest paths so ingest is pure-Jina.

0228 turned off ``contextual_retrieval_enabled`` (the per-chunk nano CR), but two
OTHER ingest-time nano paths remained on and kept the O(n^2) storm + blocked the
embed step (measured: chunks=0 after 124s, 54 nano calls):

* ``enrichment_enabled`` — legacy per-chunk context enrichment
  (ingest_stages_enrich.py:433), nano, blocks embed_store.
* ``narrate_then_embed_enabled`` — table/LaTeX block -> natural-language
  sentence via nano (the spreadsheet storm), default True (no config row).

With Jina ``late_chunking`` the cross-chunk context lands inside the embedding
pass, so all three nano paths are redundant. Turning them off makes ingest:
parse -> chunk -> Jina embed -> store (no OpenAI, no TPM storm, queryable in
seconds). Reversible: downgrade re-enables both.

Trade-off (to be measured per rule #0): without narrate, table blocks embed as
raw cell text rather than a narrated sentence — late_chunking + structured chunk
should preserve retrievability; the query load test validates whether table
recall holds.
"""
from alembic import op

revision = "0230"
down_revision = "0229"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # enrichment_enabled row exists; flip to false.
    op.execute("UPDATE system_config SET value = 'false'::jsonb WHERE key = 'enrichment_enabled'")
    # narrate_then_embed_enabled has no row (defaults True in code) — upsert false.
    op.execute(
        """
        INSERT INTO system_config (key, value, updated_at)
        SELECT 'narrate_then_embed_enabled', 'false'::jsonb, now()
        WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'narrate_then_embed_enabled')
        """
    )
    op.execute("UPDATE system_config SET value = 'false'::jsonb WHERE key = 'narrate_then_embed_enabled'")


def downgrade() -> None:
    op.execute("UPDATE system_config SET value = 'true'::jsonb WHERE key = 'enrichment_enabled'")
    op.execute("UPDATE system_config SET value = 'true'::jsonb WHERE key = 'narrate_then_embed_enabled'")
