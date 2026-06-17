"""DB sanity check — verify ragbot-py can connect + schema/extensions exist.

Usage:
    python3 scripts/db/sanity_check.py

Reads DATABASE_URL_SYNC from env or falls back to default dev URL.
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

import psycopg2


def main() -> int:
    url = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DB env missing: set DATABASE_URL_SYNC (or DATABASE_URL)"
        )
    normalized = url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    u = urlparse(normalized)

    conn = psycopg2.connect(
        host=u.hostname,
        port=u.port or 5432,
        user=u.username,
        password=u.password,
        dbname=(u.path or "/").lstrip("/"),
        connect_timeout=5,
    )
    try:
        cur = conn.cursor()
        cur.execute("SELECT current_database(), current_user, version();")
        row = cur.fetchone()
        print(f"DB OK: db={row[0]} user={row[1]}")
        print(f"       version={row[2].split(',')[0]}")

        cur.execute(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name = 'ragbot';"
        )
        has_schema = cur.fetchone() is not None
        print(f"Schema ragbot: {'YES' if has_schema else 'MISSING'}")

        cur.execute(
            "SELECT extname FROM pg_extension "
            "WHERE extname IN ('pgcrypto', 'pg_trgm', 'btree_gin', 'vector') "
            "ORDER BY extname;"
        )
        exts = [r[0] for r in cur.fetchall()]
        print(f"Extensions: {exts}")

        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'ragbot' ORDER BY table_name;"
        )
        tables = [r[0] for r in cur.fetchall()]
        print(f"Tables in ragbot schema ({len(tables)}): {tables or '(none yet)'}")

        cur.close()
    finally:
        conn.close()

    if not has_schema:
        print("FAIL: schema ragbot missing — run bootstrap_schema.sql first.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
