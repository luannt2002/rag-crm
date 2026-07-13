"""Pydantic schemas for structured LLM outputs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ragbot.shared.constants import (
    DEFAULT_DECOMPOSE_MAX_SUB_QUERIES,
    DEFAULT_GENERATE_CITATION_QUOTE_MAX_CHARS,
    DEFAULT_GENERATE_CITATIONS_MAX_N,
    DEFAULT_INTENT_CONFIDENCE_FALLBACK,
    DEFAULT_LLM_REASON_MAX_LEN,
    DEFAULT_UNDERSTAND_CONDENSED_QUERY_MAX_LEN,
)

# OpenAI strict json_schema mode requires additionalProperties:false at every level.
_STRICT_JSON_SCHEMA_CONFIG = ConfigDict(extra="forbid")


class GradeOutput(BaseModel):
    """CRAG-lite per-chunk relevance grading."""

    model_config = _STRICT_JSON_SCHEMA_CONFIG

    grade: Literal["yes", "no", "partial"]
    reason: str = Field(default="", max_length=DEFAULT_LLM_REASON_MAX_LEN)


class ReflectOutput(BaseModel):
    """Self-RAG reflection verdict on a generated answer."""

    model_config = _STRICT_JSON_SCHEMA_CONFIG

    action: Literal["keep", "rewrite", "reject"]
    reason: str = Field(default="", max_length=DEFAULT_LLM_REASON_MAX_LEN)


class DecomposeOutput(BaseModel):
    """Multi-hop decomposition into atomic sub-queries."""

    model_config = _STRICT_JSON_SCHEMA_CONFIG

    sub_queries: list[str] = Field(
        default_factory=list,
        max_length=DEFAULT_DECOMPOSE_MAX_SUB_QUERIES,
    )


class UnderstandOutput(BaseModel):
    """Condensed standalone query plus intent classification.

    Optional ``confidence`` field (LLM self-reported, range ``[0, 1]``).
    Default :data:`DEFAULT_INTENT_CONFIDENCE_FALLBACK` when the LLM omits
    it (older models / non-structured callers). The field is part of the
    *structured-output contract* — Pydantic schema
    declared once and consumed by the orchestrator (decompose gate) +
    Prometheus histogram. The orchestrator does NOT inject any extra
    instruction text into the LLM prompt; bot owners control behaviour
    exclusively via ``system_prompt``. When the LLM populates this field
    (per its own self-calibration), the application uses it; when absent,
    fallback keeps every existing flow unchanged.

    ``condensed_query`` is OPTIONAL (default ``""``). A model that does not
    emit a rewrite — or emits an empty one — must not break understand:
    the orchestrator already keeps the original ``state["query"]`` whenever
    the condensed value is empty, so the effective default is "the query
    itself". Hardening this lets a model behind an OpenAI-shape gateway
    that drops/empties the field degrade to a no-rewrite pass instead of a
    schema-validation failure that empties the whole understand result.
    """

    model_config = _STRICT_JSON_SCHEMA_CONFIG

    @model_validator(mode="before")
    @classmethod
    def _accept_query_alias(cls, data: Any) -> Any:
        """Tolerate gateways that echo the prompt under a bare ``query`` key.

        Some OpenAI-shape gateways ignore the ``condensed_query`` field name
        and return ``{"query": "<prompt>", "intent": ...}``. Map ``query`` onto
        ``condensed_query`` (canonical wins if both present) so a valid intent
        classification is kept instead of failing ``extra_forbidden`` — which
        would otherwise burn a repair round-trip and, on giving up, demote the
        turn to the ``intent`` fallback with the tightest retrieval budget.
        Runs BEFORE ``extra='forbid'``, so only ``query`` is absorbed; every
        other unexpected key is still rejected. Schema sent to strict providers
        is unchanged (validators do not affect ``model_json_schema``).
        """
        if isinstance(data, dict) and "query" in data:
            data = {**data}
            aliased = data.pop("query")
            data.setdefault("condensed_query", aliased)
        return data

    condensed_query: str = Field(
        default="",
        max_length=DEFAULT_UNDERSTAND_CONDENSED_QUERY_MAX_LEN,
    )
    intent: Literal[
        "factoid",
        "comparison",
        "multi_hop",
        "aggregation",
        "out_of_scope",
        "greeting",
        "feedback",
        "chitchat",  # short social messages
        "vu_vo",     # vague acknowledgements
    ]
    confidence: float = Field(
        default=DEFAULT_INTENT_CONFIDENCE_FALLBACK,
        ge=0.0,
        le=1.0,
        description=(
            "LLM-reported classification confidence, range [0,1]. Optional "
            "in the JSON schema — default DEFAULT_INTENT_CONFIDENCE_FALLBACK "
            "preserves legacy behaviour when the LLM omits the key."
        ),
    )


class CitationItem(BaseModel):
    """One citation: chunk reference plus verbatim quote."""

    model_config = _STRICT_JSON_SCHEMA_CONFIG

    chunk_id: str = Field(..., min_length=1)
    quote: str = Field(
        ...,
        min_length=1,
        max_length=DEFAULT_GENERATE_CITATION_QUOTE_MAX_CHARS,
    )


class SubAnswerItem(BaseModel):
    """One enumerated facet of a multi-fact answer.

    Reasoning-first scaffolding for aggregation / comparison / list
    intents: the LLM enumerates each facet (with its grounding value +
    optional citation) BEFORE composing the synthesized ``answer`` string.
    Keeping each fact as a discrete row prevents the flat-string path from
    dropping facts when a question spans multiple rows of the corpus.

    SHAPE only — the application never reads ``sub_answers`` to mutate the
    final ``answer`` text; the field exists so the model self-organizes
    multi-fact reasoning. ``citation`` is optional (``None`` when the LLM
    does not attribute a specific chunk to the facet).
    """

    model_config = _STRICT_JSON_SCHEMA_CONFIG

    facet: str = Field(..., min_length=1)
    value: str = Field(..., min_length=1)
    citation: str | None = None


class GenerateOutput(BaseModel):
    """Generation output: answer, citations, no-context flag.

    ``sub_answers`` is the OPTIONAL structured-reasoning path (default
    empty → fully backward compatible). When the orchestrator requests the
    structured schema for multi-fact intents, the model enumerates each
    facet in ``sub_answers`` first, then writes the synthesized final
    ``answer``. The application consumes ``answer`` exactly as before.
    """

    model_config = _STRICT_JSON_SCHEMA_CONFIG

    answer: str = Field(..., min_length=1)
    citations: list[CitationItem] = Field(
        default_factory=list,
        max_length=DEFAULT_GENERATE_CITATIONS_MAX_N,
    )
    sub_answers: list[SubAnswerItem] = Field(default_factory=list)
    used_no_context: bool = False


class GenerateFlatOutput(BaseModel):
    """Flat generation shape: answer + citations, no enumerated facets.

    The default generation contract for factoid / single-fact intents.
    Identical to :class:`GenerateOutput` minus ``sub_answers`` so the
    JSON schema sent to the model stays lean (no per-facet array) and
    avoids token bloat when the question maps to a single corpus fact.
    The orchestrator selects :class:`GenerateOutput` (with ``sub_answers``)
    only for multi-fact intents when the structured-sub-answer flag is ON.
    """

    model_config = _STRICT_JSON_SCHEMA_CONFIG

    answer: str = Field(..., min_length=1)
    citations: list[CitationItem] = Field(
        default_factory=list,
        max_length=DEFAULT_GENERATE_CITATIONS_MAX_N,
    )
    used_no_context: bool = False


class ChunkGradeItem(BaseModel):
    """Per-chunk grade verdict in a batch grade call."""

    model_config = _STRICT_JSON_SCHEMA_CONFIG

    chunk_id: str = Field(..., min_length=1)
    grade: Literal["yes", "no", "partial"]


class GradeBatchOutput(BaseModel):
    """Batch CRAG grading — verdicts for every candidate chunk."""

    model_config = _STRICT_JSON_SCHEMA_CONFIG

    grades: list[ChunkGradeItem] = Field(..., min_length=1)


class GroundingVerdict(BaseModel):
    """Per-claim grounding verdict."""

    model_config = _STRICT_JSON_SCHEMA_CONFIG

    claim_index: int = Field(..., ge=0)
    verdict: Literal["SUPPORTED", "NOT_SUPPORTED"]


class GroundingVerdictsOutput(BaseModel):
    """List of per-claim grounding verdicts."""

    model_config = _STRICT_JSON_SCHEMA_CONFIG

    verdicts: list[GroundingVerdict] = Field(default_factory=list)


__all__ = [
    "ChunkGradeItem",
    "CitationItem",
    "DecomposeOutput",
    "GenerateFlatOutput",
    "GenerateOutput",
    "GradeBatchOutput",
    "GradeOutput",
    "GroundingVerdict",
    "GroundingVerdictsOutput",
    "ReflectOutput",
    "SubAnswerItem",
    "UnderstandOutput",
]
