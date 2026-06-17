#!/usr/bin/env python3
"""Audit Anthropic prompt-cache utilization across LLM call sites (T1.5.S28).

Scans the last N rows of ``model_invocations`` (joined with ``ai_models``
for nominal pricing), groups by ``purpose`` (Q3 understand_query / Q4
rewrite / Q12 grading / Q14 generation / Q16 reflection / ...) and
estimates per-purpose prompt-cache hit ratio + cost savings.

Hit-ratio formula
-----------------
Anthropic charges 10% of the input rate for cache reads (5-min TTL).
Without per-row ``cached_tokens`` persisted on ``model_invocations`` we
infer the effective rate from ``cost_usd`` vs ``prompt_tokens``:

    effective_rate = total_cost_input / total_prompt_tokens
    nominal_rate   = avg(input_price_per_1k_usd) for matched models
    discount_pct   = 1 - (effective_rate / nominal_rate)

A discount near 0% means the cache is cold (every call is a miss); a
discount approaching 90% means almost every call hit the cache.

Caveats
-------
- ``model_invocations`` lumps prompt + completion into one ``cost_usd``
  column; this script approximates the input portion as
  ``cost_usd × prompt_tokens / (prompt_tokens + completion_tokens)``.
  That's accurate when the input/output rate ratio is stable across the
  bot's models — true for the canonical Anthropic deployment where one
  model serves all purposes within a binding tier.
- OpenAI models do automatic caching for prompts ≥1024 tokens; this
  script flags the OpenAI rows but doesn't compute a discount (no
  separate cached-rate column on OpenAI today).
- Rows with zero ``prompt_tokens`` (failed calls, grounding probes
  without a system prompt) are dropped — they'd skew the rate.

Usage
-----
    python scripts/audit_prompt_cache_utilization.py
    python scripts/audit_prompt_cache_utilization.py --bot-id <UUID> --limit 5000
    python scripts/audit_prompt_cache_utilization.py --since-hours 24

Read-only — never writes, safe to run against prod.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

import asyncpg  # type: ignore[import-untyped]


DEFAULT_LIMIT = 1000
DEFAULT_SINCE_HOURS = 24 * 7
ANTHROPIC_CACHE_READ_DISCOUNT = Decimal("0.10")  # Anthropic: 10% of input rate
SAVING_BREAKEVEN_DISCOUNT = Decimal("0.30")  # CLAUDE.md target: ≥30% hit ratio


@dataclass
class PurposeStats:
    purpose: str
    provider: str
    total_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    nominal_input_rate_per_1k: Decimal | None = None
    cached_input_rate_per_1k: Decimal | None = None

    @property
    def avg_prompt_tokens(self) -> int:
        if self.total_calls == 0:
            return 0
        return int(self.total_prompt_tokens / self.total_calls)

    @property
    def effective_input_cost_usd(self) -> Decimal:
        """Approximate share of cost_usd attributable to input tokens."""
        denom = self.total_prompt_tokens + self.total_completion_tokens
        if denom == 0:
            return Decimal("0")
        share = Decimal(self.total_prompt_tokens) / Decimal(denom)
        return self.total_cost_usd * share

    @property
    def effective_input_rate_per_1k(self) -> Decimal:
        if self.total_prompt_tokens == 0:
            return Decimal("0")
        return (
            self.effective_input_cost_usd
            * Decimal(1000)
            / Decimal(self.total_prompt_tokens)
        )

    @property
    def discount_pct(self) -> Decimal:
        """Estimated cache-driven discount on the nominal input rate."""
        if self.nominal_input_rate_per_1k in (None, Decimal("0")):
            return Decimal("0")
        nominal = self.nominal_input_rate_per_1k
        effective = self.effective_input_rate_per_1k
        if nominal == 0:
            return Decimal("0")
        ratio = effective / nominal
        if ratio >= 1:
            return Decimal("0")
        return Decimal(1) - ratio

    @property
    def hit_ratio_estimate(self) -> Decimal:
        """Map a 0..(1-CACHE_READ_DISCOUNT) discount → 0..1 hit ratio.

        If every call hit the cache the rate would be
        ``ANTHROPIC_CACHE_READ_DISCOUNT × nominal``, so the discount maxes
        at ``1 - ANTHROPIC_CACHE_READ_DISCOUNT = 0.90``. Linear interpolate.
        """
        max_discount = Decimal(1) - ANTHROPIC_CACHE_READ_DISCOUNT
        if max_discount == 0:
            return Decimal("0")
        ratio = self.discount_pct / max_discount
        if ratio < 0:
            return Decimal("0")
        if ratio > 1:
            return Decimal("1")
        return ratio

    @property
    def potential_savings_usd(self) -> Decimal:
        """If hit-ratio reached 100%, how much cheaper would the input be?"""
        if self.nominal_input_rate_per_1k is None:
            return Decimal("0")
        nominal = self.nominal_input_rate_per_1k
        cached_rate = (
            self.cached_input_rate_per_1k
            if self.cached_input_rate_per_1k is not None
            else nominal * ANTHROPIC_CACHE_READ_DISCOUNT
        )
        per_1k_savings = nominal - cached_rate
        return Decimal(self.total_prompt_tokens) / Decimal(1000) * per_1k_savings


def _is_anthropic(provider: str) -> bool:
    p = (provider or "").lower()
    return "anthropic" in p or "claude" in p


def aggregate_rows(rows: Iterable[dict[str, Any]]) -> list[PurposeStats]:
    """Fold raw rows into per-(purpose, provider) PurposeStats buckets.

    Pure function so the unit tests can drive it with handcrafted dicts
    without spinning up postgres.
    """
    buckets: dict[tuple[str, str], PurposeStats] = {}
    nominal_sums: dict[tuple[str, str], Decimal] = {}
    nominal_counts: dict[tuple[str, str], int] = {}
    cached_sums: dict[tuple[str, str], Decimal] = {}
    cached_counts: dict[tuple[str, str], int] = {}
    for row in rows:
        purpose = str(row.get("purpose") or "unknown")
        provider = str(row.get("provider") or "unknown")
        prompt_tokens = int(row.get("prompt_tokens") or 0)
        completion_tokens = int(row.get("completion_tokens") or 0)
        if prompt_tokens <= 0:
            continue
        cost_usd = row.get("cost_usd") or Decimal("0")
        if not isinstance(cost_usd, Decimal):
            cost_usd = Decimal(str(cost_usd))
        key = (purpose, provider)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = PurposeStats(purpose=purpose, provider=provider)
            buckets[key] = bucket
        bucket.total_calls += 1
        bucket.total_prompt_tokens += prompt_tokens
        bucket.total_completion_tokens += completion_tokens
        bucket.total_cost_usd += cost_usd
        nominal = row.get("input_price_per_1k_usd")
        if nominal is not None:
            nominal_dec = nominal if isinstance(nominal, Decimal) else Decimal(str(nominal))
            nominal_sums[key] = nominal_sums.get(key, Decimal("0")) + nominal_dec
            nominal_counts[key] = nominal_counts.get(key, 0) + 1
        cached = row.get("input_price_per_1k_cached_usd")
        if cached is not None:
            cached_dec = cached if isinstance(cached, Decimal) else Decimal(str(cached))
            cached_sums[key] = cached_sums.get(key, Decimal("0")) + cached_dec
            cached_counts[key] = cached_counts.get(key, 0) + 1
    for key, bucket in buckets.items():
        n = nominal_counts.get(key, 0)
        if n > 0:
            bucket.nominal_input_rate_per_1k = nominal_sums[key] / Decimal(n)
        c = cached_counts.get(key, 0)
        if c > 0:
            bucket.cached_input_rate_per_1k = cached_sums[key] / Decimal(c)
    return sorted(
        buckets.values(),
        key=lambda b: (b.provider, b.purpose),
    )


def render_table(stats: list[PurposeStats]) -> str:
    """Pretty-print PurposeStats as a fixed-width table."""
    header = (
        f"{'purpose':<22} {'provider':<14} {'calls':>7} "
        f"{'avg_prompt':>11} {'eff_rate':>10} {'nominal':>10} "
        f"{'discount':>9} {'hit_est':>8} {'save_est_usd':>13}"
    )
    sep = "-" * len(header)
    lines: list[str] = [header, sep]
    for s in stats:
        nominal = (
            f"{s.nominal_input_rate_per_1k:.4f}"
            if s.nominal_input_rate_per_1k is not None
            else "n/a"
        )
        cache_marker = ""
        if _is_anthropic(s.provider):
            ratio = s.hit_ratio_estimate
            if ratio < SAVING_BREAKEVEN_DISCOUNT:
                cache_marker = " *"  # below CLAUDE.md target
        lines.append(
            f"{s.purpose:<22} {s.provider:<14} {s.total_calls:>7} "
            f"{s.avg_prompt_tokens:>11} "
            f"{s.effective_input_rate_per_1k:.4f}".rjust(11)
            + f"  {nominal:>10} "
            + f"{s.discount_pct * 100:>7.1f}% "
            + f"{s.hit_ratio_estimate * 100:>6.1f}%"
            + cache_marker
            + f"  {s.potential_savings_usd:>11.4f}"
        )
    lines.append(sep)
    lines.append(
        "* = below CLAUDE.md target (≥30% hit ratio); investigate cache prefix stability."
    )
    return "\n".join(lines)


SQL_FETCH = """
SELECT
    inv.purpose,
    inv.provider,
    inv.prompt_tokens,
    inv.completion_tokens,
    inv.cost_usd,
    inv.model_id,
    inv.started_at,
    am.input_price_per_1k_usd,
    am.input_price_per_1k_cached_usd
FROM model_invocations AS inv
LEFT JOIN ai_models AS am
    ON  am.model_id = inv.model_id
     OR am.name     = inv.model_id
     OR am.model_id = split_part(inv.model_id, '/', 2)
     OR am.name     = split_part(inv.model_id, '/', 2)
WHERE inv.status = 'success'
  AND inv.started_at >= $1
  AND ($2::uuid IS NULL OR inv.record_tenant_id = $2)
ORDER BY inv.started_at DESC
LIMIT $3
"""


def _normalise_dsn(dsn: str) -> str:
    """Strip SQLAlchemy driver prefixes so asyncpg accepts the URL.

    Production wires ``DATABASE_URL=postgresql+asyncpg://...`` for the
    SQLAlchemy engine; ``asyncpg.connect`` only knows the bare schemes.
    """
    if dsn.startswith("postgresql+asyncpg://"):
        return "postgresql://" + dsn[len("postgresql+asyncpg://"):]
    if dsn.startswith("postgres+asyncpg://"):
        return "postgres://" + dsn[len("postgres+asyncpg://"):]
    return dsn


async def fetch_rows(
    dsn: str,
    *,
    since: datetime,
    record_tenant_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(_normalise_dsn(dsn))
    try:
        rows = await conn.fetch(SQL_FETCH, since, record_tenant_id, limit)
    finally:
        await conn.close()
    return [dict(r) for r in rows]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bot-id", default=None, help="(unused — filters happen via tenant)")
    p.add_argument("--tenant-id", default=None, help="record_tenant_id UUID filter")
    p.add_argument(
        "--since-hours",
        type=int,
        default=DEFAULT_SINCE_HOURS,
        help="Look back this many hours (default: 1 week).",
    )
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    p.add_argument(
        "--dsn",
        default=os.getenv("DATABASE_URL"),
        help="Postgres DSN (defaults to $DATABASE_URL).",
    )
    return p.parse_args()


async def _main_async(args: argparse.Namespace) -> int:
    if not args.dsn:
        print("error: DATABASE_URL not set and --dsn not provided", file=sys.stderr)
        return 2
    since = datetime.now(timezone.utc) - timedelta(hours=int(args.since_hours))
    rows = await fetch_rows(
        args.dsn,
        since=since,
        record_tenant_id=args.tenant_id,
        limit=int(args.limit),
    )
    if not rows:
        print(
            f"no model_invocations rows since {since.isoformat()} "
            f"(tenant_id={args.tenant_id}, limit={args.limit})",
        )
        return 0
    stats = aggregate_rows(rows)
    print(f"Audit window: since {since.isoformat()}, rows={len(rows)}")
    print(render_table(stats))
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
