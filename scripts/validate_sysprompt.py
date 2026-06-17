#!/usr/bin/env python3
"""Sysprompt 10-item validator — Stream G companion.

Runs the pre-deploy checklist from `docs/templates/SYSPROMPT_TEMPLATE.md`
against a sysprompt source (file path OR stdin OR DB row by bot_id).

Sacred: this is OWNER-FACING, not code-facing. The validator checks
shape and anti-patterns only; it never edits the sysprompt and never
calls the LLM. Exit 0 = clean, 1 = warnings, 2 = blocking issues.

Usage:
    python scripts/validate_sysprompt.py --file path/to/system_prompt.md
    cat sp.md | python scripts/validate_sysprompt.py --stdin
    python scripts/validate_sysprompt.py --bot-id 1774946011723   # reads dev DB
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import NamedTuple

# All thresholds + vocabularies live in shared/constants.py (SSoT per
# CLAUDE.md zero-hardcode rule). This script is owner-facing tooling but
# still imports from the runtime constants module so owner check and
# runtime constraints can never drift.
from ragbot.shared.constants import (
    SYSPROMPT_CHARS_PER_TOKEN_AVG,
    SYSPROMPT_EXPECTED_SECTIONS,
    SYSPROMPT_INJECT_PHRASES,
    SYSPROMPT_LEAK_PHRASES,
    SYSPROMPT_MAX_TOKENS_HARD,
    SYSPROMPT_MAX_TOKENS_TARGET,
    SYSPROMPT_OOS_MIN_VARIANTS,
    SYSPROMPT_OOS_QUOTE_MIN_LEN,
)

PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")


class Finding(NamedTuple):
    item: int
    name: str
    status: str  # PASS | WARN | BLOCK
    detail: str


def load_text(args) -> str:
    if args.file:
        return open(args.file).read()
    if args.stdin:
        return sys.stdin.read()
    if args.bot_id:
        # Best-effort dev DB read. If env not set up, error gracefully.
        import os
        try:
            import psycopg
        except ImportError:
            sys.stderr.write("psycopg not installed — install via .venv or use --file\n")
            sys.exit(2)
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            sys.stderr.write("DATABASE_URL env var required for --bot-id mode\n")
            sys.exit(2)
        with psycopg.connect(dsn) as conn:
            row = conn.execute(
                "SELECT system_prompt FROM bots WHERE bot_id = %s LIMIT 1",
                (args.bot_id,),
            ).fetchone()
            if not row or not row[0]:
                sys.stderr.write(f"No sysprompt found for bot_id={args.bot_id}\n")
                sys.exit(2)
            return row[0]
    sys.stderr.write("Provide --file, --stdin, or --bot-id\n")
    sys.exit(2)


def check_sections(text: str) -> Finding:
    upper = text.upper()
    missing = [s for s in SYSPROMPT_EXPECTED_SECTIONS if s not in upper]
    if not missing:
        return Finding(1, "Sections 1-7 covered", "PASS", "all expected markers found")
    return Finding(1, "Sections 1-7 covered", "WARN", f"missing markers: {', '.join(missing)}")


def check_token_budget(text: str) -> Finding:
    est_tokens = int(len(text) / SYSPROMPT_CHARS_PER_TOKEN_AVG)
    if est_tokens <= SYSPROMPT_MAX_TOKENS_TARGET:
        return Finding(2, "Token budget", "PASS", f"~{est_tokens} tokens (target ≤ {SYSPROMPT_MAX_TOKENS_TARGET})")
    if est_tokens <= SYSPROMPT_MAX_TOKENS_HARD:
        return Finding(2, "Token budget", "WARN", f"~{est_tokens} tokens (target ≤ {SYSPROMPT_MAX_TOKENS_TARGET}, hard ≤ {SYSPROMPT_MAX_TOKENS_HARD})")
    return Finding(2, "Token budget", "BLOCK", f"~{est_tokens} tokens > hard cap {SYSPROMPT_MAX_TOKENS_HARD}")


def check_placeholders_replaced(text: str) -> Finding:
    leftover = PLACEHOLDER_RE.findall(text)
    if not leftover:
        return Finding(3, "Placeholders replaced", "PASS", "no {{XXX}} markers remain")
    return Finding(3, "Placeholders replaced", "WARN",
                   f"{len(leftover)} placeholder(s) unreplaced: {', '.join(set(leftover[:5]))}")


def check_oos_3_variants(text: str) -> Finding:
    # Heuristic: count occurrences of "Mẫu N" markers or quoted refuse
    # phrases (≥ SYSPROMPT_OOS_QUOTE_MIN_LEN chars) under an OOS header.
    n_target = SYSPROMPT_OOS_MIN_VARIANTS
    has_n_mau = all(f"Mẫu {i}" in text for i in range(1, n_target + 1))
    if has_n_mau:
        return Finding(4, f"OOS {n_target} variants", "PASS", f"{n_target} vary patterns present")
    quote_re = re.compile(rf'"[^"]{{{SYSPROMPT_OOS_QUOTE_MIN_LEN},}}"')
    quote_blocks = len(quote_re.findall(text))
    if quote_blocks >= n_target:
        return Finding(4, f"OOS {n_target} variants", "PASS", f"{quote_blocks} quoted refuse phrases")
    return Finding(4, f"OOS {n_target} variants", "WARN",
                   f"fewer than {n_target} vary patterns detected (anti-pattern: single hard phrase)")


def check_industry_safety(text: str) -> Finding:
    keywords = ("KHÔNG cam kết", "KHÔNG chẩn đoán", "KHÔNG advise",
                "KHÔNG bịa", "anti-hallu", "ANTI-HALLU", "Anti-HALLU")
    if any(k in text for k in keywords):
        return Finding(5, "Industry safety rule", "PASS", "safety guard phrase present")
    return Finding(5, "Industry safety rule", "WARN",
                   "no explicit safety/anti-hallu rule detected")


def check_no_inject_phrases(text: str) -> Finding:
    lower = text.lower()
    hits = [p for p in SYSPROMPT_INJECT_PHRASES if p in lower]
    if not hits:
        return Finding(6, "No 'must / phải' inject phrases", "PASS", "")
    return Finding(6, "No 'must / phải' inject phrases", "WARN",
                   f"hits: {', '.join(hits)} — review whether owner is injecting behaviour rules")


def check_no_leak_phrases(text: str) -> Finding:
    lower = text.lower()
    hits = [p for p in SYSPROMPT_LEAK_PHRASES if p in lower]
    if not hits:
        return Finding(7, "No internals leaked", "PASS", "")
    return Finding(7, "No internals leaked", "BLOCK",
                   f"sysprompt mentions internals: {', '.join(hits)}")


def check_tone_concrete(text: str) -> Finding:
    cues = ("xưng", "honorific", "anh/chị", "quý khách", "emoji", "formal")
    if any(c.lower() in text.lower() for c in cues):
        return Finding(8, "Tone spec concrete", "PASS", "honorific / emoji rule present")
    return Finding(8, "Tone spec concrete", "WARN", "tone spec missing concrete cue")


def check_jailbreak_rule(text: str) -> Finding:
    cues = ("jailbreak", "tiết lộ system prompt", "role-play khác", "không tiết lộ")
    if any(c.lower() in text.lower() for c in cues):
        return Finding(9, "Jailbreak rule", "PASS", "")
    return Finding(9, "Jailbreak rule", "WARN", "no jailbreak guard cue found")


def check_anti_hallu(text: str) -> Finding:
    cues = ("KHÔNG bịa", "không bịa", "anti-hallu", "verbatim", "không suy diễn")
    if any(c in text for c in cues):
        return Finding(10, "Anti-HALLU rule", "PASS", "")
    return Finding(10, "Anti-HALLU rule", "WARN", "no anti-hallu cue found")


CHECKS = (
    check_sections, check_token_budget, check_placeholders_replaced,
    check_oos_3_variants, check_industry_safety, check_no_inject_phrases,
    check_no_leak_phrases, check_tone_concrete, check_jailbreak_rule,
    check_anti_hallu,
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--file", help="Path to sysprompt .md/.txt file")
    p.add_argument("--stdin", action="store_true", help="Read from stdin")
    p.add_argument("--bot-id", help="Read sysprompt from DB by bot_id (dev DB; needs DATABASE_URL)")
    args = p.parse_args()

    text = load_text(args)
    findings = [check(text) for check in CHECKS]

    print(f"=== Sysprompt validate — {len(text)} chars, ~{int(len(text)/SYSPROMPT_CHARS_PER_TOKEN_AVG)} tokens ===")
    print()
    for f in findings:
        marker = {"PASS": "✓", "WARN": "⚠", "BLOCK": "✗"}[f.status]
        line = f"  {marker} [{f.item:>2}] {f.name:<35} — {f.detail}"
        print(line)

    blocks = [f for f in findings if f.status == "BLOCK"]
    warns = [f for f in findings if f.status == "WARN"]
    print()
    if blocks:
        print(f"❌ {len(blocks)} blocking issue(s) — fix before deploy.")
        return 2
    if warns:
        print(f"⚠ {len(warns)} warning(s) — review before deploy.")
        return 1
    print("✅ All 10 checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
