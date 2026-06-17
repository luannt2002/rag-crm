"""Add search_vector column + GIN index to document_chunks.

Revision ID: 0028
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add tsvector column
    op.execute(text("""
        ALTER TABLE public.document_chunks
        ADD COLUMN IF NOT EXISTS search_vector tsvector
    """))

    # Populate existing rows
    op.execute(text("""
        UPDATE public.document_chunks
        SET search_vector = to_tsvector('simple', COALESCE(content, ''))
        WHERE search_vector IS NULL
    """))

    # Create GIN index
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_chunks_search_vector
        ON public.document_chunks USING GIN(search_vector)
    """))

    # Create trigger to auto-update on INSERT/UPDATE
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
        DROP TRIGGER IF EXISTS trg_chunk_search_vector ON public.document_chunks;
        CREATE TRIGGER trg_chunk_search_vector
        BEFORE INSERT OR UPDATE OF content ON public.document_chunks
        FOR EACH ROW EXECUTE FUNCTION public.update_chunk_search_vector()
    """))


def downgrade() -> None:
    op.execute(text(
        "DROP TRIGGER IF EXISTS trg_chunk_search_vector ON public.document_chunks"
    ))
    op.execute(text(
        "DROP FUNCTION IF EXISTS public.update_chunk_search_vector()"
    ))
    op.execute(text(
        "DROP INDEX IF EXISTS public.idx_chunks_search_vector"
    ))
    op.execute(text(
        "ALTER TABLE public.document_chunks DROP COLUMN IF EXISTS search_vector"
    ))
