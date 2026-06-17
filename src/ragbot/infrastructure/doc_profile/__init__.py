"""DocumentProfileAnalyzer strategies — Null + RuleBased.

AdapChunk Layer 3 refine. Owner-opt-in via
``system_config.adapchunk_layer3_doc_profile_enabled`` (default OFF).
The registry default (``"rule_based"``) is picked at DI-container time
when the flag is ON; the ``"null"`` strategy keeps the dict-only
baseline behaviour available for A/B comparison and rollback.

Inspired by the internal AdapChunk Layer 3 blueprint + Ekimetrics LREC
2026 proven adaptive-chunking metrics. No LLM call, no external
language-detection dependency — keeps the ingest hot path cheap.
"""
