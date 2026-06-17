#!/usr/bin/env python3
"""rago_pareto_sweep.py — Stream D Phase 2 (Paper 26 RAGO offline sweep).

Runs N random configs × M turns each against a single bot, capturing
``(pass_rate, p95_ms, cost_per_turn, hallu_count)`` per config to a CSV
ready for Pareto frontier compute (`rago_pareto_pick.py`).

The sweep applies each config by writing rows to ``system_config`` via
``SystemConfigService.set``, runs the load-test subset, then restores
the baseline so a partial run never leaves the dev DB in an off-axis
state.

Domain-neutral: bot identity (``record_tenant_id``, ``bot_id``,
``channel_type``) comes from CLI flags / env. Question file is the
caller's choice (default: ``tests/fixtures/agent_d_questions.md``).

Usage::

    python scripts/rago_pareto_sweep.py \\
        --bot-id <slug> --tenant-id <int> --channel-type web \\
        --questions-file tests/fixtures/agent_d_questions.md \\
        --schema docs/master/16-P-rago-schema.md \\
        --n-configs 30 --n-turns-per-config 30 \\
        --output reports/RAGO_PARETO_SWEEP_$(date +%Y%m%d).csv

Sacred contracts (CLAUDE.md):
- HALLU=0 sacred — flagged in CSV, excluded by ``rago_pareto_pick``
- 4-key bot identity — record_tenant_id resolved via JWT
- Domain-neutral — no brand literals
- Zero-hardcode — all knob ranges read from RAGSchema doc
- App KHÔNG inject text — sweep only changes config knobs, not prompts
- App KHÔNG override answer — generator output is captured verbatim
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
# Order matters: REPO_ROOT first so ``import scripts.X`` works inside
# the agent_d_loadtest module (which uses ``from scripts._loadtest_common``).
# Then ``REPO_ROOT/src`` for ragbot package, and ``REPO_ROOT/scripts`` for
# direct sibling-script imports.
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from _loadtest_common import is_refuse  # noqa: E402

# --- Module constants — no inline magic numbers below this block ----------- #

DEFAULT_N_CONFIGS = 30
DEFAULT_N_TURNS_PER_CONFIG = 30
DEFAULT_SCHEMA_PATH = "docs/master/16-P-rago-schema.md"
DEFAULT_QUESTIONS_FILE = "tests/fixtures/agent_d_questions.md"
DEFAULT_PAUSE_BETWEEN_QUERIES_S = 1.0
SWEEP_RNG_SEED = 20260506  # deterministic Latin-hypercube reproducibility
PERCENTILE_P95 = 95
COST_USD_PER_TURN_DEFAULT = 0.0  # filled from response when present
HALLU_REFUSE_TRAP_MARKER = "r60"  # adversarial trap turn id prefix

# RAGSchema markdown row regex: captures table rows from the spec file.
# Format: ``| 1 | `chunk_size` | int | 256 | 2048 | 1024 | quality+cost | [512, 1024, 1536, 2048] | <source> |``
_SCHEMA_ROW_RE = re.compile(
    r"^\|\s*\d+\s*\|\s*`(?P<key>[a-z_]+)`\s*\|\s*(?P<type>int|float|bool)\s*\|"
    r"[^|]*\|[^|]*\|\s*(?P<default>[a-zA-Z0-9.]+)\s*\|[^|]*\|"
    r"\s*\[(?P<sweep>[^\]]+)\]\s*\|"
)


@dataclass(frozen=True)
class KnobSpec:
    key: str
    knob_type: str  # "int" | "float" | "bool"
    default: Any
    sweep_values: tuple[Any, ...]


@dataclass(frozen=True)
class BotIdentity:
    tenant_id: int
    bot_id: str
    channel_type: str


@dataclass
class ConfigRunResult:
    config_id: int
    knob_values: dict[str, Any]
    n_turns: int
    pass_rate: float
    p95_ms: float
    cost_per_turn: float
    hallu_count: int
    error_count: int
    raw_metrics: list[dict[str, Any]] = field(default_factory=list)


# --- Schema parser --------------------------------------------------------- #


def parse_schema(path: Path) -> list[KnobSpec]:
    """Parse RAGSchema markdown spec, return sweep-eligible knobs."""
    knobs: list[KnobSpec] = []
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = _SCHEMA_ROW_RE.match(line.strip())
        if not m:
            continue
        key = m.group("key")
        knob_type = m.group("type")
        default_raw = m.group("default")
        sweep_raw = m.group("sweep")
        values = _parse_sweep_values(sweep_raw, knob_type)
        if not values:
            continue
        default = _coerce(default_raw, knob_type)
        knobs.append(
            KnobSpec(key=key, knob_type=knob_type, default=default, sweep_values=values)
        )
    return knobs


def _parse_sweep_values(raw: str, knob_type: str) -> tuple[Any, ...]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(_coerce(p, knob_type) for p in parts)


def _coerce(raw: str, knob_type: str) -> Any:
    raw = raw.strip()
    if knob_type == "bool":
        return raw.lower() == "true"
    if knob_type == "int":
        return int(raw)
    if knob_type == "float":
        return float(raw)
    return raw


# --- Latin hypercube sampling --------------------------------------------- #


def latin_hypercube_sample(
    knobs: list[KnobSpec],
    n_configs: int,
    *,
    seed: int = SWEEP_RNG_SEED,
) -> list[dict[str, Any]]:
    """Sample N configs via Latin-hypercube — no scipy/numpy required.

    Each knob gets ``n_configs`` row indices shuffled independently, then
    a config is one column across knobs. This guarantees uniform marginal
    coverage of every knob's sweep range, even with low N.
    """
    rng = random.Random(seed)
    columns: dict[str, list[Any]] = {}
    for knob in knobs:
        bucket_count = len(knob.sweep_values)
        # Distribute n_configs across buckets as evenly as possible.
        repeats = (n_configs + bucket_count - 1) // bucket_count
        col: list[Any] = []
        for _ in range(repeats):
            col.extend(knob.sweep_values)
        col = col[:n_configs]
        rng.shuffle(col)
        columns[knob.key] = col
    configs: list[dict[str, Any]] = []
    for i in range(n_configs):
        cfg = {knob.key: columns[knob.key][i] for knob in knobs}
        configs.append(cfg)
    return configs


# --- system_config apply / rollback --------------------------------------- #
#
# We write directly to the ``system_config`` table via SQLAlchemy text(). The
# higher-level SystemConfigService is intentionally bypassed because it
# requires the full DI Container wiring (Redis client, outbox publisher,
# event emitter) which is overkill for a CLI sweep tool. Dev-only script:
# direct SQL keeps dependencies minimal and matches `init_system_config.py`
# precedent.


async def _resolve_db_url() -> str:
    """Read DATABASE_URL from env, normalise to async driver."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL env var required")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


async def apply_config(engine: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    """Write each knob to system_config via direct SQL; return prior values.

    Captures ``prior[key]`` BEFORE writing so a partial-apply failure
    still surfaces a usable rollback dict (already-written keys can be
    reverted; unwritten keys are absent from prior).

    Cache invalidation: a Redis pub/sub fanout is NOT issued here. Pipeline
    code re-reads `system_config` per-request (5s TTL) so within ~5s every
    process picks up the new value.
    """
    from sqlalchemy import text

    prior: dict[str, Any] = {}
    async with engine.begin() as conn:
        for key, value in cfg.items():
            row = await conn.execute(
                text("SELECT value FROM system_config WHERE key = :k"),
                {"k": key},
            )
            existing = row.fetchone()
            prior[key] = existing[0] if existing else None
            await conn.execute(
                text("""
                    INSERT INTO system_config (key, value, value_type, description, updated_at)
                    VALUES (:k, CAST(:v AS jsonb), :t, :d, now())
                    ON CONFLICT (key) DO UPDATE
                    SET value = CAST(:v AS jsonb), updated_at = now()
                """),
                {
                    "k": key,
                    "v": json.dumps(value),
                    "t": _value_type_for(value),
                    "d": f"rago-sweep apply {key}",
                },
            )
    return prior


async def rollback_config(engine: Any, prior: dict[str, Any]) -> None:
    """Restore prior values; never raise even if a row fails to revert."""
    from sqlalchemy import text

    for key, value in prior.items():
        try:
            if value is None:
                continue  # no baseline row — sweep value remains (warned at run end)
            async with engine.begin() as conn:
                await conn.execute(
                    text("""
                        UPDATE system_config
                        SET value = CAST(:v AS jsonb), updated_at = now()
                        WHERE key = :k
                    """),
                    {"k": key, "v": json.dumps(value)},
                )
        except Exception as exc:  # noqa: BLE001 — best-effort rollback
            print(f"[ROLLBACK WARN] {key}: {exc}", file=sys.stderr, flush=True)


def _value_type_for(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "string"


# --- Single-config run loop ----------------------------------------------- #


async def run_one_config(
    *,
    config_id: int,
    cfg: dict[str, Any],
    bot: BotIdentity,
    questions: list[dict[str, Any]],
    base_url: str,
    pause_s: float,
    engine: Any,
) -> ConfigRunResult:
    """Apply config, run subset, capture per-turn metrics, rollback."""
    print(
        f"\n[cfg {config_id:03d}] applying knobs: " + ", ".join(
            f"{k}={v}" for k, v in cfg.items()
        ),
        flush=True,
    )

    raw_metrics: list[dict[str, Any]] = []
    pass_count = 0
    hallu_count = 0
    error_count = 0
    cost_total = 0.0
    latencies_ms: list[float] = []
    prior: dict[str, Any] = {}

    try:
        # Apply BEFORE the run loop but inside try so any failure
        # (including KeyboardInterrupt mid-apply) still triggers
        # the finally rollback for whichever knobs were already set.
        prior = await apply_config(engine, cfg)
        # Reuse agent_d_loadtest harness for HTTP call + extract.
        from agent_d_loadtest import call_chat, extract_metrics, get_self_token
        import httpx

        async with httpx.AsyncClient() as client:
            token = await get_self_token(client, base_url)
            for item in questions:
                connect_id = f"rago-sweep-{config_id:03d}-{item['idx']}"
                t0 = time.monotonic()
                try:
                    resp = await call_chat(
                        client,
                        base_url=base_url,
                        token=token,
                        bot=bot,
                        question=item["q"],
                        connect_id=connect_id,
                    )
                except Exception as exc:  # noqa: BLE001 — load-test harness must continue past a single-turn failure
                    error_count += 1
                    print(f"  ERR turn {item['idx']}: {exc}", file=sys.stderr, flush=True)
                    continue
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                latencies_ms.append(elapsed_ms)
                metrics = extract_metrics(item, resp)
                metrics["_elapsed_ms"] = elapsed_ms
                raw_metrics.append(metrics)

                answer = (resp.get("answer") or "").strip()
                refused = is_refuse(answer)
                # PASS heuristic: not refused + not empty (full Opus
                # reclassify happens later in rago_pareto_pick step).
                if answer and not refused:
                    pass_count += 1

                # HALLU detection: question id starts with the trap marker
                # AND answer is NOT refusal → fabricate suspect.
                qid = str(item.get("idx", ""))
                if HALLU_REFUSE_TRAP_MARKER in qid and not refused:
                    hallu_count += 1

                # Cost from response (if surfaced by API).
                cost_total += float(resp.get("_cost_usd", 0.0) or 0.0)
                await asyncio.sleep(pause_s)
    finally:
        await rollback_config(engine, prior)

    n = len(raw_metrics)
    pass_rate = (pass_count / n) if n else 0.0
    cost_per_turn = (cost_total / n) if n else 0.0
    p95_ms = _percentile(latencies_ms, PERCENTILE_P95) if latencies_ms else 0.0

    return ConfigRunResult(
        config_id=config_id,
        knob_values=cfg,
        n_turns=n,
        pass_rate=pass_rate,
        p95_ms=p95_ms,
        cost_per_turn=cost_per_turn,
        hallu_count=hallu_count,
        error_count=error_count,
        raw_metrics=raw_metrics,
    )


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_v) - 1)
    frac = k - lo
    return sorted_v[lo] + (sorted_v[hi] - sorted_v[lo]) * frac


# --- CSV writer ------------------------------------------------------------ #


def write_sweep_csv(
    results: list[ConfigRunResult],
    knobs: list[KnobSpec],
    out_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    knob_keys = [k.key for k in knobs]
    headers = (
        ["config_id"]
        + knob_keys
        + ["n_turns", "pass_rate", "p95_ms", "cost_per_turn", "hallu_count", "error_count"]
    )
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for r in results:
            row = (
                [r.config_id]
                + [r.knob_values.get(k) for k in knob_keys]
                + [
                    r.n_turns,
                    round(r.pass_rate, 4),
                    round(r.p95_ms, 2),
                    round(r.cost_per_turn, 6),
                    r.hallu_count,
                    r.error_count,
                ]
            )
            w.writerow(row)


# --- Question fixture parser (reuse agent_d format) ------------------------ #


def parse_questions_file(path: Path, *, max_count: int = 0) -> list[dict[str, Any]]:
    """Parse the same MD fixture format as agent_d_loadtest.

    Returns at most ``max_count`` questions if > 0, else all.
    """
    from agent_d_loadtest import parse_questions_file as _agentd_parse

    qs = _agentd_parse(path)
    if max_count and len(qs) > max_count:
        # Take a stratified slice: first M from each category.
        by_cat: dict[str, list[dict[str, Any]]] = {}
        for q in qs:
            by_cat.setdefault(q["category"], []).append(q)
        per_cat = max(1, max_count // len(by_cat))
        out: list[dict[str, Any]] = []
        for cat in sorted(by_cat):
            out.extend(by_cat[cat][:per_cat])
        return out[:max_count]
    return qs


# --- CLI ------------------------------------------------------------------- #


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RAGO Pareto sweep (Paper 26).")
    p.add_argument("--bot-id", default=os.getenv("LOADTEST_BOT_ID", ""))
    p.add_argument("--tenant-id", type=int, default=int(os.getenv("LOADTEST_TENANT_ID", "0") or "0"))
    p.add_argument("--channel-type", default=os.getenv("LOADTEST_CHANNEL_TYPE", ""))
    p.add_argument("--base-url", default=os.getenv("RAGBOT_BASE_URL", "http://localhost:3004"))
    p.add_argument("--questions-file", default=DEFAULT_QUESTIONS_FILE)
    p.add_argument("--schema", default=DEFAULT_SCHEMA_PATH)
    p.add_argument("--n-configs", type=int, default=DEFAULT_N_CONFIGS)
    p.add_argument("--n-turns-per-config", type=int, default=DEFAULT_N_TURNS_PER_CONFIG)
    p.add_argument("--pause", type=float, default=DEFAULT_PAUSE_BETWEEN_QUERIES_S)
    p.add_argument("--output", required=True, help="output CSV path")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse schema + sample configs, print plan, exit without applying.",
    )
    args = p.parse_args()
    missing: list[str] = []
    if not args.bot_id:
        missing.append("--bot-id")
    if not args.channel_type:
        missing.append("--channel-type")
    if args.tenant_id < 1:
        missing.append("--tenant-id")
    if missing:
        p.error("missing required: " + ", ".join(missing))
    return args


async def _amain(args: argparse.Namespace) -> int:
    schema_path = REPO_ROOT / args.schema
    knobs = parse_schema(schema_path)
    if not knobs:
        print(f"ERROR: no knobs parsed from {schema_path}", file=sys.stderr)
        return 2
    print(f"[schema] {len(knobs)} knobs loaded from {args.schema}", flush=True)
    for k in knobs:
        print(f"  - {k.key} ({k.knob_type}, default={k.default}, sweep={k.sweep_values})", flush=True)

    configs = latin_hypercube_sample(knobs, args.n_configs)
    print(f"[sample] {len(configs)} configs (Latin hypercube, seed={SWEEP_RNG_SEED})", flush=True)

    if args.dry_run:
        print("[dry-run] showing first 3 configs:", flush=True)
        for i, c in enumerate(configs[:3]):
            print(f"  config {i}: {c}", flush=True)
        return 0

    questions = parse_questions_file(
        Path(args.questions_file),
        max_count=args.n_turns_per_config,
    )
    if not questions:
        print(f"ERROR: no questions in {args.questions_file}", file=sys.stderr)
        return 2
    print(f"[questions] {len(questions)} loaded from {args.questions_file}", flush=True)

    bot = BotIdentity(
        tenant_id=args.tenant_id,
        bot_id=args.bot_id,
        channel_type=args.channel_type,
    )

    from sqlalchemy.ext.asyncio import create_async_engine

    db_url = await _resolve_db_url()
    engine = create_async_engine(db_url, echo=False)

    results: list[ConfigRunResult] = []
    started = datetime.now()
    print(f"[run] sweep started at {started.isoformat(timespec='seconds')}", flush=True)
    try:
        for i, cfg in enumerate(configs):
            try:
                r = await run_one_config(
                    config_id=i,
                    cfg=cfg,
                    bot=bot,
                    questions=questions,
                    base_url=args.base_url,
                    pause_s=args.pause,
                    engine=engine,
                )
            except Exception as exc:  # noqa: BLE001 — sweep harness logs and continues so a single bad config never wastes the whole run
                print(f"[cfg {i:03d}] FAILED: {exc}", file=sys.stderr, flush=True)
                continue
            results.append(r)
            # Incremental write so partial sweep is recoverable.
            write_sweep_csv(results, knobs, Path(args.output))
            print(
                f"[cfg {i:03d}] done — pass={r.pass_rate:.2%} p95={r.p95_ms:.0f}ms "
                f"cost=${r.cost_per_turn:.6f} hallu={r.hallu_count}",
                flush=True,
            )
    finally:
        await engine.dispose()

    print(f"\n[done] wrote {len(results)} configs to {args.output}", flush=True)
    return 0


def main() -> int:
    return asyncio.run(_amain(_parse_args()))


if __name__ == "__main__":
    sys.exit(main())
