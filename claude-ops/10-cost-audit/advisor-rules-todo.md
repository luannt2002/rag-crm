# Advisor Rules TODO — 4 rule chưa port từ upstream

> **Status**: 5/9 rule ported. 4 rule pending. Effort: ~1-2h pure addition vào `scripts/cost_audit.py` `cmd_advise()`.

---

## Đã port (5 rule)

| Rule | Trigger | Suggestion |
|---|---|---|
| `opus-routine` | ≥20 calls all-Opus, avg_out <500 | Routine work — consider Sonnet (CLAUDE.md cho phép subagent) |
| `low-cache-hit` | cost >$1, cache hit <40% | Avoid `/clear` mid-work-block |
| `raw-input-spike` | ≥3 calls >50K input | Compress stdout/log dump, use `zero rewrite-exec` |
| `cache-rebuild` | cw/cr >0.2 + cache_total >100K | Session quá dài, split sớm hơn |
| `session-fragmentation` | ≥3 short sessions <5 calls cùng project cùng ngày | Merge để tái dụng cache |

---

## TODO — 4 rule còn lại

### 1. `many-reads` — suggest ast-graph

**Upstream rule**:
```
≥30 Read calls + ≥40% tool ratio + supported language → use ast-graph symbol
instead of Read full file
```

**Implement**:
```python
read_count = sum(1 for ev in events if any(t["name"] == "Read" for t in ev["tool_uses"]))
tool_count = sum(len(ev["tool_uses"]) for ev in events)
read_ratio = read_count / tool_count if tool_count else 0

if read_count >= 30 and read_ratio >= 0.40:
    findings.append(f"[MANY-READS] {sid} — {read_count} Read calls ({read_ratio*100:.0f}%) — use ast-graph symbol or rg --files")
```

**Effort**: 15 min.

### 2. `day-spike` — anomaly detect

**Upstream rule**:
```
Day cost > 3× median 30-day → flag spike, suggest review top sessions
```

**Implement**:
```python
# In cmd_advise, after collecting today's data:
import statistics
weekly_costs = []  # need 30-day collect first
for ev in iter_events(PROJECT_DIR):
    ts = parse_ts(ev["ts"])
    if not ts:
        continue
    delta = (datetime.now(timezone.utc) - ts).days
    if 0 < delta <= 30:
        # accumulate per-day
        ...

median_30d = statistics.median(daily_costs)
today_cost = sum(...)

if today_cost > 3 * median_30d:
    findings.append(f"[DAY-SPIKE] today {fmt_money(today_cost)} > 3× median {fmt_money(median_30d)} — review sessions --top 5")
```

**Effort**: 30 min (cần 30-day buffer + statistics.median).

### 3. `low-cache-hit` cụ thể hơn

**Hiện tại rule đã có nhưng gen chung**. Upstream phân biệt:
- `low-cache-hit-write-heavy` (write_calls > 10 → expected, no warn)
- `low-cache-hit-read-heavy` (read_calls > 20 + cache <40% → real problem)

**Implement**:
```python
write_count = a["write_calls"]
read_count = sum(1 for ev in events if any(t["name"] == "Read" for t in ev["tool_uses"]))

if cache_hit < 0.40:
    if write_count > 10:
        # write-heavy: cache miss expected (each Edit invalidates), không warn
        pass
    elif read_count > 20:
        findings.append(f"[LOW-CACHE-HIT-READ] {sid} cache_hit={cache_hit*100:.0f}% + {read_count} reads — content rotation, consider session reset")
```

**Effort**: 15 min.

### 4. `explore-on-opus` — cost optimization signal

**Upstream rule**:
```
≥70% Opus tokens + ≥85% explore-pattern tools (Read, Grep, Glob, find via Bash)
→ suggest Opus only for synthesis, Sonnet for exploration
```

**Implement**:
```python
explore_tool_names = {"Read", "Grep", "Glob"}
explore_calls = sum(1 for ev in events for t in ev["tool_uses"]
                    if t.get("name") in explore_tool_names)
total_tools = sum(len(ev["tool_uses"]) for ev in events)
explore_ratio = explore_calls / total_tools if total_tools else 0

if a["opus_cost"] / max(a["cost"], 1e-9) >= 0.70 and explore_ratio >= 0.85:
    findings.append(
        f"[EXPLORE-ON-OPUS] {sid} — {explore_ratio*100:.0f}% explore tools on Opus. "
        f"Subagent({{model:'sonnet'}}) for explore would save ~5× per token (subject to Stream X harness verify)."
    )
```

**Effort**: 20 min.

---

## Plan ship (1-2h focused session)

```
Phase 0 — failing test
  tests/unit/test_cost_audit_advisor_v2.py:
    test_many_reads_rule_fires_at_threshold
    test_day_spike_rule_fires_at_3x_median
    test_low_cache_hit_distinguishes_read_vs_write
    test_explore_on_opus_rule_fires_at_70_85

Phase 1 — implement many-reads (simplest)
Phase 2 — implement explore-on-opus
Phase 3 — implement low-cache-hit-read split
Phase 4 — implement day-spike (cần 30-day buffer)
Phase 5 — verify against historical data — re-run advise on 2026-04-29 ($1,644 spike day)
          expect day-spike rule fires
```

---

## Sacred contracts intact

- Read-only script — không đụng `src/ragbot/`
- No new constants in `shared/constants.py` (script-internal threshold OK at module level)
- No alembic, no schema change
- T3 hygiene scope

---

## Reference

- Upstream: https://github.com/emtyty/claude-token-monitor `monitor.py` rule definitions
- Current implementation: `scripts/cost_audit.py:cmd_advise()`
- Memory: `reference_hueanmy_repos.md` (claude-token-monitor section)
