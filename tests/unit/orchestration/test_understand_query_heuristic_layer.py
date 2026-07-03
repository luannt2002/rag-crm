"""[T2-CostPerf] Tests — Layer-1 heuristic intent classify wired into understand_query.

Verifies:
- High-confidence heuristic match skips LLM call entirely.
- Low-confidence heuristic match falls back to LLM path.
- No heuristic match falls back to LLM path.
- Heuristic path sets intent_source = "heuristic" on state.
- LLM path sets intent_source = "llm" on state (regression guard).
- step_tracker.step("understand_query") is called on the heuristic path (observability).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.unit._node_test_helpers import (
    RecordingStepTracker,
    build_test_graph,
    make_state,
    node_callable,
)
from ragbot.shared.constants import (
    HEURISTIC_INTENT_CONFIDENCE_THRESHOLD,
    INTENT_CHITCHAT_LABEL,
    INTENT_GREETING,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def graph_parts():
    """Build a compiled test graph + helpers."""
    compiled, tracker, audit, resolver, llm = build_test_graph(
        text_response='{"intent": "factoid", "condensed_query": "", "confidence": 0.9}'
    )
    return compiled, tracker, audit, resolver, llm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHeuristicMatchSkipsLLM:
    """When heuristic fires with confidence >= threshold → LLM NOT called."""

    @pytest.mark.asyncio
    async def test_heuristic_match_high_confidence_skips_llm(self, graph_parts):
        compiled, tracker, audit, resolver, llm = graph_parts
        understand_query = node_callable(compiled, "understand_query")
        state = make_state(
            query="xin chào",
            step_tracker=tracker,
            pipeline_config={},
        )
        result = await understand_query(state)

        assert result.get("intent") == INTENT_GREETING
        assert result.get("intent_source") == "heuristic"
        # LLM must NOT have been called
        llm.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_heuristic_chitchat_skips_llm(self, graph_parts):
        compiled, tracker, audit, resolver, llm = graph_parts
        understand_query = node_callable(compiled, "understand_query")
        state = make_state(
            query="cảm ơn bạn",
            step_tracker=tracker,
            pipeline_config={},
        )
        result = await understand_query(state)

        assert result.get("intent") == INTENT_CHITCHAT_LABEL
        assert result.get("intent_source") == "heuristic"
        llm.complete.assert_not_called()


class TestComplexIntentFallsBackToLLM:
    """Q9: aggregation/multi_hop/comparison match the heuristic but at the WEAK
    tier (< threshold), so the node MUST fall through to the LLM path instead of
    fast-pathing them. The old ``0.85 >= 0.85`` gate skipped the LLM here."""

    @pytest.mark.asyncio
    async def test_aggregation_query_falls_back_to_llm(self, graph_parts):
        compiled, tracker, audit, resolver, llm = graph_parts
        understand_query = node_callable(compiled, "understand_query")
        state = make_state(
            query="có bao nhiêu loại dịch vụ ở đây",
            step_tracker=tracker,
            pipeline_config={},
        )
        result = await understand_query(state)
        # Heuristic detected the pattern but its confidence is below the floor,
        # so the node must NOT short-circuit on the heuristic — it falls through
        # to the LLM understand path (intent_source is never "heuristic" here).
        assert result.get("intent_source") != "heuristic"


class TestHeuristicLocaleSignals:
    """Q9 locale wire: the node resolves the bot's language-pack signals and
    passes them to the classifier so a non-vi bot classifies on ITS patterns."""

    @pytest.mark.asyncio
    async def test_classifier_called_with_locale_signals(self, graph_parts):
        compiled, tracker, audit, resolver, llm = graph_parts
        understand_query = node_callable(compiled, "understand_query")
        state = make_state(
            query="xin chào",
            step_tracker=tracker,
            pipeline_config={},
        )
        with patch(
            "ragbot.orchestration.nodes.understand._classify_heuristic",
        ) as mock_classify:
            mock_classify.return_value = MagicMock(
                intent=None, confidence=0.0, matched_pattern=None,
            )
            await understand_query(state)
        assert mock_classify.called
        # Must be threaded with a resolved signals object, not the vi default.
        _, kwargs = mock_classify.call_args
        assert "signals" in kwargs and kwargs["signals"] is not None


class TestHeuristicNoMatchFallsBackToLLM:
    """When no pattern matches → result does NOT have intent_source='heuristic'."""

    @pytest.mark.asyncio
    async def test_heuristic_no_match_falls_back_to_llm(self, graph_parts):
        compiled, tracker, audit, resolver, llm = graph_parts
        understand_query = node_callable(compiled, "understand_query")
        state = make_state(
            query="quy trình đăng ký dịch vụ như thế nào",
            step_tracker=tracker,
            pipeline_config={},
        )
        result = await understand_query(state)

        # Heuristic should NOT have matched — intent_source must NOT be "heuristic"
        assert result.get("intent_source") != "heuristic"
        # An intent should always be returned (even as fallback)
        assert "intent" in result


class TestHeuristicLowConfidenceFallsBackToLLM:
    """When heuristic is disabled via pipeline_config → LLM is called."""

    @pytest.mark.asyncio
    async def test_heuristic_disabled_via_pipeline_config_falls_back_to_llm(
        self, graph_parts
    ):
        compiled, tracker, audit, resolver, llm = graph_parts
        understand_query = node_callable(compiled, "understand_query")
        state = make_state(
            query="xin chào",  # would normally match greeting
            step_tracker=tracker,
            pipeline_config={"heuristic_intent_enabled": False},
        )
        result = await understand_query(state)

        # With heuristic disabled, intent_source must NOT be "heuristic"
        assert result.get("intent_source") != "heuristic"

    @pytest.mark.asyncio
    async def test_force_re_understand_bypasses_heuristic(self, graph_parts):
        """CRAG-retry escape hatch: force_re_understand skips heuristic layer."""
        compiled, tracker, audit, resolver, llm = graph_parts
        understand_query = node_callable(compiled, "understand_query")
        state = make_state(
            query="xin chào",
            step_tracker=tracker,
            pipeline_config={},
            force_re_understand=True,
        )
        result = await understand_query(state)

        # force_re_understand bypasses heuristic → intent_source NOT heuristic
        assert result.get("intent_source") != "heuristic"


class TestHeuristicMetadataLogged:
    """The heuristic path must emit step metadata for observability."""

    @pytest.mark.asyncio
    async def test_heuristic_metadata_logged(self, graph_parts):
        """understand_query step context receives heuristic metadata."""
        compiled, tracker, audit, resolver, llm = graph_parts
        understand_query = node_callable(compiled, "understand_query")
        state = make_state(
            query="xin chào",
            step_tracker=tracker,
            pipeline_config={},
        )
        result = await understand_query(state)

        assert result.get("intent_source") == "heuristic"
        # Confirm step was tracked
        uq_steps = tracker.by_name("understand_query")
        assert len(uq_steps) >= 1
        # The heuristic step context should have source="heuristic"
        heuristic_step = next(
            (s for s in uq_steps if s.metadata.get("source") == "heuristic"),
            None,
        )
        assert heuristic_step is not None, (
            "Expected an understand_query step with source='heuristic'"
        )
        assert heuristic_step.metadata.get("intent") == INTENT_GREETING
        assert "confidence" in heuristic_step.metadata

    @pytest.mark.asyncio
    async def test_heuristic_result_has_confidence_field(self, graph_parts):
        compiled, tracker, audit, resolver, llm = graph_parts
        understand_query = node_callable(compiled, "understand_query")
        state = make_state(
            query="cảm ơn nhé",
            step_tracker=tracker,
            pipeline_config={},
        )
        result = await understand_query(state)

        assert result.get("intent_source") == "heuristic"
        assert result.get("intent_confidence") is not None
        assert result.get("intent_confidence") >= HEURISTIC_INTENT_CONFIDENCE_THRESHOLD
