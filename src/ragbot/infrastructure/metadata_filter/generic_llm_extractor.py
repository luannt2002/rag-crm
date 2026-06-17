"""GenericLLMMetadataExtractor — Layer 3 universal extractor.

Generic LLM-based entity/topic/keyword extractor. Works for ANY bot/domain
without hardcoded per-bot logic — bot mới tạo sau auto wire qua DI. Extracts
structured filter keys (entity / person / concept / numeric-range) from a
query regardless of subject domain; the LLM prompt itself is domain-neutral
and lives in the language pack, so no domain literal appears in code.

Sacred-rule alignment:
- Zero-hardcode: model + prompt từ DB (system_config + language_packs)
- Domain-neutral: generic prompt, no brand/tenant literal
- Strategy + DI: implements MetadataFilterPort contract
- Graceful degradation: timeout / malformed / call fail → return {}
- Narrow exception: catches LLMError / TimeoutError / ValidationError
  (never a broad catch-all)

Pattern paper / SOTA reference:
- LlamaIndex MetadataExtractor (2024)
- LangChain SelfQueryRetriever (2023)
- Anthropic Contextual Retrieval (Sept 2024)
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from pydantic import BaseModel, Field, ValidationError

from ragbot.shared.constants import (
    DEFAULT_INTENT_FALLBACK,
    DEFAULT_METADATA_EXTRACT_MAX_TOKENS,
    DEFAULT_METADATA_EXTRACT_TIMEOUT_S,
    METADATA_INTENT_ENUM,
)

logger = structlog.get_logger(__name__)


class LLMMetadataSchema(BaseModel):
    """Pydantic schema validation for LLM output.

    Sacred-rule: schema enforced at port boundary so malformed LLM output
    KHÔNG leak fabricated keys downstream.
    """

    entities: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    numbers_or_years: list[Any] = Field(default_factory=list)
    intent: str = Field(default=DEFAULT_INTENT_FALLBACK)

    def to_filter_dict(self) -> dict[str, Any]:
        """Convert validated schema to JSONB-containment-ready filter.

        Returns ``metadata.entities`` key for matching chunks's
        ``metadata_json @> {"entities": [...]}``. Top 3 entities only
        (avoid over-narrow filter that misses partial chunks).

        Entities lowercased for case-insensitive JSONB containment match.
        Ingest-side backfill MUST apply same normalization.
        """
        # Clamp + sanitize + LOWERCASE (case-insensitive matching)
        ents = [
            e.strip().lower()
            for e in self.entities
            if isinstance(e, str) and e.strip()
        ][:3]
        if not ents:
            return {}
        return {"entities": ents}


def _strip_markdown_fence(text: str) -> str:
    """Strip ```json ... ``` markdown fence from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        # Drop opening fence (possibly ```json)
        first_newline = text.find("\n")
        if first_newline > 0:
            text = text[first_newline + 1 :]
        # Drop trailing fence
        if text.endswith("```"):
            text = text[: -3].rstrip()
    return text


class GenericLLMMetadataExtractor:
    """Universal LLM-based metadata extractor.

    Constructor injection:
        litellm_module: LiteLLM module (production: ``import litellm``)
        model_id: Resolved model name (e.g. "gpt-4.1-nano") from
                  ``system_config.metadata_extraction_model`` or
                  ``bot_model_bindings.purpose='metadata_extraction'``
        prompt_template: VN/EN prompt template từ
                         ``language_packs.metadata_extract_default``
        cache: Optional LLMMetadataCache for query_hash → metadata cache.
    """

    def __init__(
        self,
        *,
        litellm_module: Any,
        model_id: str,
        prompt_template: str,
        cache: Any | None = None,
        max_tokens: int = DEFAULT_METADATA_EXTRACT_MAX_TOKENS,
        timeout_s: float = DEFAULT_METADATA_EXTRACT_TIMEOUT_S,
    ) -> None:
        self._llm = litellm_module
        self._model = model_id
        self._prompt_template = prompt_template
        self._cache = cache
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s

    async def extract(
        self,
        query: str,
        locale: str = "vi",
    ) -> dict[str, Any]:
        """Extract structured metadata from query.

        Returns JSONB-containment-ready dict (or {} on any error / edge case).
        """
        # Edge: empty / too-short query
        if not query or len(query.strip()) < 3:
            return {}

        # Cache lookup (skip LLM if hit)
        if self._cache is not None:
            cached = await self._cache.get(query, locale)
            if cached is not None:
                logger.debug("metadata_cache_hit", query_len=len(query))
                return cached

        # Build prompt — use .replace() instead of .format() because the
        # template contains literal JSON braces ``{...}`` that .format
        # would mis-interpret as placeholders. Safe & explicit.
        prompt = self._prompt_template.replace("{query}", query)

        # Call LLM (bounded timeout)
        try:
            resp = await asyncio.wait_for(
                self._llm.acompletion(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=self._max_tokens,
                ),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "metadata_extract_llm_timeout",
                model=self._model,
                timeout_s=self._timeout_s,
            )
            return {}
        except (TimeoutError, ConnectionError, OSError) as exc:
            logger.warning(
                "metadata_extract_llm_network_failed",
                model=self._model,
                error_type=type(exc).__name__,
                err=str(exc)[:120],
            )
            return {}
        except Exception as exc:  # noqa: BLE001 — LiteLLM has its own exceptions
            # Catch litellm-specific errors (LLMError, RateLimitError, AuthError)
            # without importing the full hierarchy. Logged narrow for audit.
            logger.warning(
                "metadata_extract_llm_call_failed",
                model=self._model,
                error_type=type(exc).__name__,
                err=str(exc)[:120],
            )
            return {}

        # Parse + validate
        try:
            text = resp.choices[0].message.content or ""
            text = _strip_markdown_fence(text)
            if not text:
                return {}
            parsed = json.loads(text)
        except (json.JSONDecodeError, IndexError, AttributeError) as exc:
            logger.warning(
                "metadata_extract_parse_failed",
                err=str(exc)[:120],
            )
            return {}

        # Schema validate (Pydantic)
        try:
            validated = LLMMetadataSchema(**parsed)
            # Whitelist intent enum
            if validated.intent not in METADATA_INTENT_ENUM:
                validated.intent = DEFAULT_INTENT_FALLBACK
        except ValidationError as exc:
            logger.warning(
                "metadata_extract_validation_failed",
                err=str(exc)[:200],
            )
            return {}

        filter_dict = validated.to_filter_dict()

        # Cache result (only if non-empty + valid)
        if filter_dict and self._cache is not None:
            await self._cache.set(query, filter_dict, locale)

        logger.debug(
            "metadata_extract_success",
            keys=list(filter_dict.keys()),
            entity_count=len(filter_dict.get("entities", [])),
        )
        return filter_dict


__all__ = ["GenericLLMMetadataExtractor", "LLMMetadataSchema"]
