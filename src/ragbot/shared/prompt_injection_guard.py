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
# Reason: Zero references in src/. Prompt-injection handled inline in local_guardrail.py instead.
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

# """Prompt-injection detection helper — pure regex, no application override.

# Re-uses the centralized ``PROMPT_INJECTION_PATTERNS`` from ``shared/constants``
# so the ingest-time scrubber (``document_service._strip_prompt_injection``) and
# the query-time / boundary defense-in-depth helpers share one canonical pattern
# set. Compiled once at module import.

# Quality Gate #10 compliance: the helpers here ONLY *detect* / *redact* — they
# do NOT inject text into the LLM prompt, and they do NOT override the LLM
# answer. The caller decides what to do with a positive detection (typical:
# emit a structured audit event, surface ``oos_answer_template`` from the bot,
# or pass the redacted text through unchanged).

# Public API:
#     - ``detect_prompt_injection(text)`` → ``bool``
#     - ``redact_prompt_injection(text)`` → ``(redacted_text, hit_count)``
# """

# from __future__ import annotations

# import re

# from ragbot.shared.constants import PROMPT_INJECTION_PATTERNS

# Python 3.12's `re` rejects the `(?i)` inline flag after `|` joining, so
# strip the leading flag and apply `re.IGNORECASE` at compile time —
# behaviourally identical, matches the same code-path that already runs in
# ``document_service._strip_prompt_injection``.
# _INJECTION_REGEX: re.Pattern[str] = re.compile(
#     "|".join(p.removeprefix("(?i)") for p in PROMPT_INJECTION_PATTERNS),
#     re.MULTILINE | re.IGNORECASE,
# )

# _REDACTION_PLACEHOLDER = "[REDACTED]"


# def detect_prompt_injection(text: str | None) -> bool:
#     """Return ``True`` iff ``text`` contains at least one high-confidence
#     prompt-injection pattern (English or Vietnamese).

#     Empty / ``None`` input returns ``False``. Conservative by design — the
#     pattern set targets *exact* jailbreak phrases (`ignore previous
#     instructions`, `bỏ qua hướng dẫn`, `<|im_start|>`, …). Avoids false
#     positives on legitimate user phrasing.
#     """
#     if not text:
#         return False
#     return _INJECTION_REGEX.search(text) is not None


# def redact_prompt_injection(text: str | None) -> tuple[str, int]:
#     """Replace each prompt-injection pattern match with ``[REDACTED]``.

#     Returns ``(redacted_text, hit_count)``. Empty / ``None`` input returns
#     ``("", 0)``. Caller decides whether to surface the redacted text or
#     refuse the request entirely.
#     """
#     if not text:
#         return "", 0
#     return _INJECTION_REGEX.subn(_REDACTION_PLACEHOLDER, text)


# __all__ = [
#     "detect_prompt_injection",
#     "redact_prompt_injection",
# ]
