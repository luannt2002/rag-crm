"""Tests for P15-4 grounding check — citation (cheap) + LLM-as-judge.

Note: naming. The code has historically been called "NLI" but it's NOT a
true NLI entailment classifier — it's LLM-as-judge prompt-based checking.
See plans/260422-P15-next-level-roadmap/plan.md P15-4 for context.
"""

from __future__ import annotations

import pytest

from ragbot.application.dto.llm_schemas import (
    GroundingVerdict,
    GroundingVerdictsOutput,
)
from ragbot.infrastructure.guardrails.local_guardrail import OutputGuardrail


class TestCitationGroundingCheck:
    """grounding_check: passes only if answer contains at least one [chunk:...] marker."""

    def test_no_retrieved_chunks_returns_none(self):
        assert OutputGuardrail.grounding_check("any answer", retrieved_chunks=[]) is None
        assert OutputGuardrail.grounding_check("any answer", retrieved_chunks=None) is None

    def test_answer_with_citation_passes(self):
        # _CITATION_MARKER_RE matches [alnum_-]{1,64}, so bracket format is [chunk_id]
        hit = OutputGuardrail.grounding_check(
            "The price is 100k [abc-123-def].",
            retrieved_chunks=[{"id": "abc-123-def"}],
        )
        assert hit is None

    def test_answer_without_citation_flagged(self):
        hit = OutputGuardrail.grounding_check(
            "The price is 100k.",
            retrieved_chunks=[{"id": "abc-123"}],
        )
        assert hit is not None
        assert hit.rule_id == "grounding_fail"
        assert hit.severity == "warn"
        assert hit.details["retrieved_count"] == 1

    def test_empty_answer_with_chunks_flagged(self):
        hit = OutputGuardrail.grounding_check("", retrieved_chunks=[{"id": "x"}])
        assert hit is not None
        assert hit.rule_id == "grounding_fail"


class TestLLMGroundingJudge:
    """llm_grounding_check: LLM-as-judge — NOT a real NLI classifier."""

    @pytest.mark.asyncio
    async def test_no_answer_returns_none(self):
        async def fake_llm(messages):
            raise AssertionError("should not be called for empty answer")

        hit = await OutputGuardrail.llm_grounding_check(
            "", context_chunks=[{"text": "ctx"}], llm_complete_fn=fake_llm,
        )
        assert hit is None

    @pytest.mark.asyncio
    async def test_no_context_returns_none(self):
        async def fake_llm(messages):
            raise AssertionError("should not be called for empty context")

        hit = await OutputGuardrail.llm_grounding_check(
            "answer", context_chunks=[], llm_complete_fn=fake_llm,
        )
        assert hit is None

    @pytest.mark.asyncio
    async def test_all_supported_no_hit(self):
        """LLM says every sentence is supported → no hit."""
        async def fake_llm(messages):
            return {"text": "1. SUPPORTED\n2. SUPPORTED\n3. SUPPORTED"}

        hit = await OutputGuardrail.llm_grounding_check(
            "S1. S2. S3.",
            context_chunks=[{"text": "ctx"}],
            llm_complete_fn=fake_llm,
            threshold=0.5,
        )
        assert hit is None

    @pytest.mark.asyncio
    async def test_majority_unsupported_produces_hit(self):
        """>threshold fraction unsupported → warn hit."""
        async def fake_llm(messages):
            return {"text": "1. NOT_SUPPORTED\n2. NOT_SUPPORTED\n3. NOT_SUPPORTED"}

        hit = await OutputGuardrail.llm_grounding_check(
            "S1. S2. S3.",
            context_chunks=[{"text": "ctx"}],
            llm_complete_fn=fake_llm,
            threshold=0.5,
        )
        assert hit is not None
        assert hit.rule_id == "llm_grounding_fail"
        assert hit.severity == "warn"
        assert hit.details["ratio"] == 1.0

    @pytest.mark.asyncio
    async def test_below_threshold_no_hit(self):
        """Only 1 of 3 unsupported (33%) ≤ threshold 50% → no hit.

        Guard uses strict `ratio > threshold`, so 0.33 vs 0.5 → no hit.
        """
        async def fake_llm(messages):
            return {"text": "1. SUPPORTED\n2. SUPPORTED\n3. NOT_SUPPORTED"}

        hit = await OutputGuardrail.llm_grounding_check(
            "S1. S2. S3.",
            context_chunks=[{"text": "ctx"}],
            llm_complete_fn=fake_llm,
            threshold=0.5,
        )
        assert hit is None

    @pytest.mark.asyncio
    async def test_unparseable_response_skipped(self):
        """LLM returns garbled response we can't parse → no penalty, no hit."""
        async def fake_llm(messages):
            return {"text": "the answer is supported i think maybe"}

        hit = await OutputGuardrail.llm_grounding_check(
            "S1. S2.",
            context_chunks=[{"text": "ctx"}],
            llm_complete_fn=fake_llm,
            threshold=0.5,
        )
        assert hit is None

    @pytest.mark.asyncio
    async def test_llm_error_is_tolerated(self):
        """Judge LLM failure must not crash the pipeline."""
        async def broken_llm(messages):
            raise RuntimeError("LLM timeout")

        # Should not raise; returns None silently
        hit = await OutputGuardrail.llm_grounding_check(
            "answer sentence.",
            context_chunks=[{"text": "ctx"}],
            llm_complete_fn=broken_llm,
        )
        assert hit is None

    @pytest.mark.asyncio
    async def test_respects_max_sentences_cap(self):
        """Only first N sentences sent to LLM (cost cap)."""
        call_log: list[list] = []

        async def fake_llm(messages):
            call_log.append(messages)
            return {"text": "1. SUPPORTED\n2. SUPPORTED\n3. SUPPORTED"}

        answer = "S1. S2. S3. S4. S5. S6. S7. S8."
        await OutputGuardrail.llm_grounding_check(
            answer,
            context_chunks=[{"text": "ctx"}],
            llm_complete_fn=fake_llm,
            max_sentences=3,
            use_structured=False,
        )
        # LLM called exactly once with a prompt; the prompt should only
        # reference the first 3 sentences
        assert len(call_log) == 1
        joined = "\n".join(str(m) for m in call_log[0])
        assert "S1" in joined and "S2" in joined and "S3" in joined
        # Sentences past the cap must NOT appear
        assert "S5" not in joined


class TestStructuredGroundingJudge:
    """Structured-output path of llm_grounding_check (Pydantic schema)."""

    @pytest.mark.asyncio
    async def test_structured_majority_unsupported_produces_hit(self):
        """Structured judge — most claims NOT_SUPPORTED triggers warn hit."""

        async def fake_structured(messages, schema):
            return GroundingVerdictsOutput(
                verdicts=[
                    GroundingVerdict(claim_index=0, verdict="NOT_SUPPORTED"),
                    GroundingVerdict(claim_index=1, verdict="NOT_SUPPORTED"),
                    GroundingVerdict(claim_index=2, verdict="SUPPORTED"),
                ]
            )

        hit = await OutputGuardrail.llm_grounding_check(
            "S1. S2. S3.",
            context_chunks=[{"text": "ctx"}],
            structured_judge_fn=fake_structured,
            threshold=0.5,
            use_structured=True,
        )
        assert hit is not None
        assert hit.rule_id == "llm_grounding_fail"
        assert hit.details["path"] == "structured"
        assert hit.details["checked"] == 3
        assert hit.details["unsupported"] == 2

    @pytest.mark.asyncio
    async def test_structured_all_supported_no_hit(self):
        async def fake_structured(messages, schema):
            return GroundingVerdictsOutput(
                verdicts=[
                    GroundingVerdict(claim_index=0, verdict="SUPPORTED"),
                    GroundingVerdict(claim_index=1, verdict="SUPPORTED"),
                ]
            )

        hit = await OutputGuardrail.llm_grounding_check(
            "S1. S2.",
            context_chunks=[{"text": "ctx"}],
            structured_judge_fn=fake_structured,
            threshold=0.5,
            use_structured=True,
        )
        assert hit is None

    @pytest.mark.asyncio
    async def test_structured_parse_failure_falls_through(self):
        """Structured fn returning None → no hit, no crash (don't penalise)."""

        async def fake_structured(messages, schema):
            return None  # provider failure / schema validation failed

        hit = await OutputGuardrail.llm_grounding_check(
            "S1. S2.",
            context_chunks=[{"text": "ctx"}],
            structured_judge_fn=fake_structured,
            threshold=0.5,
            use_structured=True,
        )
        assert hit is None

    @pytest.mark.asyncio
    async def test_structured_ignores_out_of_range_indices(self):
        """Verdicts referencing nonexistent claim_index are dropped."""

        async def fake_structured(messages, schema):
            return GroundingVerdictsOutput(
                verdicts=[
                    GroundingVerdict(claim_index=99, verdict="NOT_SUPPORTED"),
                    GroundingVerdict(claim_index=0, verdict="SUPPORTED"),
                ]
            )

        hit = await OutputGuardrail.llm_grounding_check(
            "S1.",
            context_chunks=[{"text": "ctx"}],
            structured_judge_fn=fake_structured,
            threshold=0.5,
            use_structured=True,
        )
        assert hit is None  # only claim 0 was valid + supported

    @pytest.mark.asyncio
    async def test_use_structured_false_uses_backcompat_text_parser(self):
        """When use_structured=False, structured_judge_fn is bypassed."""
        structured_called = False

        async def fake_structured(messages, schema):
            nonlocal structured_called
            structured_called = True
            return None

        async def fake_text(messages):
            return {"text": "0. SUPPORTED"}

        hit = await OutputGuardrail.llm_grounding_check(
            "S1.",
            context_chunks=[{"text": "ctx"}],
            llm_complete_fn=fake_text,
            structured_judge_fn=fake_structured,
            threshold=0.5,
            use_structured=False,
        )
        assert structured_called is False
        assert hit is None  # supported

    @pytest.mark.asyncio
    async def test_backcompat_path_handles_zero_based_indices(self):
        """Updated prompt uses 0-based indexing — text parser must handle both."""

        async def fake_text(messages):
            return {"text": "0. NOT_SUPPORTED\n1. NOT_SUPPORTED\n2. NOT_SUPPORTED"}

        hit = await OutputGuardrail.llm_grounding_check(
            "S1. S2. S3.",
            context_chunks=[{"text": "ctx"}],
            llm_complete_fn=fake_text,
            threshold=0.5,
            use_structured=False,
        )
        assert hit is not None
        assert hit.details["unsupported"] == 3

    @pytest.mark.asyncio
    async def test_no_judge_fn_returns_none(self):
        """Both fns None → cannot evaluate → return None silently."""
        hit = await OutputGuardrail.llm_grounding_check(
            "S1.",
            context_chunks=[{"text": "ctx"}],
            llm_complete_fn=None,
            structured_judge_fn=None,
        )
        assert hit is None
