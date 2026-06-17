#!/usr/bin/env python3
"""RAGAS auto evaluation CLI — replays a golden dataset through a running
ragbot deployment and scores the 4 canonical RAGAS metrics.

Pipeline:
    dataset JSON  ──►  /api/ragbot/test/chat  ──►  (answer + contexts)
                                                        │
                                          ragas.evaluate│
                                                        ▼
                                  {faithfulness, answer_relevancy,
                                   context_precision, context_recall}
                                                        │
                                                        ▼
                                  reports/eval_ragas_<bot>_<TS>.json

This is the Wave A foundation eval (T2-CostPerf) — it verifies that
T1-Smartness ships actually move faithfulness / relevancy in the right
direction. The script never touches the live chat hot path; it is an
offline measurement tool.

Dataset shape (JSON array):
    [
      {
        "question": "...",
        "ground_truth_answer": "...",
        "ground_truth_contexts": ["...", "..."]
      },
      ...
    ]

CLI:
    python scripts/eval_ragas.py --bot <slug> --dataset <path> [--output <path>]

Exit codes:
    0 — faithfulness >= DEFAULT_RAGAS_FAITHFULNESS_GATE AND
        answer_relevancy >= DEFAULT_RAGAS_RELEVANCY_GATE
    1 — at least one gate failed OR IO/transport error
    2 — argparse usage error (argparse handles it)

The 4-key bot identity is built from CLI ``--bot`` (bot_id) plus
``--channel-type`` (default ``web``) plus ``--workspace-id`` (default falls
back to the resolved tenant slug at the server side). ``record_tenant_id``
is carried by the bearer token issued by ``/api/ragbot/test/tokens/self``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Protocol

import httpx

# Repo-root import path (mirror loadtest_90q_multi_bot.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_RAGAS_FAITHFULNESS_GATE,
    DEFAULT_RAGAS_RELEVANCY_GATE,
    RAGBOT_LOADTEST_BYPASS_ENV,
    RAGBOT_LOADTEST_BYPASS_HEADER,
)

EXIT_OK: Final[int] = 0
EXIT_FAIL: Final[int] = 1

# Default channel — protocol literal, mirrors existing loadtest scripts
# (loadtest_90q_multi_bot.py uses the same "web" default). CLI overridable
# via --channel-type.
DEFAULT_CHANNEL_TYPE_WEB: Final[str] = "web"

# RAGAS metric keys — kept as constants so test fixtures + report consumers
# share the same vocabulary.
METRIC_FAITHFULNESS: Final[str] = "faithfulness"
METRIC_ANSWER_RELEVANCY: Final[str] = "answer_relevancy"
METRIC_CONTEXT_PRECISION: Final[str] = "context_precision"
METRIC_CONTEXT_RECALL: Final[str] = "context_recall"
ALL_METRICS: Final[tuple[str, ...]] = (
    METRIC_FAITHFULNESS,
    METRIC_ANSWER_RELEVANCY,
    METRIC_CONTEXT_PRECISION,
    METRIC_CONTEXT_RECALL,
)

# Default chat endpoint timeout. Higher than synchronous LLM call budget
# because some bots hit reranker + CRAG grader before answering.
DEFAULT_CHAT_TIMEOUT_S: Final[float] = 120.0


class MetricScorer(Protocol):
    """Port for the RAGAS metric layer (mocked in unit tests).

    A scorer takes a list of evaluation rows and returns a dict mapping
    each of ``ALL_METRICS`` to a mean float in [0.0, 1.0].
    """

    def score(self, rows: list[dict[str, Any]]) -> dict[str, float]:
        ...


class _RagasScorer:
    """Real RAGAS scorer — lazy-imports ``ragas`` so the package is only
    required when the script is invoked end-to-end (not at test time)."""

    def score(self, rows: list[dict[str, Any]]) -> dict[str, float]:
        if not rows:
            return dict.fromkeys(ALL_METRICS, 0.0)
        # Lazy import — keeps unit tests free of the ragas dep.
        from datasets import Dataset  # type: ignore[import-not-found]
        from ragas import evaluate  # type: ignore[import-not-found]
        from ragas.metrics import (  # type: ignore[import-not-found]
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )

        # RAGAS expects: question, answer, contexts (list[str]), ground_truth.
        ds = Dataset.from_list(
            [
                {
                    "question": r["question"],
                    "answer": r["answer"],
                    "contexts": r["contexts"],
                    "ground_truth": r["ground_truth"],
                }
                for r in rows
            ]
        )
        result = evaluate(
            ds,
            metrics=[
                faithfulness,
                answer_relevancy,
                context_precision,
                context_recall,
            ],
        )
        # ragas returns a Result object; ``.to_pandas()`` exposes per-row,
        # whereas dict-style access exposes the aggregated mean.
        return {key: float(result[key]) for key in ALL_METRICS}


def load_dataset(path: Path) -> list[dict[str, Any]]:
    """Load the JSON dataset and validate the per-item shape.

    Returns the parsed list. Raises ``ValueError`` on any schema breach so
    we fail loud rather than silently scoring an empty dataset.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: top-level JSON must be a list")
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{path}[{idx}]: not a JSON object")
        for key in ("question", "ground_truth_answer", "ground_truth_contexts"):
            if key not in item:
                raise ValueError(f"{path}[{idx}]: missing field {key!r}")
        if not isinstance(item["question"], str) or not item["question"].strip():
            raise ValueError(f"{path}[{idx}]: question must be non-empty string")
        if not isinstance(item["ground_truth_answer"], str):
            raise ValueError(f"{path}[{idx}]: ground_truth_answer must be string")
        ctxs = item["ground_truth_contexts"]
        if not isinstance(ctxs, list) or not all(isinstance(c, str) for c in ctxs):
            raise ValueError(
                f"{path}[{idx}]: ground_truth_contexts must be list[str]"
            )
        out.append(item)
    return out


def _bypass_headers() -> dict[str, str]:
    """Operator-only loadtest bypass header — empty dict when token absent."""
    token = os.environ.get(RAGBOT_LOADTEST_BYPASS_ENV, "")
    if not token:
        return {}
    return {RAGBOT_LOADTEST_BYPASS_HEADER: token}


async def _fetch_self_token(client: httpx.AsyncClient, base_url: str) -> str:
    """Fetch a self-issued JWT for the test caller."""
    resp = await client.get(
        f"{base_url}/api/ragbot/test/tokens/self",
        headers=_bypass_headers(),
    )
    resp.raise_for_status()
    return str(resp.json()["token"])


async def _ask_one(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    *,
    bot_id: str,
    workspace_id: str | None,
    channel_type: str,
    question: str,
    connect_id: str,
) -> dict[str, Any]:
    """POST /api/ragbot/test/chat. Returns ``{answer, contexts, latency_ms}``
    on success, ``{error, latency_ms}`` on transport failure (we never
    raise — the caller decides how to score the empty answer)."""
    body: dict[str, Any] = {
        "bot_id": bot_id,
        "channel_type": channel_type,
        "question": question,
        "connect_id": connect_id,
        "bypass_cache": True,
    }
    if workspace_id is not None:
        body["workspace_id"] = workspace_id
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        **_bypass_headers(),
    }
    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{base_url}/api/ragbot/test/chat",
            headers=headers,
            json=body,
            timeout=DEFAULT_CHAT_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        # Pull retrieved chunk text from whichever field the chat endpoint
        # exposes. ``retrieved_chunks_content`` is the canonical name; some
        # responses also surface ``citations`` (list of {chunk_id, text}).
        contexts: list[str] = []
        rcc = data.get("retrieved_chunks_content")
        if isinstance(rcc, list):
            contexts = [c for c in rcc if isinstance(c, str)]
        if not contexts:
            cits = data.get("citations")
            if isinstance(cits, list):
                contexts = [
                    c["text"]
                    for c in cits
                    if isinstance(c, dict) and isinstance(c.get("text"), str)
                ]
        return {
            "answer": str(data.get("answer", "")),
            "contexts": contexts,
            "latency_ms": round((time.perf_counter() - t0) * 1000),
        }
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return {
            "error": f"{type(exc).__name__}: {exc}",
            "answer": "",
            "contexts": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000),
        }


async def collect_predictions(
    dataset: list[dict[str, Any]],
    *,
    base_url: str,
    bot_id: str,
    workspace_id: str | None,
    channel_type: str,
) -> list[dict[str, Any]]:
    """Replay each dataset item through the chat API and return the rows
    ready for the RAGAS scorer (``question / answer / contexts /
    ground_truth``). Failed turns surface as rows with empty ``answer`` so
    RAGAS scores them as 0 — never silently dropped."""
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        token = await _fetch_self_token(client, base_url)
        for idx, item in enumerate(dataset):
            resp = await _ask_one(
                client,
                base_url,
                token,
                bot_id=bot_id,
                workspace_id=workspace_id,
                channel_type=channel_type,
                question=item["question"],
                connect_id=f"ragas-eval-{idx}",
            )
            rows.append(
                {
                    "question": item["question"],
                    "answer": resp.get("answer", ""),
                    "contexts": resp.get("contexts") or item["ground_truth_contexts"],
                    "ground_truth": item["ground_truth_answer"],
                    "latency_ms": resp.get("latency_ms", 0),
                    "error": resp.get("error"),
                }
            )
    return rows


def write_report(
    *,
    output_path: Path,
    bot_id: str,
    dataset_path: Path,
    rows: list[dict[str, Any]],
    metrics: dict[str, float],
    gates: dict[str, float],
    pass_gates: bool,
) -> None:
    """Persist the eval report JSON. ``rows`` is included so re-scoring
    later (e.g. with a different ragas version) does not require another
    chat replay."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "bot_id": bot_id,
        "dataset": str(dataset_path),
        "n_rows": len(rows),
        "metrics": metrics,
        "gates": gates,
        "pass_gates": pass_gates,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def evaluate_gates(
    metrics: dict[str, float],
    *,
    faithfulness_gate: float,
    relevancy_gate: float,
) -> bool:
    """Return True iff faithfulness AND answer_relevancy clear their gates.

    Context-precision / context-recall are reported but not gated — they
    diagnose retrieval quality, not answer correctness, and tend to flap
    on per-bot corpus differences.
    """
    if metrics.get(METRIC_FAITHFULNESS, 0.0) < faithfulness_gate:
        return False
    if metrics.get(METRIC_ANSWER_RELEVANCY, 0.0) < relevancy_gate:
        return False
    return True


def default_output_path(bot_id: str) -> Path:
    """``reports/eval_ragas_<bot>_<UTC-timestamp>.json``."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("reports") / f"eval_ragas_{bot_id}_{ts}.json"


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser (exposed for unit tests)."""
    parser = argparse.ArgumentParser(
        prog="eval_ragas",
        description=(
            "Replay a golden dataset through ragbot and compute the 4 "
            "RAGAS metrics (faithfulness, answer_relevancy, "
            "context_precision, context_recall)."
        ),
    )
    parser.add_argument(
        "--bot",
        required=True,
        help="Bot slug (external bot_id). E.g. 'bot-legal-pilot'.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="Path to the JSON dataset file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON report path. Default reports/eval_ragas_<bot>_<TS>.json.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("RAGBOT_BASE_URL", "http://localhost:3004"),
        help="Ragbot HTTP base URL (default $RAGBOT_BASE_URL).",
    )
    parser.add_argument(
        "--workspace-id",
        default=None,
        help=(
            "Workspace slug (optional). When omitted, server falls back to "
            "str(record_tenant_id)."
        ),
    )
    parser.add_argument(
        "--channel-type",
        default=DEFAULT_CHANNEL_TYPE_WEB,
        help=f"Channel type (default {DEFAULT_CHANNEL_TYPE_WEB!r}).",
    )
    parser.add_argument(
        "--faithfulness-gate",
        type=float,
        default=DEFAULT_RAGAS_FAITHFULNESS_GATE,
    )
    parser.add_argument(
        "--relevancy-gate",
        type=float,
        default=DEFAULT_RAGAS_RELEVANCY_GATE,
    )
    return parser


async def run(
    args: argparse.Namespace,
    *,
    scorer: MetricScorer | None = None,
    predictions: list[dict[str, Any]] | None = None,
) -> int:
    """Async entry. ``scorer`` + ``predictions`` are injected for tests."""
    try:
        dataset = load_dataset(args.dataset)
    except (OSError, ValueError) as exc:
        print(f"ERROR: dataset load failed: {exc}", file=sys.stderr)
        return EXIT_FAIL

    if not dataset:
        print("ERROR: dataset is empty", file=sys.stderr)
        return EXIT_FAIL

    # Predictions are either supplied by the test, or fetched live.
    if predictions is None:
        try:
            rows = await collect_predictions(
                dataset,
                base_url=args.base_url,
                bot_id=args.bot,
                workspace_id=args.workspace_id,
                channel_type=args.channel_type,
            )
        except httpx.HTTPError as exc:
            print(f"ERROR: chat API unreachable: {exc}", file=sys.stderr)
            return EXIT_FAIL
    else:
        rows = predictions

    metric_scorer: MetricScorer = scorer if scorer is not None else _RagasScorer()
    metrics = metric_scorer.score(rows)
    # Coerce / clamp to [0,1] so a buggy scorer never poisons the gate.
    metrics = {key: max(0.0, min(1.0, float(metrics.get(key, 0.0)))) for key in ALL_METRICS}

    gates = {
        METRIC_FAITHFULNESS: args.faithfulness_gate,
        METRIC_ANSWER_RELEVANCY: args.relevancy_gate,
    }
    pass_gates = evaluate_gates(
        metrics,
        faithfulness_gate=args.faithfulness_gate,
        relevancy_gate=args.relevancy_gate,
    )

    output_path = args.output if args.output is not None else default_output_path(args.bot)
    write_report(
        output_path=output_path,
        bot_id=args.bot,
        dataset_path=args.dataset,
        rows=rows,
        metrics=metrics,
        gates=gates,
        pass_gates=pass_gates,
    )

    # Stdout summary (consumed by CI workflow PR comment step).
    print(f"bot={args.bot} n={len(rows)}")
    for key in ALL_METRICS:
        print(f"  {key}={metrics[key]:.4f}")
    print(f"pass_gates={pass_gates} report={output_path}")

    return EXIT_OK if pass_gates else EXIT_FAIL


def main(argv: list[str] | None = None) -> int:
    """CLI entry. ``argv`` overrides ``sys.argv[1:]`` for tests."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
