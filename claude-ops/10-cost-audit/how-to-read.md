# How to read cost_audit.py output

> Hướng dẫn đọc output cụ thể của 6 sub-cmd. Anh chạy lệnh, đối chiếu output với section dưới đây.

---

## `today`

```bash
$ python scripts/cost_audit.py today
=== Cost — 2026-05-06 UTC === (2 session(s))
model                         calls       in      out   cache_r   cache_w        cost
--------------------------------------------------------------------------------------
claude-opus-4-7                  29     1.7k    39.4k     7.68M    474.3k    $26.2748
--------------------------------------------------------------------------------------
TOTAL                                                                        $26.2748

  Sonnet leak: 0 — CLAUDE.md rule held.
```

**Đọc**:
- `2 session(s)` = số JSONL file modified hôm nay
- `calls` = số assistant turn (deduped theo msg.id)
- `in` / `out` / `cache_r` / `cache_w` = token raw (M = million, k = thousand)
- `cost` = USD (PRICING dict ở top script)
- `Sonnet leak` = số Sonnet calls (sacred = 0)

**Cảnh báo nếu**:
- `cost > $50/day` → check sessions để spot mega-session
- `Sonnet leak > 0` → CLAUDE.md rule violated

---

## `weekly --days N`

```bash
$ python scripts/cost_audit.py weekly --days 7
=== Last 7 days ===
date          calls       opus     sonnet      total
------------------------------------------------------
2026-04-29     1602  $1,644.5339    $0.0000  $1,644.5339   ← peak
2026-04-30      810   $713.4071    $0.0000   $713.4071
2026-05-01      682   $605.4065    $0.0000   $605.4065
2026-05-02      731   $702.8734    $0.0000   $702.8734
2026-05-03       88    $85.0219    $0.0000    $85.0219    ← quiet
2026-05-04      666   $598.3178    $0.0000   $598.3178
2026-05-05     1361   $992.5414    $0.0000   $992.5414
------------------------------------------------------
GRAND                                       $5,341.9920
```

**Đọc**:
- `peak` (2026-04-29) = $1,644 — V4 GA-hardening day, chấp nhận được nếu shipping major sprint
- `quiet` (2026-05-03) = $85 — light day, no major work
- Median ~$650/day → baseline normal

**Cảnh báo nếu**:
- Day spike >3× median → check `sessions --top 5` cho ngày đó để identify mega-session
- 2 ngày consecutive >$1k → consider tier policy verify Anthropic Console

---

## `sessions --top N`

```bash
$ python scripts/cost_audit.py sessions --top 5
=== Top 5 sessions by cost ===
session     calls       cost start                branch         models
------------------------------------------------------------------------------
067777d8      980  $917.1533 2026-04-22T10:51:27  main           opus-4-7
3778e1cf      987  $845.3904 2026-04-21T13:34:38  main           opus-4-6
15b0ccdc      600  $639.6720 2026-05-02T07:47:28  main           opus-4-7
61f94ea3      909  $620.5925 2026-04-20T18:11:28  main           opus-4-6
df14685d      574  $566.9274 2026-04-30T19:45:43  main           opus-4-7
```

**Đọc**:
- Top session = single mega-session = ~1000 calls = ~$917
- Pattern: V11/V10/V13 shipping days dominate

**Cảnh báo nếu**:
- 1 session > 1500 calls → consider split (ship-từng-cái rule)
- Cost > $1000/session → review work-block boundary

---

## `model-mix --days N`

```bash
$ python scripts/cost_audit.py model-mix --days 7
=== Model mix — last 7 days ===
family       calls  %calls        cost   %cost   tools   write  sessions
----------------------------------------------------------------------------
opus          5318   99.5% $4,713.1204  100.0%    1696     327        24  ✓ T-A
other           28    0.5%     $0.0000    0.0%       0       0         6

=== Tier policy check (CLAUDE.md) ===
  ✓ Sonnet usage = 0 (sacred ban held)
  ✓ Haiku write-leak = 0 (T-A boundary held)
  ✓ Haiku usage = 0.0% ≤ 30% target
```

**Đọc**:
- `opus 99.5%` = main session ratio → expected
- `Sonnet usage = 0` → harness limitation OR no subagent spawned (xem Stream X caveat)
- `other 0.5%` = synthetic events (ai_config_repository test, etc.) — ignore
- `tools 1696` = total tool_use calls
- `write 327` = Edit/Write/NotebookEdit calls

**Cảnh báo nếu**:
- Sonnet usage > 0 (sacred breach) → escalate
- Haiku usage > 0 → emergency revert
- Write count > 50% of tools → audit, có thể quá nhiều edit small

---

## `sonnet-leak`

```bash
$ python scripts/cost_audit.py sonnet-leak
No Sonnet calls found — CLAUDE.md rule held.
```

CI gate. Exit 1 nếu Sonnet xuất hiện. Wire vào pre-push hook:
```bash
# .git/hooks/pre-push
python scripts/cost_audit.py sonnet-leak || exit 1
```

---

## `tier-replay --date YYYY-MM-DD`

```bash
$ python scripts/cost_audit.py tier-replay --date 2026-05-05 --top 10
=== Tier replay — 2026-05-05 ===
Total: 14 session(s), 1361 call(s)

tier    sess  calls  %calls    actual($)    replay($)    save
----------------------------------------------------------------
T-A1       6   1230   90.4%    $950.2796    $950.2796    0.0%
T-A2       8    131    9.6%     $42.2618      $7.6676   81.9%
----------------------------------------------------------------
TOTAL          1361            $992.5414    $957.9472    3.5%

Optimal mix: 90% Opus (T-A1) · 10% Sonnet (T-A2)
Savings if applied: $34.5942 (3.5% of $992.5414)
```

**Đọc**:
- `T-A1` = session cần Opus (touch hot-path src/, alembic, sysprompt)
- `T-A2` = session pure-research (no hot write) — Sonnet OK theoretically
- `90.4% T-A1` = ngày shipping-heavy (V10 workspace 4-key) → very little saving room
- `save 3.5%` = nếu apply tier policy thì ngày này tiết kiệm $34.59

**Use case**:
- Pick worst day (>$1k), run tier-replay → see actual saving potential
- Optimal mix range:
  - 90/10 = ngày shipping-heavy (ít save)
  - 50/50 = ngày research/planning (save 25-35%)
  - 10/90 = không khả thi cho hot-path stream

---

## `advise`

```bash
$ python scripts/cost_audit.py advise
Today: no advisor findings.
```

5 rule shipped:
1. **opus-routine** (≥20 calls all-Opus, avg_out <500) — routine work warning
2. **low-cache-hit** (cost >$1, cache <40%) — cache breakdown
3. **raw-input-spike** (≥3 calls >50K input) — log dump bloat
4. **cache-rebuild** (cw/cr >0.2 + cache_total >100K) — frequent /clear
5. **session-fragmentation** (≥3 short sessions <5 calls) — split bloat

4 rule chưa port — xem `advisor-rules-todo.md`.

---

## Common scenarios

### Scenario 1 — Cost spike day

```bash
$ python scripts/cost_audit.py weekly --days 7
# Spot 1 day >3× median
$ python scripts/cost_audit.py sessions --top 5
# Identify session ID
$ python scripts/cost_audit.py advise
# Check rule trigger
```

### Scenario 2 — Pre-commit Sonnet leak gate

```bash
# Pre-push hook
$ python scripts/cost_audit.py sonnet-leak || exit 1
```

### Scenario 3 — Optimize tier policy

```bash
# Run tier-replay cho 5 ngày, pick optimal mix range
for d in 2026-04-29 2026-05-01 2026-05-03 2026-05-05; do
  python scripts/cost_audit.py tier-replay --date $d --top 5
done
```

---

## Limitations

1. **Pricing dict** ở top script — update khi Anthropic đổi giá.
2. **Subagent JSONL gap** — sidechain entries có thể không capture model swap (Stream X harness caveat).
3. **Read-only** — không truncate JSONL, không edit. Pure analysis.

---

## Reference

- Source upstream: https://github.com/emtyty/claude-token-monitor
- Memory: `project_cost_audit_shipped.md`
- CLAUDE.md MODEL TIER POLICY section quote `cost_audit.py` commands
