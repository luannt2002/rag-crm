"""Enable AdapChunk legal-dense → HYBRID chunking (A/B-gated).

Revision: 0185
Prev:     0184

Flips the platform default ``adapchunk_legal_hybrid_enabled`` ON so the AdapChunk
selector routes VN admin/legal docs (many clause markers + prose-dense) to
HYBRID = HDT macro (citable hierarchy) + PROPOSITION micro (atomic-fact recall)
instead of pure HDT. Code shipped in commit 14ec96d, default OFF; this migration
is the A/B switch (plan 260608 Phase 1, rule #0).

Scope: platform-wide is safe — the selector's legal-hybrid branch only fires for
docs with >= DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES VN clause markers AND
total_words > DEFAULT_HYBRID_LONG_DOC_WORDS; non-legal docs never reach it and
keep their existing strategy. Only docs RE-CHUNKED after this flip pick up the
new behaviour (existing chunks are untouched until re-ingest).

A/B: re-chunk luat-giao-thong + thong-tu-09-2020 → claim-level Coverage vs the
flag-OFF baseline (luat COVERAGE=0.72, commit a8c6677). Keep ON only if Coverage
lifts with HALLU=0 held; otherwise ``downgrade`` (back to default OFF).

Reversible. Rule 7 (alembic). Rule #0 (measured before kept).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0185"
down_revision: str | None = "0184"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_KEY = "adapchunk_legal_hybrid_enabled"


def upgrade() -> None:
    op.execute(
        text("""
            INSERT INTO system_config (key, value, value_type, description, updated_at)
            VALUES (:k, CAST('true' AS jsonb), 'bool',
                    'AdapChunk: legal-dense docs → HYBRID (HDT macro + PROPOSITION micro). A/B plan 260608 Phase 1.',
                    NOW())
            ON CONFLICT (key) DO UPDATE SET
                value = CAST('true' AS jsonb), value_type = 'bool',
                description = EXCLUDED.description, updated_at = NOW()
        """).bindparams(k=_KEY)
    )


def downgrade() -> None:
    op.execute(text("DELETE FROM system_config WHERE key = :k").bindparams(k=_KEY))
