#!/usr/bin/env python3
"""Pre-flight check — verify ragbot ready for deploy/runtime.

Run BEFORE ``docker-compose up`` (or before any production restart) to gate
the 4 V2-migration bug classes documented in ``docs/V2_MIGRATION_BUG_LESSONS.md``.

USAGE
-----
    .venv/bin/python scripts/preflight_check.py            # human-readable
    .venv/bin/python scripts/preflight_check.py --strict   # warnings -> exit 1
    .venv/bin/python scripts/preflight_check.py --json     # machine-readable

EXIT CODES
----------
    0   all checks passed
    1   warnings only (config drift, deprecation) AND --strict
    2   critical failure (provider unhealthy, DB unreachable, schema drift)

DESIGN
------
- Each check returns ``CheckResult(severity, message, fix_hint)``.
- Each provider call is wrapped in a NARROW exception handler so one bad
  provider does NOT crash the whole preflight (production reliability sacred).
- The DB check runs FIRST and gates all downstream checks: no DB = nothing
  else can be verified.
- Live provider probes use real network IO (Jina rerank, LiteLLM embed, LLM
  ping) but with hard timeouts so the script always finishes in <60 s.
- Fully domain-neutral: zero brand / tenant literals in this file.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# Make ``ragbot`` package importable when invoked directly from repo root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

# ---------------------------------------------------------------------------
# Domain-neutral constants — kept inline (preflight is a deploy gate, not
# hot-path runtime). Pure operational thresholds, no behavior toggles.
# ---------------------------------------------------------------------------
DB_CONNECT_TIMEOUT_S: int = 5
PROVIDER_PROBE_TIMEOUT_S: int = 10
EMBED_PROBE_INPUT: str = "preflight_health_probe"
RERANK_PROBE_QUERY: str = "preflight_health_probe"
RERANK_PROBE_DOC: str = "preflight_health_probe_document"
LLM_PROBE_PROMPT: str = "ping"
ALEMBIC_VERSIONS_DIR: Path = _PROJECT_ROOT / "alembic" / "versions"

# Required env vars per provider key. Lookup-table — extend when new
# provider added (mirrors ai_providers.code values).
PROVIDER_ENV_KEYS: dict[str, list[str]] = {
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "cohere": ["COHERE_API_KEY", "CO_API_KEY"],  # either accepted
    "jina": ["RERANKER_JINA_API_KEY", "EMBEDDING_JINA_API_KEY", "JINA_API_KEY"],
    "jina_ai": ["RERANKER_JINA_API_KEY", "EMBEDDING_JINA_API_KEY", "JINA_API_KEY"],
}

# Critical system_config keys that MUST exist (preflight fails if missing).
REQUIRED_SYSTEM_CONFIG_KEYS: tuple[str, ...] = (
    "embedding_model",
    "reranker_provider",
    "reranker_enabled",
    "llm_default_model",
    "rag_top_k",
    "rag_rerank_top_n",
)

# Legacy-naming-drift markers (BUG #1 from V2_MIGRATION_BUG_LESSONS).
LEGACY_PURPOSE_VALUES: tuple[str, ...] = ("reranker",)  # should be "rerank"
CANONICAL_PURPOSE_VALUES: tuple[str, ...] = (
    "embedding",
    "rerank",
    "llm_primary",
    "grading",
    "grounding",
    "rewriting",
    "understand_query",
    "decompose",
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
class Severity(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class CheckResult:
    name: str
    severity: Severity
    message: str
    duration_ms: int = 0
    details: dict = field(default_factory=dict)
    fix_hint: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "severity": self.severity.value,
            "message": self.message,
            "duration_ms": self.duration_ms,
            "details": self.details,
            "fix_hint": self.fix_hint,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalise_dsn(dsn: str) -> str:
    """Strip SQLAlchemy driver prefix so psycopg2 / asyncpg can ingest it."""
    return (
        dsn.replace("postgresql+psycopg2://", "postgresql://")
        .replace("postgresql+asyncpg://", "postgresql://")
    )


def _load_env() -> None:
    """Load ``.env`` from project root (idempotent)."""
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
        load_dotenv(_PROJECT_ROOT / ".env")
    except ImportError:
        # dotenv missing is OK — env vars may already be exported.
        pass


def _provider_env_value(provider_code: str) -> str:
    """Resolve the first non-empty env var for a provider code."""
    keys = PROVIDER_ENV_KEYS.get(provider_code.lower(), [])
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v
    return ""


# ---------------------------------------------------------------------------
# Check 1: DB connection
# ---------------------------------------------------------------------------
async def check_db_connection() -> CheckResult:
    t0 = time.monotonic()
    dsn_raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL", "")
    if not dsn_raw:
        return CheckResult(
            "db_connection",
            Severity.FAIL,
            "DATABASE_URL_SYNC env missing",
            fix_hint="Set DATABASE_URL_SYNC in .env (postgresql://user:pass@host:port/db)",
        )
    dsn = _normalise_dsn(dsn_raw)
    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError as exc:
        return CheckResult(
            "db_connection",
            Severity.FAIL,
            f"psycopg2 not installed: {exc!s}",
            fix_hint="pip install psycopg2-binary",
        )
    try:
        conn = psycopg2.connect(dsn, connect_timeout=DB_CONNECT_TIMEOUT_S)
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            conn.close()
    except psycopg2.Error as exc:
        return CheckResult(
            "db_connection",
            Severity.FAIL,
            f"{type(exc).__name__}: {str(exc)[:200]}",
            duration_ms=int((time.monotonic() - t0) * 1000),
            fix_hint="Check DATABASE_URL_SYNC + Postgres reachability + credentials",
        )
    dur = int((time.monotonic() - t0) * 1000)
    return CheckResult(
        "db_connection",
        Severity.OK,
        f"connected ({dur} ms)",
        duration_ms=dur,
    )


# ---------------------------------------------------------------------------
# Check 2: Alembic head consistency
# ---------------------------------------------------------------------------
async def check_alembic_head() -> CheckResult:
    """Compare ``alembic_version.version_num`` with the latest revision file."""
    t0 = time.monotonic()
    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError:
        return CheckResult(
            "alembic_head",
            Severity.SKIP,
            "psycopg2 not installed — skipping",
        )
    dsn_raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL", "")
    if not dsn_raw:
        return CheckResult("alembic_head", Severity.SKIP, "no DSN")
    if not ALEMBIC_VERSIONS_DIR.exists():
        return CheckResult(
            "alembic_head",
            Severity.WARN,
            f"alembic versions dir missing: {ALEMBIC_VERSIONS_DIR}",
        )
    # Pick highest-named revision file (date-prefixed convention).
    revisions = sorted(
        p.stem for p in ALEMBIC_VERSIONS_DIR.glob("*.py") if not p.stem.startswith("__")
    )
    if not revisions:
        return CheckResult(
            "alembic_head",
            Severity.WARN,
            "no alembic revisions found on disk",
        )
    latest_file = revisions[-1]
    try:
        conn = psycopg2.connect(_normalise_dsn(dsn_raw), connect_timeout=DB_CONNECT_TIMEOUT_S)
        try:
            cur = conn.cursor()
            cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
            row = cur.fetchone()
        finally:
            conn.close()
    except psycopg2.Error as exc:
        return CheckResult(
            "alembic_head",
            Severity.WARN,
            f"DB query failed: {type(exc).__name__}",
            fix_hint="Run `alembic upgrade head` if alembic_version table missing",
        )
    dur = int((time.monotonic() - t0) * 1000)
    db_rev = row[0] if row else None
    if not db_rev:
        return CheckResult(
            "alembic_head",
            Severity.FAIL,
            "alembic_version row missing",
            duration_ms=dur,
            fix_hint="Run `alembic upgrade head`",
        )
    # Latest file stem encodes the revision id at the trailing token; we
    # compare by substring rather than exact match because file naming
    # may include a date prefix (e.g. ``20260501_0054_xxx``).
    if db_rev in latest_file:
        return CheckResult(
            "alembic_head",
            Severity.OK,
            f"db rev {db_rev} matches head file",
            duration_ms=dur,
            details={"db_rev": db_rev, "latest_file": latest_file},
        )
    return CheckResult(
        "alembic_head",
        Severity.WARN,
        f"db rev={db_rev} but latest file={latest_file}",
        duration_ms=dur,
        details={"db_rev": db_rev, "latest_file": latest_file},
        fix_hint="Run `alembic upgrade head` (DB behind code)",
    )


# ---------------------------------------------------------------------------
# Check 3: system_config required keys
# ---------------------------------------------------------------------------
async def check_system_config_keys() -> CheckResult:
    t0 = time.monotonic()
    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError:
        return CheckResult("system_config_keys", Severity.SKIP, "psycopg2 missing")
    dsn_raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL", "")
    if not dsn_raw:
        return CheckResult("system_config_keys", Severity.SKIP, "no DSN")
    try:
        conn = psycopg2.connect(_normalise_dsn(dsn_raw), connect_timeout=DB_CONNECT_TIMEOUT_S)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT key FROM system_config WHERE key = ANY(%s)",
                (list(REQUIRED_SYSTEM_CONFIG_KEYS),),
            )
            present = {r[0] for r in cur.fetchall()}
        finally:
            conn.close()
    except psycopg2.Error as exc:
        return CheckResult(
            "system_config_keys",
            Severity.FAIL,
            f"{type(exc).__name__}: {str(exc)[:200]}",
            fix_hint="Run scripts/init_system_config.py",
        )
    missing = [k for k in REQUIRED_SYSTEM_CONFIG_KEYS if k not in present]
    dur = int((time.monotonic() - t0) * 1000)
    if missing:
        return CheckResult(
            "system_config_keys",
            Severity.FAIL,
            f"missing keys: {missing}",
            duration_ms=dur,
            details={"missing": missing, "expected": list(REQUIRED_SYSTEM_CONFIG_KEYS)},
            fix_hint="Run `python scripts/init_system_config.py`",
        )
    return CheckResult(
        "system_config_keys",
        Severity.OK,
        f"all {len(REQUIRED_SYSTEM_CONFIG_KEYS)} keys present",
        duration_ms=dur,
    )


# ---------------------------------------------------------------------------
# Check 4: ai_providers seeded
# ---------------------------------------------------------------------------
async def check_ai_providers_seeded() -> CheckResult:
    t0 = time.monotonic()
    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError:
        return CheckResult("ai_providers_seeded", Severity.SKIP, "psycopg2 missing")
    dsn_raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL", "")
    if not dsn_raw:
        return CheckResult("ai_providers_seeded", Severity.SKIP, "no DSN")
    try:
        conn = psycopg2.connect(_normalise_dsn(dsn_raw), connect_timeout=DB_CONNECT_TIMEOUT_S)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT code, enabled FROM ai_providers "
                "WHERE deleted_at IS NULL ORDER BY code",
            )
            rows = cur.fetchall()
        finally:
            conn.close()
    except psycopg2.Error as exc:
        return CheckResult(
            "ai_providers_seeded",
            Severity.FAIL,
            f"{type(exc).__name__}: {str(exc)[:200]}",
            fix_hint="Run scripts/seed_ai_config.py to seed providers",
        )
    dur = int((time.monotonic() - t0) * 1000)
    if not rows:
        return CheckResult(
            "ai_providers_seeded",
            Severity.FAIL,
            "ai_providers table empty",
            duration_ms=dur,
            fix_hint="Run `python scripts/seed_ai_config.py`",
        )
    enabled = [code for code, en in rows if en]
    return CheckResult(
        "ai_providers_seeded",
        Severity.OK,
        f"{len(rows)} providers, {len(enabled)} enabled",
        duration_ms=dur,
        details={"all": [r[0] for r in rows], "enabled": enabled},
    )


# ---------------------------------------------------------------------------
# Check 5: bot_model_bindings.purpose naming valid (BUG #1 catch)
# ---------------------------------------------------------------------------
async def check_purpose_naming() -> CheckResult:
    """CATCH BUG #1 from V2_MIGRATION_BUG_LESSONS.

    Detect bot_model_bindings.purpose values that drifted from canonical
    enum (e.g. legacy ``'reranker'`` instead of ``'rerank'``).
    """
    t0 = time.monotonic()
    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError:
        return CheckResult("bot_model_bindings_purpose_valid", Severity.SKIP, "psycopg2 missing")
    dsn_raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL", "")
    if not dsn_raw:
        return CheckResult("bot_model_bindings_purpose_valid", Severity.SKIP, "no DSN")
    try:
        conn = psycopg2.connect(_normalise_dsn(dsn_raw), connect_timeout=DB_CONNECT_TIMEOUT_S)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT purpose, COUNT(*) "
                "FROM bot_model_bindings "
                "WHERE deleted_at IS NULL GROUP BY purpose ORDER BY purpose",
            )
            rows = cur.fetchall()
        finally:
            conn.close()
    except psycopg2.Error as exc:
        return CheckResult(
            "bot_model_bindings_purpose_valid",
            Severity.WARN,
            f"{type(exc).__name__}: {str(exc)[:200]}",
            fix_hint="Check bot_model_bindings table exists",
        )
    dur = int((time.monotonic() - t0) * 1000)
    legacy_hits = [(p, c) for p, c in rows if p in LEGACY_PURPOSE_VALUES]
    unknown = [
        (p, c) for p, c in rows
        if p not in CANONICAL_PURPOSE_VALUES and p not in LEGACY_PURPOSE_VALUES
    ]
    if legacy_hits:
        return CheckResult(
            "bot_model_bindings_purpose_valid",
            Severity.FAIL,
            f"legacy purpose values found: {legacy_hits}",
            duration_ms=dur,
            details={"legacy": legacy_hits, "all": rows},
            fix_hint="Run: UPDATE bot_model_bindings SET purpose='rerank' WHERE purpose='reranker'",
        )
    if unknown:
        return CheckResult(
            "bot_model_bindings_purpose_valid",
            Severity.WARN,
            f"unknown purpose values: {unknown}",
            duration_ms=dur,
            details={"unknown": unknown, "canonical": list(CANONICAL_PURPOSE_VALUES)},
            fix_hint=f"Update purpose to one of: {CANONICAL_PURPOSE_VALUES}",
        )
    return CheckResult(
        "bot_model_bindings_purpose_valid",
        Severity.OK,
        f"{len(rows)} distinct purposes, all canonical",
        duration_ms=dur,
        details={"distinct": rows},
    )


# ---------------------------------------------------------------------------
# Check 6: env vars present
# ---------------------------------------------------------------------------
async def check_env_vars_present() -> CheckResult:
    """Verify at least one provider has its API key set.

    Returns WARN (not FAIL) because a deployment may legitimately disable
    all live providers (e.g. demo running on cached responses only). The
    FAIL gate is downstream — ``rerank_providers_live`` will fail loudly
    if a provider is configured in DB but its key is empty.
    """
    t0 = time.monotonic()
    set_keys: dict[str, list[str]] = {}
    for provider, keys in PROVIDER_ENV_KEYS.items():
        for k in keys:
            v = os.environ.get(k)
            if v:
                set_keys.setdefault(provider, []).append(k)
    dur = int((time.monotonic() - t0) * 1000)
    if not set_keys:
        return CheckResult(
            "env_vars_present",
            Severity.WARN,
            "no provider API keys set in env",
            duration_ms=dur,
            details={"checked": list(PROVIDER_ENV_KEYS.keys())},
            fix_hint="Set at least one of OPENAI_API_KEY / JINA_API_KEY / etc in .env",
        )
    return CheckResult(
        "env_vars_present",
        Severity.OK,
        f"keys set for: {sorted(set_keys.keys())}",
        duration_ms=dur,
        details={"set_keys": set_keys},
    )


# ---------------------------------------------------------------------------
# Check 7: env vs system_config consistency
# ---------------------------------------------------------------------------
async def check_env_vs_db_consistency() -> CheckResult:
    """Detect drift between ``.env`` (Settings fallback) and system_config (runtime)."""
    t0 = time.monotonic()
    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError:
        return CheckResult("env_vs_db_consistency", Severity.SKIP, "psycopg2 missing")
    dsn_raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL", "")
    if not dsn_raw:
        return CheckResult("env_vs_db_consistency", Severity.SKIP, "no DSN")
    env_embedding_model = os.environ.get("EMBEDDING_MODEL_NAME", "")
    if not env_embedding_model:
        return CheckResult(
            "env_vs_db_consistency",
            Severity.WARN,
            "EMBEDDING_MODEL_NAME not set in env",
            fix_hint="Set EMBEDDING_MODEL_NAME in .env to mirror DB system_config",
        )
    try:
        conn = psycopg2.connect(_normalise_dsn(dsn_raw), connect_timeout=DB_CONNECT_TIMEOUT_S)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT value FROM system_config WHERE key = 'embedding_model'",
            )
            row = cur.fetchone()
        finally:
            conn.close()
    except psycopg2.Error as exc:
        return CheckResult(
            "env_vs_db_consistency",
            Severity.WARN,
            f"DB read failed: {type(exc).__name__}",
        )
    dur = int((time.monotonic() - t0) * 1000)
    if not row:
        return CheckResult(
            "env_vs_db_consistency",
            Severity.WARN,
            "system_config.embedding_model missing",
            duration_ms=dur,
            fix_hint="Run scripts/init_system_config.py",
        )
    db_value_raw = row[0]
    # system_config.value is a JSONB field; trip the wrapping quotes.
    db_value = (
        db_value_raw.strip('"')
        if isinstance(db_value_raw, str)
        else (
            db_value_raw.get("value", "")
            if isinstance(db_value_raw, dict)
            else str(db_value_raw)
        )
    )
    if db_value != env_embedding_model:
        return CheckResult(
            "env_vs_db_consistency",
            Severity.WARN,
            f".env={env_embedding_model!r} vs db={db_value!r}",
            duration_ms=dur,
            details={"env": env_embedding_model, "db": db_value},
            fix_hint="Update EMBEDDING_MODEL_NAME in .env or system_config.embedding_model",
        )
    return CheckResult(
        "env_vs_db_consistency",
        Severity.OK,
        f"embedding_model agree: {db_value!r}",
        duration_ms=dur,
    )


# ---------------------------------------------------------------------------
# Check 8: embedding providers live (smoke probe)
# ---------------------------------------------------------------------------
async def _probe_embedding(model_name: str, provider_code: str) -> CheckResult:
    """Send 1 input through LiteLLM aembedding; succeed iff data returned.

    Each exception is caught NARROWLY per type so one bad provider cannot
    crash the whole preflight (production-mindset: never let a single
    integration failure cascade).
    """
    t0 = time.monotonic()
    name = f"embedding_live[{provider_code}:{model_name}]"
    api_key = _provider_env_value(provider_code)
    if not api_key:
        return CheckResult(
            name,
            Severity.WARN,
            f"no api key for provider={provider_code}",
            fix_hint=f"Set one of: {PROVIDER_ENV_KEYS.get(provider_code, [])}",
        )
    try:
        import litellm  # type: ignore[import-not-found]
    except ImportError as exc:
        return CheckResult(name, Severity.SKIP, f"litellm missing: {exc}")
    # Build kwargs domain-neutrally — only pass api_key for non-OpenAI
    # because OpenAI auto-resolves OPENAI_API_KEY env.
    # LiteLLM model wire format: ``{provider}/{model}`` for non-OpenAI
    # providers (mirrors model_resolver._build_litellm_name). When the
    # ai_models row stores the bare name we re-attach the prefix so
    # LiteLLM can route the call.
    wire_model = model_name
    if "/" not in model_name and provider_code.lower() not in {"openai", ""}:
        wire_model = f"{provider_code.lower()}/{model_name}"
    kwargs: dict[str, object] = {"model": wire_model, "input": [EMBED_PROBE_INPUT]}
    if provider_code.lower() not in {"openai"}:
        kwargs["api_key"] = api_key
    # Asymmetric embedding models need a task tag — supply passage by
    # default since that's the dominant ingest path.
    if provider_code.lower() in {"jina", "jina_ai"}:
        kwargs["task"] = "retrieval.passage"
    try:
        resp = await asyncio.wait_for(
            litellm.aembedding(**kwargs),
            timeout=PROVIDER_PROBE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return CheckResult(
            name,
            Severity.FAIL,
            f"timeout after {PROVIDER_PROBE_TIMEOUT_S}s",
            duration_ms=int((time.monotonic() - t0) * 1000),
            fix_hint="Provider unreachable — check network + api status page",
        )
    except (ValueError, TypeError, KeyError) as exc:
        return CheckResult(
            name,
            Severity.FAIL,
            f"{type(exc).__name__}: {str(exc)[:150]}",
            duration_ms=int((time.monotonic() - t0) * 1000),
            fix_hint="Bad model_name or kwargs — verify ai_models row",
        )
    except Exception as exc:  # noqa: BLE001 — preflight gate must not crash on litellm/network exc
        return CheckResult(
            name,
            Severity.FAIL,
            f"{type(exc).__name__}: {str(exc)[:150]}",
            duration_ms=int((time.monotonic() - t0) * 1000),
            fix_hint="Check provider status page + api key validity",
        )
    dur = int((time.monotonic() - t0) * 1000)
    data = getattr(resp, "data", None)
    if not data:
        return CheckResult(
            name,
            Severity.FAIL,
            "empty embedding response",
            duration_ms=dur,
            fix_hint="Provider returned 200 but no vectors — check model_name",
        )
    # Extract dimension for telemetry
    try:
        first = data[0]
        emb = first.get("embedding") if isinstance(first, dict) else getattr(first, "embedding", None)
        dim = len(emb) if emb else 0
    except (TypeError, AttributeError, IndexError):
        dim = 0
    return CheckResult(
        name,
        Severity.OK,
        f"dim={dim} ({dur} ms)",
        duration_ms=dur,
        details={"dimension": dim, "provider": provider_code, "model": model_name},
    )


async def check_embedding_providers_live() -> list[CheckResult]:
    """For each unique embedding provider in active bindings, smoke probe."""
    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError:
        return [CheckResult("embedding_providers_live", Severity.SKIP, "psycopg2 missing")]
    dsn_raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL", "")
    if not dsn_raw:
        return [CheckResult("embedding_providers_live", Severity.SKIP, "no DSN")]
    try:
        conn = psycopg2.connect(_normalise_dsn(dsn_raw), connect_timeout=DB_CONNECT_TIMEOUT_S)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT m.name, p.code
                FROM bot_model_bindings b
                JOIN ai_models     m ON b.record_model_id   = m.id
                JOIN ai_providers  p ON m.record_provider_id = p.id
                WHERE b.purpose = 'embedding'
                  AND b.active  = true
                  AND b.deleted_at IS NULL
                  AND m.enabled = true
                  AND p.enabled = true
                """,
            )
            pairs = cur.fetchall()
        finally:
            conn.close()
    except psycopg2.Error as exc:
        return [CheckResult(
            "embedding_providers_live",
            Severity.WARN,
            f"DB read failed: {type(exc).__name__}",
            fix_hint="Cannot enumerate active embedding providers",
        )]
    if not pairs:
        # Fall back to env default — still useful to verify at least
        # one model works in dev / fresh installs.
        env_model = os.environ.get("EMBEDDING_MODEL_NAME", "")
        if env_model:
            return [await _probe_embedding(env_model, "openai")]
        return [CheckResult(
            "embedding_providers_live",
            Severity.WARN,
            "no active embedding bindings + no env fallback",
            fix_hint="Seed bot_model_bindings or set EMBEDDING_MODEL_NAME",
        )]
    return [await _probe_embedding(model, code) for model, code in pairs]


# ---------------------------------------------------------------------------
# Check 9: rerank providers live
# ---------------------------------------------------------------------------
async def _probe_reranker(model_name: str, provider_code: str) -> CheckResult:
    """Smoke-rerank 1 doc via the matching strategy from registry."""
    t0 = time.monotonic()
    name = f"rerank_live[{provider_code}:{model_name}]"
    api_key = _provider_env_value(provider_code)
    if not api_key and provider_code.lower() not in {"null", "viranker_local"}:
        return CheckResult(
            name,
            Severity.FAIL,
            f"no api key for provider={provider_code}",
            fix_hint=f"Set one of: {PROVIDER_ENV_KEYS.get(provider_code, [])}",
        )
    try:
        from ragbot.infrastructure.reranker.registry import build_reranker
    except ImportError as exc:
        return CheckResult(name, Severity.SKIP, f"registry import: {exc}")
    try:
        reranker = build_reranker(
            provider=provider_code,
            api_key=api_key,
            model=model_name,
        )
    except (ValueError, TypeError, KeyError) as exc:
        return CheckResult(
            name,
            Severity.FAIL,
            f"build failed: {type(exc).__name__}: {str(exc)[:150]}",
            fix_hint="Check provider key in registry + api_key validity",
        )
    # Skip live probe for null / opt-in local providers.
    if provider_code.lower() in {"null", "viranker_local"}:
        return CheckResult(
            name,
            Severity.OK,
            f"strategy built ({provider_code}), live probe skipped",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
    try:
        out = await asyncio.wait_for(
            reranker.rerank(
                RERANK_PROBE_QUERY,
                [{"content": RERANK_PROBE_DOC, "score": 0.5}],
                top_n=1,
            ),
            timeout=PROVIDER_PROBE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return CheckResult(
            name,
            Severity.FAIL,
            f"timeout after {PROVIDER_PROBE_TIMEOUT_S}s",
            duration_ms=int((time.monotonic() - t0) * 1000),
            fix_hint="Provider unreachable",
        )
    except Exception as exc:  # noqa: BLE001 — narrow not feasible across providers
        return CheckResult(
            name,
            Severity.FAIL,
            f"{type(exc).__name__}: {str(exc)[:150]}",
            duration_ms=int((time.monotonic() - t0) * 1000),
            fix_hint="Check api key + model_name vs ai_models row",
        )
    dur = int((time.monotonic() - t0) * 1000)
    if not out:
        return CheckResult(
            name,
            Severity.FAIL,
            "empty rerank result",
            duration_ms=dur,
        )
    return CheckResult(
        name,
        Severity.OK,
        f"reranked 1 doc ({dur} ms)",
        duration_ms=dur,
        details={"top_score": out[0].get("score"), "provider": provider_code},
    )


async def check_rerank_providers_live() -> list[CheckResult]:
    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError:
        return [CheckResult("rerank_providers_live", Severity.SKIP, "psycopg2 missing")]
    dsn_raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL", "")
    if not dsn_raw:
        return [CheckResult("rerank_providers_live", Severity.SKIP, "no DSN")]
    try:
        conn = psycopg2.connect(_normalise_dsn(dsn_raw), connect_timeout=DB_CONNECT_TIMEOUT_S)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT m.name, p.code
                FROM bot_model_bindings b
                JOIN ai_models     m ON b.record_model_id   = m.id
                JOIN ai_providers  p ON m.record_provider_id = p.id
                WHERE b.purpose = 'rerank'
                  AND b.active  = true
                  AND b.deleted_at IS NULL
                  AND m.enabled = true
                  AND p.enabled = true
                """,
            )
            pairs = cur.fetchall()
        finally:
            conn.close()
    except psycopg2.Error as exc:
        return [CheckResult(
            "rerank_providers_live",
            Severity.WARN,
            f"DB read failed: {type(exc).__name__}",
        )]
    if not pairs:
        return [CheckResult(
            "rerank_providers_live",
            Severity.WARN,
            "no active rerank bindings",
            fix_hint="Seed bot_model_bindings purpose='rerank' or accept null reranker",
        )]
    return [await _probe_reranker(model, code) for model, code in pairs]


# ---------------------------------------------------------------------------
# Check 10: LLM providers live
# ---------------------------------------------------------------------------
async def check_llm_providers_live() -> list[CheckResult]:
    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError:
        return [CheckResult("llm_providers_live", Severity.SKIP, "psycopg2 missing")]
    dsn_raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL", "")
    if not dsn_raw:
        return [CheckResult("llm_providers_live", Severity.SKIP, "no DSN")]
    try:
        conn = psycopg2.connect(_normalise_dsn(dsn_raw), connect_timeout=DB_CONNECT_TIMEOUT_S)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT m.name, p.code
                FROM bot_model_bindings b
                JOIN ai_models     m ON b.record_model_id   = m.id
                JOIN ai_providers  p ON m.record_provider_id = p.id
                WHERE b.purpose = 'llm_primary'
                  AND b.active  = true
                  AND b.deleted_at IS NULL
                  AND m.enabled = true
                  AND p.enabled = true
                """,
            )
            pairs = cur.fetchall()
        finally:
            conn.close()
    except psycopg2.Error as exc:
        return [CheckResult(
            "llm_providers_live",
            Severity.WARN,
            f"DB read failed: {type(exc).__name__}",
        )]
    if not pairs:
        return [CheckResult(
            "llm_providers_live",
            Severity.WARN,
            "no active llm_primary bindings",
            fix_hint="Seed bot_model_bindings purpose='llm_primary'",
        )]
    return [await _probe_llm(model, code) for model, code in pairs]


async def _probe_llm(model_name: str, provider_code: str) -> CheckResult:
    """Send 1 short completion via litellm.acompletion."""
    t0 = time.monotonic()
    name = f"llm_live[{provider_code}:{model_name}]"
    api_key = _provider_env_value(provider_code)
    if not api_key:
        return CheckResult(
            name,
            Severity.FAIL,
            f"no api key for provider={provider_code}",
            fix_hint=f"Set one of: {PROVIDER_ENV_KEYS.get(provider_code, [])}",
        )
    try:
        import litellm  # type: ignore[import-not-found]
    except ImportError as exc:
        return CheckResult(name, Severity.SKIP, f"litellm missing: {exc}")
    wire_model = model_name
    if "/" not in model_name and provider_code.lower() not in {"openai", ""}:
        wire_model = f"{provider_code.lower()}/{model_name}"
    kwargs: dict[str, object] = {
        "model": wire_model,
        "messages": [{"role": "user", "content": LLM_PROBE_PROMPT}],
        "max_tokens": 5,
    }
    if provider_code.lower() not in {"openai"}:
        kwargs["api_key"] = api_key
    try:
        resp = await asyncio.wait_for(
            litellm.acompletion(**kwargs),
            timeout=PROVIDER_PROBE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return CheckResult(
            name,
            Severity.FAIL,
            f"timeout after {PROVIDER_PROBE_TIMEOUT_S}s",
            duration_ms=int((time.monotonic() - t0) * 1000),
            fix_hint="Provider unreachable",
        )
    except Exception as exc:  # noqa: BLE001 — narrow not feasible across providers
        return CheckResult(
            name,
            Severity.FAIL,
            f"{type(exc).__name__}: {str(exc)[:150]}",
            duration_ms=int((time.monotonic() - t0) * 1000),
            fix_hint="Check api key + model_name vs ai_models row",
        )
    dur = int((time.monotonic() - t0) * 1000)
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return CheckResult(
            name,
            Severity.FAIL,
            "empty completion",
            duration_ms=dur,
        )
    return CheckResult(
        name,
        Severity.OK,
        f"completion ok ({dur} ms)",
        duration_ms=dur,
        details={"provider": provider_code, "model": model_name},
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
async def run_all_checks() -> list[CheckResult]:
    """Run the full preflight battery, ordered by dependency."""
    _load_env()
    results: list[CheckResult] = []

    # Gate-1: DB. Without DB, downstream checks are moot.
    db_result = await check_db_connection()
    results.append(db_result)
    if db_result.severity == Severity.FAIL:
        results.append(CheckResult(
            "downstream_checks",
            Severity.SKIP,
            "skipped — DB unreachable",
        ))
        return results

    # Schema + config layer (cheap, sequential).
    results.append(await check_alembic_head())
    results.append(await check_system_config_keys())
    results.append(await check_ai_providers_seeded())
    results.append(await check_purpose_naming())

    # Env layer.
    results.append(await check_env_vars_present())
    results.append(await check_env_vs_db_consistency())

    # Live provider probes (network IO, may be slow). Run sequentially per
    # provider so a hung connection is surfaced individually rather than as
    # a swallowed gather() failure.
    results.extend(await check_embedding_providers_live())
    results.extend(await check_rerank_providers_live())
    results.extend(await check_llm_providers_live())

    return results


def _exit_code(results: list[CheckResult], strict: bool) -> int:
    has_fail = any(r.severity == Severity.FAIL for r in results)
    has_warn = any(r.severity == Severity.WARN for r in results)
    if has_fail:
        return 2
    if strict and has_warn:
        return 1
    return 0


def _print_results(results: list[CheckResult], json_output: bool) -> None:
    if json_output:
        print(json.dumps(
            [r.to_dict() for r in results],
            ensure_ascii=False,
            indent=2,
        ))
        return
    use_color = sys.stdout.isatty()
    bar = "=" * 80
    print()
    print(bar)
    print(f"RAGBOT PRE-FLIGHT CHECK  -  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(bar)
    for r in results:
        if r.severity == Severity.OK:
            icon, color = "[ OK ]", "\033[32m"
        elif r.severity == Severity.WARN:
            icon, color = "[WARN]", "\033[33m"
        elif r.severity == Severity.FAIL:
            icon, color = "[FAIL]", "\033[31m"
        else:
            icon, color = "[SKIP]", "\033[90m"
        reset = "\033[0m"
        if not use_color:
            color = reset = ""
        line_name = r.name if len(r.name) <= 50 else r.name[:47] + "..."
        print(f"  {color}{icon}{reset} {line_name:<52} {r.message}")
        if r.fix_hint and r.severity in (Severity.FAIL, Severity.WARN):
            print(f"         FIX: {r.fix_hint}")
    print(bar)
    n_ok = sum(1 for r in results if r.severity == Severity.OK)
    n_warn = sum(1 for r in results if r.severity == Severity.WARN)
    n_fail = sum(1 for r in results if r.severity == Severity.FAIL)
    n_skip = sum(1 for r in results if r.severity == Severity.SKIP)
    print(f"SUMMARY: {n_ok} OK | {n_warn} WARN | {n_fail} FAIL | {n_skip} SKIP")
    print(bar)


def _silence_third_party_logs() -> None:
    """Route structlog + python logging to stderr; suppress litellm chatter.

    Important for ``--json`` mode: any non-JSON line on stdout breaks the
    machine-readable contract. We always log to stderr regardless of mode
    so a CI consumer can ``--json`` pipe to jq cleanly.
    """
    import logging
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    # litellm prints a "Provider List" banner on first import — silence it.
    os.environ.setdefault("LITELLM_LOG", "ERROR")
    try:
        import litellm  # type: ignore[import-not-found]
        # Suppress info-level output from litellm.
        litellm.suppress_debug_info = True  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        pass
    # Re-route structlog to stderr.
    try:
        import structlog
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
            logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        )
    except (ImportError, AttributeError):
        pass


async def main(strict: bool = False, json_output: bool = False) -> int:
    _silence_third_party_logs()
    try:
        results = await run_all_checks()
    except Exception as exc:  # noqa: BLE001 — top-level entrypoint, never crash
        print(
            json.dumps({"error": f"{type(exc).__name__}: {exc}"})
            if json_output
            else f"PREFLIGHT CRASHED: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    _print_results(results, json_output)
    return _exit_code(results, strict)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ragbot pre-flight check — gate before deploy / restart.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat WARN as failure (exit 1).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON to stdout (machine-readable).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(asyncio.run(main(strict=args.strict, json_output=args.json)))
