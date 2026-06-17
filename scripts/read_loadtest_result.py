#!/usr/bin/env python3
"""Stream Y — read load-test result file written by loadtest_kick.sh.

Companion to ``scripts/loadtest_kick.sh``. After the background runner
finishes, this reader prints a 5-10 line summary so a future Claude
session (or cron) can read the verdict cheaply, without re-running the
test or watching its progress.

Sacred: read-only. Never re-runs the load test.

Usage:
    python scripts/read_loadtest_result.py --latest
    python scripts/read_loadtest_result.py --tag agent_d_loadtest_20260506_180000
    python scripts/read_loadtest_result.py --status-only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ASYNC_DIR = REPO_ROOT / "reports" / "_async"
REPORTS_DIR = REPO_ROOT / "reports"


def list_tags() -> list[str]:
    if not ASYNC_DIR.is_dir():
        return []
    return sorted({p.stem.rsplit(".", 1)[0] for p in ASYNC_DIR.glob("*.status")})


def latest_tag() -> str | None:
    files = list(ASYNC_DIR.glob("*.status"))
    if not files:
        return None
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0].stem


def status_for(tag: str) -> str:
    p = ASYNC_DIR / f"{tag}.status"
    return p.read_text().strip() if p.exists() else "(no status file)"


def latest_loadtest_json() -> Path | None:
    """agent_d_loadtest writes timestamped JSON into reports/."""
    candidates = sorted(REPORTS_DIR.glob("LOADTEST_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def summarise_json(fp: Path) -> str:
    try:
        with fp.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return f"(failed to read {fp.name}: {exc})"

    lines = [f"=== {fp.name} ==="]
    meta = data.get("meta") or {}
    if meta:
        lines.append(f"bot={meta.get('bot_id', '?')}  combo={meta.get('combo', '?')}")
    agg = data.get("aggregate") or {}
    by_status = agg.get("by_status") or {}
    if by_status:
        lines.append("status: " + " | ".join(f"{k}={v}" for k, v in by_status.items()))
    by_section = agg.get("by_section") or {}
    if by_section:
        lines.append("section breakdown:")
        for sec, counts in by_section.items():
            ans = counts.get("answered", 0)
            ref = counts.get("refuse", 0)
            err = counts.get("error", 0)
            n = counts.get("n", ans + ref + err)
            lines.append(f"  {sec:<22} n={n:>4} ans={ans:>4} refuse={ref:>4} err={err:>3}")
    lat = agg.get("latency") or {}
    if lat:
        lines.append(f"latency: p50={lat.get('p50_ms', 0):.0f}ms  p95={lat.get('p95_ms', 0):.0f}ms  p99={lat.get('p99_ms', 0):.0f}ms")
    cost = agg.get("cost") or {}
    if cost:
        lines.append(f"cost: total=${cost.get('total_usd', 0):.4f}  avg/turn=${cost.get('avg_per_turn_usd', 0):.6f}")
    cache = agg.get("cache_hit") or {}
    if cache:
        lines.append(f"cache_hit_rate: {cache.get('rate_pct', 0):.1f}%")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--latest", action="store_true", help="show status + summary of newest run")
    p.add_argument("--tag", help="explicit tag (loadtest_<script>_<ts>)")
    p.add_argument("--status-only", action="store_true", help="print status line and exit")
    p.add_argument("--json", help="path to a specific result JSON to summarise")
    args = p.parse_args()

    if args.json:
        print(summarise_json(Path(args.json)))
        return 0

    tag = args.tag or (latest_tag() if args.latest else None)
    if tag is None:
        tag = latest_tag()
    if tag:
        st = status_for(tag)
        print(f"[{tag}] status={st}")
        if args.status_only:
            return 0 if st.startswith("done") else 1
        # Best-effort: also pull the freshest result JSON written under reports/.
        latest = latest_loadtest_json()
        if latest:
            print()
            print(summarise_json(latest))
        return 0 if st.startswith("done") else 1

    # No tag — fall back to latest JSON.
    latest = latest_loadtest_json()
    if latest is None:
        print("(no async runs found, no LOADTEST_*.json under reports/)")
        return 1
    print(summarise_json(latest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
