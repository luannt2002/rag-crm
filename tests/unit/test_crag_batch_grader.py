"""BatchCragGrader + PerChunkCragGrader — unit tests.

Pins:
- BatchCragGrader uses **exactly one** structured LLM call for N <= max_chunks.
- BatchCragGrader chunk_id → score mapping respects LLM verdicts.
- Graceful degradation: unparseable LLM → all 1.0 (keep chunks, defer
  to threshold gate).
- Graceful degradation: LLM transport raises → all 1.0.
- Oversize input (N > max_chunks) splits into multiple windows.
- PerChunkCragGrader issues one call per chunk.
- Empty input → empty output, zero LLM calls (both strategies).
- Tests use only generic placeholder strings — no industry / brand
  literals (CLAUDE.md domain-neutral guard).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ragbot.application.dto.llm_schemas import (
    ChunkGradeItem,
    GradeBatchOutput,
    GradeOutput,
)
from ragbot.application.services.crag_grader.batch_grader import BatchCragGrader
from ragbot.application.services.crag_grader.per_chunk_grader import (
    PerChunkCragGrader,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_chunks(n: int, *, prefix: str = "c") -> list[dict]:
    """Generate N domain-neutral chunks with stable ids."""
    return [
        {"chunk_id": f"{prefix}{i}", "content": f"alpha-{i} beta-{i} gamma-{i}"}
        for i in range(n)
    ]


def _grade_batch_output_all_yes(chunks: list[dict]) -> GradeBatchOutput:
    return GradeBatchOutput(
        grades=[
            ChunkGradeItem(chunk_id=c["chunk_id"], grade="yes") for c in chunks
        ],
    )


# --------------------------------------------------------------------------- #
# BatchCragGrader — single call contract                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_batch_grader_uses_one_llm_call_for_n_chunks() -> None:
    """N=10 chunks → exactly 1 LLM call (master Finding #19 contract)."""
    chunks = _make_chunks(10)
    mock_call = AsyncMock(
        return_value=(_grade_batch_output_all_yes(chunks), None),
    )
    grader = BatchCragGrader(
        structured_llm_caller=mock_call,
        system_prompt="grade prompt",
    )
    scores = await grader.grade_batch(query="anything", chunks=chunks)
    assert mock_call.call_count == 1
    assert len(scores) == 10
    assert all(0.0 <= s <= 1.0 for s in scores.values())
    assert all(scores[c["chunk_id"]] == 1.0 for c in chunks)


@pytest.mark.asyncio
async def test_batch_grader_score_dict_shape() -> None:
    """Returns mapping keyed by every input chunk's id."""
    chunks = _make_chunks(3)
    mock_call = AsyncMock(
        return_value=(
            GradeBatchOutput(
                grades=[
                    ChunkGradeItem(chunk_id="c0", grade="yes"),
                    ChunkGradeItem(chunk_id="c1", grade="partial"),
                    ChunkGradeItem(chunk_id="c2", grade="no"),
                ]
            ),
            None,
        ),
    )
    grader = BatchCragGrader(
        structured_llm_caller=mock_call,
        system_prompt="grade",
    )
    scores = await grader.grade_batch(query="q", chunks=chunks)
    assert scores == {"c0": 1.0, "c1": 0.5, "c2": 0.0}


@pytest.mark.asyncio
async def test_batch_grader_empty_input_no_llm_call() -> None:
    """Empty chunks → {} and zero LLM calls."""
    mock_call = AsyncMock()
    grader = BatchCragGrader(
        structured_llm_caller=mock_call,
        system_prompt="grade",
    )
    out = await grader.grade_batch(query="q", chunks=[])
    assert out == {}
    assert mock_call.call_count == 0


@pytest.mark.asyncio
async def test_batch_grader_handles_id_field_alias() -> None:
    """Chunks may carry ``id`` instead of ``chunk_id`` — both supported."""
    chunks = [
        {"id": "a1", "text": "alpha"},
        {"id": "a2", "text": "beta"},
    ]
    mock_call = AsyncMock(
        return_value=(
            GradeBatchOutput(
                grades=[
                    ChunkGradeItem(chunk_id="a1", grade="yes"),
                    ChunkGradeItem(chunk_id="a2", grade="no"),
                ]
            ),
            None,
        ),
    )
    grader = BatchCragGrader(
        structured_llm_caller=mock_call,
        system_prompt="grade",
    )
    scores = await grader.grade_batch(query="q", chunks=chunks)
    assert scores == {"a1": 1.0, "a2": 0.0}


# --------------------------------------------------------------------------- #
# BatchCragGrader — graceful degradation                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_batch_grader_unparseable_response_falls_back_to_all_one() -> None:
    """LLM returns ``(None, None)`` (parse fail) → every chunk scored 1.0."""
    chunks = _make_chunks(5)
    mock_call = AsyncMock(return_value=(None, None))
    grader = BatchCragGrader(
        structured_llm_caller=mock_call,
        system_prompt="grade",
    )
    scores = await grader.grade_batch(query="q", chunks=chunks)
    assert mock_call.call_count == 1
    assert len(scores) == 5
    assert all(s == 1.0 for s in scores.values())


@pytest.mark.asyncio
async def test_batch_grader_empty_grades_list_falls_back_to_all_one() -> None:
    """LLM returns parsed object with empty grades → all 1.0 fallback."""
    chunks = _make_chunks(3)

    class _EmptyGrades:
        grades: list = []

    mock_call = AsyncMock(return_value=(_EmptyGrades(), None))
    grader = BatchCragGrader(
        structured_llm_caller=mock_call,
        system_prompt="grade",
    )
    scores = await grader.grade_batch(query="q", chunks=chunks)
    assert all(s == 1.0 for s in scores.values())


@pytest.mark.asyncio
async def test_batch_grader_llm_transport_exception_falls_back_to_all_one() -> None:
    """LLM caller raises → MUST NOT propagate; all chunks scored 1.0."""
    chunks = _make_chunks(4)
    mock_call = AsyncMock(side_effect=RuntimeError("network blip"))
    grader = BatchCragGrader(
        structured_llm_caller=mock_call,
        system_prompt="grade",
    )
    scores = await grader.grade_batch(query="q", chunks=chunks)
    assert len(scores) == 4
    assert all(s == 1.0 for s in scores.values())


@pytest.mark.asyncio
async def test_batch_grader_missing_chunk_in_llm_response_gets_fallback() -> None:
    """LLM omits an id from its response → that chunk falls back to 1.0
    while other chunks honour LLM verdict.
    """
    chunks = _make_chunks(3)
    mock_call = AsyncMock(
        return_value=(
            GradeBatchOutput(
                grades=[
                    ChunkGradeItem(chunk_id="c0", grade="yes"),
                    # c1 deliberately omitted by LLM
                    ChunkGradeItem(chunk_id="c2", grade="no"),
                ]
            ),
            None,
        ),
    )
    grader = BatchCragGrader(
        structured_llm_caller=mock_call,
        system_prompt="grade",
    )
    scores = await grader.grade_batch(query="q", chunks=chunks)
    assert scores == {"c0": 1.0, "c1": 1.0, "c2": 0.0}


# --------------------------------------------------------------------------- #
# BatchCragGrader — window split on oversize input                            #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_batch_grader_splits_into_windows_when_above_max_chunks() -> None:
    """N=120 chunks with max_chunks=50 → 3 LLM calls (50 + 50 + 20)."""
    chunks = _make_chunks(120)

    call_count = {"n": 0}

    async def _staged_caller(**kwargs):
        call_count["n"] += 1
        # The caller passes ``messages`` — count chunks by counting <chunk tags.
        user_msg = kwargs["messages"][1]["content"]
        chunk_tag_count = user_msg.count("<chunk id=")
        # Pretend every chunk graded "yes". Build minimal valid output.
        # Extract the ids that were sent so we echo correctly.
        import re
        ids = re.findall(r'<chunk id="([^"]+)"', user_msg)
        return (
            GradeBatchOutput(
                grades=[ChunkGradeItem(chunk_id=i, grade="yes") for i in ids],
            ),
            None,
        ), chunk_tag_count

    # AsyncMock doesn't support returning side-effect-dependent values
    # easily; use a plain async function via side_effect.
    seen_windows: list[int] = []

    async def _real_caller(**kwargs):
        user_msg = kwargs["messages"][1]["content"]
        import re
        ids = re.findall(r'<chunk id="([^"]+)"', user_msg)
        seen_windows.append(len(ids))
        return (
            GradeBatchOutput(
                grades=[ChunkGradeItem(chunk_id=i, grade="yes") for i in ids],
            ),
            None,
        )

    grader = BatchCragGrader(
        structured_llm_caller=_real_caller,
        system_prompt="grade",
        max_chunks=50,
    )
    scores = await grader.grade_batch(query="q", chunks=chunks)
    assert len(scores) == 120
    assert seen_windows == [50, 50, 20], (
        f"Window split must be 50+50+20 (got {seen_windows})"
    )
    assert all(s == 1.0 for s in scores.values())


@pytest.mark.asyncio
async def test_batch_grader_invalid_max_chunks_falls_back_to_default() -> None:
    """max_chunks=0 / negative / non-int → uses constants default silently."""
    chunks = _make_chunks(3)
    mock_call = AsyncMock(
        return_value=(_grade_batch_output_all_yes(chunks), None),
    )
    g1 = BatchCragGrader(
        structured_llm_caller=mock_call,
        system_prompt="x",
        max_chunks=0,
    )
    out = await g1.grade_batch(query="q", chunks=chunks)
    assert len(out) == 3
    g2 = BatchCragGrader(
        structured_llm_caller=mock_call,
        system_prompt="x",
        max_chunks=-5,
    )
    out2 = await g2.grade_batch(query="q", chunks=chunks)
    assert len(out2) == 3


def test_batch_grader_requires_caller_at_construct_time() -> None:
    """Missing ``structured_llm_caller`` → ValueError (registry catches)."""
    with pytest.raises(ValueError):
        BatchCragGrader(
            structured_llm_caller=None,  # type: ignore[arg-type]
            system_prompt="x",
        )


# --------------------------------------------------------------------------- #
# PerChunkCragGrader                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_per_chunk_grader_issues_one_call_per_chunk() -> None:
    """N=10 chunks → exactly 10 LLM calls (legacy contract)."""
    chunks = _make_chunks(10)

    call_count = {"n": 0}

    async def _caller(**_kwargs):
        call_count["n"] += 1
        return GradeOutput(grade="yes"), None

    grader = PerChunkCragGrader(
        structured_llm_caller=_caller,
        system_prompt="grade",
    )
    scores = await grader.grade_batch(query="q", chunks=chunks)
    assert call_count["n"] == 10
    assert len(scores) == 10
    assert all(s == 1.0 for s in scores.values())


@pytest.mark.asyncio
async def test_per_chunk_grader_handles_mixed_verdicts() -> None:
    """Per-chunk verdict variety → mapped scores."""
    chunks = _make_chunks(3)
    verdicts = iter(["yes", "no", "partial"])

    async def _caller(**_kwargs):
        return GradeOutput(grade=next(verdicts)), None

    grader = PerChunkCragGrader(
        structured_llm_caller=_caller,
        system_prompt="grade",
    )
    scores = await grader.grade_batch(query="q", chunks=chunks)
    assert scores == {"c0": 1.0, "c1": 0.0, "c2": 0.5}


@pytest.mark.asyncio
async def test_per_chunk_grader_single_failure_does_not_break_batch() -> None:
    """One chunk's LLM call raising MUST NOT affect siblings."""
    chunks = _make_chunks(3)

    async def _caller(**kwargs):
        if "<chunk>alpha-1" in kwargs["messages"][1]["content"]:
            raise RuntimeError("network blip")
        return GradeOutput(grade="yes"), None

    grader = PerChunkCragGrader(
        structured_llm_caller=_caller,
        system_prompt="grade",
    )
    scores = await grader.grade_batch(query="q", chunks=chunks)
    # The failing chunk falls back to 1.0; siblings honour LLM yes verdict.
    assert scores == {"c0": 1.0, "c1": 1.0, "c2": 1.0}


@pytest.mark.asyncio
async def test_per_chunk_grader_empty_input_no_llm_call() -> None:
    call_count = {"n": 0}

    async def _caller(**_kwargs):
        call_count["n"] += 1
        return None, None

    grader = PerChunkCragGrader(
        structured_llm_caller=_caller,
        system_prompt="grade",
    )
    out = await grader.grade_batch(query="q", chunks=[])
    assert out == {}
    assert call_count["n"] == 0


def test_per_chunk_grader_requires_caller_at_construct_time() -> None:
    with pytest.raises(ValueError):
        PerChunkCragGrader(
            structured_llm_caller=None,  # type: ignore[arg-type]
            system_prompt="x",
        )


# --------------------------------------------------------------------------- #
# Domain-neutral guard                                                        #
# --------------------------------------------------------------------------- #


def test_test_fixtures_are_domain_neutral() -> None:
    """Fixtures use only alpha/beta/gamma + chunk ids — no vertical literals."""
    fixtures_text = " ".join(
        [c["content"] for c in _make_chunks(5)] + ["grade", "anything"]
    ).lower()
    banned = ("spa", "massage", "legal", "medical", "voucher", "promo")
    for term in banned:
        assert term not in fixtures_text, (
            f"vertical literal '{term}' leaked into test fixtures"
        )
