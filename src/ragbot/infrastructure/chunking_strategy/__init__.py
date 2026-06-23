"""AdapChunk strategy-resolver adapters (Port: ChunkingStrategyResolverPort).

``rule``  → deterministic weighted-scorer (default + degradation fallback).
``llm``   → AdapChunk Tầng 3/4 LLM Strategy Selector (spec §4).

Selection is config-driven via ``chunking_strategy_provider`` (system_config);
adding a provider = drop a file + register it, no orchestrator edits.
"""
