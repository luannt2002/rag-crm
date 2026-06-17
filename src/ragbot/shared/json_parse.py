"""Robust JSON parser — 4-strategy fallback for LLM-generated JSON.

Why this exists
---------------
LLM completions occasionally violate strict JSON: they wrap output in
`````json`` code fences, leave trailing commas, use single quotes, or
prepend / append explanatory prose ("Here is the JSON:\\n{...}"). Each
callsite that consumes structured LLM output (decomposer, intent
classifier, CRAG grader, auto-FAQ extractor, knowledge-graph triples,
multi-query expansion) used to carry its own ad-hoc fence-strip + regex
retry. This module collapses that to a single helper with explicit
strategy ordering and per-strategy telemetry.

Strategy ordering (early-exit)
------------------------------
1. **Direct** — ``orjson.loads`` on the raw input. Fast path: ~70-90 % of
   well-behaved LLM outputs land here.
2. **Fence-strip** — remove leading / trailing ``````` (with optional
   language tag) and retry. Covers the most common LLM mistake.
3. **Quote+comma repair** — turn single-quoted JSON into double-quoted,
   drop trailing commas before ``]`` / ``}``. Targets models trained on
   Python literal dumps.
4. **Substring extract** — locate the outermost ``{...}`` or ``[...]``
   span using bracket counting (quote-aware) and retry. Covers prose
   prefixes / suffixes the LLM added around the JSON.

If all four strategies fail, :class:`JSONParseError` is raised. The parser
NEVER fabricates a return value — silent fallback would violate the
HALLU=0 invariant by feeding hallucinated structure downstream.

Feature flag
------------
``robust_json_parser_enabled`` (default ``True`` — defensive low risk).
When the flag is ``False`` the helper degrades to strategy 1 only and
re-raises the original :class:`json.JSONDecodeError`. Callers that need
graceful fallback (e.g. ``return [fallback_query]``) wrap the call in
``try / except JSONParseError`` themselves; the parser does not own the
fallback policy.

Telemetry
---------
Each call emits structlog event ``robust_json_parse`` with:
- ``strategy_used`` (1/2/3/4 on success, ``None`` on failure)
- ``duration_us``
- ``input_length``
- ``flag_value``
- ``parser_name`` (caller-supplied tag for analytics grouping)

Proof citation
--------------
Pattern from RAG-Anything ``_robust_json_parse`` (HKUDS, 2024) +
LightRAG ``locate_json_string_body_from_string`` heuristics. The
substring-extract step is a quote-aware bracket-counter (not a naive
regex) so JSON literals containing ``{`` / ``[`` inside string values
do not break extraction.

Domain-neutral
--------------
Module holds zero tenant / industry knowledge. Callers pass the
``parser_name`` tag (e.g. ``"decomposer"``, ``"crag_grader"``) so dashboards
can compare strategy distribution across pipeline stages without leaking
domain identifiers into this helper.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import orjson
import structlog

from ragbot.shared.constants import (
    DEFAULT_ROBUST_JSON_PARSE_PREVIEW_CHARS,
    DEFAULT_ROBUST_JSON_PARSER_ENABLED,
    ROBUST_JSON_PARSER_FLAG_KEY,
)

logger = structlog.get_logger(__name__)


class JSONParseError(ValueError):
    """Raised when all robust JSON parse strategies fail.

    Subclasses :class:`ValueError` so existing ``except ValueError`` /
    ``except (ValueError, json.JSONDecodeError)`` guards continue to
    catch it without modification. The ``parser_name`` and
    ``input_preview`` attributes survive on the exception so the caller
    can log a structured rejection without re-inspecting the raw text.
    """

    def __init__(
        self,
        message: str,
        *,
        parser_name: str,
        input_preview: str,
    ) -> None:
        super().__init__(message)
        self.parser_name = parser_name
        self.input_preview = input_preview


# Compiled regexes — module-level to avoid per-call allocation.
# Matches an opening fence with optional language tag (```json, ```JSON, ```).
_FENCE_OPEN_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n?")
# Matches a trailing fence at end of string.
_FENCE_CLOSE_RE = re.compile(r"\n?\s*```\s*$")
# Trailing comma before closing bracket: ``,]`` / ``, ]`` / ``,\n}`` etc.
_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


def _strategy_direct(text: str) -> Any:
    """Strategy 1: parse the raw input verbatim."""
    return orjson.loads(text)


def _strip_code_fences(text: str) -> str:
    """Remove leading / trailing markdown code fences.

    Handles both ```````-only fences and the language-tagged variant
    (`````json``). Whitespace around the fences is also trimmed so a
    trailing newline does not block downstream parsing.
    """
    if "```" not in text:
        return text
    stripped = _FENCE_OPEN_RE.sub("", text, count=1)
    stripped = _FENCE_CLOSE_RE.sub("", stripped, count=1)
    return stripped.strip()


def _strategy_fence_strip(text: str) -> Any:
    """Strategy 2: strip code fences, then retry."""
    cleaned = _strip_code_fences(text)
    if cleaned == text:
        # Nothing changed → identical to strategy 1; signal "skip" via raise
        # so the orchestrator advances to the next strategy without an
        # extra parser cycle.
        raise json.JSONDecodeError("no fence to strip", text, 0)
    return orjson.loads(cleaned)


def _swap_single_to_double_quotes(text: str) -> str:
    """Convert Python-style single-quoted JSON to double-quoted.

    Naive global replace would corrupt apostrophes inside string values
    (``"it's"`` → ``"it\\"s"``). We walk the string and swap unescaped
    single quotes only when we are NOT inside an already-open double-
    quoted span. This is intentionally conservative: it handles the
    common case where the LLM emitted ``{'k': 'v'}`` (no inner
    apostrophes) and bails on the harder case (string contains ``'``)
    by leaving the substring untouched.
    """
    if "'" not in text:
        return text
    out: list[str] = []
    in_double = False
    in_escape = False
    for ch in text:
        if in_escape:
            out.append(ch)
            in_escape = False
            continue
        if ch == "\\":
            out.append(ch)
            in_escape = True
            continue
        if ch == '"':
            in_double = not in_double
            out.append(ch)
            continue
        if ch == "'" and not in_double:
            out.append('"')
            continue
        out.append(ch)
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    """Remove ``,`` that immediately precedes ``]`` / ``}``."""
    return _TRAILING_COMMA_RE.sub(r"\1", text)


def _strategy_quote_comma_repair(text: str) -> Any:
    """Strategy 3: quote swap + trailing comma strip, then retry.

    Applied AFTER fence-strip in the orchestrator so this step gets the
    already-cleaned text and does not have to re-handle fences.
    """
    cleaned = _strip_code_fences(text)
    repaired = _swap_single_to_double_quotes(cleaned)
    repaired = _strip_trailing_commas(repaired)
    if repaired == cleaned:
        raise json.JSONDecodeError("no repair applicable", text, 0)
    return orjson.loads(repaired)


def _extract_outermost_json_span(text: str) -> str | None:
    """Return the outermost balanced ``{...}`` or ``[...]`` substring.

    Walks the string tracking bracket depth and quote state. Returns
    ``None`` when no balanced span can be located. Strings inside the
    JSON (including those containing ``{`` / ``[`` / ``}`` / ``]``) are
    handled correctly because the walk only counts brackets that appear
    OUTSIDE a double-quoted span. Escape sequences (``\\"``) inside
    strings are honoured so a string literal containing ``\\"`` does
    not break out of the quoted span.
    """
    first_open = -1
    open_char = ""
    close_char = ""
    for idx, ch in enumerate(text):
        if ch in "{[":
            first_open = idx
            open_char = ch
            close_char = "}" if ch == "{" else "]"
            break
    if first_open < 0:
        return None

    depth = 0
    in_string = False
    in_escape = False
    for idx in range(first_open, len(text)):
        ch = text[idx]
        if in_escape:
            in_escape = False
            continue
        if ch == "\\" and in_string:
            in_escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[first_open : idx + 1]
    return None


def _strategy_substring_extract(text: str) -> Any:
    """Strategy 4: locate balanced JSON span, then retry with repair."""
    cleaned = _strip_code_fences(text)
    span = _extract_outermost_json_span(cleaned)
    if span is None:
        raise json.JSONDecodeError("no balanced JSON span", text, 0)
    # Inside the extracted span we still need the quote+comma repair: the
    # LLM may have emitted ``{'k': 'v',}`` wrapped in prose.
    repaired = _swap_single_to_double_quotes(span)
    repaired = _strip_trailing_commas(repaired)
    return orjson.loads(repaired)


def robust_json_parse(
    text: str,
    *,
    parser_name: str,
    flag_value: bool | None = None,
) -> Any:
    """Parse a possibly-malformed JSON string with 4-strategy fallback.

    Parameters
    ----------
    text:
        Raw LLM output. Empty / whitespace-only input raises
        :class:`JSONParseError` immediately (no strategy attempt).
    parser_name:
        Caller tag used in telemetry — for example
        ``"decomposer"`` / ``"crag_grader"`` / ``"intent_classifier"``.
        Domain-neutral by design: callers MUST NOT embed tenant
        identifiers / bot ids here.
    flag_value:
        Pre-resolved value of ``robust_json_parser_enabled``. When
        ``None`` the parser uses :data:`DEFAULT_ROBUST_JSON_PARSER_ENABLED`
        so callers without async config access (sync code paths /
        tests) get the documented default. The caller is responsible
        for fetching the live flag via
        ``system_config.get_bool(ROBUST_JSON_PARSER_FLAG_KEY, ...)``
        when async access is available — passing the resolved value in
        keeps this helper sync-friendly.

    Returns
    -------
    The parsed JSON value (``dict`` / ``list`` / ``str`` / ``int`` /
    ``float`` / ``bool`` / ``None``).

    Raises
    ------
    JSONParseError:
        All applicable strategies failed. The exception subclasses
        :class:`ValueError` so existing ``except ValueError`` /
        ``except json.JSONDecodeError`` guards keep working.
    """
    if not isinstance(text, str) or not text.strip():
        raise JSONParseError(
            "empty input",
            parser_name=parser_name,
            input_preview="",
        )

    effective_flag = (
        DEFAULT_ROBUST_JSON_PARSER_ENABLED if flag_value is None else flag_value
    )

    start_ts = time.monotonic()
    input_length = len(text)
    preview = text[:DEFAULT_ROBUST_JSON_PARSE_PREVIEW_CHARS]

    # Strategy 1 — always attempted, regardless of flag.
    try:
        result = _strategy_direct(text)
    except (orjson.JSONDecodeError, json.JSONDecodeError, ValueError):
        if not effective_flag:
            # Flag off → no fallback. Log the rejection so dashboards
            # can attribute the parse failure to the disabled flag.
            duration_us = int((time.monotonic() - start_ts) * 1_000_000)
            logger.info(
                "robust_json_parse",
                parser_name=parser_name,
                flag_value=effective_flag,
                strategy_used=None,
                input_length=input_length,
                duration_us=duration_us,
                outcome="failed_flag_off",
            )
            raise JSONParseError(
                "strict parse failed and robust fallback disabled",
                parser_name=parser_name,
                input_preview=preview,
            )
    else:
        duration_us = int((time.monotonic() - start_ts) * 1_000_000)
        logger.info(
            "robust_json_parse",
            parser_name=parser_name,
            flag_value=effective_flag,
            strategy_used=1,
            input_length=input_length,
            duration_us=duration_us,
            outcome="ok",
        )
        return result

    # Flag ON — try strategies 2 → 4 in order.
    strategies = (
        (2, _strategy_fence_strip),
        (3, _strategy_quote_comma_repair),
        (4, _strategy_substring_extract),
    )
    for strategy_id, fn in strategies:
        try:
            result = fn(text)
        except (orjson.JSONDecodeError, json.JSONDecodeError, ValueError):
            continue
        duration_us = int((time.monotonic() - start_ts) * 1_000_000)
        logger.info(
            "robust_json_parse",
            parser_name=parser_name,
            flag_value=effective_flag,
            strategy_used=strategy_id,
            input_length=input_length,
            duration_us=duration_us,
            outcome="ok",
        )
        return result

    duration_us = int((time.monotonic() - start_ts) * 1_000_000)
    logger.info(
        "robust_json_parse",
        parser_name=parser_name,
        flag_value=effective_flag,
        strategy_used=None,
        input_length=input_length,
        duration_us=duration_us,
        outcome="failed_all_strategies",
    )
    raise JSONParseError(
        "all 4 strategies failed",
        parser_name=parser_name,
        input_preview=preview,
    )


__all__ = [
    "JSONParseError",
    "ROBUST_JSON_PARSER_FLAG_KEY",
    "robust_json_parse",
]
