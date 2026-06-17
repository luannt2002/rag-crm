"""[T1-Smartness] Seed article_ref_patterns for ArticleAwareFilter.

Revision ID: 010q
Revises: 010p
Create Date: 2026-05-20

Phase 2 of LEGAL-RETRIEVAL-FIX. The ArticleAwareFilter strategy
(``infrastructure/metadata_filter/article_aware_filter.py``) is DI-injected
via ``system_config.metadata_filter_provider='article_aware'`` and pulls
its regex pattern list from ``system_config.article_ref_patterns``. Until
this seed migration the key did not exist, so ``ArticleAwareFilter.extract()``
returned ``{}`` and the strategy silently degraded to a no-op — defeating
the entire point of having it wired in the orchestrator.

The patterns mirror the keywords accepted by the ingest-side
``extract_structured_refs`` (so query-side filter keys line up with the
ingest-side metadata schema):

* ``article``  → ``article_no``   (Điều N)
* ``clause``   → ``clause_no``    (Khoản N)
* ``section``  → ``section_no``   (Mục N)
* ``appendix`` → ``appendix_no``  (Phụ lục X)
* ``chapter``  → ``chapter_no``   (Chương ROMAN | N)

Idempotent: ``ON CONFLICT (key) DO NOTHING`` preserves any operator
override that may have been set manually.
"""

from __future__ import annotations

import json
import logging

from alembic import op


logger = logging.getLogger(__name__)

revision: str = "010q"
down_revision: str | None = "010p"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# Pattern list — operator can override per-tenant by overwriting the
# JSON value in ``system_config``. Schema validated by
# ``ArticleAwareFilter._compile_patterns`` at runtime (malformed entries
# are logged + skipped, the filter degrades gracefully).
_ARTICLE_REF_PATTERNS: list[dict[str, str]] = [
    {
        "name": "article",
        "regex": r"\bĐiều\s+(\d{1,4})\b",
        "flags": "IGNORECASE",
    },
    {
        "name": "clause",
        "regex": r"\bKhoản\s+(\d{1,4})\b",
        "flags": "IGNORECASE",
    },
    {
        "name": "section",
        "regex": r"\bMục\s+(\d{1,4})\b",
        "flags": "IGNORECASE",
    },
    {
        "name": "appendix",
        "regex": r"\bPhụ\s+lục\s+([A-Z0-9]{1,4})\b",
        "flags": "IGNORECASE",
    },
    {
        "name": "chapter",
        "regex": r"\bChương\s+([IVXLCDM]{1,6}|\d{1,4})\b",
        "flags": "IGNORECASE",
    },
]


def upgrade() -> None:
    """Insert article_ref_patterns row; preserve any pre-existing operator
    override via ON CONFLICT DO NOTHING.
    """
    op.execute(
        f"""
        INSERT INTO system_config (key, value, value_type, description, updated_at)
        VALUES (
            'article_ref_patterns',
            '{json.dumps(_ARTICLE_REF_PATTERNS, ensure_ascii=True)}'::jsonb,
            'json',
            'Regex patterns for ArticleAwareFilter (VN legal corpora — '
            'structural anchors mapping to ingest-side metadata keys). '
            'Per-tenant override safe via UPDATE.',
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    """Drop the seeded row. Operator overrides written via UPDATE are
    also removed — keep that in mind before rolling back in production.
    """
    op.execute("DELETE FROM system_config WHERE key='article_ref_patterns'")
