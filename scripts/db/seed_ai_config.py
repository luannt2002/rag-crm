"""Seed minimal AI config for testing — run:

    python3 scripts/db/seed_ai_config.py <tenant_id> <bot_id>

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    export RAGBOT_CONFIG_KEK=$(python3 -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())")
    python3 scripts/db/seed_ai_config.py <tenant_id> <bot_id>
"""

from __future__ import annotations

import os
import sys
import uuid
from urllib.parse import urlparse


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: seed_ai_config.py <tenant_id> <bot_id>")
        return 1

    tenant_id, bot_id = sys.argv[1], sys.argv[2]
    try:
        import psycopg2  # type: ignore
    except ImportError:
        print("psycopg2 not installed; dry-run mode.")
        print(f"Would seed provider for tenant={tenant_id} bot={bot_id}")
        return 0

    dsn = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DB env missing: set DATABASE_URL_SYNC (or DATABASE_URL)"
        )
    normalized = dsn.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    u = urlparse(normalized)
    conn = psycopg2.connect(
        host=u.hostname,
        port=u.port or 5432,
        user=u.username,
        password=u.password,
        dbname=(u.path or "/").lstrip("/"),
    )
    conn.autocommit = True
    cur = conn.cursor()

    # Insert provider Anthropic
    prov_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO ragbot.ai_providers(
            id, code, name, auth_type, base_url, enabled,
            timeout_ms, max_retries, max_concurrent
        )
        VALUES (%s, 'anthropic', 'Anthropic', 'bearer',
                'https://api.anthropic.com', true, 30000, 2, 16)
        ON CONFLICT DO NOTHING
        """,
        (prov_id,),
    )
    print(
        f"Seeded provider {prov_id} for tenant={tenant_id} bot={bot_id}. "
        f"Use /admin/ai/providers/{prov_id}/rotate-key to set API key.",
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
