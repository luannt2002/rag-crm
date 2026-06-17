#!/usr/bin/env python3
"""Cost audit cho Claude Code session logs.

Đọc JSONL ở ~/.claude/projects/-var-www-html-ragbot/ → tổng hợp cost/turn,
phát hiện Sonnet leak (CLAUDE.md ép 100% Opus), cache-hit kém, session ngắn
fragment. Pattern lấy từ hueanmy/claude-token-monitor — giản hoá còn stdlib.

Usage:
    python scripts/cost_audit.py today
    python scripts/cost_audit.py weekly
    python scripts/cost_audit.py sonnet-leak
    python scripts/cost_audit.py sessions [--top 10]
    python scripts/cost_audit.py advise
    python scripts/cost_audit.py per-feature [--days 7] [--top 30]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

PROJECT_DIR = Path(
    os.environ.get(
        "RAGBOT_CLAUDE_LOGS",
        str(Path.home() / ".claude/projects/-var-www-html-ragbot"),
    )
)

PRICING_USD_PER_MTOK = {
    "claude-opus-4-7":      {"in": 15.0, "out": 75.0, "cache_read": 1.875, "cache_write": 18.75},
    "claude-opus-4-6":      {"in": 15.0, "out": 75.0, "cache_read": 1.875, "cache_write": 18.75},
    "claude-sonnet-4-6":    {"in": 3.0,  "out": 15.0, "cache_read": 0.30,  "cache_write": 3.75},
    "claude-sonnet-4-5":    {"in": 3.0,  "out": 15.0, "cache_read": 0.30,  "cache_write": 3.75},
    "claude-haiku-4-5":     {"in": 1.0,  "out": 5.0,  "cache_read": 0.10,  "cache_write": 1.25},
}
DEFAULT_PRICE = PRICING_USD_PER_MTOK["claude-opus-4-7"]
SONNET_LEAK_FORBIDDEN = ("sonnet",)

# Per-feature subcommand defaults — tuned for typical ops audit cadence
# (1-week sliding window, top-30 features fits a terminal screen).
DEFAULT_PER_FEATURE_WINDOW_DAYS: int = 7
DEFAULT_PER_FEATURE_TOP: int = 30


def resolve_price(model: str) -> dict:
    if not model:
        return DEFAULT_PRICE
    for key, price in PRICING_USD_PER_MTOK.items():
        if key in model:
            return price
    return DEFAULT_PRICE


def cost_of(usage: dict, model: str) -> float:
    p = resolve_price(model)
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cr  = usage.get("cache_read_input_tokens", 0)
    cw  = usage.get("cache_creation_input_tokens", 0)
    return (
        inp * p["in"]
        + out * p["out"]
        + cr  * p["cache_read"]
        + cw  * p["cache_write"]
    ) / 1_000_000.0


def iter_events(project_dir: Path) -> Iterator[dict]:
    """Stream assistant events; dedupe (sessionId, message.id)."""
    seen: set[tuple[str, str]] = set()
    files = sorted(project_dir.glob("*.jsonl"))
    if not files:
        sys.stderr.write(f"[cost_audit] no JSONL found at {project_dir}\n")
        return
    for fp in files:
        try:
            with fp.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") != "assistant":
                        continue
                    msg = d.get("message") or {}
                    msg_id = msg.get("id") or ""
                    sess = d.get("sessionId") or fp.stem
                    key = (sess, msg_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    tool_uses: list[dict] = []
                    for block in msg.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            name = block.get("name") or ""
                            if not name:
                                continue
                            inp = block.get("input") or {}
                            td: dict = {"name": name}
                            if name in ("Edit", "Write", "NotebookEdit", "Read"):
                                td["file_path"] = inp.get("file_path") or ""
                            elif name == "Bash":
                                td["command"] = inp.get("command") or ""
                            tool_uses.append(td)
                    yield {
                        "session": sess,
                        "msg_id": msg_id,
                        "model": msg.get("model") or "",
                        "usage": msg.get("usage") or {},
                        "ts": d.get("timestamp") or "",
                        "branch": d.get("gitBranch") or "",
                        "cwd": d.get("cwd") or "",
                        "tool_uses": tool_uses,
                    }
        except OSError as e:
            sys.stderr.write(f"[cost_audit] skip {fp.name}: {e}\n")


def parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_money(x: float) -> str:
    return f"${x:,.4f}"


def fmt_tok(x: int) -> str:
    if x >= 1_000_000:
        return f"{x/1_000_000:.2f}M"
    if x >= 1_000:
        return f"{x/1_000:.1f}k"
    return str(x)


def cmd_today(args) -> int:
    today_utc = datetime.now(timezone.utc).date()
    by_model: dict[str, dict] = defaultdict(lambda: {
        "calls": 0, "in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0,
    })
    sessions_today: set[str] = set()
    for ev in iter_events(PROJECT_DIR):
        ts = parse_ts(ev["ts"])
        if not ts or ts.date() != today_utc:
            continue
        m = ev["model"] or "unknown"
        u = ev["usage"]
        agg = by_model[m]
        agg["calls"] += 1
        agg["in"]  += u.get("input_tokens", 0)
        agg["out"] += u.get("output_tokens", 0)
        agg["cr"]  += u.get("cache_read_input_tokens", 0)
        agg["cw"]  += u.get("cache_creation_input_tokens", 0)
        agg["cost"] += cost_of(u, m)
        sessions_today.add(ev["session"])

    if not by_model:
        print(f"No assistant events on {today_utc} (UTC) in {PROJECT_DIR}")
        return 0

    print(f"=== Cost — {today_utc} UTC === ({len(sessions_today)} session(s))")
    print(f"{'model':<28} {'calls':>6} {'in':>8} {'out':>8} {'cache_r':>9} {'cache_w':>9} {'cost':>11}")
    print("-" * 86)
    total = 0.0
    for m in sorted(by_model, key=lambda k: -by_model[k]["cost"]):
        a = by_model[m]
        print(f"{m:<28} {a['calls']:>6} {fmt_tok(a['in']):>8} {fmt_tok(a['out']):>8} "
              f"{fmt_tok(a['cr']):>9} {fmt_tok(a['cw']):>9} {fmt_money(a['cost']):>11}")
        total += a["cost"]
    print("-" * 86)
    print(f"{'TOTAL':<28} {'':>6} {'':>8} {'':>8} {'':>9} {'':>9} {fmt_money(total):>11}")

    sonnet_cost = sum(a["cost"] for m, a in by_model.items() if any(s in m.lower() for s in SONNET_LEAK_FORBIDDEN))
    if sonnet_cost > 0:
        pct = sonnet_cost / total * 100 if total else 0
        print(f"\n  SONNET LEAK: {fmt_money(sonnet_cost)} ({pct:.1f}% of today) — CLAUDE.md ép Opus 100%")
    else:
        print("\n  Sonnet leak: 0 — CLAUDE.md rule held.")
    return 0


def cmd_weekly(args) -> int:
    days = args.days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    by_day: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "calls": 0, "opus": 0.0, "sonnet": 0.0})
    for ev in iter_events(PROJECT_DIR):
        ts = parse_ts(ev["ts"])
        if not ts or ts < cutoff:
            continue
        day = ts.date().isoformat()
        c = cost_of(ev["usage"], ev["model"])
        agg = by_day[day]
        agg["cost"] += c
        agg["calls"] += 1
        ml = ev["model"].lower()
        if "opus" in ml:
            agg["opus"] += c
        elif "sonnet" in ml:
            agg["sonnet"] += c

    if not by_day:
        print(f"No events in last {days} days.")
        return 0
    print(f"=== Last {days} days ===")
    print(f"{'date':<12} {'calls':>6} {'opus':>10} {'sonnet':>10} {'total':>10}")
    print("-" * 54)
    grand = 0.0
    for day in sorted(by_day):
        a = by_day[day]
        print(f"{day:<12} {a['calls']:>6} {fmt_money(a['opus']):>10} "
              f"{fmt_money(a['sonnet']):>10} {fmt_money(a['cost']):>10}")
        grand += a["cost"]
    print("-" * 54)
    print(f"{'GRAND':<12} {'':>6} {'':>10} {'':>10} {fmt_money(grand):>10}")
    return 0


def cmd_sonnet_leak(args) -> int:
    leaks: list[dict] = []
    for ev in iter_events(PROJECT_DIR):
        ml = ev["model"].lower()
        if any(s in ml for s in SONNET_LEAK_FORBIDDEN):
            leaks.append(ev)
    if not leaks:
        print("No Sonnet calls found — CLAUDE.md rule held.")
        return 0
    by_session: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost": 0.0, "models": set(), "first_ts": "", "branch": ""})
    for ev in leaks:
        a = by_session[ev["session"]]
        a["calls"] += 1
        a["cost"] += cost_of(ev["usage"], ev["model"])
        a["models"].add(ev["model"])
        if not a["first_ts"] or ev["ts"] < a["first_ts"]:
            a["first_ts"] = ev["ts"]
            a["branch"] = ev["branch"]
    print(f"=== Sonnet leak: {len(leaks)} call(s) across {len(by_session)} session(s) ===")
    for sess in sorted(by_session, key=lambda s: -by_session[s]["cost"]):
        a = by_session[sess]
        print(f"  {sess[:8]}... calls={a['calls']:>4} cost={fmt_money(a['cost']):>10} "
              f"first={a['first_ts'][:19]} branch={a['branch']}")
        print(f"    models: {', '.join(sorted(a['models']))}")
    return 1  # non-zero exit so CI can gate


def cmd_sessions(args) -> int:
    by_sess: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost": 0.0, "first_ts": "", "last_ts": "", "models": set(), "branch": ""})
    for ev in iter_events(PROJECT_DIR):
        a = by_sess[ev["session"]]
        a["calls"] += 1
        a["cost"] += cost_of(ev["usage"], ev["model"])
        a["models"].add(ev["model"] or "?")
        if not a["first_ts"] or ev["ts"] < a["first_ts"]:
            a["first_ts"] = ev["ts"]
        if ev["ts"] > a["last_ts"]:
            a["last_ts"] = ev["ts"]
        if ev["branch"]:
            a["branch"] = ev["branch"]
    if not by_sess:
        print("No sessions.")
        return 0
    items = sorted(by_sess.items(), key=lambda kv: -kv[1]["cost"])[:args.top]
    print(f"=== Top {len(items)} sessions by cost ===")
    print(f"{'session':<10} {'calls':>6} {'cost':>10} {'start':<20} {'branch':<14} models")
    print("-" * 90)
    for sess, a in items:
        models_str = ",".join(sorted({m.replace("claude-", "") for m in a["models"]}))
        print(f"{sess[:8]:<10} {a['calls']:>6} {fmt_money(a['cost']):>10} "
              f"{a['first_ts'][:19]:<20} {a['branch'][:14]:<14} {models_str}")
    return 0


WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}

# Hot path = mandatory Opus (T-A1). Match anywhere in file_path string.
HOT_PATHS = (
    "src/ragbot/", "alembic/versions/", "docs/master/",
    "RAGBOT_MASTER.md", "RAGBOT_24STEP_PIPELINE",
)
HOT_FILES = ("CLAUDE.md",)  # exact basename match
HOT_BASH_PATTERNS = (
    "git commit", "git push", "gh pr create", "gh pr edit", "gh pr merge",
    "alembic upgrade", "alembic downgrade", "alembic revision",
)
HOT_BASH_DML_RE = ("INSERT INTO", "UPDATE ", "DELETE FROM", "TRUNCATE", "DROP TABLE", "ALTER TABLE")
SONNET_MODEL_FOR_REPLAY = "claude-sonnet-4-6"


def model_family(model: str) -> str:
    ml = model.lower()
    if "opus" in ml:    return "opus"
    if "haiku" in ml:   return "haiku"
    if "sonnet" in ml:  return "sonnet"
    return "other"


def classify_tier(tool_uses: list[dict]) -> tuple[str, str]:
    """Return (tier, reason). tier='T-A1' (Opus mandatory) or 'T-A2' (Sonnet OK)."""
    for td in tool_uses:
        name = td.get("name", "")
        if name in WRITE_TOOLS:
            fp = td.get("file_path", "") or ""
            for hp in HOT_PATHS:
                if hp in fp:
                    return "T-A1", f"{name}→{hp}"
            base = fp.rsplit("/", 1)[-1] if fp else ""
            if base in HOT_FILES:
                return "T-A1", f"{name}→{base}"
        elif name == "Bash":
            cmd = td.get("command", "") or ""
            for p in HOT_BASH_PATTERNS:
                if p in cmd:
                    return "T-A1", f"Bash:{p}"
            cmd_upper = cmd.upper()
            for p in HOT_BASH_DML_RE:
                if p in cmd_upper:
                    return "T-A1", f"Bash-DML:{p.strip()}"
    return "T-A2", "no-hot-write"


def cmd_model_mix(args) -> int:
    """Per-model breakdown + write-leak detection for tier policy enforcement."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    by_fam: dict[str, dict] = defaultdict(lambda: {
        "calls": 0, "cost": 0.0, "tool_calls": 0, "write_calls": 0,
        "models": set(), "sessions": set(),
    })
    write_leaks: list[dict] = []
    for ev in iter_events(PROJECT_DIR):
        ts = parse_ts(ev["ts"])
        if not ts or ts < cutoff:
            continue
        fam = model_family(ev["model"])
        c = cost_of(ev["usage"], ev["model"])
        a = by_fam[fam]
        a["calls"] += 1
        a["cost"] += c
        a["models"].add(ev["model"] or "?")
        a["sessions"].add(ev["session"])
        a["tool_calls"] += len(ev["tool_uses"])
        write_used = [t["name"] for t in ev["tool_uses"] if t.get("name") in WRITE_TOOLS]
        a["write_calls"] += len(write_used)
        if write_used and fam != "opus":
            write_leaks.append({
                "session": ev["session"],
                "model": ev["model"],
                "fam": fam,
                "tools": write_used,
                "ts": ev["ts"],
                "branch": ev["branch"],
            })

    if not by_fam:
        print(f"No events in last {args.days} days.")
        return 0

    total_calls = sum(a["calls"] for a in by_fam.values())
    total_cost  = sum(a["cost"]  for a in by_fam.values())
    print(f"=== Model mix — last {args.days} days ===")
    print(f"{'family':<10} {'calls':>7} {'%calls':>7} {'cost':>11} {'%cost':>7} "
          f"{'tools':>7} {'write':>7} {'sessions':>9}")
    print("-" * 76)
    for fam in ("opus", "haiku", "sonnet", "other"):
        if fam not in by_fam:
            continue
        a = by_fam[fam]
        pc = a["calls"] / total_calls * 100 if total_calls else 0
        pcost = a["cost"] / total_cost * 100 if total_cost else 0
        flag = ""
        if fam == "sonnet":   flag = "  ✗ SACRED BAN"
        elif fam == "haiku" and a["write_calls"] > 0:  flag = "  ✗ WRITE-LEAK"
        elif fam == "haiku":  flag = "  (T-B carve-out)"
        elif fam == "opus":   flag = "  ✓ T-A"
        print(f"{fam:<10} {a['calls']:>7} {pc:>6.1f}% {fmt_money(a['cost']):>11} {pcost:>6.1f}% "
              f"{a['tool_calls']:>7} {a['write_calls']:>7} {len(a['sessions']):>9}{flag}")

    print()
    # Tier policy check
    sonnet_calls = by_fam.get("sonnet", {}).get("calls", 0)
    haiku_writes = by_fam.get("haiku", {}).get("write_calls", 0)
    haiku_pct = by_fam.get("haiku", {}).get("calls", 0) / total_calls * 100 if total_calls else 0

    print("=== Tier policy check (CLAUDE.md) ===")
    rc = 0
    if sonnet_calls > 0:
        print(f"  ✗ Sonnet usage = {sonnet_calls} call(s) — sacred ban broken")
        rc = 1
    else:
        print("  ✓ Sonnet usage = 0 (sacred ban held)")
    if haiku_writes > 0:
        print(f"  ✗ Haiku write-leak = {haiku_writes} Edit/Write/NotebookEdit call(s) — T-B violated")
        rc = 1
    else:
        print(f"  ✓ Haiku write-leak = 0 (T-A boundary held)")
    if haiku_pct > 30:
        print(f"  ⚠ Haiku usage = {haiku_pct:.1f}% > 30% target (review if expected)")
    else:
        print(f"  ✓ Haiku usage = {haiku_pct:.1f}% ≤ 30% target")

    if write_leaks:
        print()
        print(f"=== Write-leak detail ({len(write_leaks)} call(s)) ===")
        for wl in write_leaks[:20]:
            print(f"  {wl['fam']:<8} {wl['model']:<28} {wl['ts'][:19]} sess={wl['session'][:8]} "
                  f"branch={wl['branch']:<14} tools={','.join(wl['tools'])}")
        if len(write_leaks) > 20:
            print(f"  ... +{len(write_leaks)-20} more")
    return rc


def _is_hot_path(fp: str) -> bool:
    if not fp:
        return False
    for hp in HOT_PATHS:
        if hp in fp:
            return True
    base = fp.rsplit("/", 1)[-1] if fp else ""
    return base in HOT_FILES


def cmd_tier_replay(args) -> int:
    """What-if replay: classify each session as T-A1/T-A2 and price T-A2 at Sonnet rates.

    Session-level classification (Claude Code sets model at session start):
      T-A1 (Opus mandatory) if ANY:
        - Write/Edit/NotebookEdit on hot path (src/ragbot/, alembic/versions/, CLAUDE.md, ...)
        - Bash with commit/push/PR/alembic/DML
        - Deepdive signal: ≥ DEEPDIVE_HOT_READS unique hot-path Reads
      T-A2 (Sonnet OK) otherwise — supporting work (scripts/, tests/, docs/, lookup, research)
    """
    target = args.date  # YYYY-MM-DD or None=all
    deepdive_threshold = args.deepdive_reads

    # Pass 1: collect per-session stats
    sess_data: dict[str, dict] = defaultdict(lambda: {
        "calls": 0, "actual_cost": 0.0, "model": "",
        "first_ts": "", "branch": "",
        "hot_writes": [], "hot_bash": [], "hot_reads": set(),
        "all_writes": defaultdict(int),  # file_path → count
        "tier": "", "tier_reason": "",
        "events": [],
    })
    for ev in iter_events(PROJECT_DIR):
        ts = parse_ts(ev["ts"])
        if not ts:
            continue
        if target and ts.date().isoformat() != target:
            continue
        s = sess_data[ev["session"]]
        s["calls"] += 1
        s["actual_cost"] += cost_of(ev["usage"], ev["model"])
        if not s["model"] and ev["model"]:
            s["model"] = ev["model"]
        if not s["first_ts"]:
            s["first_ts"] = ev["ts"]
            s["branch"] = ev["branch"]
        s["events"].append(ev)
        for td in ev["tool_uses"]:
            name = td.get("name", "")
            fp = td.get("file_path", "") or ""
            cmd = td.get("command", "") or ""
            if name in WRITE_TOOLS:
                if fp:
                    s["all_writes"][fp] += 1
                if _is_hot_path(fp):
                    s["hot_writes"].append((name, fp))
            elif name == "Read" and _is_hot_path(fp):
                s["hot_reads"].add(fp)
            elif name == "Bash":
                if any(p in cmd for p in HOT_BASH_PATTERNS):
                    s["hot_bash"].append(cmd[:80])
                else:
                    cu = cmd.upper()
                    if any(p in cu for p in HOT_BASH_DML_RE):
                        s["hot_bash"].append(cmd[:80])

    if not sess_data:
        print(f"No sessions on {target or 'any date'}.")
        return 0

    # Classify each session + recompute as-Sonnet cost for T-A2
    total_actual = 0.0
    total_replay = 0.0
    by_tier: dict[str, dict] = defaultdict(lambda: {"sessions": 0, "calls": 0, "actual": 0.0, "replay": 0.0})
    for sess, s in sess_data.items():
        if s["hot_writes"]:
            s["tier"], s["tier_reason"] = "T-A1", f"write→{s['hot_writes'][0][1].split('/')[-1]}"
        elif s["hot_bash"]:
            s["tier"], s["tier_reason"] = "T-A1", f"bash→{s['hot_bash'][0][:30]}"
        elif len(s["hot_reads"]) >= deepdive_threshold:
            s["tier"], s["tier_reason"] = "T-A1", f"deepdive {len(s['hot_reads'])} hot Reads"
        else:
            s["tier"], s["tier_reason"] = "T-A2", "no hot side-effect"

        replay_model = s["model"] if s["tier"] == "T-A1" else SONNET_MODEL_FOR_REPLAY
        replay_cost = sum(cost_of(ev["usage"], replay_model) for ev in s["events"])
        s["replay_cost"] = replay_cost

        total_actual += s["actual_cost"]
        total_replay += replay_cost
        t = by_tier[s["tier"]]
        t["sessions"] += 1
        t["calls"] += s["calls"]
        t["actual"] += s["actual_cost"]
        t["replay"] += replay_cost

    # Output
    label = target or "all data"
    print(f"=== Tier replay — {label} ===")
    print(f"Total: {len(sess_data)} session(s), {sum(s['calls'] for s in sess_data.values())} call(s)")
    print()
    print(f"{'tier':<6} {'sess':>5} {'calls':>6} {'%calls':>7} {'actual($)':>12} {'replay($)':>12} {'save':>7}")
    print("-" * 64)
    grand_calls = sum(t["calls"] for t in by_tier.values())
    for tier in ("T-A1", "T-A2"):
        if tier not in by_tier:
            continue
        t = by_tier[tier]
        pct = t["calls"] / grand_calls * 100 if grand_calls else 0
        save = (t["actual"] - t["replay"]) / t["actual"] * 100 if t["actual"] else 0
        print(f"{tier:<6} {t['sessions']:>5} {t['calls']:>6} {pct:>6.1f}% "
              f"{fmt_money(t['actual']):>12} {fmt_money(t['replay']):>12} {save:>6.1f}%")
    print("-" * 64)
    save_total = (total_actual - total_replay) / total_actual * 100 if total_actual else 0
    print(f"{'TOTAL':<6} {'':>5} {grand_calls:>6} {'':>7} "
          f"{fmt_money(total_actual):>12} {fmt_money(total_replay):>12} {save_total:>6.1f}%")
    print()
    print(f"Optimal mix: {by_tier.get('T-A1',{}).get('calls',0)/grand_calls*100:.0f}% Opus (T-A1) · "
          f"{by_tier.get('T-A2',{}).get('calls',0)/grand_calls*100:.0f}% Sonnet (T-A2)")
    print(f"Savings if applied: {fmt_money(total_actual - total_replay)} "
          f"({save_total:.1f}% of {fmt_money(total_actual)})")

    # Top sessions
    print()
    print(f"=== Top {min(args.top, len(sess_data))} sessions ===")
    print(f"{'tier':<6} {'sess':<10} {'calls':>5} {'actual':>10} {'replay':>10} {'reason':<35} branch")
    print("-" * 110)
    for sess, s in sorted(sess_data.items(), key=lambda kv: -kv[1]["actual_cost"])[:args.top]:
        print(f"{s['tier']:<6} {sess[:8]:<10} {s['calls']:>5} "
              f"{fmt_money(s['actual_cost']):>10} {fmt_money(s['replay_cost']):>10} "
              f"{s['tier_reason'][:35]:<35} {s['branch'][:14]}")

    # Top hot-path writes (validate classification)
    all_hot_writes: dict[str, int] = defaultdict(int)
    for s in sess_data.values():
        for _, fp in s["hot_writes"]:
            all_hot_writes[fp] += 1
    if all_hot_writes:
        print()
        print(f"=== Top hot-path file edits (drove T-A1) ===")
        for fp, n in sorted(all_hot_writes.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {n:>4}× {fp}")
    return 0


FEATURE_NAME_UNSET_BUCKET = "unset"


def per_feature_aggregate(rows: list[dict]) -> list[dict]:
    """Group model_invocations rows by feature_name.

    Pure function — separable from the DB source so unit tests can feed
    canned rows in. Each input row is a dict-like with at minimum
    ``feature_name`` (str | None), ``prompt_tokens`` (int),
    ``completion_tokens`` (int), ``cost_usd`` (float-ish — Decimal,
    str, float, int all OK). Missing keys default to 0.
    NULL / empty / whitespace ``feature_name`` rolls up under
    ``FEATURE_NAME_UNSET_BUCKET`` so legacy callers stay visible
    instead of being silently dropped.
    Output rows: ``feature_name``, ``calls``, ``prompt_tokens``,
    ``completion_tokens``, ``total_tokens``, ``cost_usd`` — sorted by
    ``cost_usd`` descending then by name for tie-break stability.
    """
    agg: dict[str, dict] = defaultdict(lambda: {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_usd": 0.0,
    })
    for row in rows:
        raw = row.get("feature_name")
        bucket: str
        if raw is None:
            bucket = FEATURE_NAME_UNSET_BUCKET
        else:
            stripped = str(raw).strip()
            bucket = stripped if stripped else FEATURE_NAME_UNSET_BUCKET
        a = agg[bucket]
        a["calls"] += 1
        a["prompt_tokens"] += int(row.get("prompt_tokens") or 0)
        a["completion_tokens"] += int(row.get("completion_tokens") or 0)
        cost_raw = row.get("cost_usd") or 0
        try:
            a["cost_usd"] += float(cost_raw)
        except (TypeError, ValueError):
            # Defensive: cost stored as string from psycopg Decimal repr
            # would already cast cleanly above; only truly malformed input
            # lands here. Skip — observability MUST never crash on bad data.
            pass

    out: list[dict] = []
    for name, a in agg.items():
        out.append({
            "feature_name": name,
            "calls": a["calls"],
            "prompt_tokens": a["prompt_tokens"],
            "completion_tokens": a["completion_tokens"],
            "total_tokens": a["prompt_tokens"] + a["completion_tokens"],
            "cost_usd": a["cost_usd"],
        })
    # Sort: highest cost first, then name asc — deterministic for tests.
    out.sort(key=lambda r: (-r["cost_usd"], r["feature_name"]))
    return out


def _fetch_per_feature_rows(dsn: str, days: int) -> list[dict]:
    """Read model_invocations rows for last ``days``. Sync psycopg2.

    Kept thin so the aggregator stays unit-testable without a live DB.
    """
    try:
        import psycopg2  # type: ignore
        import psycopg2.extras  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "psycopg2 required for per-feature subcommand (DB read); "
            "install psycopg2-binary or run aggregator directly on rows"
        ) from e

    # Driver hint scrubbing: the project DSN is the SQLAlchemy form
    # ``postgresql+asyncpg://...`` for async paths. psycopg2 wants the
    # plain ``postgresql://`` form, so strip the dialect suffix when
    # present. ``postgresql+psycopg2`` collapses the same way.
    clean_dsn = dsn
    for prefix, plain in (
        ("postgresql+asyncpg://", "postgresql://"),
        ("postgresql+psycopg2://", "postgresql://"),
    ):
        if clean_dsn.startswith(prefix):
            clean_dsn = plain + clean_dsn[len(prefix):]
            break

    sql = """
        SELECT feature_name, prompt_tokens, completion_tokens, cost_usd
          FROM model_invocations
         WHERE started_at >= NOW() - (%s || ' days')::interval
    """
    conn = psycopg2.connect(clean_dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(days),))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def cmd_per_feature(args) -> int:
    """Per-feature cost rollup from model_invocations.

    Output: ``feature_name | calls | prompt | completion | total | cost``
    table sorted by cost desc.
    """
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        sys.stderr.write("DATABASE_URL env var required for per-feature\n")
        return 2
    try:
        rows = _fetch_per_feature_rows(dsn, args.days)
    except RuntimeError as e:
        sys.stderr.write(f"[cost_audit] per-feature: {e}\n")
        return 2

    agg = per_feature_aggregate(rows)
    if not agg:
        print(f"No model_invocations in last {args.days} days.")
        return 0

    print(f"=== Per-feature cost — last {args.days} days "
          f"({sum(r['calls'] for r in agg)} call(s), "
          f"{len(agg)} feature(s)) ===")
    print(f"{'feature_name':<36} {'calls':>7} {'prompt':>10} {'compl':>10} "
          f"{'total':>10} {'cost':>11}")
    print("-" * 89)
    total_calls = 0
    total_prompt = 0
    total_compl = 0
    total_cost = 0.0
    for r in agg[:args.top]:
        print(f"{r['feature_name'][:36]:<36} {r['calls']:>7} "
              f"{fmt_tok(r['prompt_tokens']):>10} "
              f"{fmt_tok(r['completion_tokens']):>10} "
              f"{fmt_tok(r['total_tokens']):>10} "
              f"{fmt_money(r['cost_usd']):>11}")
        total_calls += r["calls"]
        total_prompt += r["prompt_tokens"]
        total_compl += r["completion_tokens"]
        total_cost += r["cost_usd"]
    if len(agg) > args.top:
        rest = len(agg) - args.top
        rest_calls = sum(r["calls"] for r in agg[args.top:])
        rest_cost = sum(r["cost_usd"] for r in agg[args.top:])
        print(f"{'(+ ' + str(rest) + ' more)':<36} {rest_calls:>7} "
              f"{'':>10} {'':>10} {'':>10} {fmt_money(rest_cost):>11}")
        total_calls += rest_calls
        total_cost += rest_cost
    print("-" * 89)
    print(f"{'TOTAL':<36} {total_calls:>7} "
          f"{fmt_tok(total_prompt):>10} {fmt_tok(total_compl):>10} "
          f"{fmt_tok(total_prompt + total_compl):>10} "
          f"{fmt_money(total_cost):>11}")
    return 0


def cmd_advise(args) -> int:
    """Run advisor rules — adapted from claude-token-monitor."""
    today_utc = datetime.now(timezone.utc).date()
    by_sess: dict[str, dict] = defaultdict(lambda: {
        "calls": 0, "cost": 0.0, "opus_cost": 0.0, "sonnet_cost": 0.0,
        "out_total": 0, "cache_read": 0, "cache_write": 0, "input_total": 0,
        "raw_input_spikes": 0, "first_ts": "", "branch": "",
    })
    for ev in iter_events(PROJECT_DIR):
        ts = parse_ts(ev["ts"])
        if not ts or ts.date() != today_utc:
            continue
        c = cost_of(ev["usage"], ev["model"])
        a = by_sess[ev["session"]]
        a["calls"] += 1
        a["cost"] += c
        ml = ev["model"].lower()
        if "opus" in ml:
            a["opus_cost"] += c
        elif "sonnet" in ml:
            a["sonnet_cost"] += c
        u = ev["usage"]
        a["out_total"] += u.get("output_tokens", 0)
        a["cache_read"]  += u.get("cache_read_input_tokens", 0)
        a["cache_write"] += u.get("cache_creation_input_tokens", 0)
        a["input_total"] += u.get("input_tokens", 0)
        if u.get("input_tokens", 0) > 50_000:
            a["raw_input_spikes"] += 1
        if not a["first_ts"]:
            a["first_ts"] = ev["ts"]
            a["branch"] = ev["branch"]

    if not by_sess:
        print("No sessions today to advise on.")
        return 0

    findings: list[str] = []
    for sess, a in by_sess.items():
        sid = sess[:8]
        avg_out = a["out_total"] / a["calls"] if a["calls"] else 0
        cache_total = a["cache_read"] + a["cache_write"]
        cache_hit = a["cache_read"] / cache_total if cache_total else 0
        cw_cr_ratio = a["cache_write"] / a["cache_read"] if a["cache_read"] else 0

        if a["sonnet_cost"] > 0:
            findings.append(f"[SONNET-LEAK] {sid} sonnet_cost={fmt_money(a['sonnet_cost'])} — CLAUDE.md ép 100% Opus")
        if a["calls"] >= 20 and a["opus_cost"] / max(a["cost"], 1e-9) >= 0.95 and avg_out < 500:
            findings.append(f"[OPUS-ROUTINE] {sid} {a['calls']} calls all-Opus, avg_out={avg_out:.0f} — routine work, "
                          "nhưng CLAUDE.md cấm Sonnet → KEEP nhưng lưu ý nếu cost spike")
        if a["cost"] > 1.0 and cache_hit < 0.40:
            findings.append(f"[LOW-CACHE-HIT] {sid} cost={fmt_money(a['cost'])} cache_hit={cache_hit*100:.1f}% — "
                          f"tránh /clear, giữ work liên quan trong 1 session")
        if a["raw_input_spikes"] >= 3:
            findings.append(f"[RAW-INPUT-SPIKE] {sid} {a['raw_input_spikes']} call >50k input — nén stdout/log dump trước khi paste")
        if cw_cr_ratio > 0.2 and cache_total > 100_000:
            findings.append(f"[CACHE-REBUILD] {sid} cw/cr={cw_cr_ratio:.2f} — session dài, cân nhắc /clear sớm hơn")

    short_sessions = [s for s, a in by_sess.items() if a["calls"] < 5]
    if len(short_sessions) >= 3:
        findings.append(f"[SESSION-FRAGMENTATION] {len(short_sessions)} session ngắn (<5 calls) hôm nay — "
                       "merge vào 1 session để tái dụng cache")

    if not findings:
        print("Today: no advisor findings.")
        return 0
    print(f"=== Advisor findings — {today_utc} UTC ({len(findings)}) ===")
    for f in findings:
        print(f"  • {f}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="cost_audit", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("today",       help="Today's cost grouped by model")
    w = sub.add_parser("weekly",  help="Last N days cost trend")
    w.add_argument("--days", type=int, default=7)
    sub.add_parser("sonnet-leak", help="List any Sonnet calls (CI gate)")
    s = sub.add_parser("sessions", help="Top sessions by cost")
    s.add_argument("--top", type=int, default=10)
    m = sub.add_parser("model-mix", help="Tier policy check: Opus/Haiku/Sonnet ratio + write-leak")
    m.add_argument("--days", type=int, default=7)
    r = sub.add_parser("tier-replay", help="What-if: classify T-A1/T-A2 + price T-A2 at Sonnet")
    r.add_argument("--date", type=str, default=None, help="YYYY-MM-DD; default = all data")
    r.add_argument("--top", type=int, default=10)
    r.add_argument("--deepdive-reads", type=int, default=5,
                   help="Min unique hot-path Reads to flag T-A1 deepdive (default 5)")
    sub.add_parser("advise",      help="Run advisor rules on today's sessions")
    pf = sub.add_parser("per-feature",
                        help="Per-feature cost rollup from model_invocations DB")
    pf.add_argument("--days", type=int, default=DEFAULT_PER_FEATURE_WINDOW_DAYS,
                    help=f"Trailing window (default {DEFAULT_PER_FEATURE_WINDOW_DAYS})")
    pf.add_argument("--top", type=int, default=DEFAULT_PER_FEATURE_TOP,
                    help=f"Top N features to show (default {DEFAULT_PER_FEATURE_TOP})")
    args = p.parse_args()

    return {
        "today":        cmd_today,
        "weekly":       cmd_weekly,
        "sonnet-leak":  cmd_sonnet_leak,
        "sessions":     cmd_sessions,
        "model-mix":    cmd_model_mix,
        "tier-replay":  cmd_tier_replay,
        "advise":       cmd_advise,
        "per-feature":  cmd_per_feature,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
