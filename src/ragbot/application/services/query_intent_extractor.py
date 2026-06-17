"""Query intent extractor — read-side companion of metadata extraction.

LLM labels a user query with the same vocabulary as the write-side ingest
metadata extractor. Result is a small JSON dict the caller feeds into
``WHERE metadata_json @> ...``. Empty dict = skip the filter.

Both vocabulary and prompt come from ``system_config`` so the platform
stays domain-neutral; bot owners / operators seed their own enum.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from ragbot.shared.constants import (
    DEFAULT_HTTP_TIMEOUT_S,
    DEFAULT_INTENT_EXTRACTOR_MAX_TOKENS,
    DEFAULT_INTENT_EXTRACTOR_QUERY_PREVIEW_CHARS,
    DEFAULT_METADATA_EXTRACTION_MODEL,
)

logger = structlog.get_logger(__name__)


def _strip_fences(raw: str) -> str:
    """Strip ```json ... ``` markdown fences. Returns body or empty string."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    return cleaned.strip()


def _normalise(payload: dict[str, Any], allowed_doc_types: frozenset[str]) -> dict[str, Any]:
    """Drop unknown / out-of-vocab keys; return empty dict when nothing valid."""
    out: dict[str, Any] = {}
    raw_doc_type = payload.get("document_type")
    if isinstance(raw_doc_type, str):
        normalised = raw_doc_type.strip().lower()
        if allowed_doc_types and normalised in allowed_doc_types:
            out["document_type"] = normalised
    entity = payload.get("entity")
    if isinstance(entity, str):
        entity_clean = entity.strip()
        if entity_clean:
            out["entity"] = entity_clean
    return out


async def extract_intent(
    query: str,
    *,
    model_id: str | None = None,
    system_prompt: str | None = None,
    allowed_doc_types: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Return a JSONB-containment-ready filter dict for ``query``.

    Args:
        query: User question (rewritten or raw).
        model_id: Optional per-call model override.
        system_prompt: Operator-supplied prompt. Empty/None → skip the call,
            return ``{}``.
        allowed_doc_types: Operator-supplied vocabulary. Empty → no
            ``document_type`` field in the output.

    Returns:
        Small dict like ``{"document_type": "<value>"}`` or ``{}``. Empty
        dict means "skip the filter".
    """
    text = (query or "").strip()
    if not text or not system_prompt:
        return {}

    model = model_id or DEFAULT_METADATA_EXTRACTION_MODEL
    preview = text[:DEFAULT_INTENT_EXTRACTOR_QUERY_PREVIEW_CHARS]
    vocab = allowed_doc_types or frozenset()

    try:
        import litellm as _litellm

        resp = await _litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": preview},
            ],
            temperature=0.0,
            max_tokens=DEFAULT_INTENT_EXTRACTOR_MAX_TOKENS,
            timeout=DEFAULT_HTTP_TIMEOUT_S,
        )
        raw = (resp.choices[0].message.content or "").strip()
        body = _strip_fences(raw)
        if not body:
            return {}
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            logger.debug("intent_extractor_non_dict", raw=raw[:120])
            return {}
        return _normalise(parsed, vocab)
    except json.JSONDecodeError:
        logger.debug("intent_extractor_json_parse_failed", raw=raw[:120])
        return {}
    except Exception:  # noqa: BLE001 — provider/network failure: relax to no-filter
        logger.warning("intent_extractor_failed", exc_info=True)
        return {}


__all__ = ["extract_intent"]
