#!/usr/bin/env python3
"""Auditor: classify a load-test round + RAGAS metrics + 24+ step pipeline coverage.

Reads a transcript JSON produced by ``scripts/test_75q_load.py`` (or merged
150q variant) PLUS the ``request_steps`` table for the included
``request_id`` values, then emits a markdown verdict report covering:

1. Per-turn verdict (CORRECT_GROUNDED / CORRECT_PARTIAL / HALLUCINATION /
   WRONG_TOPIC / REFUSE_CORRECT / REFUSE_GAP / CHITCHAT_OK / EMPTY_FAIL).
2. RAGAS aggregate (faithfulness / answer_relevance / context_precision)
   delegated to ``scripts/eval_75q_ragas.py`` via subprocess.
3. 24+ step pipeline coverage matrix (count fired + p50/p95 latency,
   marking steps NOT_INSTRUMENTED when absent).

Usage:
  python3 scripts/auditor_analyze_round.py \\
    --input /tmp/mega_round1_full.json \\
    --output reports/MEGA_ROUND1_VERDICT_$(date +%Y%m%d_%H%M).md \\
    --bot-id <bot> --tenant-id <tid> --channel-type <ch>

Bot identity (3-key) is REQUIRED — falls back to env
``LOADTEST_BOT_ID / LOADTEST_TENANT_ID / LOADTEST_CHANNEL_TYPE``.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_DEEPEVAL_FAITHFULNESS_THRESHOLD,
)

# ---------------------------------------------------------------------------
# Module constants — zero magic numbers
# ---------------------------------------------------------------------------
FAITHFULNESS_HALLUCINATE_THRESHOLD: float = 0.5
FAITHFULNESS_PARTIAL_THRESHOLD: float = DEFAULT_DEEPEVAL_FAITHFULNESS_THRESHOLD
TOP_SCORE_GAP_THRESHOLD: float = 0.4  # below this, retrieval considered weak
CHITCHAT_MIN_ANSWER_LEN: int = 30
HALLUCINATE_MIN_ANSWER_LEN: int = 50
TOP_WEAKEST_N: int = 5
PERCENTILE_P50: float = 50.0
PERCENTILE_P95: float = 95.0
RAGAS_SUBPROCESS_TIMEOUT_S: int = 1800

# Verdicts
V_CORRECT_GROUNDED = "CORRECT_GROUNDED"
V_CORRECT_PARTIAL = "CORRECT_PARTIAL"
V_HALLUCINATION = "HALLUCINATION"
V_WRONG_TOPIC = "WRONG_TOPIC"  # reserved for human review
V_REFUSE_CORRECT = "REFUSE_CORRECT"
V_REFUSE_GAP = "REFUSE_GAP"
V_CHITCHAT_OK = "CHITCHAT_OK"
V_EMPTY_FAIL = "EMPTY_FAIL"
V_CANNOT_VERIFY = "CANNOT_VERIFY"
ALL_VERDICTS: tuple[str, ...] = (
    V_CORRECT_GROUNDED,
    V_CORRECT_PARTIAL,
    V_HALLUCINATION,
    V_WRONG_TOPIC,
    V_REFUSE_CORRECT,
    V_REFUSE_GAP,
    V_CHITCHAT_OK,
    V_EMPTY_FAIL,
    V_CANNOT_VERIFY,
)

# Step verdict labels
STEP_PERFECT = "PERFECT"
STEP_SUFFICIENT = "SUFFICIENT"
STEP_SUBOPTIMAL = "SUBOPTIMAL"
STEP_BROKEN = "BROKEN"
STEP_NOT_INSTRUMENTED = "NOT_INSTRUMENTED"

# Latency targets per step (p95 in ms). None = no upper-bound check.
STEP_P95_BUDGET_MS: dict[str, int | None] = {
    "guard_input": 200,
    "cache_check": 100,
    "understand_query": 1500,
    "rewrite": 1500,
    "decompose": 4000,
    "retrieve": 1500,
    "rerank": 800,
    "grade": 4000,
    "mmr_dedup": 200,
    "generate": 5000,
    "guard_output": 2000,
    "reflect": 3000,
    "persist": 100,
}

# Canonical 24+ step map: code → (label, db_step_name | None).
# When db_step_name is None, the step is documented but NOT_INSTRUMENTED.
@dataclass(frozen=True)
class CanonicalStep:
    code: str
    label: str
    db_step_name: str | None  # actual step_name in request_steps; None = not wired


CANONICAL_STEPS: tuple[CanonicalStep, ...] = (
    # U-side (pre-pipeline)
    CanonicalStep("U1", "auth_resolve", None),
    CanonicalStep("U2", "rate_limit", None),
    CanonicalStep("U3", "bot_registry_lookup", None),
    CanonicalStep("U4", "guard_input", "guard_input"),
    CanonicalStep("U5", "language_detect", None),
    CanonicalStep("U6", "history_load", None),
    CanonicalStep("U7", "router_select_model", None),
    # Q-side (RAG core)
    CanonicalStep("Q1", "hash_lookup_cache", None),
    CanonicalStep("Q2", "semantic_cache_check", None),
    CanonicalStep("Q3", "intent_extract", "understand_query"),
    CanonicalStep("Q4", "query_rewrite", "rewrite"),
    CanonicalStep("Q5", "multi_query_fanout", None),
    CanonicalStep("Q6", "decompose_split", "decompose"),
    CanonicalStep("Q7", "retrieve_dense", "retrieve"),
    CanonicalStep("Q8", "retrieve_sparse", None),
    CanonicalStep("Q9", "rrf_fuse", None),
    CanonicalStep("Q10", "rerank", "rerank"),
    CanonicalStep("Q11", "crag_grade", "grade"),
    CanonicalStep("Q12", "mmr_dedup", "mmr_dedup"),
    CanonicalStep("Q13", "litm_order", None),
    CanonicalStep("Q14", "prompt_build", None),
    CanonicalStep("Q15", "generate_llm", "generate"),
    CanonicalStep("Q16", "grounding_check", None),
    CanonicalStep("Q17", "citations_extract", None),
    # Post
    CanonicalStep("P1", "guard_output", "guard_output"),
    CanonicalStep("P2", "reflect", "reflect"),
    CanonicalStep("P3", "persist", "persist"),
)

# Refuse classifications produced by test_75q_load.py
REFUSE_CLASSIFICATIONS: frozenset[str] = frozenset(
    {"REFUSE_NO_DOCS", "REFUSE_WITH_DOCS"}
)


# ---------------------------------------------------------------------------
# Loading + classification
# ---------------------------------------------------------------------------
def _load_input(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    turns = raw.get("turns") or []
    summary = raw.get("summary") or {}
    if not isinstance(turns, list):
        raise ValueError(f"{path}: 'turns' is not a list")
    return turns, summary


def _retrieved_chunks_have_answer(turn: dict[str, Any]) -> bool:
    """Heuristic — corpus likely contains answer if retrieval hit ≥1 chunk
    above ``TOP_SCORE_GAP_THRESHOLD``. Refuses despite this signal a smartness gap.
    """
    try:
        chunks_used = int(turn.get("chunks_used") or 0)
        top_score = float(turn.get("top_score") or 0.0)
    except (TypeError, ValueError):
        return False
    return chunks_used > 0 and top_score >= TOP_SCORE_GAP_THRESHOLD


def _classify_turn(
    turn: dict[str, Any],
    *,
    faithfulness: float | None,
) -> str:
    """Return verdict — heuristic but explicit. RAGAS faithfulness refines
    CORRECT_GROUNDED into HALLUCINATION / CORRECT_PARTIAL when available.
    """
    error = turn.get("error")
    answer = (turn.get("answer") or "").strip()
    classification = (turn.get("classification") or "").upper()

    if error:
        return V_EMPTY_FAIL
    if not answer:
        return V_EMPTY_FAIL

    if classification in REFUSE_CLASSIFICATIONS:
        return V_REFUSE_GAP if _retrieved_chunks_have_answer(turn) else V_REFUSE_CORRECT

    try:
        chunks_used = int(turn.get("chunks_used") or 0)
    except (TypeError, ValueError):
        chunks_used = 0

    if chunks_used == 0 and len(answer) >= CHITCHAT_MIN_ANSWER_LEN:
        return V_CHITCHAT_OK

    # Default: grounded answer with chunks. Refine with faithfulness.
    if faithfulness is None:
        return V_CORRECT_GROUNDED
    if (
        faithfulness < FAITHFULNESS_HALLUCINATE_THRESHOLD
        and len(answer) >= HALLUCINATE_MIN_ANSWER_LEN
    ):
        return V_HALLUCINATION
    if faithfulness < FAITHFULNESS_PARTIAL_THRESHOLD:
        return V_CORRECT_PARTIAL
    return V_CORRECT_GROUNDED


# ---------------------------------------------------------------------------
# RAGAS subprocess
# ---------------------------------------------------------------------------
def _run_ragas_subprocess(
    input_path: Path,
    tmp_output: Path,
    *,
    extra_args: Iterable[str] = (),
) -> tuple[dict[tuple[Any, Any], dict[str, float]], dict[str, Any]]:
    """Invoke ``scripts/eval_75q_ragas.py`` as subprocess, return tuple of
    ``(per_turn_metric_map_keyed_by_(room,idx), aggregate_dict)``.
    Empty tuple parts on failure.
    """
    venv_py = ROOT / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else sys.executable
    cmd = [
        py,
        str(ROOT / "scripts" / "eval_75q_ragas.py"),
        "--input",
        str(input_path),
        "--output",
        str(tmp_output),
        *extra_args,
    ]
    print(f"[ragas] {' '.join(cmd)}", flush=True)
    try:
        subprocess.run(  # noqa: S603 — args list, not shell
            cmd,
            check=True,
            timeout=RAGAS_SUBPROCESS_TIMEOUT_S,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"[ragas] WARN: subprocess failed: {exc}", file=sys.stderr)
        return {}, {}

    if not tmp_output.exists():
        return {}, {}
    try:
        data = json.loads(tmp_output.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ragas] WARN: cannot parse {tmp_output}: {exc}", file=sys.stderr)
        return {}, {}

    out: dict[tuple[Any, Any], dict[str, float]] = {}
    for entry in data.get("per_turn") or []:
        if not isinstance(entry, dict):
            continue
        key = (entry.get("room"), entry.get("idx"))
        score = entry.get("score") or {}
        try:
            out[key] = {
                "faithfulness": float(score.get("faithfulness", 0.0)),
                "answer_relevance": float(score.get("answer_relevance", 0.0)),
                "context_precision": float(score.get("context_precision", 0.0)),
            }
        except (TypeError, ValueError):
            continue
    return out, data.get("aggregate") or {}


# ---------------------------------------------------------------------------
# request_steps fetch + matrix
# ---------------------------------------------------------------------------
def _resolve_dsn() -> str:
    raw = os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL")
    if not raw:
        raise RuntimeError(
            "DATABASE_URL_SYNC / DATABASE_URL env var required (source .env first)"
        )
    return raw.replace("+psycopg2", "").replace("+asyncpg", "")


def _fetch_request_steps(
    dsn: str,
    request_ids: list[str],
    *,
    table: str,
) -> dict[str, list[tuple[int, str]]]:
    """Return ``{step_name: [(duration_ms, status), ...]}``.

    ``table`` should be ``request_steps`` (real pipeline events). ``audit_log``
    accepted for forward compatibility but typically empty for pipeline data.
    """
    if not request_ids:
        return {}
    import psycopg2  # local import — optional dep style, narrow scope

    if table == "audit_log":
        # audit_log is admin RBAC trail — not pipeline events. Return empty.
        return {}

    if table != "request_steps":
        raise ValueError(f"Unsupported audit table: {table}")

    out: dict[str, list[tuple[int, str]]] = {}
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT step_name, duration_ms, status "
            "FROM request_steps "
            "WHERE record_request_id = ANY(%s::uuid[])",
            (request_ids,),
        )
        for step_name, duration_ms, status in cur.fetchall():
            out.setdefault(step_name, []).append((int(duration_ms or 0), status or ""))
    return out


def _percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    sv = sorted(values)
    k = (len(sv) - 1) * (pct / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(sv[int(k)])
    return sv[lo] + (sv[hi] - sv[lo]) * (k - lo)


@dataclass
class StepRow:
    code: str
    label: str
    db_name: str | None
    fired: int = 0
    errors: int = 0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    verdict: str = STEP_NOT_INSTRUMENTED


def _build_step_matrix(
    steps_data: dict[str, list[tuple[int, str]]],
    *,
    n_turns: int,
) -> list[StepRow]:
    rows: list[StepRow] = []
    for cs in CANONICAL_STEPS:
        row = StepRow(code=cs.code, label=cs.label, db_name=cs.db_step_name)
        if cs.db_step_name is None:
            rows.append(row)
            continue
        bucket = steps_data.get(cs.db_step_name) or []
        if not bucket:
            row.verdict = STEP_BROKEN if n_turns > 0 else STEP_NOT_INSTRUMENTED
            rows.append(row)
            continue
        durations = [d for d, _s in bucket]
        errors = sum(1 for _d, s in bucket if s and s.lower() not in ("ok", "success"))
        row.fired = len(bucket)
        row.errors = errors
        row.p50_ms = round(_percentile(durations, PERCENTILE_P50), 1)
        row.p95_ms = round(_percentile(durations, PERCENTILE_P95), 1)
        budget = STEP_P95_BUDGET_MS.get(cs.db_step_name)
        if errors > 0:
            row.verdict = STEP_BROKEN
        elif budget is not None and row.p95_ms > budget * 2:
            row.verdict = STEP_SUBOPTIMAL
        elif budget is not None and row.p95_ms > budget:
            row.verdict = STEP_SUFFICIENT
        else:
            row.verdict = STEP_PERFECT
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------
def _verdict_counts(verdicts: list[str]) -> dict[str, int]:
    out = {v: 0 for v in ALL_VERDICTS}
    for v in verdicts:
        out[v] = out.get(v, 0) + 1
    return out


def _per_category_pass_rate(
    turns: list[dict[str, Any]], verdicts: list[str]
) -> dict[str, dict[str, Any]]:
    """Group by ``classification`` (REFUSE_NO_DOCS / ANSWERED / ...) — show
    per-category PASS rate where PASS = verdict in CORRECT_GROUNDED|CHITCHAT_OK|REFUSE_CORRECT.
    """
    pass_set = {V_CORRECT_GROUNDED, V_CHITCHAT_OK, V_REFUSE_CORRECT}
    by_cat: dict[str, list[bool]] = {}
    for turn, verdict in zip(turns, verdicts, strict=False):
        cat = (turn.get("classification") or "UNKNOWN").upper()
        by_cat.setdefault(cat, []).append(verdict in pass_set)
    out: dict[str, dict[str, Any]] = {}
    for cat, flags in sorted(by_cat.items()):
        n = len(flags)
        pass_n = sum(1 for f in flags if f)
        out[cat] = {
            "n": n,
            "pass": pass_n,
            "rate": round(pass_n / n, 4) if n else 0.0,
        }
    return out


def _top_weakest_steps(rows: list[StepRow]) -> list[StepRow]:
    instrumented = [r for r in rows if r.verdict != STEP_NOT_INSTRUMENTED]
    rank = {STEP_BROKEN: 0, STEP_SUBOPTIMAL: 1, STEP_SUFFICIENT: 2, STEP_PERFECT: 3}
    instrumented.sort(key=lambda r: (rank.get(r.verdict, 9), -r.p95_ms))
    return instrumented[:TOP_WEAKEST_N]


# Recommended fix file:line per step (best-effort static map; documented).
STEP_FIX_HINT: dict[str, str] = {
    "guard_input": "src/ragbot/orchestration/nodes/guard_input.py",
    "understand_query": "src/ragbot/orchestration/nodes/understand_query.py",
    "rewrite": "src/ragbot/orchestration/nodes/rewrite.py",
    "decompose": "src/ragbot/orchestration/nodes/decompose.py",
    "retrieve": "src/ragbot/orchestration/nodes/retrieve.py",
    "rerank": "src/ragbot/orchestration/nodes/rerank.py",
    "grade": "src/ragbot/orchestration/nodes/grade.py",
    "mmr_dedup": "src/ragbot/orchestration/nodes/mmr_dedup.py",
    "generate": "src/ragbot/orchestration/nodes/generate.py",
    "guard_output": "src/ragbot/orchestration/nodes/guard_output.py",
    "reflect": "src/ragbot/orchestration/nodes/reflect.py",
    "persist": "src/ragbot/orchestration/nodes/persist.py",
}


def _render_markdown(
    *,
    bot_id: str,
    tenant_id: int,
    channel_type: str,
    input_path: Path,
    n_turns: int,
    verdict_counts: dict[str, int],
    ragas_aggregate: dict[str, Any],
    step_rows: list[StepRow],
    weakest: list[StepRow],
    per_category: dict[str, dict[str, Any]],
    extra_subjects: list[str],
) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    lines: list[str] = []
    lines.append(f"# Round Verdict — bot=`{bot_id}` channel=`{channel_type}` tenant=`{tenant_id}`")
    lines.append("")
    lines.append(f"**Generated**: {ts}")
    lines.append(f"**Input**: `{input_path}`")
    lines.append(f"**Total turns**: {n_turns}")
    lines.append("")
    lines.append("## 1. Per-turn verdict counts")
    lines.append("")
    lines.append("| Verdict | Count | % |")
    lines.append("|---|---:|---:|")
    for v in ALL_VERDICTS:
        c = verdict_counts.get(v, 0)
        pct = round(100 * c / n_turns, 1) if n_turns else 0.0
        lines.append(f"| {v} | {c} | {pct}% |")
    lines.append("")
    lines.append("Targets: `HALLUCINATION = 0`, `EMPTY_FAIL = 0`.")
    lines.append("")

    lines.append("## 2. RAGAS aggregate")
    lines.append("")
    if ragas_aggregate:
        rooms = ragas_aggregate.get("rooms") or {}
        lines.append("| Room | N | Faithfulness | AnsRel | CtxPrec |")
        lines.append("|---|---:|---:|---:|---:|")
        for room, agg in rooms.items():
            lines.append(
                f"| {room} | {agg.get('n')} | "
                f"{agg.get('faithfulness_mean', 0):.3f} | "
                f"{agg.get('answer_relevance_mean', 0):.3f} | "
                f"{agg.get('context_precision_mean', 0):.3f} |"
            )
        lines.append("")
        lines.append(f"Targets: faithfulness ≥ {FAITHFULNESS_PARTIAL_THRESHOLD:.2f}.")
    else:
        lines.append("_RAGAS skipped or failed; see stderr._")
    lines.append("")

    lines.append("## 3. 24+ step pipeline coverage")
    lines.append("")
    lines.append("| Code | Step | DB step_name | Fired | Errors | p50 ms | p95 ms | Verdict |")
    lines.append("|---|---|---|---:|---:|---:|---:|---|")
    for r in step_rows:
        db = r.db_name or "_(not wired)_"
        lines.append(
            f"| {r.code} | {r.label} | {db} | {r.fired} | {r.errors} | "
            f"{r.p50_ms} | {r.p95_ms} | {r.verdict} |"
        )
    lines.append("")
    instrumented_n = sum(1 for r in step_rows if r.db_name)
    lines.append(
        f"Instrumented: {instrumented_n} / {len(step_rows)} canonical steps. "
        f"Steps marked NOT_INSTRUMENTED have no `request_steps.step_name` row — "
        f"consider adding emitter where step actually executes."
    )
    lines.append("")

    lines.append("## 4. Top-5 weakest instrumented steps")
    lines.append("")
    if weakest:
        lines.append("| Code | Step | Verdict | p95 ms | Errors | Fix hint |")
        lines.append("|---|---|---|---:|---:|---|")
        for r in weakest:
            hint = STEP_FIX_HINT.get(r.db_name or "", "_(no hint)_")
            lines.append(
                f"| {r.code} | {r.label} | {r.verdict} | {r.p95_ms} | {r.errors} | `{hint}` |"
            )
    else:
        lines.append("_No instrumented steps observed._")
    lines.append("")

    lines.append("## 5. Per-category PASS rate (input JSON `classification`)")
    lines.append("")
    lines.append("| Category | N | Pass | Rate |")
    lines.append("|---|---:|---:|---:|")
    for cat, info in per_category.items():
        lines.append(
            f"| {cat} | {info['n']} | {info['pass']} | {info['rate'] * 100:.1f}% |"
        )
    lines.append("")

    if extra_subjects:
        lines.append("## 6. Audit subjects observed but NOT mapped to U/Q step")
        lines.append("")
        for s in extra_subjects:
            lines.append(f"- `{s}`")
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # 3-key identity required (CLAUDE.md rule).
    if not args.bot_id or not args.channel_type or not args.tenant_id:
        print(
            "ERROR: --bot-id, --tenant-id, --channel-type are ALL required "
            "(or set LOADTEST_BOT_ID / LOADTEST_TENANT_ID / LOADTEST_CHANNEL_TYPE).",
            file=sys.stderr,
        )
        return 2

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input not found: {input_path}", file=sys.stderr)
        return 2

    turns, _summary = _load_input(input_path)
    n_turns = len(turns)
    print(f"Loaded {n_turns} turns from {input_path}", flush=True)

    # ---------------- RAGAS ----------------
    ragas_per_turn: dict[tuple[Any, Any], dict[str, float]] = {}
    ragas_aggregate: dict[str, Any] = {}
    if not args.skip_ragas:
        tmp_out = (
            Path(args.ragas_output)
            if args.ragas_output
            else Path(f"/tmp/ragas_round_{int(time.time())}.json")
        )
        ragas_per_turn, ragas_aggregate = _run_ragas_subprocess(input_path, tmp_out)

    # ---------------- Verdicts ----------------
    verdicts: list[str] = []
    for turn in turns:
        key = (turn.get("room"), turn.get("idx"))
        faith = (ragas_per_turn.get(key) or {}).get("faithfulness")
        verdicts.append(_classify_turn(turn, faithfulness=faith))
    verdict_counts = _verdict_counts(verdicts)

    # ---------------- Pipeline coverage ----------------
    request_ids = [
        str(t.get("request_id"))
        for t in turns
        if t.get("request_id")
    ]
    dsn = _resolve_dsn()
    steps_data = _fetch_request_steps(dsn, request_ids, table=args.audit_log_table)
    step_rows = _build_step_matrix(steps_data, n_turns=n_turns)
    weakest = _top_weakest_steps(step_rows)

    # Subjects observed but not mapped to canonical step
    mapped_db_names = {
        cs.db_step_name for cs in CANONICAL_STEPS if cs.db_step_name
    }
    extra_subjects = sorted(
        s for s in steps_data.keys() if s not in mapped_db_names
    )

    # ---------------- Render ----------------
    per_category = _per_category_pass_rate(turns, verdicts)
    md = _render_markdown(
        bot_id=args.bot_id,
        tenant_id=args.tenant_id,
        channel_type=args.channel_type,
        input_path=input_path,
        n_turns=n_turns,
        verdict_counts=verdict_counts,
        ragas_aggregate=ragas_aggregate,
        step_rows=step_rows,
        weakest=weakest,
        per_category=per_category,
        extra_subjects=extra_subjects,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")

    print(f"Wrote {out_path}", flush=True)
    print("Verdict counts:", json.dumps(verdict_counts, ensure_ascii=False))
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Auditor: classify load-test round + RAGAS + pipeline coverage. "
            "3-key bot identity required."
        )
    )
    p.add_argument("--input", required=True, help="JSON output from test_75q_load.py")
    p.add_argument(
        "--output",
        default=f"reports/MEGA_VERDICT_{time.strftime('%Y%m%d_%H%M')}.md",
    )
    p.add_argument(
        "--bot-id",
        default=os.getenv("LOADTEST_BOT_ID", ""),
        help="Bot slug (REQUIRED; env LOADTEST_BOT_ID)",
    )
    p.add_argument(
        "--tenant-id",
        type=int,
        default=int(os.getenv("LOADTEST_TENANT_ID", "0") or "0"),
        help="Tenant ID int (REQUIRED; env LOADTEST_TENANT_ID)",
    )
    p.add_argument(
        "--channel-type",
        default=os.getenv("LOADTEST_CHANNEL_TYPE", ""),
        help="Channel type (REQUIRED; env LOADTEST_CHANNEL_TYPE)",
    )
    p.add_argument(
        "--audit-log-table",
        default="request_steps",
        choices=["request_steps", "audit_log"],
        help=(
            "Source table for pipeline events. Default request_steps "
            "(audit_log accepted but contains admin RBAC trail only)."
        ),
    )
    p.add_argument(
        "--skip-ragas",
        action="store_true",
        help="Skip RAGAS subprocess (faster; verdicts will not refine HALLUCINATION/PARTIAL).",
    )
    p.add_argument(
        "--ragas-output",
        default="",
        help="Optional path for RAGAS subprocess JSON (default /tmp/ragas_round_<ts>.json).",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
