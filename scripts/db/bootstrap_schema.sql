-- ragbot-py — bootstrap schema + extensions (v0.3.0-mvp)
-- Run: psql "$DATABASE_URL_SYNC" -f scripts/db/bootstrap_schema.sql
-- Idempotent: can be re-run safely.

-- 1) Dedicated schema for ragbot (isolated from other teams' tables).
CREATE SCHEMA IF NOT EXISTS ragbot AUTHORIZATION postgres;

-- 2) Extensions in `public` so other modules can share them.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;
-- pgvector optional — uncomment if hybrid search via Postgres is needed later.
-- CREATE EXTENSION IF NOT EXISTS vector;

-- 3) Sanity probe.
SELECT current_database() AS db,
       current_schema()   AS schema,
       now()              AS server_time;
