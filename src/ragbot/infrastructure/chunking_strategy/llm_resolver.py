"""LLMChunkingStrategyResolver — AdapChunk Tầng 3/4 LLM Strategy Selector (spec §4).

Given the rule-extracted ``DocumentProfile``, asks an LLM to pick a chunking
strategy and return a structured decision:

    {"strategy", "confidence", "reasoning", "detected_type", "risk_factors"}

The LLM only ever sees document SHAPE statistics (headings / tables / prose
density) — domain-neutral, never industry or brand vocabulary.

Graceful degradation (aux-never-kills-main / HALLU=0): any failure path — LLM
transport error, circuit-breaker, empty completion, unparseable JSON, or an
out-of-vocabulary strategy — falls back to the injected rule resolver. The
caller still runs the deterministic ``apply_cross_check`` guard (spec Tầng 5)
on the returned strategy, so an unreasonable LLM pick is OVERRIDDEN, not
trusted blindly.
"""
from __future__ import annotations

import json
from typing import cast

import structlog

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.application.ports.llm_port import LLMMessage, LLMPort
from ragbot.application.ports.strategy_ports import (
    ChunkingDecision,
    ChunkingStrategyResolverPort,
)
from ragbot.domain.entities.document_profile import DocumentProfile
from ragbot.shared.constants import (
    DEFAULT_STRATEGY_MIN_CONFIDENCE,
    DEFAULT_STRATEGY_REASONING_MAX_CHARS,
)
from ragbot.shared.errors import CircuitBreakerOpen, LLMError, RetrievalError
from ragbot.shared.types import BotId, ChunkingStrategyName, TenantId, TraceId

logger = structlog.get_logger(__name__)

# Prose strategies the LLM may choose among. CSV → table_csv and legal → hdt are
# decided deterministically by the fast-path BEFORE the LLM is consulted, so they
# are intentionally not offered here (no point paying for an LLM call on a case the
# rule already nails).
_ALLOWED: frozenset[str] = frozenset(
    {"hdt", "semantic", "proposition", "hybrid", "recursive"}
)

_SYSTEM_INSTRUCTION = (
    "You are a domain-agnostic document-chunking strategist for a retrieval index. "
    "Given QUANTITATIVE structure statistics of a document, choose the single best "
    "chunking strategy. Judge by SHAPE only (headings, tables, prose density) — never "
    "by topic, industry, or brand.\n\n"
    "Strategies:\n"
    "- HDT: split by heading hierarchy; each chunk keeps its structural path. Best for "
    "reports/theses with a clear heading tree.\n"
    "- SEMANTIC: split prose at semantic-shift boundaries. Best for long flowing prose "
    "with few headings.\n"
    "- PROPOSITION: split into atomic self-contained statements. Best for dense "
    "legal/contract/regulation text.\n"
    "- HYBRID: HDT at macro level + finer splitting inside long sections. Safe default "
    "when uncertain.\n"
    "- RECURSIVE: size-based, table-aware splitting. Conservative fallback.\n\n"
    "Return ONLY a JSON object — no markdown, no preamble:\n"
    '{"strategy":"HDT|SEMANTIC|PROPOSITION|HYBRID|RECURSIVE","confidence":0.0-1.0,'
    '"reasoning":"short why","detected_type":"document type","risk_factors":["..."]}'
)


def _profile_block(dp: DocumentProfile) -> str:
    hc = dp.heading_counts
    return (
        "Document profile (rule-extracted, quantitative):\n"
        f"- headings: H1={hc.h1} H2={hc.h2} H3={hc.h3} H4={hc.h4} (total={hc.total})\n"
        f"- has_table_of_contents: {dp.has_toc}\n"
        f"- tables: {dp.table_count} (avg {dp.table_avg_rows:.1f} rows)\n"
        f"- formulas: {dp.formula_count} · images: {dp.image_count} · "
        f"code_blocks: {dp.code_block_count}\n"
        f"- avg_text_block_length: {dp.avg_text_block_length:.1f} words\n"
        f"- heading_ratio: {dp.heading_ratio:.3f} · "
        f"mixed_content_score: {dp.mixed_content_score:.3f}\n"
        f"- total_blocks: {dp.total_blocks} · total_words: {dp.total_words} · "
        f"language: {dp.detected_language}"
    )


def _block_list_summary(blocks: list | None, *, max_blocks: int = 60) -> str:
    """Render the document's block list for the LLM (AdapChunk spec 4.1).

    The LLM judges chunking by SHAPE, so it needs to SEE the structure, not just
    the profile counts: heading text, a TEXT block's size + opening, a TABLE's
    header + dimensions, a FORMULA's LaTeX, an IMAGE's description. Defensive
    getattr/get so any Block-like shape (entity or dict) renders; truncated to
    ``max_blocks`` so a huge doc never blows the selector's token budget.
    """
    if not blocks:
        return ""
    def _attr(b: object, name: str, default: object = "") -> object:
        if isinstance(b, dict):
            return b.get(name, default)
        return getattr(b, name, default)
    lines: list[str] = []
    for b in blocks[:max_blocks]:
        btype = str(_attr(b, "type", _attr(b, "block_type", "TEXT"))).upper()
        content = str(_attr(b, "content", "") or "")
        if btype == "HEADING":
            lvl = _attr(b, "level", "")
            lines.append(f"HEADING(H{lvl}): {content[:120].strip()}")
        elif btype == "TABLE":
            first = content.splitlines()[0] if content else ""
            nrows = content.count("\n") + 1 if content else 0
            lines.append(f"TABLE[{nrows} rows] header: {first[:120].strip()}")
        elif btype == "FORMULA":
            lines.append(f"FORMULA: {content[:120].strip()}")
        elif btype == "IMAGE":
            desc = _attr(b, "description", "") or _attr(b, "ocr_description", "") or content
            lines.append(f"IMAGE: {str(desc)[:120].strip()}")
        else:  # TEXT / CODE / other
            wc = _attr(b, "word_count", len(content.split()))
            opening = " ".join(content.split()[:18])
            lines.append(f"TEXT[{wc}w]: {opening[:120].strip()}")
    if len(blocks) > max_blocks:
        lines.append(f"... (+{len(blocks) - max_blocks} more blocks)")
    return "BLOCK LIST (in order):\n" + "\n".join(lines)


def _extract_json(text: str) -> dict:
    """Pull the first balanced JSON object out of an LLM completion."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object in completion")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("completion JSON is not an object")
    return parsed


class LLMChunkingStrategyResolver:
    """LLM-backed AdapChunk strategy selector — degrades to a rule fallback.

    @param llm: ``LLMPort`` to call — provider-agnostic via LiteLLM, so the
        model comes from ``spec`` (e.g. the cheap/fast ``gpt-4.1-nano`` tier).
    @param spec: ``LLMSpec`` (model + max_tokens + temperature from config).
    @param fallback: rule resolver used on any LLM/parse failure.
    @param record_tenant_id / trace_id: scope + trace threaded to the LLM call.
    """

    def __init__(
        self,
        *,
        llm: LLMPort,
        spec: LLMSpec,
        fallback: ChunkingStrategyResolverPort,
        record_tenant_id: TenantId,
        trace_id: TraceId,
    ) -> None:
        self._llm = llm
        self._spec = spec
        self._fallback = fallback
        self._record_tenant_id = record_tenant_id
        self._trace_id = trace_id

    @staticmethod
    def get_provider_name() -> str:
        return "llm"

    async def resolve_strategy(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
        document_profile: DocumentProfile,
        blocks: list | None = None,
    ) -> ChunkingDecision:
        try:
            # AdapChunk spec 4.1: the selector sees BOTH the quantitative profile
            # AND the full block list (shape detail) — profile alone hides whether
            # headings are real sections or whether a "table" is a one-row caption.
            _user = _profile_block(document_profile)
            _bl = _block_list_summary(blocks)
            if _bl:
                _user = f"{_user}\n\n{_bl}"
            response = await self._llm.complete(
                messages=[
                    LLMMessage(role="system", content=_SYSTEM_INSTRUCTION),
                    LLMMessage(role="user", content=_user),
                ],
                spec=self._spec,
                record_tenant_id=record_tenant_id,
                trace_id=self._trace_id,
            )
            data = _extract_json(response.content or "")
            strategy = str(data["strategy"]).strip().lower()
            if strategy not in _ALLOWED:
                raise ValueError(f"out-of-vocab strategy: {strategy!r}")
            confidence = min(
                max(float(data.get("confidence", DEFAULT_STRATEGY_MIN_CONFIDENCE)), 0.0),
                1.0,
            )
            reasoning = str(data.get("reasoning", ""))[:DEFAULT_STRATEGY_REASONING_MAX_CHARS]
        except (
            LLMError,
            CircuitBreakerOpen,
            RetrievalError,
            OSError,
            ValueError,
            TypeError,
            KeyError,
            TimeoutError,
        ) as exc:
            # Aux never kills main — degrade to the deterministic rule path.
            logger.warning(
                "llm_strategy_resolver_degraded",
                error=str(exc),
                error_type=type(exc).__name__,
                feature_flag="chunking_strategy_provider",
            )
            return await self._fallback.resolve_strategy(
                record_bot_id,
                record_tenant_id=record_tenant_id,
                document_profile=document_profile,
                blocks=blocks,
            )

        logger.info(
            "llm_strategy_resolver_selected",
            strategy=strategy,
            confidence=confidence,
            tokens_in=getattr(response, "tokens_in", None),
            tokens_out=getattr(response, "tokens_out", None),
            cost_usd=getattr(response, "cost_usd", None),
        )
        return ChunkingDecision(
            strategy=cast("ChunkingStrategyName", strategy),
            forced=False,
            confidence=round(confidence, 2),
            reasoning=reasoning or "llm-selected",
        )


__all__ = ["LLMChunkingStrategyResolver"]
