"""Seed Jina v3 embedding model + per-bot binding swap.

Idempotent (``ON CONFLICT DO NOTHING`` / ``UPDATE ... WHERE``); verifies
the swap by reading back ``bot_model_bindings`` joined to ``ai_models``.

Usage::

    set -a && source .env && set +a
    python3 scripts/db/seed_jina_v3_binding.py <record_bot_uuid>
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from urllib.parse import urlparse

# Constants — keep in sync with shared/constants.py
JINA_V3_MODEL_NAME: str = "jina-embeddings-v3"
JINA_V3_DIMENSION: int = 1024
JINA_PROVIDER_CODE: str = "jina_ai"
JINA_PROVIDER_DISPLAY_NAME: str = "Jina AI (embeddings)"
JINA_BASE_URL: str = "https://api.jina.ai/v1"
DEFAULT_TASK_QUERY: str = "retrieval.query"
DEFAULT_TASK_PASSAGE: str = "retrieval.passage"
EMBEDDING_PURPOSE: str = "embedding"


def _connect():
    """Open a psycopg2 connection from ``DATABASE_URL_SYNC`` / ``DATABASE_URL``."""
    import psycopg2  # type: ignore

    dsn = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DB env missing: set DATABASE_URL_SYNC (or DATABASE_URL) before running.",
        )
    normalized = dsn.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://",
    )
    u = urlparse(normalized)
    return psycopg2.connect(
        host=u.hostname,
        port=u.port or 5432,
        user=u.username,
        password=u.password,
        dbname=(u.path or "/").lstrip("/"),
    )


def _seed_provider(cur) -> uuid.UUID:
    """Insert the ``jina_ai`` provider if absent; return its id."""
    cur.execute(
        "SELECT id FROM ai_providers WHERE code = %s",
        (JINA_PROVIDER_CODE,),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    new_id = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO ai_providers
            (id, code, name, type, base_url, auth_type, enabled,
             metadata_json, timeout_ms, connect_timeout_ms,
             max_retries, max_concurrent)
        VALUES (%s, %s, %s, 'cloud', %s, 'api_key', true,
                '{}'::jsonb, 30000, 5000, 3, 16)
        """,
        (str(new_id), JINA_PROVIDER_CODE, JINA_PROVIDER_DISPLAY_NAME, JINA_BASE_URL),
    )
    print(f"[seed] inserted provider code={JINA_PROVIDER_CODE} id={new_id}")
    return new_id


def _seed_model(cur, provider_id: uuid.UUID) -> uuid.UUID:
    """Insert the ``jina-embeddings-v3`` model row if absent; return its id."""
    cur.execute(
        "SELECT id FROM ai_models WHERE record_provider_id = %s AND name = %s",
        (str(provider_id), JINA_V3_MODEL_NAME),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    new_id = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO ai_models
            (id, record_provider_id, name, kind, context_window,
             max_output_tokens, input_price_per_1k_usd,
             output_price_per_1k_usd, supports_streaming, supports_tools,
             supports_vision, supports_json_mode, embedding_dimension,
             enabled, languages, metadata_json, model_id,
             quality_tier, supports_caching, supports_reasoning)
        VALUES (%s, %s, %s, 'embedding', 8192, 0,
                0.00002, 0, false, false, false, false, %s, true,
                ARRAY['en','vi','zh','ja','ko','ar','de','fr','es']::varchar[],
                %s::jsonb, %s, 'standard', false, false)
        """,
        (
            str(new_id),
            str(provider_id),
            JINA_V3_MODEL_NAME,
            JINA_V3_DIMENSION,
            json.dumps({"task_query": DEFAULT_TASK_QUERY, "task_passage": DEFAULT_TASK_PASSAGE}),
            JINA_V3_MODEL_NAME,
        ),
    )
    print(
        f"[seed] inserted model name={JINA_V3_MODEL_NAME} dim={JINA_V3_DIMENSION} "
        f"id={new_id}",
    )
    return new_id


def _swap_binding(cur, *, record_bot_id: str, model_id: uuid.UUID) -> int:
    """Repoint the embedding binding for ``record_bot_id`` at the v3 model."""
    extra = json.dumps(
        {
            "task_query": DEFAULT_TASK_QUERY,
            "task_passage": DEFAULT_TASK_PASSAGE,
            "dimension": JINA_V3_DIMENSION,
        },
    )
    cur.execute(
        """
        UPDATE bot_model_bindings
           SET record_model_id = %s,
               extra_params    = %s::jsonb,
               updated_at      = now()
         WHERE record_bot_id   = %s
           AND purpose         = %s
        """,
        (str(model_id), extra, record_bot_id, EMBEDDING_PURPOSE),
    )
    return cur.rowcount


def _verify(cur, *, record_bot_id: str) -> None:
    """Echo the resolved binding/model so the operator can eyeball the swap."""
    cur.execute(
        """
        SELECT b.id, b.purpose, m.name, m.embedding_dimension, b.extra_params
          FROM bot_model_bindings b
          JOIN ai_models m ON b.record_model_id = m.id
         WHERE b.record_bot_id = %s
           AND b.purpose       = %s
        """,
        (record_bot_id, EMBEDDING_PURPOSE),
    )
    rows = cur.fetchall()
    if not rows:
        print(f"[verify] NO embedding binding found for bot={record_bot_id}")
        return
    for r in rows:
        print(
            f"[verify] binding_id={r[0]} purpose={r[1]} model={r[2]} "
            f"dim={r[3]} extra={r[4]}",
        )


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: seed_jina_v3_binding.py <record_bot_uuid>", file=sys.stderr)
        return 2
    record_bot_id = sys.argv[1].strip()
    try:
        uuid.UUID(record_bot_id)
    except ValueError:
        print(f"invalid UUID: {record_bot_id!r}", file=sys.stderr)
        return 2

    try:
        import psycopg2  # type: ignore  # noqa: F401
    except ImportError:
        print("psycopg2 not installed", file=sys.stderr)
        return 1

    conn = _connect()
    conn.autocommit = False
    try:
        cur = conn.cursor()
        provider_id = _seed_provider(cur)
        model_id = _seed_model(cur, provider_id)
        rowcount = _swap_binding(cur, record_bot_id=record_bot_id, model_id=model_id)
        print(f"[swap] bot={record_bot_id} updated_rows={rowcount}")
        if rowcount == 0:
            print(
                f"[swap] WARN: no embedding binding row for bot={record_bot_id}; "
                "create one via admin UI before re-running.",
                file=sys.stderr,
            )
        conn.commit()
        _verify(cur, record_bot_id=record_bot_id)
    except Exception:  # noqa: BLE001 — script-level entrypoint (log + rollback on any error)
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
