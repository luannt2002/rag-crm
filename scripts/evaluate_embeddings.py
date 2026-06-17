#!/usr/bin/env python3
"""P15-9: Embedding model evaluation on a golden Q/A dataset.

Compares multiple embedding models on intrinsic retrieval quality without
needing to re-ingest the corpus. For each question in the golden set:

  1. Encode the question (q_vec) and its ground_truth answer (gt_vec).
  2. positive_sim = cos(q_vec, gt_vec)  — higher = model aligns Q with its answer.
  3. For each *other* question's ground_truth, compute negative_sim.
     mean_negative = mean(cos(q_vec, other_gt_vec)).
  4. contrast = positive_sim - mean_negative  — separation between true pair and noise.
  5. rank = position of the true ground_truth in cos-sorted list of all ground_truths.
     Recall@1 = 1 if rank == 0, Recall@5 = 1 if rank < 5.

Aggregates across questions: mean positive_sim, mean contrast, MRR, Recall@K.

Models tested:
  - All rows of `ai_models` with kind='embedding' and enabled=true (live providers via LiteLLM).
  - Optional local BGE-M3 via `sentence-transformers` (skipped cleanly if not installed).

Usage:
    python scripts/evaluate_embeddings.py \
        --golden golden_set/sample_evaluation.json \
        --output-dir reports/

Exit 0 always (informational). Local-only tool — no CI gating wired.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Make src/ importable when running as script
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from ragbot.shared.constants import DEFAULT_EVAL_RELEVANCE_THRESHOLD, DEFAULT_EVAL_TOP_K


def _cos(a: list[float], b: list[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


@dataclass
class ModelResult:
    model_name: str
    dim: int
    n_questions: int
    mean_positive_sim: float
    mean_contrast: float
    recall_at_1: float
    recall_at_5: float
    mrr: float
    latency_ms_mean: float
    errors: int
    per_question: list[dict] = field(default_factory=list)


# ---------- Encoder backends ------------------------------------------------


class EncoderError(RuntimeError):
    pass


class LiteLLMEncoder:
    """Uses LiteLLM acompletion -> aembedding for any provider registered in ai_models."""

    def __init__(self, model_id: str, provider_key_env: str | None = None):
        self.model_id = model_id
        self._provider_env = provider_key_env

    async def encode(self, texts: list[str]) -> tuple[list[list[float]], float]:
        try:
            import litellm  # type: ignore
        except ImportError as exc:
            raise EncoderError(f"litellm not installed: {exc}") from exc

        t0 = time.perf_counter()
        try:
            resp = await litellm.aembedding(model=self.model_id, input=texts)
        except Exception as exc:
            raise EncoderError(f"litellm aembedding failed: {exc}") from exc
        latency_ms = (time.perf_counter() - t0) * 1000

        vectors = [item["embedding"] for item in resp["data"]]
        return vectors, latency_ms


class BGEM3Encoder:
    """Local BGE-M3 via sentence-transformers. Optional dependency."""

    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self.model_name = model_name
        self._model = None

    def _ensure_loaded(self):
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise EncoderError(
                f"sentence-transformers not installed. Install via `pip install sentence-transformers` to evaluate BGE-M3: {exc}"
            ) from exc
        self._model = SentenceTransformer(self.model_name)

    async def encode(self, texts: list[str]) -> tuple[list[list[float]], float]:
        self._ensure_loaded()
        t0 = time.perf_counter()
        # sentence-transformers is sync; run in executor to not block the loop
        loop = asyncio.get_running_loop()
        vectors = await loop.run_in_executor(
            None, lambda: self._model.encode(texts, normalize_embeddings=True).tolist()
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        return vectors, latency_ms


# ---------- Evaluation core -------------------------------------------------


async def evaluate_model(
    model_name: str,
    encoder,
    questions: list[dict],
    top_k: int,
) -> ModelResult:
    queries = [q["question"] for q in questions]
    ground_truths = [q["ground_truth"] for q in questions]

    errors = 0
    latencies = []
    try:
        # Encode in two batches so we have clear q/gt slices
        q_vecs, q_lat = await encoder.encode(queries)
        gt_vecs, gt_lat = await encoder.encode(ground_truths)
        latencies = [q_lat, gt_lat]
    except EncoderError as exc:
        print(f"  ERROR encoding with {model_name}: {exc}")
        return ModelResult(
            model_name=model_name, dim=0, n_questions=len(questions),
            mean_positive_sim=0.0, mean_contrast=0.0,
            recall_at_1=0.0, recall_at_5=0.0, mrr=0.0,
            latency_ms_mean=0.0, errors=len(questions),
        )

    dim = len(q_vecs[0]) if q_vecs else 0

    # Per-question metrics
    positive_sims = []
    contrasts = []
    ranks = []
    per_question = []

    n = len(questions)
    for i in range(n):
        q = q_vecs[i]
        pos_sim = _cos(q, gt_vecs[i])

        # Rank true ground_truth against all ground_truths
        all_sims = [(_cos(q, gt_vecs[j]), j) for j in range(n)]
        all_sims.sort(reverse=True)
        rank = next(pos for pos, (_, j) in enumerate(all_sims) if j == i)

        # Mean similarity to OTHER ground_truths (noise floor)
        others = [s for s, j in all_sims if j != i]
        mean_neg = sum(others) / len(others) if others else 0.0
        contrast = pos_sim - mean_neg

        positive_sims.append(pos_sim)
        contrasts.append(contrast)
        ranks.append(rank)

        per_question.append({
            "id": questions[i].get("id"),
            "question": questions[i]["question"],
            "positive_sim": round(pos_sim, 4),
            "mean_negative_sim": round(mean_neg, 4),
            "contrast": round(contrast, 4),
            "rank": rank,
        })

    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    recall_1 = sum(1 for r in ranks if r == 0) / n
    recall_5 = sum(1 for r in ranks if r < 5) / n
    mrr = _mean([1.0 / (r + 1) for r in ranks])

    return ModelResult(
        model_name=model_name,
        dim=dim,
        n_questions=n,
        mean_positive_sim=round(_mean(positive_sims), 4),
        mean_contrast=round(_mean(contrasts), 4),
        recall_at_1=round(recall_1, 4),
        recall_at_5=round(recall_5, 4),
        mrr=round(mrr, 4),
        latency_ms_mean=round(_mean(latencies), 1),
        errors=errors,
        per_question=per_question,
    )


# ---------- Model discovery -------------------------------------------------


async def _discover_db_embedding_models() -> list[tuple[str, str]]:
    """Return [(display_name, litellm_model_id), ...] from ai_models table."""
    try:
        import asyncpg  # type: ignore
    except ImportError:
        print("  WARN: asyncpg not installed, skipping DB model discovery")
        return []

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DSN")
    if not dsn:
        print("  WARN: DATABASE_URL / POSTGRES_DSN not set, skipping DB discovery")
        return []

    # Strip SQLAlchemy driver suffix: "postgresql+asyncpg://..." -> "postgresql://..."
    if "+" in dsn.split("://", 1)[0]:
        scheme, rest = dsn.split("://", 1)
        dsn = scheme.split("+", 1)[0] + "://" + rest

    rows = []
    try:
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(
                """
                SELECT p.name AS provider, m.model_id, m.name
                FROM ai_models m JOIN ai_providers p ON p.id = m.record_provider_id
                WHERE m.kind = 'embedding' AND m.enabled = true
                """
            )
        finally:
            await conn.close()
    except Exception as exc:
        print(f"  WARN: DB discovery failed: {exc}")
        return []

    models = []
    for r in rows:
        provider = (r["provider"] or "").lower()
        model_id = r["model_id"]
        # LiteLLM format: "<provider>/<model>" for non-openai, bare id for openai
        if provider in ("openai", ""):
            litellm_id = model_id
        else:
            litellm_id = f"{provider}/{model_id}"
        models.append((r["name"], litellm_id))
    return models


# ---------- CLI -------------------------------------------------------------


async def _amain(args):
    golden_path = Path(args.golden)
    if not golden_path.exists():
        print(f"ERROR: golden file not found: {golden_path}")
        sys.exit(2)

    with open(golden_path) as f:
        golden = json.load(f)

    questions = [q for q in golden.get("questions", []) if q.get("question") and q.get("ground_truth")]
    if not questions:
        print("ERROR: golden file has no usable questions (need question + ground_truth)")
        sys.exit(2)

    if args.limit > 0:
        questions = questions[:args.limit]

    print(f"Loaded {len(questions)} questions from {golden_path.name} (domain={golden.get('domain', 'n/a')})")
    print()

    # Build candidate list
    candidates: list[tuple[str, object]] = []

    db_models = await _discover_db_embedding_models()
    for display_name, litellm_id in db_models:
        candidates.append((display_name, LiteLLMEncoder(litellm_id)))

    if args.include_bge_m3:
        candidates.append(("BGE-M3 (local)", BGEM3Encoder()))

    if args.extra_model:
        for m in args.extra_model:
            candidates.append((m, LiteLLMEncoder(m)))

    if not candidates:
        print("ERROR: no candidate models discovered. Provide --extra-model or --include-bge-m3.")
        sys.exit(2)

    print(f"Evaluating {len(candidates)} candidate(s): {[c[0] for c in candidates]}")
    print()

    results: list[ModelResult] = []
    for name, encoder in candidates:
        print(f"[{name}] encoding...")
        res = await evaluate_model(name, encoder, questions, top_k=args.top_k)
        results.append(res)
        print(
            f"  pos_sim={res.mean_positive_sim:.3f}  contrast={res.mean_contrast:.3f}  "
            f"R@1={res.recall_at_1:.2%}  R@5={res.recall_at_5:.2%}  MRR={res.mrr:.3f}  "
            f"dim={res.dim}  lat={res.latency_ms_mean:.0f}ms  errors={res.errors}"
        )

    # Rank by MRR
    results.sort(key=lambda r: r.mrr, reverse=True)

    print()
    print("=" * 80)
    print(f"{'RANK':<5} {'MODEL':<35} {'DIM':<6} {'POS':<7} {'CONT':<7} {'R@1':<7} {'R@5':<7} {'MRR':<7}")
    print("=" * 80)
    for i, r in enumerate(results):
        marker = "★" if i == 0 else " "
        print(
            f"{marker}{i+1:<4} {r.model_name[:34]:<35} {r.dim:<6} "
            f"{r.mean_positive_sim:<7.3f} {r.mean_contrast:<7.3f} "
            f"{r.recall_at_1:<7.2%} {r.recall_at_5:<7.2%} {r.mrr:<7.3f}"
        )
    print("=" * 80)

    # Persist report
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"embedding_eval_{stamp}.json"
    report = {
        "golden_set": str(golden_path),
        "golden_domain": golden.get("domain"),
        "n_questions": len(questions),
        "top_k": args.top_k,
        "relevance_threshold": DEFAULT_EVAL_RELEVANCE_THRESHOLD,
        "winner": results[0].model_name if results else None,
        "models": [
            {
                "model_name": r.model_name,
                "dim": r.dim,
                "n_questions": r.n_questions,
                "mean_positive_sim": r.mean_positive_sim,
                "mean_contrast": r.mean_contrast,
                "recall_at_1": r.recall_at_1,
                "recall_at_5": r.recall_at_5,
                "mrr": r.mrr,
                "latency_ms_mean": r.latency_ms_mean,
                "errors": r.errors,
                "per_question": r.per_question if args.verbose else [],
            }
            for r in results
        ],
    }
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nReport saved: {out_path}")


def main():
    p = argparse.ArgumentParser(description="P15-9 embedding model eval")
    p.add_argument("--golden", required=True, help="Path to golden dataset JSON (per-bot, domain-specific)")
    p.add_argument("--output-dir", default="reports/", help="Where to write the JSON report")
    p.add_argument("--top-k", type=int, default=DEFAULT_EVAL_TOP_K)
    p.add_argument("--limit", type=int, default=0, help="Limit questions (0 = all)")
    p.add_argument("--include-bge-m3", action="store_true", help="Also evaluate local BGE-M3 (needs sentence-transformers)")
    p.add_argument("--extra-model", action="append", help="Extra LiteLLM model id (repeatable)")
    p.add_argument("--verbose", action="store_true", help="Include per-question detail in report")
    args = p.parse_args()

    asyncio.run(_amain(args))


if __name__ == "__main__":
    random.seed(42)
    main()
