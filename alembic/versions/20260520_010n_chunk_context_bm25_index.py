"""[T1-Smartness] BM25 functional GIN index over content + chunk_context.

Revision ID: 010n
Revises: 010m
Create Date: 2026-05-20

Wave A CT-3 builds on the WA-3 ``chunk_context`` storage column
(alembic 010l). When a bot flips ``plan_limits.cr_enhanced_enabled``
the ``PgBM25Retrieval`` adapter widens its tsvector surface from
``dc.search_vector`` (trigger-maintained over ``content`` only) to
``to_tsvector('simple', coalesce(content, '') || ' ' || coalesce(chunk_context, ''))``
so the per-chunk situated-context tokens become BM25-rank-visible —
this is what activates the Anthropic Contextual Retrieval -49%
retrieval-failure path (Sep 2024 paper).

The combined tsvector is computed on the fly at query time. Without
an index, Postgres would re-tokenize every chunk for every query —
unacceptable at corpus size > 10K. This migration adds a functional
GIN index on the exact same expression so the BM25 query plan stays
a single GIN scan even on the opt-in path.

Why functional (not stored column + trigger):
* Trigger maintenance on two columns (``content`` and ``chunk_context``)
  doubles INSERT/UPDATE overhead for legacy bots that never opt in.
* Functional index is incremental: only the opt-in branch pays its
  build cost; opted-out queries continue to hit the existing
  ``idx_chunks_search_vector`` (alembic 0028) bit-exact.
* ``coalesce(NULL, '')`` ensures unenriched legacy chunks index
  cleanly under the same expression — no NULL-row exclusion needed.

Coordination note: Wave A WA-6 has reserved revision ``010m`` for
its own storage-layer migration which is not yet merged into the
current branch. If WA-6 lands first, this file's
``down_revision = "010m"`` chains correctly. If CT-3 lands first the
Auditor renumbers ``down_revision`` to ``010l`` at merge time —
this migration is data-additive (no DDL on existing columns) and
re-bases trivially.

Down: drop the functional index. Storage column from 010l is
untouched (owned by WA-3).
"""

from __future__ import annotations

from alembic import op

revision = "010n"
down_revision = "010m"
branch_labels = None
depends_on = None


# Index name kept short + descriptive — no version-ref / no brand.
# Lives alongside ``idx_chunks_search_vector`` (alembic 0028) which
# indexes the content-only path; the two are mutually exclusive at
# query time so Postgres planner picks the right one based on the
# tsvector expression in the SELECT.
_IDX_NAME = "idx_chunks_search_vector_combined"


def upgrade() -> None:
    # Functional GIN tsvector over (content + chunk_context). Matches
    # the exact expression PgBM25Retrieval emits when cr_enhanced=True
    # so the planner picks this index instead of a seq scan.
    #
    # ``WHERE chunk_context IS NOT NULL`` keeps the index lean — rows
    # with no context contribute nothing new vs the existing 0028 index
    # and would only bloat the GIN tree.
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {_IDX_NAME}
        ON document_chunks
        USING GIN (
            to_tsvector(
                'simple',
                coalesce(content, '') || ' ' || coalesce(chunk_context, '')
            )
        )
        WHERE chunk_context IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_IDX_NAME}")
