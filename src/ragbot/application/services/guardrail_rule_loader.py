"""DB-driven guardrail rule loader (Agent J).

Replaces the hard-compiled regex constants previously held at the top of
``infrastructure/guardrails/local_guardrail.py``. Pulls rule rows from
the ``guardrail_rules`` table (alembic 010f), compiles them once per
``(pattern, flags)`` tuple, and serves a ``RuleSet`` to the guardrail
orchestrator.

Resolution semantics — per Agent J §7:
  * ``record_tenant_id IS NULL`` rows are the platform default applied to
    every tenant.
  * A row with ``record_tenant_id = :tenant_id`` and the same ``rule_id``
    OVERRIDES the platform default (it can disable, retune, or change
    severity/action). Override wins regardless of priority.
  * ``enabled = false`` rows are filtered out.

Cache layout:
  * L1 in-process dict: ``(tenant_uuid|None) -> (RuleSet, monotonic_ts)``.
    TTL = ``DEFAULT_GUARDRAIL_RULE_LOADER_TTL_S`` (60s by default). A
    single-flight ``asyncio.Lock`` per cache key prevents thundering-herd
    re-fetches when many requests miss simultaneously.
  * L2 Redis: not used yet — kept as a future extension hook. Adding L2
    requires invalidate fan-out via the outbox event
    ``SUBJECT_GUARDRAIL_RULES_CHANGED``; the loader already publishes the
    event from ``invalidate()`` so an L2 plug-in will Just Work.

Compile failures (malformed regex authored by an admin) are LOGGED at
WARN with ``rule_id`` + traceback and the row is SKIPPED — never crash
the loader, since one bad pattern would take down moderation entirely.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import UUID

import orjson
import structlog
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.infrastructure.guardrails._default_patterns import parse_flag_mask
from ragbot.shared.constants import (
    DEFAULT_GUARDRAIL_RULE_LOADER_TTL_S,
    SUBJECT_GUARDRAIL_RULES_CHANGED,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CompiledRule:
    """One compiled moderation rule ready for runtime evaluation."""

    rule_id: str
    pattern: re.Pattern[str]
    severity: str  # info | warn | block
    action: str  # allow | redact | block | hitl
    scope: str  # input | output | both
    priority: int
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuleSet:
    """Compiled rules grouped by scope, priority-sorted.

    ``version`` is a monotonic counter the loader bumps on every refresh —
    callers may check it to invalidate downstream caches keyed off a
    RuleSet identity.
    """

    input_rules: tuple[CompiledRule, ...]
    output_rules: tuple[CompiledRule, ...]
    version: int = 0


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
class GuardrailRuleLoader:
    """DB-backed cache of compiled moderation rules.

    Single-flight, TTL-bounded. Holding the ``_lock`` for the duration of
    a DB fetch is fine because rule sets are tiny (≤ 100 rows) and the
    lock is per-process — the L1 cache means a hot tenant pays the DB
    cost at most once per TTL window.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis_client: Any | None = None,
        ttl_seconds: int = DEFAULT_GUARDRAIL_RULE_LOADER_TTL_S,
    ) -> None:
        self._sf = session_factory
        self._redis = redis_client
        self._ttl_s = ttl_seconds
        # Cache key is the tenant UUID (or None for platform-only fetches).
        self._cache: dict[UUID | None, tuple[RuleSet, float]] = {}
        self._locks: dict[UUID | None, asyncio.Lock] = {}
        self._version_counter = 0

    # ---- bootstrap ------------------------------------------------------
    async def bootstrap(self) -> None:
        """Pre-warm the platform-default rule set at app startup.

        Logs a CRITICAL warning when the table is empty — that signals the
        seed migration didn't land. Does NOT raise: keeping the service up
        on a degraded ruleset is better than refusing boot (the runtime
        fallback inside LocalGuardrail still honours the SSoT defaults).
        """
        ruleset = await self.get_rules(record_tenant_id=None)
        total = len(ruleset.input_rules) + len(ruleset.output_rules)
        if total == 0:
            logger.critical(
                "guardrail_rule_loader_empty",
                hint=(
                    "Run `alembic upgrade head` — migration 010f seeds "
                    "12 platform-default rules into guardrail_rules."
                ),
            )
        else:
            logger.info(
                "guardrail_rule_loader_bootstrap_done",
                input_rule_count=len(ruleset.input_rules),
                output_rule_count=len(ruleset.output_rules),
            )

    # ---- read path ------------------------------------------------------
    async def get_rules(
        self,
        record_tenant_id: UUID | None,
        scope: str | None = None,  # noqa: ARG002 — reserved for future per-scope cache split
    ) -> RuleSet:
        """Return the compiled rule set for *record_tenant_id*.

        Pass ``None`` to fetch the platform default set only. The returned
        RuleSet is sliced into ``input_rules`` / ``output_rules`` — rules
        with ``scope='both'`` appear in BOTH lists so the orchestrator
        doesn't need to dispatch.

        ``scope`` is reserved for a future per-scope cache split; passing
        it has no effect yet (the loader returns the full set).
        """
        now = time.monotonic()
        cached = self._cache.get(record_tenant_id)
        if cached is not None:
            ruleset, ts = cached
            if (now - ts) <= self._ttl_s:
                return ruleset

        lock = self._locks.setdefault(record_tenant_id, asyncio.Lock())
        async with lock:
            # Re-check after acquiring the lock — another coroutine may have
            # filled the cache while we were waiting.
            cached = self._cache.get(record_tenant_id)
            if cached is not None:
                ruleset, ts = cached
                if (now - ts) <= self._ttl_s:
                    return ruleset

            ruleset = await self._fetch_and_compile(record_tenant_id)
            self._cache[record_tenant_id] = (ruleset, time.monotonic())
            return ruleset

    async def _fetch_and_compile(
        self,
        record_tenant_id: UUID | None,
    ) -> RuleSet:
        rows = await self._fetch_rows(record_tenant_id)
        merged = self._merge_tenant_override(rows, record_tenant_id)
        compiled = self._compile_rows(merged)
        self._version_counter += 1
        input_rules = tuple(
            sorted(
                (r for r in compiled if r.scope in ("input", "both")),
                key=lambda r: r.priority,
            ),
        )
        output_rules = tuple(
            sorted(
                (r for r in compiled if r.scope in ("output", "both")),
                key=lambda r: r.priority,
            ),
        )
        return RuleSet(
            input_rules=input_rules,
            output_rules=output_rules,
            version=self._version_counter,
        )

    async def _fetch_rows(
        self,
        record_tenant_id: UUID | None,
    ) -> list[dict[str, Any]]:
        """SELECT enabled rules for tenant + platform default, single round-trip."""
        sql_platform_only = """
            SELECT rule_id, pattern, pattern_flags, severity, action_taken,
                   scope, priority, metadata_json, record_tenant_id
            FROM guardrail_rules
            WHERE enabled = true AND record_tenant_id IS NULL
        """
        sql_with_tenant = """
            SELECT rule_id, pattern, pattern_flags, severity, action_taken,
                   scope, priority, metadata_json, record_tenant_id
            FROM guardrail_rules
            WHERE enabled = true
              AND (record_tenant_id IS NULL OR record_tenant_id = :tenant)
        """
        try:
            async with self._sf() as session:
                if record_tenant_id is None:
                    result = await session.execute(text(sql_platform_only))
                else:
                    result = await session.execute(
                        text(sql_with_tenant), {"tenant": record_tenant_id},
                    )
                return [dict(r._mapping) for r in result.fetchall()]
        except SQLAlchemyError:
            logger.warning(
                "guardrail_rule_loader_db_error",
                record_tenant_id=str(record_tenant_id) if record_tenant_id else None,
                exc_info=True,
            )
            return []

    @staticmethod
    def _merge_tenant_override(
        rows: list[dict[str, Any]],
        record_tenant_id: UUID | None,
    ) -> list[dict[str, Any]]:
        """Tenant-specific row replaces platform default with the same rule_id."""
        if record_tenant_id is None:
            return rows
        platform: dict[str, dict[str, Any]] = {}
        override: dict[str, dict[str, Any]] = {}
        for r in rows:
            if r.get("record_tenant_id") is None:
                platform[r["rule_id"]] = r
            else:
                override[r["rule_id"]] = r
        merged = {**platform, **override}
        return list(merged.values())

    def _compile_rows(
        self,
        rows: list[dict[str, Any]],
    ) -> list[CompiledRule]:
        compiled: list[CompiledRule] = []
        for r in rows:
            rule_id = r["rule_id"]
            pattern_src = r["pattern"]
            flags_csv = r.get("pattern_flags") or ""
            try:
                pattern = re.compile(pattern_src, parse_flag_mask(flags_csv))
            except re.error:
                logger.warning(
                    "guardrail_rule_compile_failed",
                    rule_id=rule_id,
                    pattern=pattern_src[:100],
                    exc_info=True,
                )
                continue
            metadata_raw = r.get("metadata_json") or {}
            if isinstance(metadata_raw, (bytes, str)):
                try:
                    metadata = orjson.loads(metadata_raw)
                except orjson.JSONDecodeError:
                    metadata = {}
            else:
                metadata = dict(metadata_raw)
            compiled.append(
                CompiledRule(
                    rule_id=rule_id,
                    pattern=pattern,
                    severity=r["severity"],
                    action=r["action_taken"],
                    scope=r["scope"],
                    priority=int(r.get("priority") or 100),
                    metadata=metadata,
                ),
            )
        return compiled

    # ---- write path -----------------------------------------------------
    async def invalidate(self, record_tenant_id: UUID | None = None) -> None:
        """Drop the L1 cache for *record_tenant_id* (or all when None).

        Also publishes ``SUBJECT_GUARDRAIL_RULES_CHANGED`` so peer
        processes can pick up the change once an outbox listener is
        wired. Best-effort: a Redis error logs and continues — local
        cache eviction has already happened.
        """
        if record_tenant_id is None:
            self._cache.clear()
        else:
            self._cache.pop(record_tenant_id, None)

        if self._redis is None:
            return
        try:
            payload = orjson.dumps(
                {
                    "record_tenant_id": (
                        str(record_tenant_id) if record_tenant_id else None
                    ),
                },
            )
            await self._redis.publish(SUBJECT_GUARDRAIL_RULES_CHANGED, payload)
        except RedisError:
            logger.warning(
                "guardrail_rule_invalidate_publish_failed",
                exc_info=True,
            )


__all__ = [
    "CompiledRule",
    "GuardrailRuleLoader",
    "RuleSet",
]
