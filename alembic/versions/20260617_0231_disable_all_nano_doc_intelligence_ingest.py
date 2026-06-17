"""Disable ALL remaining nano/mini doc-intelligence ingest paths → pure-Jina.

0228+0230 turned off CR-classic / enrichment / narrate, but the ingest pipeline
has MORE LLM-analysis steps that are NOT embedding and kept the storm alive
(legal doc stuck at 0 chunks, 19k-token nano calls):

* ``cr_enhanced_enabled`` — the WA-3 "Enhanced CR", a SECOND contextual-retrieval
  implementation independent of contextual_retrieval_enabled. Per-chunk nano with
  full-doc context = the O(n^2) blocker still firing. THIS is the legal blocker.
* ``structured_ref_extraction_enabled`` — legal Điều/Chương reference extraction,
  sends the full legal doc to the LLM (heavy on banking/legal corpora).

Both are redundant with Jina late_chunking for retrieval context. Turning them
off makes ingest truly: parse → chunk → Jina embed → store (zero OpenAI on the
ingest path; ChatGPT only at query time).

Kept ON (intentionally, NOT a storm):
* ``metadata_extraction_enabled`` — returns {} unless a metadata_extraction_system_prompt
  is configured (no-op for these bots), and powers query-time metadata filtering;
  mini, per-doc, bounded. Leave on.

Reversible: downgrade re-enables both. If query-side recall on legal articles
drops without structured_ref, re-enable JUST that one (it is the cheaper of the
two) rather than reviving cr_enhanced.
"""
from alembic import op

revision = "0231"
down_revision = "0230"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE system_config SET value = 'false'::jsonb WHERE key = 'cr_enhanced_enabled'")
    op.execute("UPDATE system_config SET value = 'false'::jsonb WHERE key = 'structured_ref_extraction_enabled'")


def downgrade() -> None:
    op.execute("UPDATE system_config SET value = 'true'::jsonb WHERE key = 'cr_enhanced_enabled'")
    op.execute("UPDATE system_config SET value = 'true'::jsonb WHERE key = 'structured_ref_extraction_enabled'")
