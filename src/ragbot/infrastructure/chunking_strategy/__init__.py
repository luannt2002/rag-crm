# ============================================================
# DEAD-CODE NOTICE — chunking-strategy resolver not yet wired
# ============================================================
# build_chunking_resolver / RuleChunkingStrategyResolver /
# LLMChunkingStrategyResolver have ZERO production callers — verified
# by grep over src/ (only tests/unit/test_llm_chunking_strategy_resolver.py
# references them); bootstrap has no provider and U4 still calls the bare
# select_strategy() inline.
#
# Why not wired in this pass (evidence, not guess):
#   * resolve_strategy() scores via profile_to_dict(dp) which HARDCODES
#     is_csv_format=False + vn_hierarchical_markers=0 (rule_resolver.py:38-39),
#     so routing it as a drop-in replacement for the U4 select_strategy()
#     call would BYPASS the CSV->table and legal->HDT fast-paths that
#     select_strategy() resolves from is_csv / vn_markers
#     (analyze.py:429-438) — a strategy-selection regression class.
#   * The port signature carries no text / table_strategy / ekimetrics flag,
#     so preserving those fast-paths requires extending the Port + both
#     resolvers + their tests + U4 — out of scope for a byte-identical pass.
#   * The "llm" branch picks the strategy with an LLM call; enabling it on the
#     ingest hot path needs a load-test soak (HALLU=0 / cost) before default.
#
# Status: code kept INTACT + reachable (registry valid). Remove this header
# once the Port gains the fast-path inputs and a load-test validates the LLM
# branch, then add the bootstrap Singleton + DocumentService injection + U4
# call. Tracked in reports/EXPERT_FIX_CHUNK_NARRATE_I18N_20260623.md (B-1).
# ============================================================
"""AdapChunk strategy-resolver adapters (Port: ChunkingStrategyResolverPort).

``rule``  → deterministic weighted-scorer (default + degradation fallback).
``llm``   → AdapChunk LLM Strategy Selector.

Selection is config-driven via ``chunking_strategy_provider`` (system_config);
adding a provider = drop a file + register it, no orchestrator edits.
"""
