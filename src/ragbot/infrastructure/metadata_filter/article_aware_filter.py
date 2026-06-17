"""ArticleAwareFilter — regex-driven structural-reference pre-filter.

Scans the user query for structural-reference patterns (article, clause,
chapter, section, appendix) and returns a JSONB-containment-ready filter
dict that hybrid_search uses to narrow chunk candidates to those whose
ingest-side metadata recorded the same anchor.

Domain-neutral by design: the regex pattern list is **operator-supplied**
via the ``patterns`` constructor kwarg (sourced from
``system_config.article_ref_patterns``). The strategy itself has no
hardcoded language / locale literals — a bot owner targeting a
non-Vietnamese corpus configures their own keyword set and the same
extraction loop runs unmodified.

Pattern schema (each entry):

    {
        "name": "article",          # → output key ``<name>_no``
        "regex": r"\\b<keyword>\\s+(\\d+)\\b",
                                    # raw regex; first capture group = number
        "flags": "IGNORECASE"       # optional; "IGNORECASE" only
    }

Output keys follow ``<name>_no`` convention so they line up with the
ingest-side metadata schema written by
:func:`ragbot.application.services.structured_ref_extractor.extract_structured_refs`.

First-occurrence-wins per pattern: queries that mention two articles
(e.g. "so sánh Điều 5 và Điều 7") store the leading anchor; the
orchestrator's comparison-intent path is responsible for handling
multi-anchor queries with a different retrieval strategy.

Failure mode: any malformed pattern entry is logged and skipped — the
filter degrades gracefully to whatever subset of patterns compiles, so
an operator typo cannot crash retrieval.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# Re.IGNORECASE is the only flag accepted in operator config so far. Any
# extension MUST whitelist a new string value here — never accept arbitrary
# flag integers from config (defence vs operator-supplied ``re.DEBUG`` etc.
# slowing the hot path).
_FLAG_LOOKUP: dict[str, int] = {
    "IGNORECASE": re.IGNORECASE,
}


def _compile_patterns(
    raw_patterns: list[dict[str, Any]] | None,
) -> list[tuple[str, re.Pattern[str]]]:
    """Compile operator-supplied pattern entries; skip malformed ones.

    @param raw_patterns: list of dicts with ``name`` + ``regex`` (+ optional
        ``flags``). ``None`` / empty list → returns ``[]`` (filter is a
        no-op).
    @return: list of ``(name, compiled_pattern)`` tuples preserving config
        order (precedence-sensitive: first match by pattern wins).
    """
    if not raw_patterns:
        return []
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for idx, entry in enumerate(raw_patterns):
        if not isinstance(entry, dict):
            logger.warning(
                "article_aware_filter_pattern_skipped_non_dict",
                index=idx,
                kind=type(entry).__name__,
            )
            continue
        name = entry.get("name")
        regex = entry.get("regex")
        if not isinstance(name, str) or not name.strip():
            logger.warning(
                "article_aware_filter_pattern_skipped_no_name", index=idx,
            )
            continue
        if not isinstance(regex, str) or not regex:
            logger.warning(
                "article_aware_filter_pattern_skipped_no_regex",
                index=idx,
                name=name,
            )
            continue
        flags = 0
        flag_field = entry.get("flags")
        if isinstance(flag_field, str):
            for token in flag_field.split("|"):
                tok = token.strip().upper()
                if tok in _FLAG_LOOKUP:
                    flags |= _FLAG_LOOKUP[tok]
                elif tok:
                    logger.warning(
                        "article_aware_filter_pattern_unknown_flag",
                        index=idx,
                        name=name,
                        flag=tok,
                    )
        try:
            compiled.append((name.strip(), re.compile(regex, flags)))
        except re.error as exc:
            logger.warning(
                "article_aware_filter_pattern_compile_failed",
                index=idx,
                name=name,
                error=str(exc),
            )
    return compiled


class ArticleAwareFilter:
    """Regex-driven query-side metadata pre-filter (Strategy pattern).

    Construct with a list of operator-supplied pattern entries. The
    constructor compiles them once; subsequent ``extract`` calls are pure
    regex scans (no I/O, no allocation beyond match objects).
    """

    def __init__(
        self,
        *,
        patterns: list[dict[str, Any]] | None = None,
        **_: object,
    ) -> None:
        self._patterns: list[tuple[str, re.Pattern[str]]] = _compile_patterns(
            patterns,
        )

    @staticmethod
    def get_provider_name() -> str:
        return "article_aware"

    def extract(self, query: str) -> dict[str, str]:
        """Return JSONB-containment-ready dict of structural keys.

        @param query: raw user query string. Empty / whitespace-only →
            returns ``{}``.
        @return: dict like ``{"article_no": "3"}``. Empty dict when no
            pattern matches.
        """
        if not query or not query.strip():
            return {}
        if not self._patterns:
            # No compiled patterns means operator config was empty or fully
            # malformed — degrade silently to no-filter so retrieval still
            # runs the standard hybrid search path.
            return {}
        out: dict[str, str] = {}
        for name, pattern in self._patterns:
            key = f"{name}_no"
            if key in out:
                # First-occurrence wins per pattern key — preserve config
                # order semantics so two entries named ``article`` (e.g.
                # Vietnamese + English keyword) don't overwrite each other.
                continue
            m = pattern.search(query)
            if not m:
                continue
            try:
                value = m.group(1)
            except IndexError:
                # Pattern missing a capture group is operator misconfig —
                # log but don't crash; surface the full match as a fallback
                # so the filter still narrows candidates.
                logger.warning(
                    "article_aware_filter_pattern_missing_group",
                    name=name,
                    pattern=pattern.pattern,
                )
                value = m.group(0)
            if not isinstance(value, str) or not value:
                continue
            out[key] = value.upper() if not value.isdigit() else value
        return out


__all__ = ["ArticleAwareFilter"]
