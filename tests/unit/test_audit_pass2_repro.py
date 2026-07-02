"""[AUDIT PASS-2 REPRODUCTION / REGRESSION GUARD] — 2026-07-03

Each test here REPRODUCES a CONFIRMED finding from the two-pass deep audit
(reports/DEEPDIVE2_20260703/PLAN-ALL-FLOWS-PASS2.md). Every test asserts the
*desired/correct* behaviour, so it is RED today (= empirical runtime proof the
bug is real) and turns GREEN when the corresponding fix lands — i.e. it is the
regression guard that stops the old bug coming back (CLAUDE.md /tdd: failing
test FIRST).

Pure unit tests only — NO DB / Redis / server needed, so they run in CI as-is.
Run:  python -m pytest tests/unit/test_audit_pass2_repro.py -v

Findings covered (all CONFIRMED by re-reading source in pass-2):
  T1  S1  — LangGraph drops undeclared GraphState keys (paid-token/rerank_score_mode/... dead)
  T2  S3  — happy-case box: out-of-vocab CSV header → 0 entities (stats route silently dead)
  T3  F2  — retrieval_filter re-exports removed → 5 pin-test files fail at collection
  T4  L2-4— GraphRAG call uses bot_id= vs signature record_bot_id → TypeError both directions
  T5  SEC-4— ingest idempotency key omits record_bot_id (chat key includes it — asymmetry)
  T6  SEC-3— ai_config_repository targets non-existent schema `ragbot.ai_keys`
"""
from __future__ import annotations

import inspect

import pytest


# ──────────────────────────────────────────────────────────────────────────
# T1 · S1 — undeclared GraphState keys are dropped by langgraph 1.2.4
#   Evidence: state.py declares 58 keys; these cross-node keys are NOT declared,
#   so they vanish at the reducer → paid-token budget=0, grade never sees the
#   rerank score mode, slot-extraction reverts, xml-wrap date-default dead.
#   pass2-X-systemic-security.md S1.1 (22 undeclared-but-used keys).
# ──────────────────────────────────────────────────────────────────────────
class TestS1StateKeyDrop:
    # (key, where written, where read) — all confirmed cross-node in pass-2
    CROSS_NODE_KEYS = [
        "bot_extra_output_tokens_per_response",  # graph_assembly:193 → generate:739 (paid feature=0)
        "rerank_score_mode",                     # rerank:498 → grade:486 (NEW pass-2, floor gate dead)
        "raw_user_message",                      # graph_assembly:177 → generate:251 (slot fix reverted)
        "embedding_column",                      # query_graph:1337 → :2697 (preflight false alarm/turn)
        "_total_graph_iterations",               # grade:83 → routing:245 (loop cap dead)
    ]

    def _declared_keys(self) -> set[str]:
        from ragbot.orchestration.state import GraphState
        return set(GraphState.__annotations__)

    @pytest.mark.parametrize("key", CROSS_NODE_KEYS)
    def test_cross_node_key_is_declared_in_graphstate(self, key: str) -> None:
        declared = self._declared_keys()
        assert key in declared, (
            f"'{key}' is written in one node and read in another but is NOT "
            f"declared in GraphState — langgraph 1.2.4 drops it at the reducer, "
            f"silently killing the feature. Declare it in state.py (S1 fix)."
        )


# ──────────────────────────────────────────────────────────────────────────
# T2 · S3 — happy-case box: a well-formed CSV whose header is out-of-vocab
#   yields ZERO entities → the deterministic stats/count/list/superlative route
#   is silently dead for that corpus (canary 25/25 shape).
#   pass2-L1-ingest.md / pass2-X S3.1 ; document_stats.py:156 vocab-gated header.
# ──────────────────────────────────────────────────────────────────────────
class TestS3HappyCaseBox:
    def test_out_of_vocab_csv_still_extracts_entities(self) -> None:
        from ragbot.shared.document_stats import parse_table_chunks
        # well-formed table, unknown-domain headers (no vi/en vocab match), text values
        md = (
            "Field876A,Field530B,Field141C\n"
            "alpha,beta,gamma\n"
            "delta,epsilon,zeta\n"
        )
        entities = parse_table_chunks([{"content": md}])
        assert len(entities) > 0, (
            "A well-formed CSV with out-of-vocabulary headers degrades to ZERO "
            "entities (header→col_N→noise-drop). Needs a shape-only header "
            "fallback so unknown-domain / non-vi-en corpora degrade gracefully, "
            "not to zero (S3 fix)."
        )

    @pytest.mark.xfail(strict=True, reason="Phase-3 remaining: date-shaped 8-digit "
                       "integer guard (ambiguous vs a real ~31M price); deferred to "
                       "avoid false-rejecting legitimate prices without a load-test.")
    def test_date_like_integer_is_not_read_as_a_price(self) -> None:
        from ragbot.shared.number_format import parse_money_vn
        # 31.12.2026 flattened by an upstream parser → 31122026 sits under the
        # 500M ceiling and is currently accepted as a price.
        val = parse_money_vn("31122026")
        assert val is None or val != 31122026, (
            "An 8-digit date-shaped integer (31122026 = 31/12/2026) is accepted "
            "as a price because it is below the 500M VND ceiling — a date/serial "
            "must not become a catalog price (number-HALLU class)."
        )


# ──────────────────────────────────────────────────────────────────────────
# T3 · F2 — retrieval_filter re-exports were deleted from query_graph (24f2451)
#   → 5 pin-test files fail at COLLECTION, so the cliff/threshold/CRAG invariants
#   are currently unguarded. pass2-L2-query.md (5 collection errors, not 7).
# ──────────────────────────────────────────────────────────────────────────
class TestF2ReExportBreak:
    @pytest.mark.parametrize("symbol", [
        "_cliff_detect_filter",
        "_rerank_threshold_gate",
        "CRAG_GRADE_IRRELEVANT",
    ])
    def test_query_graph_reexports_retrieval_filter_symbol(self, symbol: str) -> None:
        import importlib
        qg = importlib.import_module("ragbot.orchestration.query_graph")
        assert hasattr(qg, symbol), (
            f"query_graph no longer re-exports '{symbol}' (removed by 24f2451, "
            f"stale comment left behind). Test files import it from here and "
            f"fail at collection → the pin is not guarding anything. Restore the "
            f"1-line re-export (F2 fix)."
        )


# ──────────────────────────────────────────────────────────────────────────
# T4 · L2-4 — GraphRAG is called with bot_id= but the method declares
#   record_bot_id → TypeError on EVERY call, swallowed by except Exception.
#   Ingest pays LLM triple-extraction cost then discards; query returns 0.
#   pass2-X S2.1 ; knowledge_graph.py:130/182 vs graph_retriever.py:61 + ingest_core.py:802.
# ──────────────────────────────────────────────────────────────────────────
class TestL2GraphRAGKwarg:
    """Guard: the GraphRAG call sites must use record_bot_id= (not bot_id=) so the
    kwargs bind to KnowledgeGraphService.{query_graph,store_triples}. Reverting to
    bot_id= re-introduces the swallowed TypeError (query dead / triples discarded)."""

    def test_query_graph_signature_takes_record_bot_id(self) -> None:
        import pathlib
        from ragbot.infrastructure.graph.knowledge_graph import KnowledgeGraphService
        assert "record_bot_id" in inspect.signature(KnowledgeGraphService.query_graph).parameters
        src = pathlib.Path("src/ragbot/infrastructure/graph/graph_retriever.py").read_text()
        assert "bot_id=record_bot_id" not in src.replace("record_bot_id=record_bot_id", ""), (
            "graph_retriever must call query_graph(record_bot_id=...), not bot_id= "
            "(L2-4: bot_id= → TypeError swallowed → GraphRAG query always 0)."
        )

    def test_store_triples_call_site_uses_record_bot_id(self) -> None:
        import pathlib
        from ragbot.infrastructure.graph.knowledge_graph import KnowledgeGraphService
        assert "record_bot_id" in inspect.signature(KnowledgeGraphService.store_triples).parameters
        src = pathlib.Path("src/ragbot/application/services/document_service/ingest_core.py").read_text()
        # the store_triples call must not pass a bare bot_id= kwarg
        assert "store_triples(\n                            bot_id=" not in src and \
               "store_triples(bot_id=" not in src, (
            "ingest_core must call store_triples(record_bot_id=...), not bot_id= "
            "(L2-4: bot_id= → TypeError → extracted triples discarded after LLM cost)."
        )


# ──────────────────────────────────────────────────────────────────────────
# T5 · SEC-4 — ingest idempotency key omits record_bot_id (chat key includes it)
#   → a second bot ingesting the same source_url within TTL is swallowed.
#   pass2-X SEC-4 ; idempotency_key.py:40-52 (no bot) vs :23-37 (has bot).
# ──────────────────────────────────────────────────────────────────────────
class TestSEC4IdempotencyOmitsBot:
    def test_ingest_idempotency_key_includes_bot_identity(self) -> None:
        from ragbot.domain.value_objects import idempotency_key as ik
        params = set(inspect.signature(ik.for_ingest_document).parameters)
        assert "record_bot_id" in params, (
            "for_ingest_document() derives the key from (tenant, source_url, "
            "corpus_version) with NO bot — two bots in one tenant ingesting the "
            "same URL collide and the 2nd is silently swallowed. for_chat_message "
            "correctly includes record_bot_id; mirror it (SEC-4 fix)."
        )


# ──────────────────────────────────────────────────────────────────────────
# T6 · SEC-3 — ai_config_repository hardcodes schema `ragbot.ai_keys` which does
#   not exist (real table = public.ai_keys) → every encrypted-key call 500s.
#   pass2-X SEC-3 (DB-verified: to_regclass('ragbot.ai_keys') = NULL).
# ──────────────────────────────────────────────────────────────────────────
class TestSEC3AiKeysSchema:
    def test_ai_config_repo_does_not_target_nonexistent_ragbot_schema(self) -> None:
        import pathlib
        src = pathlib.Path("src/ragbot/infrastructure/repositories/ai_config_repository.py").read_text()
        assert "ragbot.ai_keys" not in src, (
            "ai_config_repository references `ragbot.ai_keys` but that schema does "
            "not exist (table is public.ai_keys) → UndefinedTableError 500 on every "
            "key add/rotate. Drop the `ragbot.` prefix (SEC-3 fix)."
        )
