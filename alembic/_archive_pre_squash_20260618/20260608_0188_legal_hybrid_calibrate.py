"""Seed AdapChunk legal-hybrid calibration knobs (S5, T1-Smartness).

Revision: 0188
Prev:     0187

Trigger (CONSOLIDATED_PROBLEMS_20260608 — AdapChunk threshold calibration):
  The legal-hybrid fast-path in ``shared/chunking.py::select_strategy``
  routes a clause-dense + prose-dense VN legal doc to ``hybrid`` (HDT macro
  for citation + PROPOSITION micro for atomic-fact recall) instead of pure
  ``hdt``. MEASURED 2026-06-08: real VN legal docs are clause-dense but SHORT
  (a single Luật/Thông tư article ~1134 words), well under the generic
  ``DEFAULT_HYBRID_LONG_DOC_WORDS`` (2000) gate — so the legal-hybrid path was
  inert. A dedicated lower floor ``DEFAULT_LEGAL_HYBRID_MIN_WORDS`` (800) lets
  genuinely legal-dense-but-short docs reach HYBRID, co-gated by a strong
  ``vn_markers`` count so generic short docs do NOT flip.

This migration seeds the two operator-tunable knobs into ``system_config``
so ops can A/B the feature and recalibrate the word floor WITHOUT redeploy.
Both default to the code constants, so applying this migration is a pure
no-op for behaviour until ``adapchunk_legal_hybrid_enabled`` is flipped on:

  - ``adapchunk_legal_hybrid_enabled``  = 'false' (DEFAULT_ADAPCHUNK_LEGAL_HYBRID_ENABLED)
  - ``adapchunk_legal_hybrid_min_words`` = '800'  (DEFAULT_LEGAL_HYBRID_MIN_WORDS)

Sacred-rule alignment:
  - Pure alembic DML (CLAUDE.md rule 7 — no psql hot-fix).
  - Reversible — downgrade deletes both seeded keys.
  - Default OFF — zero behaviour change until A/B validates the recall lift
    (rule #0, no unmeasured default change).
  - Zero-hardcode — seeded values mirror the shared/constants.py defaults;
    code reads system_config first, constant as fallback.
  - Domain-neutral — reuses the existing VN language-structure signal; no
    customer/brand/industry literal.
  - Idempotent — INSERT ... ON CONFLICT DO NOTHING; safe to re-run.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0188"
down_revision: str | None = "0187"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Seed the legal-hybrid flag + word-floor knobs (default = code constants)."""
    op.execute(text("""
        INSERT INTO system_config (key, value, value_type, description, updated_at)
        VALUES (
            'adapchunk_legal_hybrid_enabled',
            'false',
            'bool',
            'AdapChunk: route clause-dense + prose-dense VN legal docs to hybrid '
            '(HDT macro + PROPOSITION micro) instead of pure HDT. Default OFF; '
            'flip ON after a re-ingest A/B validates the multi-fact recall lift.',
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
    """))
    op.execute(text("""
        INSERT INTO system_config (key, value, value_type, description, updated_at)
        VALUES (
            'adapchunk_legal_hybrid_min_words',
            '800',
            'int',
            'AdapChunk: min total words for the legal-hybrid fast-path to fire. '
            'Lower than the generic 2000-word HYBRID gate so legal-dense-but-short '
            'docs (a Luat/Thong tu article ~1134 words) qualify; the strong '
            'vn_markers co-gate keeps generic short docs on their existing path.',
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
    """))


def downgrade() -> None:
    """Remove the seeded calibration knobs (code constants remain the fallback)."""
    op.execute(text("""
        DELETE FROM system_config
        WHERE key IN (
            'adapchunk_legal_hybrid_enabled',
            'adapchunk_legal_hybrid_min_words'
        )
    """))
