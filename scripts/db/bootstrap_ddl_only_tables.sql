-- ragbot-py — DDL-only tables bootstrap (post-V11, alembic head 0063 final form).
--
-- Use case: when alembic replay from scratch is broken (migration 0001 uses
-- Base.metadata.create_all with current models, downstream migrations assume
-- V0 schema). Workflow for clean dev DB rebuild:
--
--   1. DROP SCHEMA public CASCADE; CREATE SCHEMA public;
--   2. CREATE EXTENSION vector;
--   3. python -c "from ragbot.infrastructure.db.models import Base;
--      from sqlalchemy import create_engine;
--      Base.metadata.create_all(create_engine(os.environ['DATABASE_URL_SYNC']))"
--   4. psql "$DATABASE_URL_SYNC" -f scripts/db/bootstrap_ddl_only_tables.sql
--   5. alembic stamp head
--   6. python scripts/init_system_config.py
--   7. python scripts/seed_rbac_permissions_s11b.py
--   8. python scripts/seed_rbac_permissions_s12a.py
--   9. (then: seed your tenant + bot + ai providers/models + bindings,
--       seed language_packs from migration 0056 _SEED_ROWS, FLUSHDB redis,
--       restart workers)
--
-- This file ships the 10 tables whose models live ONLY in alembic migrations
-- (no SQLAlchemy ORM Mapped class). Idempotent CREATE IF NOT EXISTS.

CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- document_chunks (V11 final, post-0063)
-- ============================================================
CREATE TABLE IF NOT EXISTS public.document_chunks (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    record_document_id  UUID         NOT NULL,
    chunk_index         INT          NOT NULL,
    content             TEXT         NOT NULL,
    content_segmented   TEXT         NULL,
    content_hash        CHAR(64)     NOT NULL,
    embedding           vector(1024) NULL,
    metadata_json       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    parent_chunk_id     UUID         NULL REFERENCES public.document_chunks(id) ON DELETE SET NULL,
    chunk_chars         INTEGER      NULL,
    search_vector       tsvector     NULL,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT fk_chunks_document
        FOREIGN KEY (record_document_id) REFERENCES public.documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_chunks_document
    ON public.document_chunks (record_document_id);
CREATE INDEX IF NOT EXISTS ix_chunks_content_hash
    ON public.document_chunks (content_hash);
CREATE INDEX IF NOT EXISTS idx_chunks_parent
    ON public.document_chunks (parent_chunk_id) WHERE parent_chunk_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chunks_search_vector
    ON public.document_chunks USING GIN (search_vector);
CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw
    ON public.document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 32, ef_construction = 200);

CREATE OR REPLACE FUNCTION public.update_chunk_search_vector()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_vector = to_tsvector('simple', COALESCE(NEW.content_segmented, NEW.content, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_chunk_search_vector ON public.document_chunks;
CREATE TRIGGER trg_chunk_search_vector
    BEFORE INSERT OR UPDATE OF content, content_segmented
    ON public.document_chunks
    FOR EACH ROW EXECUTE FUNCTION public.update_chunk_search_vector();

-- ============================================================
-- semantic_cache (V11 final, post-0063)
-- ============================================================
CREATE TABLE IF NOT EXISTS public.semantic_cache (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    record_bot_id     UUID         NOT NULL,
    record_tenant_id  UUID         NULL,
    workspace_id      VARCHAR(64)  NOT NULL,
    bot_version       TEXT         NOT NULL DEFAULT 'latest',
    corpus_version    TEXT         NOT NULL DEFAULT 'latest',
    query_embedding   vector(1024) NULL,
    query_hash        CHAR(64)     NOT NULL,
    answer            TEXT         NOT NULL,
    citations         JSONB        NOT NULL DEFAULT '[]'::jsonb,
    model_name        TEXT         NOT NULL DEFAULT '',
    cached_at_ts      BIGINT       NOT NULL DEFAULT 0,
    metadata_json     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ  NULL,
    CONSTRAINT semantic_cache_workspace_id_format_check
        CHECK (length(workspace_id) >= 1
               AND length(workspace_id) <= 64
               AND workspace_id ~ '^[a-zA-Z0-9-]+$')
);
CREATE INDEX IF NOT EXISTS ix_sem_cache_bot
    ON public.semantic_cache (record_bot_id, query_hash);
CREATE INDEX IF NOT EXISTS ix_semantic_cache_bot
    ON public.semantic_cache (record_bot_id);
CREATE INDEX IF NOT EXISTS ix_semantic_cache_ws
    ON public.semantic_cache (record_bot_id, workspace_id);
CREATE INDEX IF NOT EXISTS ix_sem_cache_embedding_hnsw
    ON public.semantic_cache USING hnsw (query_embedding vector_cosine_ops)
    WITH (m = 32, ef_construction = 200);

-- ============================================================
-- language_packs (V11 final, post-0056)
-- ============================================================
CREATE TABLE IF NOT EXISTS public.language_packs (
    code        VARCHAR(8)  NOT NULL,
    prompt_key  VARCHAR(64) NOT NULL,
    content     TEXT        NOT NULL,
    version     INTEGER     NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_language_packs PRIMARY KEY (code, prompt_key)
);

-- ============================================================
-- api_tokens
-- ============================================================
CREATE TABLE IF NOT EXISTS public.api_tokens (
    id                  UUID            PRIMARY KEY,
    service_name        VARCHAR(128)    NOT NULL UNIQUE,
    description         TEXT            NOT NULL DEFAULT '',
    token_hash          VARCHAR(64)     NOT NULL,
    version             INTEGER         NOT NULL DEFAULT 1,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    revoked_at          TIMESTAMPTZ,
    role                VARCHAR(16)     NOT NULL DEFAULT 'service',
    rate_limit_value    INTEGER         NOT NULL DEFAULT 120,
    rate_limit_window   INTEGER         NOT NULL DEFAULT 60
);
CREATE INDEX IF NOT EXISTS ix_api_tokens_service
    ON public.api_tokens (service_name) WHERE revoked_at IS NULL;

-- ============================================================
-- chat_histories
-- ============================================================
CREATE TABLE IF NOT EXISTS public.chat_histories (
    id              BIGSERIAL       PRIMARY KEY,
    record_bot_id   UUID            NOT NULL,
    channel_type    VARCHAR(64)     NOT NULL DEFAULT 'web',
    connect_id      VARCHAR(255)    NOT NULL,
    role            VARCHAR(16)     NOT NULL,
    content         TEXT            NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_chat_histories_room
    ON public.chat_histories (record_bot_id, channel_type, connect_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_chat_histories_bot
    ON public.chat_histories (record_bot_id, created_at DESC);

-- ============================================================
-- knowledge_edges
-- ============================================================
CREATE TABLE IF NOT EXISTS public.knowledge_edges (
    id                UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    record_bot_id     UUID          NOT NULL REFERENCES public.bots(id) ON DELETE CASCADE,
    channel_type      VARCHAR(64)   NOT NULL DEFAULT 'web',
    subject           TEXT          NOT NULL,
    relation          TEXT          NOT NULL,
    object            TEXT          NOT NULL,
    source_document   TEXT,
    source_chunk_id   UUID,
    confidence        FLOAT         DEFAULT 1.0,
    created_at        TIMESTAMPTZ   DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_edges_unique
    ON public.knowledge_edges (record_bot_id, channel_type, subject, relation, object);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_bot_channel
    ON public.knowledge_edges (record_bot_id, channel_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_subject
    ON public.knowledge_edges (record_bot_id, channel_type, subject);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_object
    ON public.knowledge_edges (record_bot_id, channel_type, object);

-- ============================================================
-- module_permissions
-- ============================================================
CREATE TABLE IF NOT EXISTS public.module_permissions (
    id              SERIAL          PRIMARY KEY,
    module          VARCHAR(64)     NOT NULL,
    permission      VARCHAR(64)     NOT NULL,
    min_role_level  INTEGER         NOT NULL,
    description     TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    UNIQUE (module, permission)
);
CREATE INDEX IF NOT EXISTS ix_module_perm_module
    ON public.module_permissions (module);

-- ============================================================
-- role_definitions
-- ============================================================
CREATE TABLE IF NOT EXISTS public.role_definitions (
    id          SERIAL          PRIMARY KEY,
    role_name   VARCHAR(32)     NOT NULL UNIQUE,
    level       INTEGER         NOT NULL,
    scope       VARCHAR(32)     NOT NULL DEFAULT 'workspace',
    description TEXT,
    is_system   BOOLEAN         NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_role_def_level
    ON public.role_definitions (level);

-- ============================================================
-- system_config
-- ============================================================
CREATE TABLE IF NOT EXISTS public.system_config (
    key         VARCHAR(128)    PRIMARY KEY,
    value       JSONB           NOT NULL,
    value_type  VARCHAR(32)     NOT NULL DEFAULT 'string',
    description TEXT,
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_system_config_updated
    ON public.system_config (updated_at DESC);
