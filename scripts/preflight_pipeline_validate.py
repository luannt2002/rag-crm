"""preflight_pipeline_validate.py — boot-time pipeline config drift detector.

Catches 6 categories of silent regression that bypass unit tests and let
the pipeline fall back to NullReranker silently:

1. Alembic migration files using ``sa.JSONB`` (must use ``JSONB`` imported
   from ``sqlalchemy.dialects.postgresql``)
2. system_config.reranker_enabled value = false / 'false' / null
3. system_config.reranker_provider value = null / 'null' / empty
4. ai_providers.code IS NULL for non-deleted active rows
5. Env JINA_API_KEY (or aliased keys) missing when ai_providers has an
   active row whose code maps to a known provider that requires a key
6. Registry alias mismatch — every active ai_providers.code used for
   reranking must exist as a key in the reranker registry _REGISTRY

Design notes
------------
- Pure warn-only by default (exit 0); ``--strict`` flips to exit 1 on any
  FAIL so a CI gate can be dropped in without changing day-to-day workflow.
- Alembic check (6.1) is a pure file scan — no DB needed.
- DB checks (6.2–6.6) share a single asyncpg connection pool.
- Exceptions narrowed to ``OSError`` (file I/O) and ``sqlalchemy`` async
  errors; no bare ``except Exception`` outside top-level entrypoint.
- Zero hardcode: all sentinel values are module-level CONSTs; provider env
  mapping is a dict so adding a new provider = one dict entry.
- Domain-neutral: zero brand / tenant literals.

Usage
-----
    python scripts/preflight_pipeline_validate.py            # warn-only
    python scripts/preflight_pipeline_validate.py --strict   # exit 1 on FAIL
    python scripts/preflight_pipeline_validate.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import asyncio
import json as _json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Make ``ragbot`` package importable when run directly from repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

# ---------------------------------------------------------------------------
# Module-level constants (CLAUDE.md zero-hardcode — no inline magic values)
# ---------------------------------------------------------------------------

ALEMBIC_VERSIONS_DIR: Path = REPO_ROOT / "alembic" / "versions"

# Pattern that should NOT appear in migration files (correct form is the
# imported symbol JSONB, not the SQLAlchemy dialect accessor sa.JSONB).
JSONB_BAD_PATTERN: re.Pattern[str] = re.compile(r"\bsa\.JSONB\b")
JSONB_GOOD_IMPORT: str = "from sqlalchemy.dialects.postgresql import JSONB"

# Values that indicate a legacy / unconfigured state for system_config keys.
LEGACY_RERANKER_VALUES: frozenset[str] = frozenset({
    "false", "null", '"null"', "none", '""', "",
})

# Env vars required per provider code (any one of the tuple suffices).
# Extend here when a new provider is added — no edits elsewhere.
PROVIDER_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "jina_ai": ("JINA_API_KEY", "RERANKER_JINA_API_KEY", "EMBEDDING_JINA_API_KEY"),
    "jina": ("JINA_API_KEY", "RERANKER_JINA_API_KEY", "EMBEDDING_JINA_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "cohere": ("COHERE_API_KEY", "CO_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
}

# Providers that operate without an API key (local / self-hosted).
KEY_FREE_PROVIDERS: frozenset[str] = frozenset({"null", "viranker_local"})


# ---------------------------------------------------------------------------
# Individual checks — each returns a list[dict] of failures (empty = OK)
# ---------------------------------------------------------------------------


async def check_alembic_jsonb_pattern() -> list[dict[str, str]]:
    """Check 6.1 — find ``sa.JSONB`` in any migration file without proper import.

    ``sa.JSONB`` is not a valid SQLAlchemy attribute; the correct form is::

        from sqlalchemy.dialects.postgresql import JSONB

    Any migration that uses ``sa.JSONB`` will silently create a TEXT column
    (fallback) instead of a real JSONB column.
    """
    fails: list[dict[str, str]] = []
    if not ALEMBIC_VERSIONS_DIR.exists():
        return [{"file": str(ALEMBIC_VERSIONS_DIR), "issue": "alembic versions dir missing"}]
    for path in sorted(ALEMBIC_VERSIONS_DIR.glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            fails.append({"file": path.name, "issue": f"read failed: {exc}"})
            continue
        if JSONB_BAD_PATTERN.search(text) and JSONB_GOOD_IMPORT not in text:
            fails.append({
                "file": path.name,
                "issue": "uses sa.JSONB without 'from sqlalchemy.dialects.postgresql import JSONB'",
            })
    return fails


async def check_system_config_reranker(engine: Any) -> list[dict[str, str]]:
    """Check 6.2 + 6.3 — reranker_enabled is truthy; reranker_provider is set."""
    from sqlalchemy import text as sa_text

    fails: list[dict[str, str]] = []
    async with engine.connect() as conn:
        # 6.2 — reranker_enabled must not be legacy-false
        row = (await conn.execute(
            sa_text("SELECT value::text FROM system_config WHERE key = 'reranker_enabled'")
        )).fetchone()
        if row is None:
            fails.append({"key": "reranker_enabled", "issue": "row missing in system_config"})
        else:
            val = (row[0] or "").strip().lower().strip('"')
            if val in LEGACY_RERANKER_VALUES:
                fails.append({"key": "reranker_enabled", "issue": f"value={row[0]!r} (legacy/disabled)"})

        # 6.3 — reranker_provider must be a non-empty, non-null string
        row = (await conn.execute(
            sa_text("SELECT value::text FROM system_config WHERE key = 'reranker_provider'")
        )).fetchone()
        if row is None:
            fails.append({"key": "reranker_provider", "issue": "row missing in system_config"})
        else:
            val = (row[0] or "").strip().lower().strip('"')
            if val in LEGACY_RERANKER_VALUES:
                fails.append({"key": "reranker_provider", "issue": f"value={row[0]!r} (provider not configured)"})

    return fails


async def check_ai_providers_code(engine: Any) -> list[dict[str, str]]:
    """Check 6.4 — ai_providers.code NOT NULL for active, non-deleted rows."""
    from sqlalchemy import text as sa_text

    fails: list[dict[str, str]] = []
    async with engine.connect() as conn:
        rows = (await conn.execute(sa_text(
            "SELECT id, name FROM ai_providers "
            "WHERE deleted_at IS NULL AND enabled = true AND code IS NULL"
        ))).fetchall()
    for row in rows:
        fails.append({"provider_id": str(row[0]), "name": str(row[1]), "issue": "code IS NULL"})
    return fails


async def check_provider_env_keys(engine: Any) -> list[dict[str, str]]:
    """Check 6.5 — required env key present when active provider row exists."""
    from sqlalchemy import text as sa_text

    fails: list[dict[str, str]] = []
    async with engine.connect() as conn:
        rows = (await conn.execute(sa_text(
            "SELECT DISTINCT code FROM ai_providers "
            "WHERE deleted_at IS NULL AND enabled = true AND code IS NOT NULL"
        ))).fetchall()

    for row in rows:
        code = row[0]
        if code in KEY_FREE_PROVIDERS:
            continue
        env_keys = PROVIDER_ENV_KEYS.get(code, ())
        if not env_keys:
            continue  # unknown provider — not our responsibility here
        if not any(os.environ.get(k) for k in env_keys):
            fails.append({
                "provider_code": code,
                "issue": f"none of {list(env_keys)} set in env",
            })
    return fails


async def check_reranker_registry_alias(engine: Any) -> list[dict[str, str]]:
    """Check 6.6 — every active reranker provider code exists in registry.

    Queries for provider codes that back at least one enabled reranker model,
    then verifies each code has a matching key in the reranker registry.
    """
    from sqlalchemy import text as sa_text

    try:
        from ragbot.infrastructure.reranker.registry import list_providers as _list_providers
        rerank_keys: frozenset[str] = frozenset(_list_providers())
    except ImportError as exc:
        return [{"issue": f"registry import failed: {exc}"}]

    fails: list[dict[str, str]] = []
    async with engine.connect() as conn:
        rows = (await conn.execute(sa_text(
            """
            SELECT DISTINCT p.code
            FROM ai_providers p
            JOIN ai_models m ON m.record_provider_id = p.id
            WHERE p.deleted_at IS NULL
              AND p.enabled = true
              AND m.kind = 'reranker'
              AND m.enabled = true
            """
        ))).fetchall()

    for row in rows:
        code = row[0]
        if code and code not in rerank_keys:
            fails.append({
                "provider_code": code,
                "issue": f"not in reranker registry — registered={sorted(rerank_keys)}",
            })
    return fails


# ---------------------------------------------------------------------------
# DB URL helper
# ---------------------------------------------------------------------------


def _resolve_db_url() -> str:
    """Read DATABASE_URL from env and convert to asyncpg DSN form."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL env var required")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url  # already prefixed (e.g. postgresql+asyncpg://)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def _amain(args: argparse.Namespace) -> int:
    # Suppress noisy third-party logging during script execution.
    import logging
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

    results: dict[str, list[dict[str, str]]] = {
        "alembic_jsonb_pattern": [],
        "system_config_reranker": [],
        "ai_providers_code": [],
        "provider_env_keys": [],
        "reranker_registry_alias": [],
    }

    # 6.1 — pure file scan, no DB required
    results["alembic_jsonb_pattern"] = await check_alembic_jsonb_pattern()

    # 6.2–6.6 — DB-backed checks
    try:
        db_url = _resolve_db_url()
    except RuntimeError as exc:
        _emit(args, results, extra_error=str(exc))
        return 1

    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(db_url, echo=False, pool_pre_ping=False)
    try:
        results["system_config_reranker"] = await check_system_config_reranker(engine)
        results["ai_providers_code"] = await check_ai_providers_code(engine)
        results["provider_env_keys"] = await check_provider_env_keys(engine)
        results["reranker_registry_alias"] = await check_reranker_registry_alias(engine)
    except Exception as exc:  # noqa: BLE001 — top-level entrypoint; DB may be down
        _emit(args, results, extra_error=f"{type(exc).__name__}: {exc}")
        await engine.dispose()
        return 1
    finally:
        await engine.dispose()

    _emit(args, results)
    total_fails = sum(len(v) for v in results.values())
    if args.strict and total_fails > 0:
        return 1
    return 0


def _emit(
    args: argparse.Namespace,
    results: dict[str, list[dict[str, str]]],
    extra_error: str | None = None,
) -> None:
    total_fails = sum(len(v) for v in results.values())
    if extra_error:
        total_fails += 1

    if args.json:
        payload: dict[str, object] = {
            "ok": total_fails == 0,
            "total_fails": total_fails,
            "checks": results,
        }
        if extra_error:
            payload["error"] = extra_error
        print(_json.dumps(payload, indent=2, default=str))
        return

    # Human-readable output
    bar = "=" * 70
    print(bar)
    print("RAGBOT PREFLIGHT PIPELINE VALIDATE")
    print(bar)
    for check_name, fails in results.items():
        status = "OK" if not fails else f"FAIL ({len(fails)})"
        print(f"  [{status:<9}] {check_name}")
        for entry in fails:
            print(f"      -> {entry}")
    if extra_error:
        print(f"  [ERROR    ] {extra_error}")
    print(bar)
    print(f"Total fails: {total_fails}")
    print(bar)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Preflight pipeline config validator — catches silent regressions before deployment."
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on any FAIL (use as CI gate).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON to stdout.",
    )
    return p.parse_args()


def main() -> int:
    return asyncio.run(_amain(_parse_args()))


if __name__ == "__main__":
    sys.exit(main())
