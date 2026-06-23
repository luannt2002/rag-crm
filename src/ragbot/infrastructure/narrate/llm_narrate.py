"""LLMNarrateGenerator — linearise TABLE / FORMULA / IMAGE via an LLM.

Stream 2A — Anthropic Contextual Retrieval (Sep 2024,
https://www.anthropic.com/news/contextual-retrieval): reports a
**-49% retrieval failure rate** when chunks are augmented with
LLM-generated context before embedding. RAG-Anything (HKUDS) extends
the same insight to non-prose blocks: tables, formulas, and images need
declarative natural-language linearisation BEFORE the embedder sees
them — raw markdown pipes or LaTeX braces sit far from real questions
in vector space.

Block-type prompt routing:
    - TABLE   → "Linearize this Markdown table into 1-2 natural language sentences."
    - FORMULA → "Describe this LaTeX formula in 1-2 natural language sentences."
    - IMAGE   → "Describe what this image (OCR/caption) conveys in 1-2 sentences."
    - other   → bypass (return content verbatim) — narration only applies
      to non-prose blocks; prose chunks are already embed-friendly.

Graceful degradation contract: any failure path (LLM adapter raise,
timeout, empty content) returns the **original content** so ingest
keeps working — narration is an enhancement, never a hard dependency.
HALLU=0 sacred: when narration fails we keep the source text, we do
NOT silently swap in a fabricated description or an empty string.

Domain-neutral: the system instruction never mentions any specific
industry / domain — it instructs the model to mirror the source content
exactly and produce a declarative summary.
"""

from __future__ import annotations

import structlog

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.application.ports.llm_port import LLMMessage, LLMPort
from ragbot.shared.constants import DEFAULT_LANGUAGE
from ragbot.shared.errors import CircuitBreakerOpen, LLMError, RetrievalError
from ragbot.shared.types import BlockType, TenantId, TraceId

logger = structlog.get_logger(__name__)


# Block-type-specific user prompt scaffolds. The system instruction is
# constant across block types — domain-neutral and declarative. Each block
# prompt is short by design (focus on CONTENT, not wrapper text) and
# LANGUAGE-NEUTRAL: the output language is named at runtime from the
# document's language code so an English / Khmer / Spanish corpus is never
# narrated with a hardcoded one. The system instruction also pins
# "preserve the source language exactly" as a second guard.
_NARRATE_SYSTEM_INSTRUCTION = (
    "You are a domain-agnostic content linearizer for a retrieval index. "
    "Given a non-prose block (table, formula, or image caption), produce "
    "a SHORT (1-2 sentence) natural-language description that conveys "
    "the same information.\n\n"
    "Rules:\n"
    "- Stay strictly grounded in the input — do NOT invent facts or numbers.\n"
    "- Preserve the source language exactly (do not translate).\n"
    "- Use declarative style (statements, not questions).\n"
    "- Output only the description, no preamble, no markdown."
)

_BLOCK_PROMPTS: dict[str, str] = {
    "TABLE": (
        "Linearize the table / data row below into 1-2 natural sentences in "
        "the {language} language, naming the key columns and what the row "
        "conveys. Return ONLY the description, no markdown, no preamble:\n\n"
        "{content}"
    ),
    "FORMULA": (
        "Describe the LaTeX formula / expression below in 1-2 natural sentences "
        "in the {language} language, naming the operation and the meaningful "
        "variables. Return ONLY the description, no markdown, no preamble:\n\n"
        "{content}"
    ),
    "IMAGE": (
        "Describe the image / OCR caption below in 1-2 natural sentences in the "
        "{language} language. Return ONLY the description:\n\n{content}"
    ),
}


class LLMNarrateGenerator:
    """LLM-backed Narrate strategy.

    @param llm: the ``LLMPort`` to call (typically the small/fast tier,
        Anthropic Haiku via Batch API for 50% discount).
    @param spec: ``LLMSpec`` bound at construction; model + max_tokens +
        temperature flow from constants / system_config so the call site
        carries no magic numbers.
    @param record_tenant_id: tenant scope for the LLM call.
    @param trace_id: distributed trace id to thread through the LLM call.
    """

    def __init__(
        self,
        *,
        llm: LLMPort,
        spec: LLMSpec,
        record_tenant_id: TenantId,
        trace_id: TraceId,
    ) -> None:
        self._llm = llm
        self._spec = spec
        self._record_tenant_id = record_tenant_id
        self._trace_id = trace_id

    @staticmethod
    def get_provider_name() -> str:
        return "llm"

    async def narrate(
        self,
        content: str,
        block_type: BlockType,
        *,
        language: str = DEFAULT_LANGUAGE,
    ) -> str:
        """Linearise ``content`` of ``block_type`` for embedding.

        @param language: the document's language code — named in the per-block
            prompt so the narration is produced in the source language.

        Returns:
            The LLM-drafted narration text on success; the **original**
            ``content`` if the block_type is unsupported (e.g. plain
            HEADING/TEXT/CODE/LIST — those embed fine raw), the input is
            empty, the LLM returns empty content, or the adapter raises
            a known transport / value error (degrade silent — HALLU=0).
        """
        if not content or not content.strip():
            return content

        prompt_template = _BLOCK_PROMPTS.get(block_type)
        if prompt_template is None:
            # Prose-like block types embed fine raw — skip the LLM hop.
            logger.debug(
                "llm_narrate_skip_block_type",
                block_type=block_type,
                content_chars=len(content),
            )
            return content

        user_message = prompt_template.format(
            content=content, language=language or DEFAULT_LANGUAGE,
        )

        try:
            response = await self._llm.complete(
                messages=[
                    LLMMessage(role="system", content=_NARRATE_SYSTEM_INSTRUCTION),
                    LLMMessage(role="user", content=user_message),
                ],
                spec=self._spec,
                record_tenant_id=self._record_tenant_id,
                trace_id=self._trace_id,
            )
        except (LLMError, CircuitBreakerOpen, RetrievalError, OSError, ValueError, TimeoutError) as exc:
            # Degrade silent — narration is an enhancement, never a hard dep.
            # HALLU=0: returning the raw content is safe; embedding it produces
            # a worse-but-truthful vector. Substituting an empty string OR a
            # fabricated description would either drop the chunk from recall
            # or seed hallucination downstream.
            # LLMError/CircuitBreakerOpen cover provider rate-limit (429) +
            # breaker-open: a single rate-limited chunk must NOT fail the whole
            # document ingest (which would re-run every chunk → retry storm).
            logger.warning(
                "llm_narrate_adapter_failure",
                error=str(exc),
                error_type=type(exc).__name__,
                block_type=block_type,
                content_chars=len(content),
                step_name=_step_name_for(block_type),
                feature_flag="narrate_then_embed_enabled",
            )
            return content

        narration = (response.content or "").strip()
        if not narration:
            logger.info(
                "llm_narrate_empty_completion",
                block_type=block_type,
                content_chars=len(content),
                step_name=_step_name_for(block_type),
                feature_flag="narrate_then_embed_enabled",
            )
            return content

        logger.debug(
            "llm_narrate_generated",
            block_type=block_type,
            content_chars=len(content),
            narrate_chars=len(narration),
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cost_usd=response.cost_usd,
            step_name=_step_name_for(block_type),
            feature_flag="narrate_then_embed_enabled",
        )
        return narration


def _step_name_for(block_type: BlockType) -> str:
    """Return the structlog ``step_name`` for telemetry per block type.

    Stable strings — the cost-audit dashboards filter on these.
    """
    lowered = (block_type or "").lower()
    if lowered in ("table", "formula", "image"):
        return f"narrate_{lowered}"
    return "narrate_other"


__all__ = ["LLMNarrateGenerator"]
