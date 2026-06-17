"""Tests for query pipeline gap fixes (Tasks 14-16)."""

import pytest


class TestGreetingIntent:
    def test_greeting_in_valid_intents(self):
        from ragbot.orchestration.query_graph import _VALID_INTENTS

        assert "greeting" in _VALID_INTENTS

    def test_greeting_answer_field_exists_and_empty_by_default(self):
        """Bot owner controls greeting via DB column. i18n default = empty string."""
        from ragbot.shared.i18n import get_pack
        assert get_pack("vi").greeting_answer == ""
        assert get_pack("en").greeting_answer == ""


class TestGuardrailConfig:
    def test_guardrail_accepts_custom_max_length(self):
        from ragbot.infrastructure.guardrails.local_guardrail import LocalGuardrail

        g = LocalGuardrail(max_input_length=500)
        assert g._max_input_length == 500

    def test_guardrail_default_max_length(self):
        from ragbot.infrastructure.guardrails.local_guardrail import LocalGuardrail

        g = LocalGuardrail()
        assert g._max_input_length == 8000

    def test_guardrail_accepts_custom_min_alpha(self):
        from ragbot.infrastructure.guardrails.local_guardrail import LocalGuardrail

        g = LocalGuardrail(min_alpha_chars=5)
        assert g._min_alpha_chars == 5

    def test_guardrail_default_min_alpha(self):
        from ragbot.infrastructure.guardrails.local_guardrail import LocalGuardrail

        g = LocalGuardrail()
        assert g._min_alpha_chars == 2

    @pytest.mark.asyncio
    async def test_guardrail_uses_custom_max_length(self):
        from ragbot.infrastructure.guardrails.local_guardrail import (
            GuardrailBlocked,
            LocalGuardrail,
        )
        from uuid import uuid4

        g = LocalGuardrail(max_input_length=10)
        with pytest.raises(GuardrailBlocked) as exc_info:
            await g.check_input(
                "a" * 11,
                tenant_id=uuid4(),
                message_id=1,
            )
        assert any(h.rule_id == "length_limit" for h in exc_info.value.hits)

    @pytest.mark.asyncio
    async def test_guardrail_uses_custom_min_alpha(self):
        from ragbot.infrastructure.guardrails.local_guardrail import (
            GuardrailBlocked,
            LocalGuardrail,
        )
        from uuid import uuid4

        g = LocalGuardrail(min_alpha_chars=5)
        with pytest.raises(GuardrailBlocked) as exc_info:
            await g.check_input(
                "abc",
                tenant_id=uuid4(),
                message_id=1,
            )
        assert any(h.rule_id == "too_short" for h in exc_info.value.hits)


class TestSystemPromptHashInState:
    def test_system_prompt_in_graph_state_type(self):
        from ragbot.orchestration.state import GraphState

        assert "system_prompt" in GraphState.__annotations__
