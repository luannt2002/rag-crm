"""Pure, stateless helpers extracted from ``query_graph``.

These functions close over nothing in ``build_graph`` — they depend only on
their arguments plus stdlib / a single shared constant. Keeping them in a
separate module shrinks the ``query_graph`` god-file without touching the
graph-node closures (which DO capture ``di_kwargs`` and must stay put).

``query_graph`` re-imports every name defined here, so existing import paths
(``from ragbot.orchestration.query_graph import _is_null_lexical`` etc.) and
the di_kwargs threading into node functions keep working unchanged.
"""
from __future__ import annotations

import hashlib
import json as _json_mod
from typing import Any
from uuid import UUID

from ragbot.shared.constants import (
    DEFAULT_BOT_CACHE_VERSION_HASH_LEN,
    DEFAULT_PARSE_DECOMPOSED_MAX_SUB,
)


def parse_decomposed_sub_queries(
    raw_llm_text: str, *, max_sub: int = DEFAULT_PARSE_DECOMPOSED_MAX_SUB,
) -> list[str]:
    """Parse LLM JSON-array output. raw text → list[str] (max `max_sub`)."""
    if not raw_llm_text:
        return []
    text = raw_llm_text.strip()
    if not text.startswith("["):
        return []
    try:
        parsed = _json_mod.loads(text)
    except (ValueError, TypeError):
        # Malformed JSON / non-string input — defensive parse failure.
        return []
    # A "decomposition" is structurally ≥2 sub-queries; fewer means the LLM
    # decided the query was atomic — not a magic threshold but a definition.
    if not isinstance(parsed, list) or len(parsed) < 2:  # noqa: PLR2004
        return []
    return [str(q).strip() for q in parsed[:max_sub] if str(q).strip()]


def expand_parent_chunks(
    chunks: list[dict],
    parent_map: dict[str, dict],
) -> list[dict]:
    """Swap child chunks for their parent content (small-to-big retrieval); dedup by parent id."""
    seen_parents: set[str] = set()
    expanded: list[dict] = []
    for chunk in chunks:
        pcid = chunk.get("parent_chunk_id")
        if pcid and str(pcid) in parent_map:
            pcid_str = str(pcid)
            if pcid_str in seen_parents:
                continue  # dedup
            seen_parents.add(pcid_str)
            parent = parent_map[pcid_str]
            expanded.append({
                **chunk,
                "content": parent["content"],
                "text": parent.get("text", parent["content"]),
                "chunk_id": pcid_str,
                "is_parent_expanded": True,
            })
        else:
            expanded.append(chunk)
    return expanded


def _uuid_or_none(value: Any) -> Any:
    """Coerce a state UUID-like value to UUID, or None on missing/invalid."""
    if value is None:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None


def _parse_doc_type_vocabulary(raw: Any) -> frozenset[str]:
    """Parse comma-separated or JSON-list vocabulary string into a frozenset."""
    if not raw:
        return frozenset()
    if isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset(str(v).strip().lower() for v in raw if str(v).strip())
    text = str(raw).strip()
    if not text:
        return frozenset()
    if text.startswith("["):
        try:
            parsed = _json_mod.loads(text)
            if isinstance(parsed, list):
                return frozenset(str(v).strip().lower() for v in parsed if str(v).strip())
        except (ValueError, TypeError):
            return frozenset()
    return frozenset(t.strip().lower() for t in text.split(",") if t.strip())


def _render_captured_slots(action_state: dict, action_cfg: dict) -> str:
    """Render captured + still-missing slot DATA for owner placeholder binding.

    Sacred-rule 10: this emits structured DATA only (key="value" + a neutral
    ``missing:`` list of required-but-unfilled slot names) — NO behavioural
    text, NO instruction, NO brand/domain literal. The bot owner places
    ``{captured_slots}`` in their ``system_prompt`` and writes the surrounding
    instruction themselves; the platform merely substitutes the live values so
    the LLM can ask only for what is missing instead of re-asking captured info.

    Tokens ``missing``/``none`` are neutral technical markers (not Vietnamese
    behavioural copy), keeping the binding language- and domain-agnostic.
    """
    filled: dict = (action_state or {}).get("slots_filled", {}) or {}
    # Required slots come from the matching sub-schema (by current intent, else
    # the first declared sub-schema) — same selection slot_extractor uses.
    schema: dict = (action_cfg or {}).get("slots_schema", {}) or {}
    intent = (action_state or {}).get("intent") or ""
    sub_key = intent if intent in schema else next(iter(schema), None)
    required: list = list((schema.get(sub_key, {}) or {}).get("required", [])) if sub_key else []

    pairs = [f'{k}="{v}"' for k, v in filled.items() if v not in (None, "")]
    missing = [s for s in required if not filled.get(s)]
    filled_str = ", ".join(pairs) if pairs else "none"
    missing_str = ", ".join(missing) if missing else "none"
    return f"{filled_str}; missing: {missing_str}"


def _compute_bot_cache_version(system_prompt: str | None, oos_answer_template: str | None) -> str:
    """Derive cache-bust version; changes when system_prompt or oos_answer_template change."""
    payload = (system_prompt or "") + "|" + (oos_answer_template or "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:DEFAULT_BOT_CACHE_VERSION_HASH_LEN]


def _pcfg(state: Any, key: str, default: Any = None) -> Any:
    """Read pipeline config value from GraphState with fallback default.

    260525 Bug #12 — also treat ``None`` as "missing". The Bug #7c bulk
    closure populates 78 keys with ``raw.get(key, None)`` so the key is
    PRESENT in the dict but the value is ``None`` (no operator override
    in ``system_config``). Pre-fix this short-circuited the caller-side
    ``DEFAULT_*`` fallback and propagated ``None`` straight through to
    code like ``float(_pcfg(...))`` which then crashed.

    Semantically ``None`` means "no operator override → use caller
    default", which is what callers already pass as ``default``.
    """
    raw = (state.get("pipeline_config") or {}).get(key, default)
    return default if raw is None else raw


def _is_null_lexical(adapter: Any) -> bool:
    """Return True when ``adapter`` is the Null Object for lexical retrieval.

    Probes ``get_provider_name``/``mode`` instead of an ``isinstance`` check
    so test doubles + future replacement Null adapters work uniformly.
    A bare object that doesn't expose either marker is treated as a real
    adapter (conservative — better to attempt a search than silently skip).
    """
    if adapter is None:
        return True
    get_name = getattr(adapter, "get_provider_name", None)
    if callable(get_name):
        try:
            if get_name() == "null":
                return True
        except (AttributeError, TypeError, RuntimeError):
            # Best-effort probe; treat probe failure as real adapter
            # (conservative — better to attempt search than silently skip).
            return False
    mode_attr = getattr(adapter, "mode", None)
    return isinstance(mode_attr, str) and mode_attr == "null"
