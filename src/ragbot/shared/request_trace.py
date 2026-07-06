"""P4 verifiable request trace — one JSON per request, dev/uat only.

Writes the FULL flow of a single chat request to ``{trace_dir}/{request_id}.json``
so the owner can verify end-to-end what happened: the question, the steps that
ran, the chunks that reached the LLM, the exact final prompt, the RAW answer
BEFORE any guard substitution, the guard verdict, and the final answer served.

Gated to ``development``/``uat`` (``REQUEST_TRACE_ENVS``): production keeps this
off because the full prompt/answer can carry PII and the per-request file write
is debug overhead — production already has the structured ``request_steps`` +
``request_chunk_refs`` trail.

Auxiliary by contract: a write failure degrades silently (graceful degradation —
an aux dependency must never kill the main request). Every captured string field
is capped so a runaway context cannot fill the disk.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ragbot.shared.constants import (
    REQUEST_TRACE_DIR,
    REQUEST_TRACE_ENVS,
    REQUEST_TRACE_MAX_FIELD_CHARS,
)


def is_request_trace_enabled() -> bool:
    """True only in the envs where the full-fidelity trace is safe to write."""
    return os.getenv("APP_ENV", "development") in REQUEST_TRACE_ENVS


def _trace_dir() -> Path:
    return Path(os.getenv("RAGBOT_TRACE_DIR", REQUEST_TRACE_DIR))


def _cap(value: Any, *, max_chars: int) -> Any:
    """Cap strings; recurse into list/dict so a huge chunk/prompt cannot bloat
    the file. Non-container, non-string values pass through untouched."""
    if isinstance(value, str):
        if len(value) > max_chars:
            return value[:max_chars] + "…[truncated]"
        return value
    if isinstance(value, list):
        return [_cap(v, max_chars=max_chars) for v in value]
    if isinstance(value, dict):
        return {k: _cap(v, max_chars=max_chars) for k, v in value.items()}
    return value


def write_request_trace(
    *,
    request_id: str,
    trace: dict[str, Any],
    max_field_chars: int = REQUEST_TRACE_MAX_FIELD_CHARS,
) -> str | None:
    """Write *trace* to ``{trace_dir}/{request_id}.json``; return the path.

    No-op (returns None) when tracing is disabled or on any I/O error — the
    caller must never depend on the result.
    """
    if not is_request_trace_enabled():
        return None
    try:
        d = _trace_dir()
        d.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(c for c in str(request_id) if c.isalnum() or c in "-_") or "unknown"
        path = d / f"{safe_id}.json"
        payload = _cap(trace, max_chars=max_field_chars)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        return str(path)
    except (OSError, TypeError, ValueError):
        return None


__all__ = ["is_request_trace_enabled", "write_request_trace"]
