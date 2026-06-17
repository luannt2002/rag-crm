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
# Reason: Only referenced by proposition_decomposer_port.py spec; never wired.
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

# """Proposition LLM Atomic Decomposition.

# Implements Chen et al. EMNLP 2024 "Dense X Retrieval: What Retrieval
# Granularity Should We Use?" (https://arxiv.org/abs/2312.06648).

# A *proposition* is "an atomic expression of a single factual claim" that
# can be understood without external context — i.e. pronouns and
# coreferents have been replaced by their full entity names. The paper
# benchmarks proposition-level retrieval vs sentence and 100-word
# passage chunks on FActScore / NaturalQuestions / TriviaQA and reports
# **+55% relative Exact Match** over Contriever on factoid QA when the
# retriever embeds propositions.

# This module ships:

# * ``NullPropositionDecomposer`` — default OFF Null Object. Returns
#   ``[text]`` verbatim so callers can wire ``await decomposer.decompose(t)``
#   unconditionally and still pay zero LLM cost until opt-in.
# * ``LLMPropositionDecomposer`` — calls an injected ``LLMPort`` with the
#   Chen et al. decomposition prompt. Domain-neutral (no industry/brand
#   literals leaking into the prompt). HALLU=0 sacred: on ANY adapter
#   failure / empty completion / malformed output, returns ``[text]`` (the
#   original paragraph) — never fabricated propositions.
# * ``build_proposition_decomposer(provider, **kwargs)`` — DI registry
#   factory; the DI container reads ``proposition_llm_provider`` from
#   ``system_config`` (Redis-cached) and asks for the matching
#   ``PropositionDecomposerPort`` implementation.

# The output format expected from the LLM is **one proposition per line**.
# Numbered / bulleted prefixes (``"1. "``, ``"- "``, ``"* "``) are
# stripped defensively so a model that ignores the "no numbering"
# instruction still produces clean propositions.
# """

# from __future__ import annotations

# import re
# from typing import Any

# import structlog

# from ragbot.application.dto.ai_specs import LLMSpec
# from ragbot.application.ports.llm_port import LLMMessage, LLMPort
# from ragbot.application.ports.proposition_decomposer_port import (
#     PropositionDecomposerPort,
# )
# from ragbot.shared.constants import (
#     DEFAULT_PROPOSITION_LLM_MAX_INPUT_CHARS,
#     DEFAULT_PROPOSITION_LLM_MIN_LEN,
# )
# from ragbot.shared.errors import RetrievalError
# from ragbot.shared.types import TenantId, TraceId

# logger = structlog.get_logger(__name__)


# Domain-neutral system instruction adapted from Chen et al. (Dense X
# Retrieval, EMNLP 2024, Appendix A "Decomposition Prompt"). Key
# requirements:
#   1. Each proposition expresses a single factual claim.
#   2. Pronouns and coreferring noun phrases are replaced by their full
#      entity names — the proposition must stand alone outside the
#      paragraph.
#   3. The model must NOT invent facts that are not present in the
#      source (HALLU=0 sacred — Application MUST NOT inject and the
#      retrieval enhancement MUST NOT fabricate).
#   4. Output: one proposition per line, no numbering, no preamble.
#   5. Preserve the source language (Vietnamese stays Vietnamese,
#      English stays English) so the embedder receives text in the
#      same language as the corpus.
# _PROPOSITION_SYSTEM_INSTRUCTION = (
#     "You are a domain-agnostic information-extraction assistant. Decompose "
#     "the user paragraph into a list of atomic, self-contained propositions.\n\n"
#     "Rules:\n"
#     "- Each proposition expresses ONE single factual claim from the source.\n"
#     "- Replace pronouns (he/she/it/they/this/that/...) and other coreferring "
#     "noun phrases with the full entity name they refer to, so the proposition "
#     "is understandable on its own without the surrounding paragraph.\n"
#     "- Use ONLY facts present in the source paragraph. Do NOT add, infer, or "
#     "extrapolate any information that is not stated.\n"
#     "- Preserve the source language exactly (do not translate).\n"
#     "- Output format: one proposition per line. No numbering, no bullets, no "
#     "preamble, no trailing commentary. Just the propositions."
# )


# Strip a leading enumeration marker like ``"1. "``, ``"1) "``, ``"- "``,
# ``"* "``, ``"• "`` that some models add despite the prompt forbidding
# numbering. Done defensively so a single model regression does not
# leak garbage tokens into the embedder.
# _LEADING_ENUM_PATTERN = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


# def _clean_proposition_line(line: str) -> str:
#     """Strip enumeration prefix + surrounding whitespace from one line."""
#     stripped = _LEADING_ENUM_PATTERN.sub("", line.strip())
#     return stripped.strip()


# def _parse_proposition_output(content: str, min_len: int) -> list[str]:
#     """Parse the LLM completion into a clean list of propositions.

#     Splits on newlines, drops empty lines, strips enumeration prefixes,
#     drops anything shorter than ``min_len`` characters (residual noise
#     from a malformed completion — e.g. one stray comma on its own line).
#     """
#     if not content:
#         return []
#     cleaned: list[str] = []
#     for raw_line in content.splitlines():
#         prop = _clean_proposition_line(raw_line)
#         if len(prop) >= min_len:
#             cleaned.append(prop)
#     return cleaned


# class NullPropositionDecomposer:
#     """No-op Null Object: returns the input as a single-element list.

#     Default-OFF baseline. Selecting this implementation is a deliberate
#     operator choice (or the platform default until opt-in). Logged at
#     debug so an operator can confirm the Null branch is in effect
#     without spamming hot-path logs.
#     """

#     @staticmethod
#     def get_provider_name() -> str:
#         return "null"

#     async def decompose(self, text: str) -> list[str]:
#         """Return ``[text]`` verbatim — i.e. legacy single-chunk behaviour.

#         Returns an empty list ONLY when ``text`` itself is empty /
#         whitespace — callers can rely on a non-empty list whenever the
#         input had real content.
#         """
#         if not text or not text.strip():
#             return []
#         logger.debug("null_proposition_bypass", text_chars=len(text))
#         return [text]


# class LLMPropositionDecomposer:
#     """LLM-backed proposition decomposition strategy (Chen et al. 2024).

#     @param llm: the ``LLMPort`` to call (typically the small/fast tier).
#     @param spec: ``LLMSpec`` bound at construction; model + max_tokens +
#         temperature flow from constants / system_config so the call site
#         carries no magic numbers.
#     @param record_tenant_id: tenant scope for the LLM call.
#     @param trace_id: distributed trace id to thread through the LLM call.
#     @param max_input_chars: per-call source-text ceiling — paragraphs
#         longer than this are returned as-is (``[text]``) so a single LLM
#         call never blows the context window. Upstream chunker is
#         responsible for splitting megaparagraphs before reaching here.
#     @param min_proposition_len: minimum proposition length (characters);
#         shorter lines from the completion are dropped as noise.
#     """

#     def __init__(
#         self,
#         *,
#         llm: LLMPort,
#         spec: LLMSpec,
#         record_tenant_id: TenantId,
#         trace_id: TraceId,
#         max_input_chars: int = DEFAULT_PROPOSITION_LLM_MAX_INPUT_CHARS,
#         min_proposition_len: int = DEFAULT_PROPOSITION_LLM_MIN_LEN,
#     ) -> None:
#         self._llm = llm
#         self._spec = spec
#         self._record_tenant_id = record_tenant_id
#         self._trace_id = trace_id
#         self._max_input_chars = max_input_chars
#         self._min_proposition_len = min_proposition_len

#     @staticmethod
#     def get_provider_name() -> str:
#         return "llm"

#     async def decompose(self, text: str) -> list[str]:
#         """Decompose ``text`` into atomic propositions via the LLM.

#         Returns:
#             List of self-contained propositions on success. The original
#             paragraph wrapped as ``[text]`` (single element) on any
#             failure path — empty/whitespace input, oversized input
#             (> ``max_input_chars``), LLM adapter error, empty
#             completion, or completion that parsed to zero usable
#             propositions. HALLU=0 sacred: never fabricates.
#         """
#         if not text or not text.strip():
#             return []

        # Guard: oversized input → degrade silent (caller already
        # chunked, but defence-in-depth so we never blow context).
#         if len(text) > self._max_input_chars:
#             logger.info(
#                 "llm_proposition_oversized_input_fallback",
#                 text_chars=len(text),
#                 max_input_chars=self._max_input_chars,
#             )
#             return [text]

#         try:
#             response = await self._llm.complete(
#                 messages=[
#                     LLMMessage(
#                         role="system",
#                         content=_PROPOSITION_SYSTEM_INSTRUCTION,
#                     ),
#                     LLMMessage(role="user", content=text),
#                 ],
#                 spec=self._spec,
#                 record_tenant_id=self._record_tenant_id,
#                 trace_id=self._trace_id,
#             )
#         except (RetrievalError, OSError, ValueError, TimeoutError) as exc:
            # Degrade silent — proposition decomp is best-effort. HALLU=0
            # sacred: fall back to original chunk, never fabricate.
#             logger.warning(
#                 "llm_proposition_adapter_failure",
#                 error=str(exc),
#                 error_type=type(exc).__name__,
#                 text_chars=len(text),
#             )
#             return [text]

#         propositions = _parse_proposition_output(
#             response.content or "",
#             min_len=self._min_proposition_len,
#         )
#         if not propositions:
            # Empty / malformed completion → fall back to original
            # chunk so the embedder still receives the source paragraph
            # and ingest never silently drops content.
#             logger.info(
#                 "llm_proposition_empty_completion",
#                 text_chars=len(text),
#                 tokens_in=response.tokens_in,
#                 tokens_out=response.tokens_out,
#             )
#             return [text]

#         logger.info(
#             "proposition_llm_done",
#             text_chars=len(text),
#             decomp_count=len(propositions),
#             tokens_in=response.tokens_in,
#             tokens_out=response.tokens_out,
#             cost_usd=response.cost_usd,
#             latency_ms=response.latency_ms,
#             feature_flag="proposition_llm_decomp_enabled",
#         )
#         return propositions


# ---------------------------------------------------------------------------
# Registry (Strategy + DI pattern)
# ---------------------------------------------------------------------------

# _REGISTRY: dict[str, type[PropositionDecomposerPort]] = {
#     "null": NullPropositionDecomposer,
#     "llm": LLMPropositionDecomposer,
# }


# def build_proposition_decomposer(
#     provider: str, **kwargs: Any
# ) -> PropositionDecomposerPort:
#     """Construct the proposition decomposer matching ``provider``.

#     @param provider: registry key (``"null"`` | ``"llm"``).
#     @param kwargs: forwarded to the strategy constructor — ``llm=``,
#         ``spec=``, ``record_tenant_id=``, ``trace_id=`` are required for
#         ``"llm"``; ignored for ``"null"``.
#     @return: ``PropositionDecomposerPort`` instance.
#     @raise ValueError: unknown provider key — owner-opt-in surfaces loud,
#         not silent fallback.
#     """
#     key = (provider or "").strip().lower()
#     cls = _REGISTRY.get(key)
#     if cls is None:
#         raise ValueError(
#             f"unknown proposition decomposer provider: {provider!r}; "
#             f"registered={sorted(_REGISTRY.keys())}"
#         )
#     instance: PropositionDecomposerPort = cls(**kwargs)
#     return instance


# def list_providers() -> list[str]:
#     """Return registered provider keys (sorted, for stable test asserts)."""
#     return sorted(_REGISTRY.keys())


# __all__ = [
#     "LLMPropositionDecomposer",
#     "NullPropositionDecomposer",
#     "build_proposition_decomposer",
#     "list_providers",
# ]
