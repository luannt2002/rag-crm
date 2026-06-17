#!/usr/bin/env python3
"""Stream W — STATE_SNAPSHOT.md drift checker.

Reads STATE_SNAPSHOT.md, compares against current git state + test
count, prints what's stale. Read-only by default; owner edits the
snapshot manually based on the diff.

Idea: STATE_SNAPSHOT is owner-curated narrative; auto-overwriting it
would lose the editorial structure. This script just surfaces drift.

Usage:
    python scripts/check_state_snapshot.py
    python scripts/check_state_snapshot.py --since 24h
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = REPO_ROOT / "STATE_SNAPSHOT.md"


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        out = subprocess.run(
            cmd,
            cwd=str(cwd or REPO_ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return out.stdout.strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"(error: {e})"


def snapshot_mtime() -> float | None:
    if not SNAPSHOT_PATH.exists():
        return None
    return SNAPSHOT_PATH.stat().st_mtime


def commits_since_snapshot() -> list[str]:
    mtime = snapshot_mtime()
    if mtime is None:
        return []
    import datetime
    since = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).isoformat()
    out = _run(["git", "log", f"--since={since}", "--oneline"])
    return out.splitlines() if out and not out.startswith("(") else []


def latest_loadtest_summary() -> tuple[Path, dict] | None:
    reports = REPO_ROOT / "reports"
    if not reports.is_dir():
        return None
    candidates = sorted(reports.glob("LOADTEST_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    fp = candidates[0]
    try:
        import json
        with fp.open() as f:
            return fp, json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def latest_reclassify_pass_rate() -> tuple[Path, str] | None:
    reports = REPO_ROOT / "reports"
    if not reports.is_dir():
        return None
    candidates = sorted(reports.glob("LOADTEST*RECLASSIFY*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    fp = candidates[0]
    text = fp.read_text(errors="replace")
    m = re.search(r"PASS_RATE.*?\*\*?(\d+\.?\d*)\s*%", text)
    rate = m.group(1) + "%" if m else "(not parsed)"
    return fp, rate


def current_branch_state() -> dict:
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    head = _run(["git", "rev-parse", "--short", "HEAD"])
    behind = _run(["git", "rev-list", "--count", f"HEAD..origin/{branch}"])
    ahead = _run(["git", "rev-list", "--count", f"origin/{branch}..HEAD"])
    return {"branch": branch, "head": head, "ahead": ahead, "behind": behind}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--since", default=None, help="(reserved) override window")
    args = p.parse_args()

    if not SNAPSHOT_PATH.exists():
        print(f"[check] STATE_SNAPSHOT.md not found at {SNAPSHOT_PATH}")
        return 2

    print(f"=== STATE_SNAPSHOT drift check — {SNAPSHOT_PATH.name} ===")
    print()

    # Branch position
    bs = current_branch_state()
    print(f"Branch: {bs['branch']} @ {bs['head']}  ahead={bs['ahead']}  behind={bs['behind']}")

    # Commits since snapshot last edited
    commits = commits_since_snapshot()
    if commits:
        print(f"\n{len(commits)} commit(s) since STATE_SNAPSHOT.md was last edited:")
        for line in commits[:25]:
            print(f"  {line}")
        if len(commits) > 25:
            print(f"  … +{len(commits) - 25} more")

    # Latest loadtest aggregate
    loadtest = latest_loadtest_summary()
    if loadtest:
        fp, data = loadtest
        meta = data.get("meta") or {}
        agg = data.get("aggregate") or {}
        bs_status = agg.get("by_status") or {}
        print(f"\nLatest LOADTEST JSON: {fp.name}")
        print(f"  bot={meta.get('bot_id', '?')} combo={meta.get('combo', '?')}")
        print(f"  status: {bs_status}")
        lat = agg.get("latency") or {}
        if lat:
            print(f"  p50={lat.get('p50_ms', 0):.0f}ms p95={lat.get('p95_ms', 0):.0f}ms")
        cost = agg.get("cost") or {}
        if cost:
            print(f"  cost total=${cost.get('total_usd', 0):.4f} avg/turn=${cost.get('avg_per_turn_usd', 0):.6f}")

    # Latest reclassify
    reclass = latest_reclassify_pass_rate()
    if reclass:
        fp, rate = reclass
        print(f"\nLatest reclassify: {fp.name} → PASS_RATE={rate}")

    # Sanity check: snapshot mentions counts that match reality?
    snap_text = SNAPSHOT_PATH.read_text(errors="replace")
    drift_flags: list[str] = []
    if "112 fail" in snap_text:
        drift_flags.append('"112 fail" — current full-suite is 113 (per Stream L Phase 1 audit)')
    if "98.9%" in snap_text and "V13" in snap_text:
        # check whether "98.9% (V12 baseline)" still appears as the V13 claim
        if "DOES NOT apply to V13" not in snap_text:
            drift_flags.append('"98.9%" might still be quoted for V13 incorrectly — verify')
    if "5 commits ahead" in snap_text:
        drift_flags.append('"5 commits ahead" claim — current ahead=' + bs["ahead"])

    if drift_flags:
        print("\nDrift flags:")
        for f in drift_flags:
            print(f"  ⚠ {f}")
    else:
        print("\nNo obvious drift flags.")

    print()
    print("Owner reviews → edits STATE_SNAPSHOT.md manually if needed.")
    print("(Auto-overwrite would lose the editorial Sprint history; intentional.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
