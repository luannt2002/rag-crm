# ============================================================
# DISABLED — UNUSED (commented-marker, NOT deleted)
# ============================================================
# The LLM/rule chunking-strategy SELECTOR (AdapChunk Tang-4 Port) has
# ZERO runtime callers: ``chunking_strategy_provider`` is read by nothing;
# strategy routing is done by the deterministic profile router
# (shared/chunking/analyze.select_strategy + apply_cross_check).
# WHY kept: reversible escape-hatch — remove this header to reactivate.
# Policy: disabled-by-comment, physical removal deferred to operator.
# ============================================================
"""AdapChunk strategy-resolver adapters (Port: ChunkingStrategyResolverPort).

``rule``  → deterministic weighted-scorer (default + degradation fallback).
``llm``   → AdapChunk Tầng 3/4 LLM Strategy Selector (spec §4).

Selection is config-driven via ``chunking_strategy_provider`` (system_config);
adding a provider = drop a file + register it, no orchestrator edits.
"""
