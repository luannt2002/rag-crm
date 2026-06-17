#!/usr/bin/env python3
"""DeepEval RAGAS-compatible runner — Sprint 10 Tier-IQ #3.

Loads ``golden_set/golden_questions_v2.json``, calls ``/test/chat`` with the
3-key external identity ``(tenant_id, bot_id, channel_type)`` for each
non-TODO question, then scores each (input, actual_output, expected_output,
retrieval_context) tuple using DeepEval's RAGAS-equivalent metrics:

  * ``FaithfulnessMetric``         — answer grounded in retrieved context
  * ``AnswerRelevancyMetric``      — answer relevant to user question
  * ``ContextualPrecisionMetric``  — retrieved context usefully ranked
  * ``ContextualRecallMetric``     — retrieved context covers ground truth

Output: ``reports/deepeval_run_<timestamp>.json`` with per-question scores
and aggregate pass-rate vs the 4 thresholds in
``shared/constants.py``.

Usage::

    set -a && source .env && set +a
    python scripts/deepeval_runner.py \\
        --tenant-id 32 \\
        --bot-id test-bot-v1 \\
        --channel-type web \\
        --n-questions 5

Requires ``OPENAI_API_KEY`` for the DeepEval judge model. Judge model is
read from ``DEEPEVAL_JUDGE_MODEL`` env (or ``DEFAULT_DEEPEVAL_JUDGE_MODEL``
constant fallback) — never hardcoded inline (CLAUDE.md zero-hardcode rule).

Honest scope: this runner uses ``debug=full`` to receive
``retrieved_chunks_content`` from ``/test/chat`` and pass them as DeepEval's
``retrieval_context``. If a deployment does not surface retrieved chunks the
runner skips contextual metrics for that question and notes it in the
report.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Project root → sys.path (script is invoked outside the package)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

import httpx  # noqa: E402

from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_DEEPEVAL_FAITHFULNESS_THRESHOLD,
    DEFAULT_DEEPEVAL_JUDGE_MODEL,
    DEFAULT_DEEPEVAL_PRECISION_THRESHOLD,
    DEFAULT_DEEPEVAL_RECALL_THRESHOLD,
    DEFAULT_DEEPEVAL_RELEVANCY_THRESHOLD,
    DEFAULT_HTTP_TIMEOUT_S,
)


# ── Constants surfaced via env (zero-hardcode) ─────────────────────────────

GOLDEN_SET_PATH = _PROJECT_ROOT / "golden_set" / "golden_questions_v2.json"
REPORTS_DIR = _PROJECT_ROOT / "reports"
TODO_MARKER = "TODO"


# ── HTTP helpers ───────────────────────────────────────────────────────────

async def _get_self_token(client: httpx.AsyncClient, base_url: str) -> str:
    r = await client.get(f"{base_url}/api/ragbot/test/tokens/self")
    r.raise_for_status()
    return r.json()["token"]


async def _ask(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token: str,
    tenant_id: int,
    bot_id: str,
    channel_type: str,
    question: str,
) -> dict[str, Any]:
    """Call /test/chat with 3-key identity + debug=full → returns flat dict."""
    t0 = time.perf_counter()
    payload = {
        "tenant_id": tenant_id,
        "bot_id": bot_id,
        "channel_type": channel_type,
        "question": question,
        "debug": "full",
    }
    try:
        r = await client.post(
            f"{base_url}/api/ragbot/test/chat",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60.0,
        )
        wall_ms = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            return {"_error": f"HTTP {r.status_code}", "_wall_ms": wall_ms, "_body": r.text[:400]}
        body = r.json()
    except Exception as exc:  # noqa: BLE001 — runner-level catch is intentional
        return {"_error": str(exc)[:300], "_wall_ms": (time.perf_counter() - t0) * 1000}

    data = body.get("data") if isinstance(body.get("data"), dict) else body
    debug_payload = data.get("debug") or {}
    # /test/chat surfaces ``retrieved_chunks_content`` at the TOP LEVEL of the
    # response (next to ``answer``), not inside ``debug``. Older runner code
    # only looked under ``debug`` so retrieval_context was always empty and
    # DeepEval's faithfulness/precision/recall metrics were skipped.
    chunks = (
        data.get("retrieved_chunks_content")
        or data.get("retrieved_chunks")
        or debug_payload.get("retrieved_chunks_content")
        or debug_payload.get("retrieved_chunks")
        or []
    )
    # Normalise chunks → list[str] for DeepEval retrieval_context.
    retrieval_context: list[str] = []
    for c in chunks:
        if isinstance(c, str):
            retrieval_context.append(c)
        elif isinstance(c, dict):
            txt = c.get("content") or c.get("text") or c.get("chunk_text") or ""
            if txt:
                retrieval_context.append(str(txt))

    return {
        "answer": data.get("answer") or "",
        "answer_type": data.get("answer_type"),
        "retrieval_context": retrieval_context,
        "duration_ms": data.get("duration_ms"),
        "_wall_ms": wall_ms,
    }


# ── DeepEval scoring ────────────────────────────────────────────────────────

def _build_metrics(judge_model: str) -> dict[str, Any]:
    """Construct the four RAGAS-equivalent DeepEval metrics.

    Imported lazily so the runner module can be imported without the heavy
    ``deepeval`` package present (e.g. for unit-test collection).
    """
    from deepeval.metrics import (  # noqa: PLC0415 — lazy import
        AnswerRelevancyMetric,
        ContextualPrecisionMetric,
        ContextualRecallMetric,
        FaithfulnessMetric,
    )

    return {
        "faithfulness": FaithfulnessMetric(
            threshold=DEFAULT_DEEPEVAL_FAITHFULNESS_THRESHOLD,
            model=judge_model,
            include_reason=True,
            async_mode=False,
        ),
        "answer_relevancy": AnswerRelevancyMetric(
            threshold=DEFAULT_DEEPEVAL_RELEVANCY_THRESHOLD,
            model=judge_model,
            include_reason=True,
            async_mode=False,
        ),
        "contextual_precision": ContextualPrecisionMetric(
            threshold=DEFAULT_DEEPEVAL_PRECISION_THRESHOLD,
            model=judge_model,
            include_reason=True,
            async_mode=False,
        ),
        "contextual_recall": ContextualRecallMetric(
            threshold=DEFAULT_DEEPEVAL_RECALL_THRESHOLD,
            model=judge_model,
            include_reason=True,
            async_mode=False,
        ),
    }


def _score_one(
    metrics: dict[str, Any],
    *,
    question: str,
    actual_output: str,
    expected_output: str,
    retrieval_context: list[str],
) -> dict[str, Any]:
    """Run all metrics on one test case; return per-metric score+reason."""
    from deepeval.test_case import LLMTestCase  # noqa: PLC0415 — lazy import

    out: dict[str, Any] = {}
    for name, metric in metrics.items():
        # Contextual metrics require non-empty retrieval_context; skip if missing.
        needs_ctx = name in ("faithfulness", "contextual_precision", "contextual_recall")
        if needs_ctx and not retrieval_context:
            out[name] = {"score": None, "reason": "retrieval_context empty — skipped", "passed": None}
            continue
        try:
            tc = LLMTestCase(
                input=question,
                actual_output=actual_output,
                expected_output=expected_output,
                retrieval_context=retrieval_context or None,
            )
            metric.measure(tc)
            out[name] = {
                "score": getattr(metric, "score", None),
                "reason": getattr(metric, "reason", None),
                "passed": getattr(metric, "is_successful", lambda: None)(),
            }
        except Exception as exc:  # noqa: BLE001 — record + continue, don't crash batch
            out[name] = {"score": None, "reason": f"metric error: {exc}", "passed": None}
    return out


# ── Aggregation ─────────────────────────────────────────────────────────────

def _aggregate(per_q: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = ("faithfulness", "answer_relevancy", "contextual_precision", "contextual_recall")
    agg: dict[str, Any] = {"n_questions": len(per_q)}
    for m in metric_names:
        scores = [
            q["metrics"][m]["score"]
            for q in per_q
            if q.get("metrics", {}).get(m, {}).get("score") is not None
        ]
        passed = [
            q["metrics"][m]["passed"]
            for q in per_q
            if q.get("metrics", {}).get(m, {}).get("passed") is True
        ]
        agg[m] = {
            "n_scored": len(scores),
            "mean": round(sum(scores) / len(scores), 4) if scores else None,
            "pass_count": len(passed),
            "pass_rate": round(len(passed) / max(len(scores), 1), 4) if scores else None,
        }
    return agg


# ── Runner ──────────────────────────────────────────────────────────────────

def _load_golden(path: Path, n: int | None) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    questions = [
        q for q in raw.get("questions", [])
        if q.get("question") and q.get("question") != TODO_MARKER
    ]
    if n is not None:
        questions = questions[:n]
    return questions


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    base_url = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
    judge_model = os.getenv("DEEPEVAL_JUDGE_MODEL", DEFAULT_DEEPEVAL_JUDGE_MODEL)

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY env var required for DeepEval judge — source .env first."
        )

    questions = _load_golden(GOLDEN_SET_PATH, args.n_questions)
    if not questions:
        raise RuntimeError(f"No non-TODO questions found in {GOLDEN_SET_PATH}")

    metrics = _build_metrics(judge_model)
    per_q: list[dict[str, Any]] = []

    timeout = httpx.Timeout(DEFAULT_HTTP_TIMEOUT_S * 4.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        token = await _get_self_token(client, base_url)
        for i, q in enumerate(questions, 1):
            qid = q.get("id", f"q{i}")
            print(f"[{i}/{len(questions)}] {qid}: {q['question'][:60]}")
            chat = await _ask(
                client,
                base_url=base_url,
                token=token,
                tenant_id=args.tenant_id,
                bot_id=args.bot_id,
                channel_type=args.channel_type,
                question=q["question"],
            )
            if chat.get("_error"):
                per_q.append({
                    "id": qid,
                    "question": q["question"],
                    "expected_answer": q.get("expected_answer", ""),
                    "actual_answer": "",
                    "retrieval_context_n": 0,
                    "answer_type": None,
                    "duration_ms": chat.get("duration_ms"),
                    "_error": chat["_error"],
                    "metrics": {},
                })
                continue

            scored = _score_one(
                metrics,
                question=q["question"],
                actual_output=chat["answer"],
                expected_output=q.get("expected_answer", ""),
                retrieval_context=chat["retrieval_context"],
            )
            per_q.append({
                "id": qid,
                "question": q["question"],
                "expected_answer": q.get("expected_answer", ""),
                "actual_answer": chat["answer"],
                "retrieval_context_n": len(chat["retrieval_context"]),
                "answer_type": chat["answer_type"],
                "duration_ms": chat["duration_ms"],
                "metrics": scored,
            })

    agg = _aggregate(per_q)
    return {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "tenant_id": args.tenant_id,
            "bot_id": args.bot_id,
            "channel_type": args.channel_type,
            "judge_model": judge_model,
            "thresholds": {
                "faithfulness": DEFAULT_DEEPEVAL_FAITHFULNESS_THRESHOLD,
                "answer_relevancy": DEFAULT_DEEPEVAL_RELEVANCY_THRESHOLD,
                "contextual_precision": DEFAULT_DEEPEVAL_PRECISION_THRESHOLD,
                "contextual_recall": DEFAULT_DEEPEVAL_RECALL_THRESHOLD,
            },
        },
        "questions": per_q,
        "summary": agg,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="DeepEval RAGAS-compatible runner")
    p.add_argument("--tenant-id", type=int, required=True, help="External tenant_id (INT)")
    p.add_argument("--bot-id", type=str, required=True, help="External bot_id slug")
    p.add_argument("--channel-type", type=str, default="web", help="External channel_type")
    p.add_argument(
        "--n-questions", type=int, default=None,
        help="Limit questions (default: all non-TODO entries)",
    )
    p.add_argument("--output", type=str, default="", help="Optional output path override")
    args = p.parse_args()

    result = asyncio.run(_run(args))

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = (
        Path(args.output) if args.output
        else REPORTS_DIR / f"deepeval_run_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    s = result["summary"]
    print()
    print("=" * 70)
    print(f"DeepEval RAGAS Summary  ({s['n_questions']} questions, judge={result['config']['judge_model']})")
    print("=" * 70)
    for m in ("faithfulness", "answer_relevancy", "contextual_precision", "contextual_recall"):
        info = s.get(m, {})
        mean = info.get("mean")
        rate = info.get("pass_rate")
        mean_str = f"{mean:.3f}" if isinstance(mean, (int, float)) else "n/a"
        rate_str = f"{rate:.1%}" if isinstance(rate, (int, float)) else "n/a"
        print(f"  {m:<24} mean={mean_str}  pass_rate={rate_str}  scored={info.get('n_scored', 0)}")
    print(f"\nReport: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
