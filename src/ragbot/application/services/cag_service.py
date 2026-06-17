# ============================================================
# DEAD-CODE NOTICE — 2026-06-03
# ============================================================
# This module is NOT reachable from any production entry point.
# Verified via:
#   * AST import-graph reachability scan (entry: FastAPI app +
#     workers + middlewares + routes)
#   * 10-agent multi-trace audit (Agent 9 vulture + Agent 10
#     runtime-path)
#
# Reason: CAG sub-system shipped but never plumbed into query_graph (build_graph signature has no cag port).
#
# Status:
#   * Code kept INTACT (reversible — remove this header to reactivate)
#   * Safe to delete physically; defer to operator decision
#
# To reactivate:
#   1. Confirm a runtime caller is intentional (search registry
#      strings, dynamic imports)
#   2. Remove this header block
#   3. Wire the registry / DI binding in bootstrap.py
# ============================================================

# """CAG application service — owner-opt-in retrieve-bypass coordinator.

# Cache-Augmented Generation (CAG) Mode.

# This module is the thin **application-layer** seam that the query graph
# calls before kicking off the retrieve/rerank stages. It holds the
# injected ``CAGServicePort`` strategy and exposes a single high-level
# method, ``decide(...)``, returning a small dataclass the graph can switch on.

# Layering
# --------
# - ``application/ports/cag_port.py``       — strategy contract (Protocol).
# - ``application/services/cag_service.py`` — THIS FILE: orchestration glue
#                                             (decides, logs, wraps payload).
# - ``infrastructure/cag/{null,anthropic}_cag.py`` — strategy adapters.
# - ``infrastructure/cag/registry.py``     — DI factory.

# The query graph imports ``CAGDecision`` + ``CAGService``
# from THIS file and never touches the registry or adapter directly. That
# keeps the orchestrator decoupled from CAG provider details — adding a
# new provider is "drop a file in ``infrastructure/cag`` + register it"
# with no orchestrator edit.

# Citation
# --------
# Chan et al. 2024 — "Don't Do RAG: When Cache-Augmented Generation is All
# You Need for Knowledge Tasks", https://arxiv.org/abs/2412.15605
# (ACM Web 2025 peer-reviewed). 10.9-40.5x latency reduction vs RAG on
# sub-context-window corpora.

# HALLU=0 sacred + App-mindset
# ----------------------------
# - This service NEVER injects text the bot owner didn't author. The corpus
#   block reflects ONLY the bot's uploaded documents.
# - This service NEVER overrides the LLM answer. It only decides whether
#   to bypass retrieval; the LLM still generates the response using the
#   bot owner's ``system_prompt`` as single source of truth.
# - On any failure path the decision is "fall back to RAG" — never "answer
#   from parametric memory".
# """

# from __future__ import annotations

# from dataclasses import dataclass

# import structlog

# from ragbot.application.ports.cag_port import CAGPayload, CAGServicePort
# from ragbot.shared.types import TenantId

# logger = structlog.get_logger(__name__)


# @dataclass(frozen=True, slots=True)
# class CAGDecision:
#     """Outcome of the per-turn CAG gate, ready for the query graph to act on.

#     @param engaged: when True the orchestrator MUST skip retrieve/rerank
#         and inject ``payload`` into the LLM call. When False the
#         orchestrator runs standard RAG retrieval.
#     @param payload: the corpus block to inject when ``engaged=True``;
#         ``None`` when ``engaged=False`` (and ALSO ``None`` if the gate
#         engaged but the payload load failed — the orchestrator MUST treat
#         a ``None`` payload as "fall back to RAG" even if ``engaged=True``
#         was returned earlier, to keep HALLU=0 safe across race conditions).
#     """

#     engaged: bool
#     payload: CAGPayload | None = None


# class CAGService:
#     """Application-layer orchestration glue around a ``CAGServicePort``.

#     Holds the injected strategy and exposes ``decide(...)`` to the query
#     graph. Keep this class thin — it MUST NOT carry tenant-specific state
#     across calls (each turn re-runs the gate so config flips at the DB
#     layer take effect on the next turn, not on next process restart).

#     @param strategy: the underlying ``CAGServicePort`` adapter, built by
#         ``infrastructure.cag.registry.build_cag(...)`` at DI wiring time.
#     """

#     def __init__(self, *, strategy: CAGServicePort) -> None:
#         self._strategy = strategy

#     async def decide(
#         self,
#         *,
#         record_tenant_id: TenantId,
#         record_bot_id: str,
#     ) -> CAGDecision:
#         """Decide whether this turn should engage CAG.

#         @return: ``CAGDecision(engaged=False)`` whenever the strategy
#             declines OR the payload load fails after the gate accepted.
#             The orchestrator treats both as "run RAG" — fail-soft, no
#             HALLU risk.
#         """
#         engaged = await self._strategy.should_engage(
#             record_tenant_id=record_tenant_id,
#             record_bot_id=record_bot_id,
#         )
#         if not engaged:
#             return CAGDecision(engaged=False, payload=None)

#         payload = await self._strategy.build_corpus_payload(
#             record_tenant_id=record_tenant_id,
#             record_bot_id=record_bot_id,
#         )
#         if payload is None:
            # Defensive: gate accepted but payload load failed (e.g. race
            # between snapshot read and payload read). Fall back to RAG
            # rather than letting the LLM answer from memory.
#             logger.warning(
#                 "cag_lookup_payload_missing_after_gate",
#                 step_name="cag_lookup",
#                 feature_flag="cag_mode_enabled",
#                 record_tenant_id=str(record_tenant_id),
#                 record_bot_id=record_bot_id,
#                 engaged=False,
#             )
#             return CAGDecision(engaged=False, payload=None)

#         return CAGDecision(engaged=True, payload=payload)


# __all__ = ["CAGDecision", "CAGService"]
