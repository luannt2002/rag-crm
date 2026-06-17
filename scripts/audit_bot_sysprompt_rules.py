#!/usr/bin/env python3
"""Audit ``bots.system_prompt`` for anti-fake / anti-fabricate / anti-hallucinate rules.

T1-Smartness — HALLU=0 sacred safeguard. Memory note (V15 Stream Z,
2026-05-07) showed that bots without explicit anti-fabricate rules in
their ``system_prompt`` reach the LLM with no in-prompt brake, so a
threshold change alone could not prevent fabricated-number breaches.

This script connects read-only to the production DB pointed to by
``DATABASE_URL_SYNC`` / ``DATABASE_URL`` and prints a per-bot summary of
which of the three anti-fake markers are present, flagging bots whose
prompts contain NONE of them.

Usage::

    set -a && source .env && set +a
    python scripts/audit_bot_sysprompt_rules.py
    python scripts/audit_bot_sysprompt_rules.py --json-out /tmp/audit.json
    python scripts/audit_bot_sysprompt_rules.py --dsn postgresql://...

No write access — owner reviews flagged bots and decides remediation
manually. Pattern reuse: ``scripts/diagnose_p95_bottleneck.py``.

Exit codes:
    0 — success (even if WARN flags raised; warn-only output)
    2 — DB unreachable
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras

# ---------- defaults ---------------------------------------------------------
EXIT_DB_UNREACHABLE: int = 2
MIN_PROMPT_LENGTH: int = 50  # skip empty/template default prompts

# Marker labels surfaced in the formatted output; aligned with SQL flag
# column names below so the audit row formatter can iterate uniformly.
ANTI_FAKE_FLAGS: tuple[str, ...] = (
    "has_anti_fake",
    "has_anti_fabricate_vi",
    "has_anti_hallucinate",
)


# ---------- helpers ----------------------------------------------------------
def resolve_dsn(cli_dsn: str | None) -> str:
    """Convert async URL → sync psycopg2 URL. Strip ``+asyncpg``/``+psycopg``."""
    raw = cli_dsn or os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL")
    if not raw:
        sys.stderr.write(
            "[audit_sysprompt] DATABASE_URL not set. "
            "Run: set -a && source .env && set +a\n",
        )
        sys.exit(EXIT_DB_UNREACHABLE)
    for suffix in ("+asyncpg", "+psycopg2", "+psycopg"):
        raw = raw.replace(suffix, "")
    return raw


def bot_sysprompt_audit_query() -> str:
    """Select all non-empty bot prompts plus anti-fake marker flags.

    The three flags map directly to anti-hallucination rule taxonomies:

    * ``has_anti_fake`` — English ``anti-fake`` / ``anti fake`` phrase
      (matches the sysprompt v6 ``anti-fake-section`` convention).
    * ``has_anti_fabricate_vi`` — Vietnamese ``KHÔNG bịa`` /
      ``không bịa`` (used in Vietnamese-locale bot sysprompts).
    * ``has_anti_hallucinate`` — generic English ``hallucinat`` stem
      (matches ``hallucinate``, ``hallucination``, etc.).

    Bot rows missing all three flags should be reviewed by the owner;
    HALLU=0 holds best when at least one anti-fabricate brake exists.
    """
    return f"""
    SELECT bot_id,
           workspace_id,
           channel_type,
           LENGTH(system_prompt) AS prompt_chars,
           (system_prompt ILIKE '%anti-fake%' OR system_prompt ILIKE '%anti fake%')
                                                       AS has_anti_fake,
           (system_prompt ILIKE '%KHÔNG bịa%' OR system_prompt ILIKE '%không bịa%')
                                                       AS has_anti_fabricate_vi,
           (system_prompt ILIKE '%hallucinat%')        AS has_anti_hallucinate
    FROM bots
    WHERE system_prompt IS NOT NULL
      AND LENGTH(system_prompt) > {MIN_PROMPT_LENGTH}
    ORDER BY prompt_chars DESC
    """


def format_audit_row(row: dict[str, Any]) -> str:
    """Format a single bot row for the formatted table.

    A row with ALL three flags False yields the ``⚠`` warn marker;
    otherwise the row earns ``✓``. Bot identifier is the 3-tuple
    ``(bot_id, workspace_id, channel_type)`` per the 4-key identity
    rule (record_tenant_id omitted — not actionable in audit output).
    """
    flags = [bool(row.get(name)) for name in ANTI_FAKE_FLAGS]
    marker = "⚠" if not any(flags) else "✓"
    bot_id = (row.get("bot_id") or "<unknown>")[:24]
    ws = (row.get("workspace_id") or "<unknown>")[:16]
    ch = (row.get("channel_type") or "—")[:8]
    chars = row.get("prompt_chars") or 0
    flag_str = (
        f"anti-fake={'Y' if flags[0] else '.'}  "
        f"anti-bia-vi={'Y' if flags[1] else '.'}  "
        f"anti-hallu={'Y' if flags[2] else '.'}"
    )
    return (
        f"  {marker}  {bot_id:<24} {ws:<16} {ch:<8} "
        f"{chars:>6}ch  {flag_str}"
    )


def is_bot_missing_all_anti_fake(row: dict[str, Any]) -> bool:
    """True when none of the three anti-fake markers are present."""
    return not any(bool(row.get(name)) for name in ANTI_FAKE_FLAGS)


# ---------- output -----------------------------------------------------------
@dataclass
class AuditReport:
    generated_at: str
    n_bots: int
    n_warn: int
    bots: list[dict[str, Any]]


def print_section(title: str) -> None:
    print()
    print("=" * 78)
    print(f"  {title}")
    print("=" * 78)


def print_audit_table(rows: list[dict[str, Any]]) -> int:
    """Print the audit table. Returns the count of WARN-flagged rows."""
    print_section("BOT SYSPROMPT ANTI-FAKE AUDIT")
    if not rows:
        print("  [no bots with non-empty system_prompt found]")
        return 0
    print(
        f"  {'flag':<3} {'bot_id':<24} {'workspace_id':<16} "
        f"{'channel':<8} {'chars':>8}  flags",
    )
    print("  " + "-" * 76)
    warn = 0
    for r in rows:
        print(format_audit_row(r))
        if is_bot_missing_all_anti_fake(r):
            warn += 1
    print()
    if warn:
        print(
            f"  ⚠  {warn}/{len(rows)} bot(s) missing ALL anti-fake markers — "
            "owner review recommended.",
        )
        print(
            "     HALLU=0 sacred holds best when at least one of "
            "anti-fake / anti-bia / anti-hallucinate rule is present.",
        )
    else:
        print(
            f"  ✓  All {len(rows)} bot(s) carry at least one "
            "anti-fake marker.",
        )
    return warn


def write_json(path: str, report: AuditReport) -> None:
    with open(path, "w") as f:
        json.dump(asdict(report), f, indent=2, default=str)
    print(f"\n  → JSON written: {path}")


# ---------- main -------------------------------------------------------------
def run(args: argparse.Namespace) -> int:
    dsn = resolve_dsn(args.dsn)
    try:
        conn = psycopg2.connect(dsn, connect_timeout=10)
    except psycopg2.OperationalError as exc:
        sys.stderr.write(f"[audit_sysprompt] DB connect failed: {exc}\n")
        return EXIT_DB_UNREACHABLE
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(bot_sysprompt_audit_query())
    rows = [dict(r) for r in cur.fetchall()]

    cur.close()
    conn.close()

    n_warn = sum(1 for r in rows if is_bot_missing_all_anti_fake(r))
    report = AuditReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        n_bots=len(rows),
        n_warn=n_warn,
        bots=rows,
    )

    if args.json_out:
        write_json(args.json_out, report)
        # Still print summary line for operator visibility.
        print(
            f"  bots={report.n_bots}  warn={report.n_warn}",
        )
        return 0

    print(f"Bot Sysprompt Audit — generated_at={report.generated_at}")
    print_audit_table(rows)
    # Warn-only — exit 0 regardless. Owner reviews and decides
    # remediation manually; do NOT fail CI on missing markers.
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dsn", type=str, default=None,
        help="Override DATABASE_URL (sync psycopg2 URL).",
    )
    p.add_argument(
        "--json-out", type=str, default=None,
        help="Write audit report as JSON to this path.",
    )
    return p


if __name__ == "__main__":
    sys.exit(run(build_parser().parse_args()))
