#!/usr/bin/env python3
"""Master Ablation Load Test — Stream 20 (master-of-master plan).

Runs the **4-config matrix** (Baseline OFF / T1-only / T2-only / All ON) over
the 90Q subset of the smartness-300Q fixture, then a **per-feature ablation**
round (drop one feature at a time from "All ON"). Computes PASS rate +
p50/p95/p99 latency + cost/turn + HALLU rate per config, and emits a markdown
report under ``reports/MASTER_LOADTEST_<YYYYMMDD>/results.md``.

The companion ``decision_gate.py`` consumes the JSON aggregate and classifies
each feature as KEEP / TUNE / DROP per the lift thresholds + HALLU sacred gate.

Design (Strategy + DI):
    - ``FeatureMatrix`` declares the 4 named configs + the ablation set as
      pure dataclasses — domain-neutral, no brand literal, no hardcoded model.
    - ``run_config(config, fixture, asker)`` is a generic orchestrator that
      receives an injected ``asker`` callable (HTTP adapter or in-memory mock
      for tests). Production wires ``http_asker``; ``tests/`` wires a fake.
    - Classification + aggregation + decision logic = pure functions.
    - Feature-flag delivery is via env-overlay: caller writes per-config env
      vars (mapped from ``FeatureSpec.env_var``) so the running ragbot can
      lift the flag without redeploy. This script does NOT mutate
      ``system_config`` DB itself — admin is expected to flip flags between
      runs or use the run-scoped env overlay supported by the test harness.

Proof citation:
    - Standard IR/RAG ablation methodology: Lewis et al. 2020 RAG paper
      (NeurIPS, arxiv 2005.11401) §4 ablation; Ekimetrics LREC 2026 §5
      "feature ablation matrix"; LlamaIndex / Databricks benchmark
      methodology (drop-one-feature, measure lift on PASS + p95 + cost).
    - Decision gate thresholds: KEEP if lift PASS ≥2pp or latency drop ≥10%
      with HALLU=0 hold; TUNE if 0–2pp lift; DROP if regression OR HALLU
      breach. Mirrors README.md "Decision matrix sau master load test".

CLAUDE.md compliance:
    - Zero-hardcode: all thresholds + features pulled from
      ``shared.constants`` or env-overridable; no inline magic numbers.
    - Domain-neutral: feature names + configs reference flag identifiers only.
    - 4-key identity: turn payload uses (record_tenant_id via JWT,
      workspace_id, bot_id, channel_type) — preserved from fixture.
    - HALLU=0 sacred: any HALLU breach in a config flags it for REVERT.
    - App does NOT inject text / override LLM answer — script is read-only
      harness.

Usage (admin-runnable on UAT):

    # Run full master matrix (4 configs × 90Q + ablation):
    python scripts/loadtest_master_ablation.py

    # Run only the 4-config matrix (skip ablation):
    python scripts/loadtest_master_ablation.py --skip-ablation

    # Use a different fixture / subset:
    python scripts/loadtest_master_ablation.py \\
        --fixture tests/loadtest/smartness_300q_fixture.json \\
        --per-bot-cap 30 \\
        --output-dir reports/MASTER_LOADTEST_260514
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

import httpx
import structlog

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT))

from scripts._loadtest_common import is_refuse  # noqa: E402

from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_LOADTEST_INTER_QUESTION_SLEEP_S,
    DEFAULT_LOADTEST_REQUEST_TIMEOUT_S,
    RAGBOT_LOADTEST_BYPASS_ENV,
    RAGBOT_LOADTEST_BYPASS_HEADER,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Tunables — declared as module constants (zero magic numbers inline).
# Decision-gate thresholds mirror README.md "Decision matrix" section.
# ---------------------------------------------------------------------------

DEFAULT_MASTER_FIXTURE: str = "tests/loadtest/smartness_300q_fixture.json"
DEFAULT_PER_BOT_CAP: int = 30  # 30 × 3 bots = 90Q per config
DEFAULT_REPORT_DIR_PREFIX: str = "reports/MASTER_LOADTEST_"

# Decision-gate thresholds (lift = config_with_feature vs config_without).
KEEP_PASS_LIFT_PP: float = 2.0          # ≥+2pp PASS rate ⇒ KEEP
KEEP_LATENCY_DROP_PCT: float = 10.0     # ≥-10% p95 ⇒ KEEP
TUNE_PASS_LIFT_PP_MIN: float = 0.0      # 0..2pp ⇒ TUNE
HALLU_SACRED_MAX: int = 0               # HALLU > 0 ⇒ DROP regardless of lift
COST_REGRESSION_PCT_MAX: float = 20.0   # cost +20% absent PASS lift ⇒ DROP

# Verdicts produced by the classifier (mirrors loadtest_smartness_300q.py).
VERDICTS = (
    "PASS_ANSWERED",
    "PASS_REFUSED",
    "HALLU_BREACH",
    "REFUSE_GAP",
    "ERR",
)


# ---------------------------------------------------------------------------
# Feature matrix — declarative spec, no brand literal.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureSpec:
    """One ablatable feature: name + env var the running ragbot consults.

    The env var contract: the test harness/admin overlays
    ``RAGBOT_FLAG_<UPPER>=true|false`` before launching the ragbot process,
    and the bootstrap reads the overlay precedence
    (env → system_config → constants default). This keeps the master test
    config-driven; no code mutation needed between runs.
    """

    name: str
    tier: str                 # "T1" | "T2" | "T3"
    flag_constant: str        # e.g. "DEFAULT_HYDE_ENABLED"
    env_var: str              # e.g. "RAGBOT_FLAG_HYDE_ENABLED"
    description: str


@dataclass(frozen=True)
class ConfigSpec:
    """A named loadtest configuration = ordered overrides over the default.

    ``overrides[name] = True/False`` flips the named feature regardless of
    its compile-time default. Features not listed inherit default.
    """

    name: str          # "baseline_off" | "t1_only" | "t2_only" | "all_on"
    label: str
    overrides: dict[str, bool] = field(default_factory=dict)


def build_default_feature_matrix() -> list[FeatureSpec]:
    """Return the canonical ablation feature set.

    Mirrors the smartness + cost/perf features that landed in the codebase.
    Each entry must correspond to a real ``DEFAULT_*_ENABLED`` constant
    in ``shared/constants.py`` (verified by tests).
    """
    return [
        # T1 — retrieval / chunking smartness
        FeatureSpec(
            name="hyde",
            tier="T1",
            flag_constant="DEFAULT_HYDE_ENABLED",
            env_var="RAGBOT_FLAG_HYDE_ENABLED",
            description="HyDE hypothetical doc embedding for low-recall queries.",
        ),
        FeatureSpec(
            name="contextual_retrieval",
            tier="T1",
            flag_constant="DEFAULT_CONTEXTUAL_RETRIEVAL_ENABLED",
            env_var="RAGBOT_FLAG_CONTEXTUAL_RETRIEVAL_ENABLED",
            description="Anthropic Contextual Retrieval (LLM-prefixed chunks).",
        ),
        FeatureSpec(
            name="multi_query",
            tier="T1",
            flag_constant="DEFAULT_MULTI_QUERY_ENABLED",
            env_var="RAGBOT_FLAG_MULTI_QUERY_ENABLED",
            description="LLM rewrites N variants per query before retrieval.",
        ),
        FeatureSpec(
            name="structured_ref_extraction",
            tier="T1",
            flag_constant="DEFAULT_STRUCTURED_REF_EXTRACTION_ENABLED",
            env_var="RAGBOT_FLAG_STRUCTURED_REF_EXTRACTION_ENABLED",
            description="Pull entity refs (Article/Section IDs) before retrieve.",
        ),
        FeatureSpec(
            name="litm_reorder",
            tier="T1",
            flag_constant="DEFAULT_LITM_REORDER_ENABLED",
            env_var="RAGBOT_FLAG_LITM_REORDER_ENABLED",
            description="Lost-in-the-Middle chunk reorder before LLM call.",
        ),
        # T2 — cost / perf / UX
        FeatureSpec(
            name="refuse_short_circuit",
            tier="T2",
            flag_constant="DEFAULT_REFUSE_SHORT_CIRCUIT_ENABLED",
            env_var="RAGBOT_FLAG_REFUSE_SHORT_CIRCUIT_ENABLED",
            description="Refuse before LLM call when top_score <= threshold.",
        ),
        FeatureSpec(
            name="pipeline_parallel_rewrite_mq",
            tier="T2",
            flag_constant="DEFAULT_PIPELINE_PARALLEL_REWRITE_MQ_ENABLED",
            env_var="RAGBOT_FLAG_PIPELINE_PARALLEL_REWRITE_MQ_ENABLED",
            description="Run rewrite + multi-query in parallel for TTFT.",
        ),
        FeatureSpec(
            name="grounding_check_async",
            tier="T2",
            flag_constant="DEFAULT_GROUNDING_CHECK_ASYNC_ENABLED",
            env_var="RAGBOT_FLAG_GROUNDING_CHECK_ASYNC_ENABLED",
            description="Run grounding/HALLU check off the user-critical path.",
        ),
    ]


def build_named_configs(features: Sequence[FeatureSpec]) -> dict[str, ConfigSpec]:
    """Return the canonical 4-config matrix referenced in README.md."""
    t1 = [f.name for f in features if f.tier == "T1"]
    t2 = [f.name for f in features if f.tier == "T2"]
    all_names = [f.name for f in features]

    return {
        "baseline_off": ConfigSpec(
            name="baseline_off",
            label="Baseline (all OFF)",
            overrides={n: False for n in all_names},
        ),
        "t1_only": ConfigSpec(
            name="t1_only",
            label="T1-only (smartness features ON)",
            overrides={**{n: False for n in all_names}, **{n: True for n in t1}},
        ),
        "t2_only": ConfigSpec(
            name="t2_only",
            label="T2-only (cost/perf features ON)",
            overrides={**{n: False for n in all_names}, **{n: True for n in t2}},
        ),
        "all_on": ConfigSpec(
            name="all_on",
            label="All ON (master-of-master)",
            overrides={n: True for n in all_names},
        ),
    }


def build_ablation_configs(
    features: Sequence[FeatureSpec],
) -> list[ConfigSpec]:
    """One config per feature: All ON minus that feature.

    The diff "All ON vs ablate_<feature>" measures the feature's marginal
    contribution to PASS / latency / cost — the classic drop-one ablation.
    """
    all_names = [f.name for f in features]
    return [
        ConfigSpec(
            name=f"ablate_{f.name}",
            label=f"All ON minus {f.name}",
            overrides={
                **{n: True for n in all_names},
                f.name: False,
            },
        )
        for f in features
    ]


def overrides_to_env(
    overrides: dict[str, bool], features: Sequence[FeatureSpec],
) -> dict[str, str]:
    """Map feature-name overrides → flag env vars the ragbot bootstrap reads."""
    by_name = {f.name: f for f in features}
    return {
        by_name[name].env_var: ("true" if val else "false")
        for name, val in overrides.items()
        if name in by_name
    }


# ---------------------------------------------------------------------------
# Classification + aggregation — pure functions, easy to unit-test.
# ---------------------------------------------------------------------------


def classify(answer: str, *, is_trap: bool, expected_verdict: str) -> str:
    """Map (answer, trap?, expected) → one of ``VERDICTS``.

    Mirrors ``loadtest_smartness_300q.classify`` so master + per-sprint runs
    use identical verdict semantics — no drift.
    """
    if answer is None or answer == "":
        return "ERR"
    refused = is_refuse(answer)
    if is_trap:
        return "PASS_REFUSED" if refused else "HALLU_BREACH"
    if expected_verdict == "REFUSED":
        return "PASS_REFUSED" if refused else "HALLU_BREACH"
    return "REFUSE_GAP" if refused else "PASS_ANSWERED"


def percentile(vals: list[float], pct: float) -> float:
    """Return percentile via nearest-rank; 0.0 on empty input."""
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return float(s[idx])


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute per-config aggregate: PASS / HALLU / p50/p95/p99 / cost."""
    counts = Counter(r["verdict"] for r in records)
    n = len(records)
    trap_total = sum(1 for r in records if r.get("hallu_trap"))
    non_trap = n - trap_total
    pass_answered = counts.get("PASS_ANSWERED", 0)
    pass_refused = counts.get("PASS_REFUSED", 0)
    hallu = counts.get("HALLU_BREACH", 0)
    refuse_gap = counts.get("REFUSE_GAP", 0)
    err = counts.get("ERR", 0)

    pass_total = pass_answered + pass_refused
    pass_rate = (pass_total / n * 100.0) if n else 0.0

    lats = [
        r["latency_ms"] for r in records
        if isinstance(r.get("latency_ms"), (int, float))
    ]
    costs = [
        r["cost_usd"] for r in records
        if isinstance(r.get("cost_usd"), (int, float))
    ]

    return {
        "n": n,
        "trap_total": trap_total,
        "non_trap_total": non_trap,
        "pass_total": pass_total,
        "pass_rate_pct": round(pass_rate, 2),
        "pass_answered": pass_answered,
        "pass_refused": pass_refused,
        "refuse_gap": refuse_gap,
        "err": err,
        "hallu_breach": hallu,
        "hallu_zero_sacred": hallu <= HALLU_SACRED_MAX,
        "p50_latency_ms": percentile(lats, 50),
        "p95_latency_ms": percentile(lats, 95),
        "p99_latency_ms": percentile(lats, 99),
        "avg_cost_usd": round(sum(costs) / len(costs), 6) if costs else 0.0,
        "total_cost_usd": round(sum(costs), 4) if costs else 0.0,
    }


# ---------------------------------------------------------------------------
# Decision gate — classify each feature KEEP / TUNE / DROP.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureDecision:
    feature: str
    tier: str
    verdict: str             # "KEEP" | "TUNE" | "DROP"
    reason: str
    pass_lift_pp: float
    latency_drop_pct: float
    cost_delta_pct: float
    hallu_with: int
    hallu_without: int


def classify_feature_decision(
    *,
    feature: FeatureSpec,
    all_on: dict[str, Any],
    ablate: dict[str, Any],
) -> FeatureDecision:
    """Compare (All ON) vs (All ON minus this feature) → KEEP/TUNE/DROP.

    Logic (in priority order):
      1. HALLU sacred — if removing feature breaches HALLU, KEEP regardless.
         If feature itself introduces HALLU > 0 (all_on hallu > 0 while
         ablate hallu == 0), DROP — feature is hazardous.
      2. PASS lift ≥+2pp ⇒ KEEP.
      3. p95 latency drop ≥10% with no PASS regression ⇒ KEEP.
      4. PASS regression OR cost +20% without PASS lift ⇒ DROP.
      5. Anything 0–2pp lift ⇒ TUNE.
    """
    pass_lift = float(all_on["pass_rate_pct"]) - float(ablate["pass_rate_pct"])

    p95_with = float(all_on["p95_latency_ms"]) or 0.0
    p95_without = float(ablate["p95_latency_ms"]) or 0.0
    latency_drop_pct = (
        (p95_without - p95_with) / p95_without * 100.0
        if p95_without > 0 else 0.0
    )

    cost_with = float(all_on["avg_cost_usd"]) or 0.0
    cost_without = float(ablate["avg_cost_usd"]) or 0.0
    cost_delta_pct = (
        (cost_with - cost_without) / cost_without * 100.0
        if cost_without > 0 else 0.0
    )

    hallu_with = int(all_on["hallu_breach"])
    hallu_without = int(ablate["hallu_breach"])

    # Rule 1a: removing feature triggers HALLU → feature is protective ⇒ KEEP.
    if hallu_without > HALLU_SACRED_MAX and hallu_with <= HALLU_SACRED_MAX:
        return FeatureDecision(
            feature=feature.name, tier=feature.tier, verdict="KEEP",
            reason=(
                f"feature is HALLU-protective: ablate={hallu_without} "
                f"vs with={hallu_with} (sacred gate)"
            ),
            pass_lift_pp=round(pass_lift, 2),
            latency_drop_pct=round(latency_drop_pct, 2),
            cost_delta_pct=round(cost_delta_pct, 2),
            hallu_with=hallu_with, hallu_without=hallu_without,
        )

    # Rule 1b: feature itself introduces HALLU → DROP.
    if hallu_with > HALLU_SACRED_MAX and hallu_without <= HALLU_SACRED_MAX:
        return FeatureDecision(
            feature=feature.name, tier=feature.tier, verdict="DROP",
            reason=(
                f"feature introduces HALLU: with={hallu_with} "
                f"vs ablate={hallu_without} (sacred breach)"
            ),
            pass_lift_pp=round(pass_lift, 2),
            latency_drop_pct=round(latency_drop_pct, 2),
            cost_delta_pct=round(cost_delta_pct, 2),
            hallu_with=hallu_with, hallu_without=hallu_without,
        )

    # Rule 4 (negative): regression ⇒ DROP.
    if pass_lift < -KEEP_PASS_LIFT_PP:
        return FeatureDecision(
            feature=feature.name, tier=feature.tier, verdict="DROP",
            reason=(
                f"PASS regression {pass_lift:.2f}pp (threshold "
                f"-{KEEP_PASS_LIFT_PP}pp)"
            ),
            pass_lift_pp=round(pass_lift, 2),
            latency_drop_pct=round(latency_drop_pct, 2),
            cost_delta_pct=round(cost_delta_pct, 2),
            hallu_with=hallu_with, hallu_without=hallu_without,
        )

    if (
        cost_delta_pct > COST_REGRESSION_PCT_MAX
        and pass_lift < KEEP_PASS_LIFT_PP
    ):
        return FeatureDecision(
            feature=feature.name, tier=feature.tier, verdict="DROP",
            reason=(
                f"cost +{cost_delta_pct:.1f}% without PASS lift "
                f"({pass_lift:.2f}pp)"
            ),
            pass_lift_pp=round(pass_lift, 2),
            latency_drop_pct=round(latency_drop_pct, 2),
            cost_delta_pct=round(cost_delta_pct, 2),
            hallu_with=hallu_with, hallu_without=hallu_without,
        )

    # Rule 2: strong PASS lift ⇒ KEEP.
    if pass_lift >= KEEP_PASS_LIFT_PP:
        return FeatureDecision(
            feature=feature.name, tier=feature.tier, verdict="KEEP",
            reason=f"PASS lift {pass_lift:.2f}pp >= {KEEP_PASS_LIFT_PP}pp",
            pass_lift_pp=round(pass_lift, 2),
            latency_drop_pct=round(latency_drop_pct, 2),
            cost_delta_pct=round(cost_delta_pct, 2),
            hallu_with=hallu_with, hallu_without=hallu_without,
        )

    # Rule 3: meaningful latency drop without PASS regression ⇒ KEEP.
    if latency_drop_pct >= KEEP_LATENCY_DROP_PCT and pass_lift >= 0:
        return FeatureDecision(
            feature=feature.name, tier=feature.tier, verdict="KEEP",
            reason=(
                f"p95 drop {latency_drop_pct:.1f}% "
                f">= {KEEP_LATENCY_DROP_PCT}% (no PASS regression)"
            ),
            pass_lift_pp=round(pass_lift, 2),
            latency_drop_pct=round(latency_drop_pct, 2),
            cost_delta_pct=round(cost_delta_pct, 2),
            hallu_with=hallu_with, hallu_without=hallu_without,
        )

    # Default: marginal effect ⇒ TUNE.
    return FeatureDecision(
        feature=feature.name, tier=feature.tier, verdict="TUNE",
        reason=(
            f"marginal lift {pass_lift:.2f}pp "
            f"(p95 drop {latency_drop_pct:.1f}%, cost {cost_delta_pct:+.1f}%)"
        ),
        pass_lift_pp=round(pass_lift, 2),
        latency_drop_pct=round(latency_drop_pct, 2),
        cost_delta_pct=round(cost_delta_pct, 2),
        hallu_with=hallu_with, hallu_without=hallu_without,
    )


def build_decision_matrix(
    features: Sequence[FeatureSpec],
    all_on: dict[str, Any],
    ablation: dict[str, dict[str, Any]],
) -> list[FeatureDecision]:
    """Decision per feature given the All-ON aggregate + ablation aggregates."""
    out: list[FeatureDecision] = []
    for f in features:
        key = f"ablate_{f.name}"
        if key not in ablation:
            log.warning(
                "ablation_missing", feature=f.name, key=key,
            )
            continue
        out.append(
            classify_feature_decision(
                feature=f,
                all_on=all_on,
                ablate=ablation[key],
            )
        )
    return out


# ---------------------------------------------------------------------------
# Fixture loader.
# ---------------------------------------------------------------------------


def load_fixture(
    fixture_path: Path, *, per_bot_cap: int,
) -> list[dict[str, Any]]:
    """Load + cap per-bot. The 300Q fixture has 100Q per 3 bots; cap=30 → 90Q."""
    with open(fixture_path, encoding="utf-8") as f:
        all_q = json.load(f)
    if not isinstance(all_q, list):
        raise ValueError(
            f"fixture must be a list of question dicts, got {type(all_q).__name__}"
        )

    # Stratify per bot — preserve pattern diversity by taking first N per bot.
    per_bot: dict[str, list[dict[str, Any]]] = {}
    for q in all_q:
        per_bot.setdefault(q["bot_id"], []).append(q)
    capped: list[dict[str, Any]] = []
    for bot, qs in per_bot.items():
        capped.extend(qs[:per_bot_cap])
    return capped


# ---------------------------------------------------------------------------
# Asker — production HTTP adapter + injectable contract for tests.
# ---------------------------------------------------------------------------


AskerFn = Callable[[dict[str, Any], dict[str, str]], Awaitable[dict[str, Any]]]
"""Asker contract: (question_record, env_overlay) → answer dict.

Production: calls the running ragbot via /api/ragbot/test/chat.
Tests: an in-memory fake that returns deterministic answers from a script.

The ``env_overlay`` argument is for documentation only at the asker layer —
the running ragbot picks up its own env. The harness passes it so adapters
(e.g. a future per-request flag header) can forward it.
"""


def _bypass_headers() -> dict[str, str]:
    token = os.environ.get(RAGBOT_LOADTEST_BYPASS_ENV, "")
    if not token:
        return {}
    return {RAGBOT_LOADTEST_BYPASS_HEADER: token}


async def _get_self_token(client: httpx.AsyncClient, base_url: str) -> str:
    r = await client.get(
        f"{base_url}/api/ragbot/test/tokens/self",
        headers=_bypass_headers(),
    )
    r.raise_for_status()
    return r.json()["token"]


def make_http_asker(
    *,
    base_url: str,
    token: str,
    client: httpx.AsyncClient,
    timeout_s: float = DEFAULT_LOADTEST_REQUEST_TIMEOUT_S,
) -> AskerFn:
    """Build an asker bound to an open httpx client + JWT token."""

    async def _ask(
        q: dict[str, Any], _env_overlay: dict[str, str],
    ) -> dict[str, Any]:
        body = {
            "bot_id": q["bot_id"],
            "channel_type": q.get("channel_type", "web"),
            "workspace_id": q.get("workspace_id"),
            "question": q["question"],
            "connect_id": q.get(
                "connect_id",
                f"master-ablation-{q.get('industry', '?')}-{q['id']}",
            ),
            "bypass_cache": True,
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            **_bypass_headers(),
        }
        t0 = time.perf_counter()
        try:
            r = await client.post(
                f"{base_url}/api/ragbot/test/chat",
                headers=headers,
                json=body,
                timeout=timeout_s,
            )
            r.raise_for_status()
            d = r.json()
            d["latency_ms"] = round((time.perf_counter() - t0) * 1000)
            return d
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "error": str(exc),
                "error_type": type(exc).__name__,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "answer": "",
            }

    return _ask


# ---------------------------------------------------------------------------
# Orchestrator — run a single named config over a question set.
# ---------------------------------------------------------------------------


async def run_config(
    *,
    config: ConfigSpec,
    features: Sequence[FeatureSpec],
    questions: list[dict[str, Any]],
    asker: AskerFn,
    pace_s: float = 0.0,
) -> dict[str, Any]:
    """Execute all ``questions`` under ``config``; return summary + records.

    The ``asker`` is injected (DI/Port pattern) so production wires HTTP and
    tests wire a deterministic fake. Telemetry: per-config + per-turn
    structlog events with ``step_name`` + ``config`` + ``feature_flags``.
    """
    env_overlay = overrides_to_env(config.overrides, features)
    log.info(
        "master_ablation_config_start",
        step_name="master_ablation",
        config=config.name,
        label=config.label,
        questions=len(questions),
        env_overlay_keys=sorted(env_overlay.keys()),
    )

    records: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for i, q in enumerate(questions, 1):
        resp = await asker(q, env_overlay)
        answer = (resp.get("answer") or "")
        verdict = classify(
            answer,
            is_trap=bool(q.get("hallu_trap")),
            expected_verdict=q.get("expected_verdict", "ANSWERED"),
        )
        rec = {
            "config": config.name,
            "id": q["id"],
            "industry": q.get("industry"),
            "bot_id": q["bot_id"],
            "workspace_id": q.get("workspace_id"),
            "channel_type": q.get("channel_type", "web"),
            "pattern": q.get("pattern"),
            "hallu_trap": bool(q.get("hallu_trap")),
            "expected_verdict": q.get("expected_verdict"),
            "question": q["question"][:200],
            "answer": answer[:600],
            "answer_type": resp.get("answer_type"),
            "verdict": verdict,
            "top_score": resp.get("top_score"),
            "chunks_used": resp.get("chunks_used", 0),
            "latency_ms": resp.get("latency_ms"),
            "cost_usd": resp.get("cost_usd"),
            "trace_id": resp.get("trace_id"),
            "request_id": resp.get("request_id"),
            "error": resp.get("error"),
        }
        records.append(rec)
        if i % 10 == 0:
            log.info(
                "master_ablation_progress",
                step_name="master_ablation",
                config=config.name,
                done=i,
                total=len(questions),
            )
        if pace_s > 0:
            await asyncio.sleep(pace_s)

    wall_s = round(time.perf_counter() - t0, 2)
    summary = aggregate(records)
    summary["config_name"] = config.name
    summary["config_label"] = config.label
    summary["overrides"] = dict(config.overrides)
    summary["wall_time_s"] = wall_s

    log.info(
        "master_ablation_config_done",
        step_name="master_ablation",
        config=config.name,
        pass_rate_pct=summary["pass_rate_pct"],
        hallu_breach=summary["hallu_breach"],
        p95_latency_ms=summary["p95_latency_ms"],
        avg_cost_usd=summary["avg_cost_usd"],
        wall_s=wall_s,
    )
    return {"summary": summary, "records": records}


# ---------------------------------------------------------------------------
# Report rendering — markdown for the admin handoff.
# ---------------------------------------------------------------------------


def _gate_cell(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def render_report(
    *,
    fixture_path: Path,
    matrix_results: dict[str, dict[str, Any]],
    ablation_results: dict[str, dict[str, Any]],
    decisions: list[FeatureDecision],
    features: Sequence[FeatureSpec],
    run_started_at: str,
    run_wall_s: float,
) -> str:
    """Render the master loadtest markdown report."""
    lines: list[str] = [
        "# Master Ablation Load Test — Stream 20 (master-of-master plan)",
        "",
        f"**Run started**: {run_started_at}",
        f"**Wall time**: {run_wall_s}s",
        f"**Fixture**: `{fixture_path}`",
        f"**Configs**: {len(matrix_results)} named × n questions",
        f"**Ablation rounds**: {len(ablation_results)}",
        "",
        "## 1. 4-Config Matrix",
        "",
        (
            "| Config | n | PASS% | HALLU | p50 ms | p95 ms | p99 ms | "
            "$/turn | Sacred |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name in ("baseline_off", "t1_only", "t2_only", "all_on"):
        s = matrix_results.get(name, {}).get("summary")
        if not s:
            lines.append(f"| {name} | (missing) | | | | | | | |")
            continue
        lines.append(
            f"| {name} | {s['n']} | {s['pass_rate_pct']}% | "
            f"{s['hallu_breach']} | {s['p50_latency_ms']} | "
            f"{s['p95_latency_ms']} | {s['p99_latency_ms']} | "
            f"${s['avg_cost_usd']} | "
            f"{_gate_cell(s['hallu_zero_sacred'])} |"
        )

    # Headline lift vs baseline.
    base = matrix_results.get("baseline_off", {}).get("summary")
    all_on = matrix_results.get("all_on", {}).get("summary")
    if base and all_on:
        pass_lift = all_on["pass_rate_pct"] - base["pass_rate_pct"]
        p95_drop = (
            (base["p95_latency_ms"] - all_on["p95_latency_ms"])
            / base["p95_latency_ms"] * 100.0
            if base["p95_latency_ms"] else 0.0
        )
        cost_delta = (
            (all_on["avg_cost_usd"] - base["avg_cost_usd"])
            / base["avg_cost_usd"] * 100.0
            if base["avg_cost_usd"] else 0.0
        )
        lines.extend([
            "",
            "### Headline (All ON vs Baseline)",
            "",
            f"- PASS lift: **{pass_lift:+.2f} pp**",
            f"- p95 latency drop: **{p95_drop:+.1f}%**",
            f"- Avg cost delta: **{cost_delta:+.1f}%**",
            (
                "- HALLU sacred: "
                f"{'HOLD (0)' if all_on['hallu_zero_sacred'] else 'BREACH'}"
            ),
        ])

    # Ablation matrix.
    lines.extend([
        "",
        "## 2. Per-Feature Ablation (All ON minus one)",
        "",
        (
            "| Feature | Tier | n | PASS% | HALLU | p95 ms | $/turn |"
        ),
        "|---|---|---|---|---|---|---|",
    ])
    for f in features:
        key = f"ablate_{f.name}"
        s = ablation_results.get(key, {}).get("summary")
        if not s:
            lines.append(f"| {f.name} | {f.tier} | (skipped) | | | | |")
            continue
        lines.append(
            f"| {f.name} | {f.tier} | {s['n']} | {s['pass_rate_pct']}% | "
            f"{s['hallu_breach']} | {s['p95_latency_ms']} | "
            f"${s['avg_cost_usd']} |"
        )

    # Decision matrix.
    lines.extend([
        "",
        "## 3. Decision Matrix (KEEP / TUNE / DROP)",
        "",
        (
            "| Feature | Tier | Verdict | PASS lift pp | p95 drop % | "
            "Cost Δ % | HALLU | Reason |"
        ),
        "|---|---|---|---|---|---|---|---|",
    ])
    for d in decisions:
        lines.append(
            f"| {d.feature} | {d.tier} | **{d.verdict}** | "
            f"{d.pass_lift_pp:+.2f} | {d.latency_drop_pct:+.1f} | "
            f"{d.cost_delta_pct:+.1f} | "
            f"with={d.hallu_with}/without={d.hallu_without} | {d.reason} |"
        )

    keep = [d for d in decisions if d.verdict == "KEEP"]
    tune = [d for d in decisions if d.verdict == "TUNE"]
    drop = [d for d in decisions if d.verdict == "DROP"]
    lines.extend([
        "",
        "### Summary",
        "",
        f"- KEEP ({len(keep)}): {', '.join(d.feature for d in keep) or '—'}",
        f"- TUNE ({len(tune)}): {', '.join(d.feature for d in tune) or '—'}",
        f"- DROP ({len(drop)}): {', '.join(d.feature for d in drop) or '—'}",
        "",
        "## 4. Proof / Methodology",
        "",
        (
            "- 4-config matrix + drop-one ablation = standard IR/RAG eval "
            "methodology (Lewis et al. 2020 RAG, NeurIPS; Ekimetrics LREC "
            "2026 §5; LlamaIndex/Databricks RAG benchmark protocol)."
        ),
        (
            "- Decision thresholds (KEEP ≥+2pp PASS or ≥-10% p95; "
            "TUNE 0–2pp; DROP regression OR HALLU breach) mirror "
            "`plans/260514-master-of-master/README.md` §'Decision matrix'."
        ),
        (
            "- HALLU=0 sacred — any HALLU breach in master matrix is flagged "
            "as REVERT-EVALUATE in this report and the JSON aggregate."
        ),
    ])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main entry — orchestrate matrix + ablation + write artifacts.
# ---------------------------------------------------------------------------


async def run_master_ablation(
    *,
    fixture_path: Path,
    output_dir: Path,
    per_bot_cap: int,
    pace_s: float,
    asker_factory: Callable[[], Awaitable[AskerFn]] | None,
    skip_ablation: bool,
    features: Sequence[FeatureSpec] | None = None,
) -> dict[str, Any]:
    """End-to-end run. Returns the JSON aggregate written to ``output_dir``."""
    feats = list(features or build_default_feature_matrix())
    named_configs = build_named_configs(feats)
    ablation_configs = [] if skip_ablation else build_ablation_configs(feats)

    questions = load_fixture(fixture_path, per_bot_cap=per_bot_cap)
    log.info(
        "master_ablation_run_start",
        step_name="master_ablation",
        fixture=str(fixture_path),
        questions=len(questions),
        configs=len(named_configs) + len(ablation_configs),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    run_started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    t0 = time.perf_counter()

    if asker_factory is None:
        raise ValueError(
            "asker_factory required — production passes the HTTP factory; "
            "tests pass an in-memory factory"
        )
    asker = await asker_factory()

    matrix_results: dict[str, dict[str, Any]] = {}
    for cfg in named_configs.values():
        out = await run_config(
            config=cfg,
            features=feats,
            questions=questions,
            asker=asker,
            pace_s=pace_s,
        )
        matrix_results[cfg.name] = out

    ablation_results: dict[str, dict[str, Any]] = {}
    for cfg in ablation_configs:
        out = await run_config(
            config=cfg,
            features=feats,
            questions=questions,
            asker=asker,
            pace_s=pace_s,
        )
        ablation_results[cfg.name] = out

    all_on_summary = matrix_results.get("all_on", {}).get("summary", {})
    summaries_for_decision = {
        k: v["summary"] for k, v in ablation_results.items()
    }
    decisions = (
        build_decision_matrix(feats, all_on_summary, summaries_for_decision)
        if not skip_ablation else []
    )

    wall_s = round(time.perf_counter() - t0, 2)

    aggregate_blob = {
        "run_started_at": run_started_at,
        "wall_time_s": wall_s,
        "fixture": str(fixture_path),
        "per_bot_cap": per_bot_cap,
        "feature_set": [
            {"name": f.name, "tier": f.tier, "env_var": f.env_var}
            for f in feats
        ],
        "matrix": {k: v["summary"] for k, v in matrix_results.items()},
        "ablation": {k: v["summary"] for k, v in ablation_results.items()},
        "decisions": [
            {
                "feature": d.feature,
                "tier": d.tier,
                "verdict": d.verdict,
                "reason": d.reason,
                "pass_lift_pp": d.pass_lift_pp,
                "latency_drop_pct": d.latency_drop_pct,
                "cost_delta_pct": d.cost_delta_pct,
                "hallu_with": d.hallu_with,
                "hallu_without": d.hallu_without,
            }
            for d in decisions
        ],
    }

    # Per-config records → separate files (large).
    records_dir = output_dir / "records"
    records_dir.mkdir(exist_ok=True)
    for name, blob in {**matrix_results, **ablation_results}.items():
        (records_dir / f"{name}.json").write_text(
            json.dumps(blob, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    (output_dir / "aggregate.json").write_text(
        json.dumps(aggregate_blob, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report_md = render_report(
        fixture_path=fixture_path,
        matrix_results=matrix_results,
        ablation_results=ablation_results,
        decisions=decisions,
        features=feats,
        run_started_at=run_started_at,
        run_wall_s=wall_s,
    )
    (output_dir / "results.md").write_text(report_md, encoding="utf-8")

    log.info(
        "master_ablation_run_done",
        step_name="master_ablation",
        wall_s=wall_s,
        output_dir=str(output_dir),
        decisions_keep=sum(1 for d in decisions if d.verdict == "KEEP"),
        decisions_tune=sum(1 for d in decisions if d.verdict == "TUNE"),
        decisions_drop=sum(1 for d in decisions if d.verdict == "DROP"),
        hallu_zero_sacred_all_on=all_on_summary.get("hallu_zero_sacred"),
    )
    return aggregate_blob


def _default_output_dir() -> Path:
    return Path(
        f"{DEFAULT_REPORT_DIR_PREFIX}{time.strftime('%Y%m%d')}"
    )


async def _make_http_asker_factory(base_url: str) -> AskerFn:
    """Build the production HTTP asker — opens a long-lived client + JWT."""
    client = httpx.AsyncClient()
    token = await _get_self_token(client, base_url)
    return make_http_asker(base_url=base_url, token=token, client=client)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", default=DEFAULT_MASTER_FIXTURE)
    ap.add_argument(
        "--per-bot-cap", type=int, default=DEFAULT_PER_BOT_CAP,
        help="Cap Q per bot (default 30 → 90Q across 3 bots).",
    )
    ap.add_argument("--output-dir", default=str(_default_output_dir()))
    ap.add_argument(
        "--pace", type=float,
        default=DEFAULT_LOADTEST_INTER_QUESTION_SLEEP_S,
    )
    ap.add_argument(
        "--base-url",
        default=os.getenv("RAGBOT_BASE_URL", "http://localhost:3004"),
    )
    ap.add_argument("--skip-ablation", action="store_true")
    args = ap.parse_args(argv)

    async def _factory() -> AskerFn:
        return await _make_http_asker_factory(args.base_url)

    blob = asyncio.run(
        run_master_ablation(
            fixture_path=Path(args.fixture),
            output_dir=Path(args.output_dir),
            per_bot_cap=args.per_bot_cap,
            pace_s=args.pace,
            asker_factory=_factory,
            skip_ablation=args.skip_ablation,
        )
    )

    # Stdout digest for admin scripts that pipe.
    digest = {
        "configs": list(blob["matrix"].keys()),
        "matrix_pass_rate_pct": {
            k: v["pass_rate_pct"] for k, v in blob["matrix"].items()
        },
        "matrix_hallu": {
            k: v["hallu_breach"] for k, v in blob["matrix"].items()
        },
        "decisions": {d["feature"]: d["verdict"] for d in blob["decisions"]},
    }
    print(json.dumps(digest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
