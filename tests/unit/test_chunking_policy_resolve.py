"""Phase A — chunking policy resolve chain.

Resolves the effective chunking policy from a 3-tier chain (mirrors
``shared/bot_limits.py``): per-bot ``plan_limits.chunking_config`` →
platform ``system_config.chunking_policy`` → ``shared/constants`` default.
The platform-default behaviour MUST stay byte-identical to today
(``table_strategy == DEFAULT_TABLE_STRATEGY``, no forced strategy).
"""
from __future__ import annotations

from ragbot.shared.chunking_policy import resolve_chunking_policy
from ragbot.shared.constants import DEFAULT_TABLE_STRATEGY


class TestResolveChunkingPolicy:
    def test_empty_chain_returns_constant_default(self):
        pol = resolve_chunking_policy(plan_limits=None, platform_policy=None)
        assert pol["table_strategy"] == DEFAULT_TABLE_STRATEGY
        assert pol["force_strategy"] is None

    def test_platform_policy_overrides_constant(self):
        pol = resolve_chunking_policy(
            plan_limits=None,
            platform_policy={"table_strategy": "table_dual_index"},
        )
        assert pol["table_strategy"] == "table_dual_index"

    def test_per_bot_overrides_platform(self):
        pol = resolve_chunking_policy(
            plan_limits={"chunking_config": {"table_strategy": "table_dual_index"}},
            platform_policy={"table_strategy": "table_csv"},
        )
        # per-bot wins
        assert pol["table_strategy"] == "table_dual_index"

    def test_invalid_table_strategy_falls_back_to_default(self):
        pol = resolve_chunking_policy(
            plan_limits={"chunking_config": {"table_strategy": "bogus_xyz"}},
            platform_policy=None,
        )
        assert pol["table_strategy"] == DEFAULT_TABLE_STRATEGY

    def test_force_strategy_passthrough_when_valid(self):
        pol = resolve_chunking_policy(
            plan_limits={"chunking_config": {"force_strategy": "hdt"}},
            platform_policy=None,
        )
        assert pol["force_strategy"] == "hdt"

    def test_force_strategy_invalid_dropped(self):
        pol = resolve_chunking_policy(
            plan_limits={"chunking_config": {"force_strategy": "not_a_strategy"}},
            platform_policy=None,
        )
        assert pol["force_strategy"] is None

    def test_non_dict_inputs_tolerated(self):
        # Defensive: plan_limits or policy stored as a non-dict (legacy/null)
        # must not raise — degrade to default.
        pol = resolve_chunking_policy(plan_limits="oops", platform_policy=123)
        assert pol["table_strategy"] == DEFAULT_TABLE_STRATEGY
        assert pol["force_strategy"] is None


class TestResolveStyleProfile:
    """P3 Tenant-Profiling — per-bot ingest style normalizer config."""

    def test_empty_chain_style_profile_is_inert(self):
        pol = resolve_chunking_policy(plan_limits=None, platform_policy=None)
        sp = pol["style_profile"]
        assert sp["heading_uppercase_promote"] is False
        assert sp["table_separator"] == ""

    def test_per_bot_style_profile_resolved(self):
        pol = resolve_chunking_policy(
            plan_limits={"chunking_config": {"style_profile": {
                "heading_uppercase_promote": True,
                "table_separator": ";",
            }}},
            platform_policy=None,
        )
        sp = pol["style_profile"]
        assert sp["heading_uppercase_promote"] is True
        assert sp["table_separator"] == ";"

    def test_platform_style_profile_default_then_per_bot_override(self):
        pol = resolve_chunking_policy(
            plan_limits={"chunking_config": {"style_profile": {"table_separator": "~"}}},
            platform_policy={"style_profile": {"table_separator": ";"}},
        )
        # per-bot wins
        assert pol["style_profile"]["table_separator"] == "~"

    def test_invalid_separator_dropped(self):
        # A reserved/multi-char separator is dropped to "" (no normalization).
        for bad in ["||", "ab", "#", "*", "|", "x"]:
            pol = resolve_chunking_policy(
                plan_limits={"chunking_config": {"style_profile": {"table_separator": bad}}},
                platform_policy=None,
            )
            assert pol["style_profile"]["table_separator"] == "", bad

    def test_non_bool_uppercase_flag_coerced_false(self):
        pol = resolve_chunking_policy(
            plan_limits={"chunking_config": {"style_profile": {
                "heading_uppercase_promote": "yes",
            }}},
            platform_policy=None,
        )
        assert pol["style_profile"]["heading_uppercase_promote"] is False

    def test_non_dict_style_profile_tolerated(self):
        pol = resolve_chunking_policy(
            plan_limits={"chunking_config": {"style_profile": "oops"}},
            platform_policy=None,
        )
        sp = pol["style_profile"]
        assert sp["heading_uppercase_promote"] is False
        assert sp["table_separator"] == ""
