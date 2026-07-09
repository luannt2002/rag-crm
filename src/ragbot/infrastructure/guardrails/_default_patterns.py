"""Platform-default guardrail rule definitions (SSoT).

This module is the SINGLE SOURCE OF TRUTH for the 12 platform-default
patterns previously hard-compiled at the top of ``local_guardrail.py``.
Both consumers read from here:

1. The alembic seed migration (``20260516_010f_guardrail_rules_table``)
   inserts these rows into ``guardrail_rules`` with
   ``record_tenant_id IS NULL`` so they apply platform-wide.
2. The runtime ``GuardrailRuleLoader`` (and the fallback path inside
   ``LocalGuardrail`` when no loader is wired — i.e. legacy unit tests
   that build the guardrail directly) compile patterns from the same
   strings, guaranteeing parity between DB and code.

Adding a new platform-default rule:
  - Append an entry to ``DEFAULT_GUARDRAIL_RULES``.
  - Author a new alembic migration that inserts the same row (the
    seed migration is immutable; new rules ship in fresh migrations).
  - No code path in ``local_guardrail.py`` should ever ``re.compile``
    a literal again — patterns live here.

Schema columns (mirrors ``guardrail_rules`` table):
  * ``rule_id``        — stable identifier used by ``GuardrailHit.rule_id``
  * ``pattern``        — regex source string (no leading ``r"(?i)"``;
                          flags live in ``pattern_flags``)
  * ``pattern_flags``  — comma-separated re flag names: ``"IGNORECASE"``,
                          ``"MULTILINE"``, etc. Empty string = no flags.
  * ``severity``       — ``info`` | ``warn`` | ``block``
  * ``action_taken``   — ``allow`` | ``redact`` | ``block`` | ``hitl``
  * ``scope``          — ``input`` | ``output`` | ``both``
  * ``priority``       — INT, lower runs first (DB sorts ASC)
  * ``metadata``       — dict, free-form. Reserved keys:
       - ``classic``: bool — included in classic ``detect_prompt_injection``
       - ``pii_category``: str — phone | email | cmnd | ssn (rule_id remap)
"""

from __future__ import annotations

import re
from typing import Any, Final

# ---------------------------------------------------------------------------
# 12 platform-default rules — frozen at module-load time.
# DO NOT re.compile anything here; loader/alembic consume strings.
# ---------------------------------------------------------------------------
DEFAULT_GUARDRAIL_RULES: Final[tuple[dict[str, Any], ...]] = (
    # ── Input: prompt-injection (active matcher used by InputGuardrail
    #          .prompt_injection_patterns) ───────────────────────────────────
    {
        "rule_id": "prompt_injection",
        "pattern": (
            r"(ignore previous|disregard (all |the )?instructions"
            r"|system prompt|you are now|DAN|base64:|decode this)"
        ),
        "pattern_flags": "IGNORECASE",
        "severity": "block",
        "action_taken": "block",
        "scope": "input",
        "priority": 10,
        "metadata": {},
    },
    # ── Input: classic prompt-injection patterns (consumed by
    #          detect_prompt_injection() — exposed via GuardrailPort) ───────
    {
        "rule_id": "prompt_injection_classic_ignore_prev",
        "pattern": r"ignore\s+(previous|above|prior)\s+instructions?",
        "pattern_flags": "IGNORECASE",
        "severity": "block",
        "action_taken": "block",
        "scope": "input",
        "priority": 20,
        "metadata": {"classic": True},
    },
    {
        "rule_id": "prompt_injection_classic_you_are_now",
        "pattern": r"you\s+are\s+now\s+(an?|the)",
        "pattern_flags": "IGNORECASE",
        "severity": "block",
        "action_taken": "block",
        "scope": "input",
        "priority": 21,
        "metadata": {"classic": True},
    },
    {
        "rule_id": "prompt_injection_classic_system_marker",
        "pattern": r"system\s*[:>]",
        "pattern_flags": "IGNORECASE",
        "severity": "block",
        "action_taken": "block",
        "scope": "input",
        "priority": 22,
        "metadata": {"classic": True},
    },
    {
        "rule_id": "prompt_injection_classic_disregard",
        "pattern": r"(disregard|forget)\s+(all|any|previous)",
        "pattern_flags": "IGNORECASE",
        "severity": "block",
        "action_taken": "block",
        "scope": "input",
        "priority": 23,
        "metadata": {"classic": True},
    },
    {
        "rule_id": "prompt_injection_classic_new_instructions",
        "pattern": r"new\s+instructions?\s*[:>]",
        "pattern_flags": "IGNORECASE",
        "severity": "block",
        "action_taken": "block",
        "scope": "input",
        "priority": 24,
        "metadata": {"classic": True},
    },
    # ── Input: Vietnamese prompt-injection (NON-classic → enforced by
    #          ``check_input`` via ``_run_db_input_regex_rules``; the English
    #          ``prompt_injection`` rule does not cover VN phrasings, so a VN
    #          injection like "bỏ qua hướng dẫn trước đó" previously passed).
    #          Patterns require the injection OBJECT (instructions/system/
    #          rules) after the trigger verb to keep false-positives off
    #          ordinary Vietnamese ("bỏ qua bước này", "đóng vai trò"). ──────
    {
        "rule_id": "prompt_injection_vi",
        "pattern": (
            r"(?:bỏ qua|phớt lờ|quên(?:\s+(?:đi|hết|sạch))?)\s+"
            r"(?:mọi\s+|tất cả\s+|các\s+|những\s+|hết\s+)?"
            r"(?:hướng dẫn|chỉ dẫn|chỉ thị|lệnh|quy tắc|nguyên tắc|yêu cầu)"
            r"(?:\s+(?:trước|phía trên|bên trên|ở trên|trước đó|đã cho))?"
            r"|(?:(?:bạn|mày|ngươi)\s+(?:bây giờ|giờ đây)"
            r"|(?:từ (?:bây )?giờ|kể từ giờ)[,\s]+(?:bạn|mày|ngươi))"
            r"\s+(?:là|sẽ (?:là|đóng)|đóng vai)"
            r"|(?:tiết lộ|in ra|cho (?:tôi|mình) xem)\s+.{0,20}?"
            r"(?:system prompt|prompt hệ thống|câu lệnh hệ thống|chỉ thị hệ thống)"
        ),
        "pattern_flags": "IGNORECASE",
        "severity": "block",
        "action_taken": "block",
        "scope": "input",
        "priority": 11,
        "metadata": {},
    },
    # ── Input: PII (VN phone, email, CMND/CCCD; US SSN) ────────────────────
    {
        "rule_id": "pii_vi_phone",
        "pattern": r"(0\d{9,10}|\+84\d{9,10})",
        "pattern_flags": "",
        "severity": "warn",
        "action_taken": "redact",
        "scope": "input",
        "priority": 50,
        "metadata": {"pii_category": "phone"},
    },
    {
        "rule_id": "pii_vi_email",
        "pattern": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "pattern_flags": "",
        "severity": "warn",
        "action_taken": "redact",
        "scope": "input",
        "priority": 51,
        "metadata": {"pii_category": "email"},
    },
    {
        "rule_id": "pii_vi_cmnd",
        "pattern": r"\b(\d{9}|\d{12})\b",
        "pattern_flags": "",
        "severity": "warn",
        "action_taken": "redact",
        "scope": "input",
        "priority": 52,
        "metadata": {"pii_category": "cmnd"},
    },
    {
        "rule_id": "pii_en_ssn",
        "pattern": r"\b\d{3}-\d{2}-\d{4}\b",
        "pattern_flags": "",
        "severity": "warn",
        "action_taken": "redact",
        "scope": "input",
        "priority": 53,
        "metadata": {"pii_category": "ssn"},
    },
    # ── Input: SQL injection ───────────────────────────────────────────────
    {
        "rule_id": "sql_injection",
        "pattern": (
            r"(union\s+select|;drop\s+table|;delete\s+from"
            r"|';|'\s+or\s+'?1'?\s*=\s*'?1)"
        ),
        "pattern_flags": "IGNORECASE",
        "severity": "block",
        "action_taken": "block",
        "scope": "input",
        "priority": 30,
        "metadata": {},
    },
    # ── Output: secret leak (API keys, tokens) ─────────────────────────────
    {
        "rule_id": "secret_leak",
        "pattern": r"(sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{20,}|AKIA[A-Z0-9]{16})",
        "pattern_flags": "",
        "severity": "block",
        "action_taken": "block",
        "scope": "output",
        "priority": 10,
        "metadata": {},
    },
)


# ---------------------------------------------------------------------------
# Lazy-compiled pattern access for the LocalGuardrail no-loader fallback.
# Compilation happens on first read, cached per (pattern, flags). This keeps
# the SSoT a pure-string dict (alembic-friendly) while preserving the legacy
# static-method API (InputGuardrail.prompt_injection_patterns etc.) that
# existing unit tests rely on.
# ---------------------------------------------------------------------------
_FLAG_NAME_TO_INT: Final[dict[str, int]] = {
    "IGNORECASE": re.IGNORECASE,
    "MULTILINE": re.MULTILINE,
    "DOTALL": re.DOTALL,
    "VERBOSE": re.VERBOSE,
    "UNICODE": re.UNICODE,
    "ASCII": re.ASCII,
}

_compile_cache: dict[tuple[str, str], re.Pattern[str]] = {}


def parse_flag_mask(flags_csv: str) -> int:
    """Translate ``"IGNORECASE,MULTILINE"`` to the corresponding ``re`` mask.

    Unknown flag names are silently dropped (DB rows authored by a future
    admin shouldn't crash the loader). Returns ``0`` for empty input.
    """
    if not flags_csv:
        return 0
    mask = 0
    for raw in flags_csv.split(","):
        name = raw.strip().upper()
        if not name:
            continue
        mask |= _FLAG_NAME_TO_INT.get(name, 0)
    return mask


def get_default_compiled(rule_id: str) -> re.Pattern[str] | None:
    """Return the compiled pattern for a platform-default rule, or None.

    Used by ``LocalGuardrail`` when no ``GuardrailRuleLoader`` is wired
    (e.g. legacy unit tests). Compilation is cached per (pattern, flags)
    tuple so repeated lookups are O(1).
    """
    for row in DEFAULT_GUARDRAIL_RULES:
        if row["rule_id"] != rule_id:
            continue
        key = (row["pattern"], row["pattern_flags"])
        compiled = _compile_cache.get(key)
        if compiled is None:
            compiled = re.compile(row["pattern"], parse_flag_mask(row["pattern_flags"]))
            _compile_cache[key] = compiled
        return compiled
    return None


def get_classic_injection_compiled() -> tuple[re.Pattern[str], ...]:
    """Return the compiled regex tuple for the classic detect_prompt_injection.

    Selects all rules with ``metadata['classic'] is True`` from the SSoT
    list and returns them in priority order. Used to preserve the
    ``GuardrailPort.detect_prompt_injection`` contract used by callers
    that haven't migrated to the new ``check_input`` surface.
    """
    classic_rows = sorted(
        (r for r in DEFAULT_GUARDRAIL_RULES if r["metadata"].get("classic") is True),
        key=lambda r: r["priority"],
    )
    out: list[re.Pattern[str]] = []
    for row in classic_rows:
        key = (row["pattern"], row["pattern_flags"])
        compiled = _compile_cache.get(key)
        if compiled is None:
            compiled = re.compile(row["pattern"], parse_flag_mask(row["pattern_flags"]))
            _compile_cache[key] = compiled
        out.append(compiled)
    return tuple(out)


__all__ = [
    "DEFAULT_GUARDRAIL_RULES",
    "get_default_compiled",
    "get_classic_injection_compiled",
    "parse_flag_mask",
]
