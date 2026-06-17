"""RAGAS-style metrics — faithfulness / answer_relevance / context_precision.

Pure post-hoc evaluator: reads a bot transcript (question, answer, retrieved
chunks) and emits 3 quality scores. Domain-neutral, swappable via Protocol.
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ragbot.shared.constants import (
    DEFAULT_RAGAS_EMBED_MODEL,
    DEFAULT_RAGAS_JUDGE_MODEL,
    DEFAULT_RAGAS_MAX_CONCURRENCY,
    DEFAULT_RAGAS_REVERSE_QUESTIONS_N,
)


@dataclass(frozen=True, slots=True)
class TurnInput:
    """One turn payload to evaluate. ``retrieved_chunks`` are the chunk texts
    actually shown to the answer-LLM (post-rerank, not raw retrieval)."""

    question: str
    answer: str
    retrieved_chunks: tuple[str, ...]
    citations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TurnScore:
    """Per-turn RAGAS scores. All in [0.0, 1.0]; 0.0 = unscoreable / fully
    bad, 1.0 = perfect on that axis."""

    faithfulness: float
    answer_relevance: float
    context_precision: float
    n_claims: int = 0
    n_reverse_qs: int = 0
    n_chunks: int = 0
    judge_calls: int = 0
    embed_calls: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class MetricEvaluator(Protocol):
    """Port for swappable metric evaluators (LLM-as-judge, embedding-only,
    NLI-based, …). One method ``score_turn`` returning ``TurnScore``."""

    async def score_turn(self, turn: TurnInput) -> TurnScore: ...


# ---------------------------------------------------------------------------
# Pydantic schemas for LLM-as-judge structured output
# ---------------------------------------------------------------------------


class _ClaimList(BaseModel):
    """Atomic claims extracted from the answer."""

    claims: list[str] = Field(default_factory=list)


class _ClaimVerdict(BaseModel):
    """Per-claim grounding verdict."""

    grounded: bool


class _RelevanceQuestions(BaseModel):
    """Reverse questions generated from the answer (RAGAS answer-relevance)."""

    questions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Default LLM-as-judge implementation
# ---------------------------------------------------------------------------


_CLAIM_EXTRACTION_PROMPT = (
    "You are an extraction tool. Given an answer text, decompose it into a list "
    "of atomic factual claims. Each claim must be a single statement that can be "
    "independently verified. Skip greetings, fillers, sales upsell, and questions "
    "back to the user. If there are no factual claims, return an empty list. "
    'Output ONLY JSON of shape: {{"claims": ["claim 1", "claim 2"]}}.\n\n'
    "ANSWER:\n{answer}"
)

_CLAIM_VERIFY_PROMPT = (
    "You are a fact-checker. Given a CLAIM and a CONTEXT (a set of source chunks), "
    "return whether the CONTEXT supports the CLAIM. Be strict: paraphrase is OK, "
    "but invented facts (numbers, names, services) not present in CONTEXT are NOT "
    'grounded. Output ONLY JSON: {{"grounded": true}} or {{"grounded": false}}.\n\n'
    "CLAIM:\n{claim}\n\nCONTEXT:\n{context}"
)

_REVERSE_Q_PROMPT = (
    "Given the ANSWER below, generate {n} short candidate questions that this "
    "answer would correctly resolve. Use the same language as the answer. "
    'Output ONLY JSON: {{"questions": ["q1", "q2"]}}.\n\nANSWER:\n{answer}'
)

_CONTEXT_RELEVANCE_PROMPT = (
    "You are a relevance judge. Given a USER QUESTION and one CONTEXT chunk, "
    "decide whether this chunk is relevant to answering the question (even "
    'partially). Output ONLY JSON: {{"grounded": true}} or {{"grounded": false}}.\n\n'
    "QUESTION:\n{question}\n\nCONTEXT:\n{context}"
)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine in [-1, 1]. RAGAS clips negatives to 0 (no negative relevance)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    sim = dot / (na * nb)
    if sim < 0.0:
        return 0.0
    if sim > 1.0:
        return 1.0
    return sim


def _safe_json_loads(text: str) -> dict[str, Any] | None:
    """Parse JSON object from LLM output; tolerate fenced code blocks."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        # strip ```json ... ``` fence
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    try:
        loaded = json.loads(s)
    except (ValueError, json.JSONDecodeError):
        # try to slice {...} substring
        i, j = s.find("{"), s.rfind("}")
        if 0 <= i < j:
            try:
                loaded = json.loads(s[i : j + 1])
            except (ValueError, json.JSONDecodeError):
                return None
        else:
            return None
    return loaded if isinstance(loaded, dict) else None


class LLMRagasEvaluator:
    """Default RAGAS metric evaluator using LLM-as-judge + embedding similarity.

    Implementation detail: dispatches to ``litellm.acompletion`` and
    ``litellm.aembedding``. Provider name is encoded in the model strings
    (e.g. ``openai/gpt-4.1-mini``) — no explicit if/elif provider branching
    here, registry lives in litellm itself.
    """

    def __init__(
        self,
        *,
        judge_model: str = DEFAULT_RAGAS_JUDGE_MODEL,
        embed_model: str = DEFAULT_RAGAS_EMBED_MODEL,
        max_concurrency: int = DEFAULT_RAGAS_MAX_CONCURRENCY,
        reverse_questions_n: int = DEFAULT_RAGAS_REVERSE_QUESTIONS_N,
        completion_fn: Any | None = None,
        embedding_fn: Any | None = None,
    ) -> None:
        self._judge_model = judge_model
        self._embed_model = embed_model
        self._sem = asyncio.Semaphore(max_concurrency)
        self._reverse_n = reverse_questions_n
        # Injection seams for unit tests; production = litellm by default.
        self._completion_fn = completion_fn
        self._embedding_fn = embedding_fn
        self._judge_calls = 0
        self._embed_calls = 0

    # --- low-level LLM helpers ------------------------------------------------

    async def _judge(self, prompt: str) -> dict[str, Any] | None:
        self._judge_calls += 1
        if self._completion_fn is not None:
            resp = await self._completion_fn(
                model=self._judge_model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
        else:
            import litellm  # local import keeps unit tests fast

            resp = await litellm.acompletion(
                model=self._judge_model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
        # litellm response shape: choices[0].message.content
        try:
            content = resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = getattr(resp.choices[0].message, "content", "")  # type: ignore[index]
        return _safe_json_loads(content or "")

    async def _embed(self, text: str) -> list[float]:
        self._embed_calls += 1
        if self._embedding_fn is not None:
            resp = await self._embedding_fn(model=self._embed_model, input=[text])
        else:
            import litellm

            resp = await litellm.aembedding(model=self._embed_model, input=[text])
        try:
            return list(resp["data"][0]["embedding"])
        except (KeyError, IndexError, TypeError):
            return list(resp.data[0]["embedding"])  # type: ignore[index]

    # --- step primitives ------------------------------------------------------

    async def _extract_claims(self, answer: str) -> list[str]:
        prompt = _CLAIM_EXTRACTION_PROMPT.format(answer=answer)
        parsed = await self._judge(prompt)
        if not parsed:
            return []
        try:
            cl = _ClaimList.model_validate(parsed)
        except (ValueError, TypeError):
            return []
        return [c.strip() for c in cl.claims if c and c.strip()]

    async def _verify_claim(self, claim: str, chunks: tuple[str, ...]) -> bool:
        if not chunks:
            return False
        ctx = "\n\n---\n\n".join(chunks)
        parsed = await self._judge(
            _CLAIM_VERIFY_PROMPT.format(claim=claim, context=ctx)
        )
        if not parsed:
            return False
        try:
            v = _ClaimVerdict.model_validate(parsed)
        except (ValueError, TypeError):
            return False
        return bool(v.grounded)

    async def _generate_reverse_questions(self, answer: str) -> list[str]:
        prompt = _REVERSE_Q_PROMPT.format(n=self._reverse_n, answer=answer)
        parsed = await self._judge(prompt)
        if not parsed:
            return []
        try:
            rq = _RelevanceQuestions.model_validate(parsed)
        except (ValueError, TypeError):
            return []
        return [q.strip() for q in rq.questions if q and q.strip()][: self._reverse_n]

    async def _embedding_similarity(self, q1: str, q2: str) -> float:
        e1, e2 = await asyncio.gather(self._embed(q1), self._embed(q2))
        return _cosine_similarity(e1, e2)

    async def _judge_context_relevance(self, question: str, chunk: str) -> bool:
        parsed = await self._judge(
            _CONTEXT_RELEVANCE_PROMPT.format(question=question, context=chunk)
        )
        if not parsed:
            return False
        try:
            v = _ClaimVerdict.model_validate(parsed)
        except (ValueError, TypeError):
            return False
        return bool(v.grounded)

    # --- public RAGAS metrics -------------------------------------------------

    async def score_faithfulness(self, turn: TurnInput) -> tuple[float, int]:
        if not turn.answer.strip() or not turn.retrieved_chunks:
            return 0.0, 0
        claims = await self._extract_claims(turn.answer)
        if not claims:
            # No factual claims (greetings / clarifying question) — treat as
            # vacuously faithful so a clean refuse doesn't drag the mean down.
            return 1.0, 0
        verdicts = await asyncio.gather(
            *[self._verify_claim(c, turn.retrieved_chunks) for c in claims]
        )
        score = sum(1.0 for v in verdicts if v) / len(verdicts)
        return score, len(claims)

    async def score_answer_relevance(self, turn: TurnInput) -> tuple[float, int]:
        if not turn.answer.strip() or not turn.question.strip():
            return 0.0, 0
        reverse_qs = await self._generate_reverse_questions(turn.answer)
        if not reverse_qs:
            return 0.0, 0
        sims = await asyncio.gather(
            *[self._embedding_similarity(turn.question, rq) for rq in reverse_qs]
        )
        if not sims:
            return 0.0, 0
        return sum(sims) / len(sims), len(reverse_qs)

    async def score_context_precision(self, turn: TurnInput) -> tuple[float, int]:
        """RAGAS Average Precision @ K — rank-sensitive precision metric.

        Reference: RAGAS paper §3.3 (Es et al. 2024,
        https://arxiv.org/abs/2309.15217).

        Formula:
            AP@K = (1 / n_relevant) × Σ_{k: rel(k)=1} P@k
        where P@k = (n_relevant chunks at positions 1..k) / k.

        Why this matters: previous implementation used flat
        fraction_relevant = n_relevant / n_total, which under-weighted
        top-ranked correct chunks. Example: 1 relevant chunk at rank 1
        of 20 returned chunks gave the old formula 1/20 = 0.05, while
        the standard AP@K gives 1.0 (perfect top-1 retrieval).

        The flat fraction also penalises broad retrieval (large k)
        unfairly. With AP@K, a system that places the correct chunk at
        rank 1 scores the same regardless of how many irrelevant chunks
        follow — matching the user-facing reality that the answer LLM
        primarily reads the top chunks.
        """
        if not turn.retrieved_chunks or not turn.question.strip():
            return 0.0, 0
        verdicts = await asyncio.gather(
            *[
                self._judge_context_relevance(turn.question, ch)
                for ch in turn.retrieved_chunks
            ]
        )
        n = len(verdicts)
        n_relevant = sum(1 for v in verdicts if v)
        if n_relevant == 0:
            return 0.0, n
        precision_at_k_sum = 0.0
        n_relevant_so_far = 0
        for k, v in enumerate(verdicts, start=1):
            if v:
                n_relevant_so_far += 1
                precision_at_k_sum += n_relevant_so_far / k
        return precision_at_k_sum / n_relevant, n

    async def score_turn(self, turn: TurnInput) -> TurnScore:
        async with self._sem:
            judge_before = self._judge_calls
            embed_before = self._embed_calls
            (faith, n_claims), (rel, n_rev), (prec, n_chunks) = await asyncio.gather(
                self.score_faithfulness(turn),
                self.score_answer_relevance(turn),
                self.score_context_precision(turn),
            )
        return TurnScore(
            faithfulness=faith,
            answer_relevance=rel,
            context_precision=prec,
            n_claims=n_claims,
            n_reverse_qs=n_rev,
            n_chunks=n_chunks,
            judge_calls=self._judge_calls - judge_before,
            embed_calls=self._embed_calls - embed_before,
        )


__all__ = [
    "TurnInput",
    "TurnScore",
    "MetricEvaluator",
    "LLMRagasEvaluator",
]
