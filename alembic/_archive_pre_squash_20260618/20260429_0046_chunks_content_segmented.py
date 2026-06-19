"""P22 Option B: add document_chunks.content_segmented + retarget BM25 trigger.

Pre-segmenting Vietnamese compound words ("chăm sóc da" → "chăm_sóc da")
before to_tsvector indexing fixes a long-standing BM25 boundary asymmetry
between ingest and query: the query side stopped calling
``underthesea.word_tokenize`` (Option A, Sprint 2) so multi-word VN
compounds in user queries used to miss compound chunks indexed as 3 loose
tokens. With this migration the BM25 vector is built from a dedicated
``content_segmented`` column when present, falling back to the original
``content`` for backward compatibility (legacy rows + non-VN content).

Embedding still indexes original ``content`` — embedding models
(bge-m3 / openai text-embedding-3-*) handle Vietnamese natively and
underscore-joined tokens hurt cosine quality. Only the BM25 GIN index
sees the segmented form.

Revision: 0046
Down revision: 0045
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add nullable column — legacy rows stay NULL; trigger COALESCEs to content.
    op.execute(text("""
        ALTER TABLE public.document_chunks
        ADD COLUMN IF NOT EXISTS content_segmented TEXT
    """))

    # Retarget BM25 trigger to prefer content_segmented when present.
    op.execute(text("""
        CREATE OR REPLACE FUNCTION public.update_chunk_search_vector()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.search_vector = to_tsvector(
                'simple',
                COALESCE(NEW.content_segmented, NEW.content, '')
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """))

    # Trigger watches both columns so updating either rebuilds the tsvector.
    op.execute(text("""
        DROP TRIGGER IF EXISTS trg_chunk_search_vector ON public.document_chunks
    """))
    op.execute(text("""
        CREATE TRIGGER trg_chunk_search_vector
        BEFORE INSERT OR UPDATE OF content, content_segmented
        ON public.document_chunks
        FOR EACH ROW EXECUTE FUNCTION public.update_chunk_search_vector()
    """))


def downgrade() -> None:
    # Restore original trigger (content only).
    op.execute(text("""
        CREATE OR REPLACE FUNCTION public.update_chunk_search_vector()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.search_vector = to_tsvector('simple', COALESCE(NEW.content, ''));
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """))
    op.execute(text("""
        DROP TRIGGER IF EXISTS trg_chunk_search_vector ON public.document_chunks
    """))
    op.execute(text("""
        CREATE TRIGGER trg_chunk_search_vector
        BEFORE INSERT OR UPDATE OF content ON public.document_chunks
        FOR EACH ROW EXECUTE FUNCTION public.update_chunk_search_vector()
    """))
    op.execute(text("""
        ALTER TABLE public.document_chunks DROP COLUMN IF EXISTS content_segmented
    """))
