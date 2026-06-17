"""Unit tests: bot_limits — resolve, validate, get_effective_config."""
from __future__ import annotations

import pytest
from uuid import uuid4

from ragbot.shared.bot_limits import (
    PLAN_LIMIT_SCHEMA,
    get_effective_config,
    resolve_bot_limit,
    validate_plan_limits,
)
from ragbot.application.dto.bot_config import BotConfig
from tests.conftest import TEST_TENANT_UUID


def _bot(**overrides) -> BotConfig:
    defaults = {
        "id": uuid4(),
        "bot_id": "test",
        "channel_type": "api",
        "bot_name": "Test",
        "record_tenant_id": TEST_TENANT_UUID,
        "workspace_id": str(TEST_TENANT_UUID),
    }
    defaults.update(overrides)
    return BotConfig(**defaults)


# ── resolve_bot_limit ────────────────────────────────────────────────────

class TestResolveBotLimit:
    def test_column_wins_over_plan_limits(self):
        bot = _bot(rerank_top_n=3, plan_limits={"rerank_top_n": 10})
        assert resolve_bot_limit(bot, "rerank_top_n") == 3

    def test_plan_limits_wins_over_system_default(self):
        bot = _bot(plan_limits={"retrieval_top_k": 50})
        assert resolve_bot_limit(bot, "retrieval_top_k", system_default=20) == 50

    def test_system_default_wins_over_schema(self):
        bot = _bot()
        assert resolve_bot_limit(bot, "retrieval_top_k", system_default=30) == 30

    def test_schema_default_used_as_fallback(self):
        bot = _bot()
        assert resolve_bot_limit(bot, "retrieval_top_k") == 20

    def test_none_column_falls_through(self):
        bot = _bot(rerank_top_n=None, plan_limits={"rerank_top_n": 7})
        assert resolve_bot_limit(bot, "rerank_top_n") == 7

    def test_unknown_key_returns_none(self):
        bot = _bot()
        assert resolve_bot_limit(bot, "nonexistent_key") is None

    def test_max_documents_column(self):
        bot = _bot(max_documents=10)
        assert resolve_bot_limit(bot, "max_documents") == 10

    def test_max_documents_default(self):
        bot = _bot()
        assert resolve_bot_limit(bot, "max_documents") == 5

    def test_bool_from_plan_limits(self):
        bot = _bot(plan_limits={"reflection_enabled": True})
        assert resolve_bot_limit(bot, "reflection_enabled") is True

    def test_bool_schema_default(self):
        bot = _bot()
        assert resolve_bot_limit(bot, "reflection_enabled") is False


# ── validate_plan_limits ─────────────────────────────────────────────────

class TestValidatePlanLimits:
    def test_valid_full_dict(self):
        data = {
            "retrieval_top_k": 50,
            "reflection_enabled": True,
            "grounding_check_enabled": False,
            "enrichment_mode": "llm",
            "cache_ttl_s": 7200,
            "embedding_model": "text-embedding-3-small",
            "graph_rag_mode": "adaptive",
            "priority_tier": "priority",
        }
        result = validate_plan_limits(data)
        assert result == data

    def test_unknown_keys_dropped(self):
        result = validate_plan_limits({"unknown_key": 42, "retrieval_top_k": 50})
        assert "unknown_key" not in result
        assert result["retrieval_top_k"] == 50

    def test_int_clamped_to_min(self):
        result = validate_plan_limits({"retrieval_top_k": 1})
        assert result["retrieval_top_k"] == 5  # min=5

    def test_int_clamped_to_max(self):
        result = validate_plan_limits({"retrieval_top_k": 999})
        assert result["retrieval_top_k"] == 200  # max=200

    def test_invalid_enum_raises(self):
        with pytest.raises(ValueError, match="must be one of"):
            validate_plan_limits({"enrichment_mode": "invalid"})

    def test_wrong_type_int_raises(self):
        with pytest.raises(ValueError, match="expected int"):
            validate_plan_limits({"retrieval_top_k": "not_an_int"})

    def test_wrong_type_bool_raises(self):
        with pytest.raises(ValueError, match="expected bool"):
            validate_plan_limits({"reflection_enabled": "yes"})

    def test_wrong_type_str_raises(self):
        with pytest.raises(ValueError, match="expected str"):
            validate_plan_limits({"enrichment_mode": 123})

    def test_empty_dict_ok(self):
        assert validate_plan_limits({}) == {}

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="must be a dict"):
            validate_plan_limits("not a dict")

    def test_cache_ttl_clamped(self):
        result = validate_plan_limits({"cache_ttl_s": 10})
        assert result["cache_ttl_s"] == 60  # min=60


# ── get_effective_config ─────────────────────────────────────────────────

class TestGetEffectiveConfig:
    def test_merges_all_keys(self):
        bot = _bot(
            max_documents=10,
            rerank_top_n=3,
            plan_limits={"retrieval_top_k": 50, "reflection_enabled": True},
        )
        system_defaults = {
            "grounding_check_enabled": True,
            "cache_ttl_s": 1800,
        }
        config = get_effective_config(bot, system_defaults)

        assert config["max_documents"] == 10
        assert config["rerank_top_n"] == 3
        assert config["retrieval_top_k"] == 50
        assert config["reflection_enabled"] is True
        assert config["grounding_check_enabled"] is True  # system default
        assert config["cache_ttl_s"] == 1800  # system default
        assert config["enrichment_mode"] == "template"  # schema default

    def test_empty_bot_uses_schema_defaults(self):
        bot = _bot()
        config = get_effective_config(bot, {})
        assert config["retrieval_top_k"] == 20
        assert config["reflection_enabled"] is False
        assert config["graph_rag_mode"] == "disabled"
        assert config["max_documents"] == 5

    def test_column_keys_present(self):
        bot = _bot(prompt_max_tokens=4000)
        config = get_effective_config(bot, {})
        assert config["prompt_max_tokens"] == 4000
        assert config["max_history"] is None
