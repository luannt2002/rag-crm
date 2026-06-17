# Token Economics — Vì sao tier policy như vậy

> **TL;DR**: 30-day Ragbot data: $11,072 total / 13,420 calls / 99.6% Opus. Saving thật KHÔNG đến từ "downgrade main session" mà từ **session granularity** + **subagent delegate** (khi platform enable proper).

---

## 1. Cost breakdown 30 ngày Ragbot (cost_audit.py thật)

```bash
$ python scripts/cost_audit.py weekly --days 30
```

| Date | Calls | Cost | Notes |
|---|---|---|---|
| 2026-04-22 | 1,328 | **$1,409** | Day spike (refactor V11 prep) |
| 2026-04-29 | 1,602 | **$1,644** | V4 GA-hardening peak |
| 2026-05-05 | 1,361 | **$992** | V10 workspace 4-key shipping |
| 2026-05-06 | (varies) | $200-1000 | Stream A pipeline + tier policy + audit work |
| **Total 30d** | **13,420** | **$11,072** | 99.6% Opus 4.7, 0 Sonnet leak |

**Avg/day**: ~$370.
**Avg/turn**: ~$0.83 (raw, not steady-state).

---

## 2. Tại sao 99.6% Opus?

### Stream X finding

Em spawn 5+ `Agent({model:"sonnet"})` invocations test (multi-agent + audit). JSONL chỉ thấy `claude-opus-4-7`. Kết quả:

- Trên Opus-1M harness, `model="sonnet"` parameter **KHÔNG được honored** OR **không được log**.
- Empirically: tất cả token billed ở Opus rate.

→ Tier policy "Sonnet subagent" hiện tại **decorative**. Forward-compat khi Anthropic ship per-Agent model swap proper.

---

## 3. Saving thật đến từ đâu?

### A. Cache hit rate (lớn nhất)

| Metric | Cold start | Warm cache | Save |
|---|---|---|---|
| Cost/turn V12 baseline | — | $0.001619 | — |
| Cost/turn V13 cold | $0.000496 | — | — |
| Cache hit (Anthropic auto) | 0% | 89.7% | 89% |

**Mechanism**: cùng prompt + < 5 phút giữa turn = cache hit. Cached tokens 10% giá gốc.

**Action**:
- Avoid `/clear` mid-work-block (resets cache)
- Keep related work in 1 session
- `cost_audit.py advise` sẽ flag `low-cache-hit` rule

### B. Session granularity

```
Mega session 980 calls / 1 session = $917 (sample 067777d8 từ baseline)
```

vs

```
4 small sessions × 245 calls average = ~$650-700 nếu cache rebuild đúng cách
```

**Mechanism**: smaller session → less context bloat → less input tokens charged per turn.

**Action**:
- "Ship từng cái" rule: 1 work-block = 1 plan + 1 commit
- Avoid 980-call mega sessions doing "everything"
- `cost_audit.py advise` flag `session-fragmentation` (≥3 short sessions same project) — counter-signal

### C. Test pollution → audit overhead

113-120 fail trong full suite = mỗi session em phải audit fail list trước khi commit. Audit = +input tokens reading test output.

**Action**: Stream L Phase 4 — `@pytest.mark.integration` skip-by-default. Reduce fail to ≤30.

### D. Subagent delegate (khi platform support)

Per CLAUDE.md tier policy, Sonnet subagent cho lookup-only sẽ save ~10-20% trên ngày trung bình. **Pending Anthropic Console probe verify**.

---

## 4. Cost lever — what to do, what NOT to do

### ✅ DO

| Action | Saving |
|---|---|
| Keep cache warm (avoid `/clear` mid-work) | 89% trên cached tokens |
| Ship từng cái (atomic commit, focused session) | -10-20% via less bloat |
| Async load test (Stream Y `loadtest_kick.sh`) | -$10-15/run (no agent wait) |
| Fix test pollution (Stream L Phase 4) | -5-10% via cleaner CI signal |
| Use `cost_audit.py advise` daily | Detect anomaly early |

### ❌ DON'T

| Anti-pattern | Vì sao xấu |
|---|---|
| Switch main session sang Sonnet để "save cost" | Pollution + bug history |
| Mix Opus 4.7 với 4.6/4.5 | Same price tier, không save cost, chỉ giảm chất lượng |
| Spawn Haiku subagent | Silent miss risk |
| Mega session 1000+ calls | Cache bloat + context limit risk |
| `/clear` mid-work-block | Cache reset, hết save 89% |

---

## 5. Thực tế Ragbot baseline

| Sprint | Cost | Quality |
|---|---|---|
| V10 workspace 4-key shipping | ~$1k | ✅ shipped |
| V11 embedding consolidation | ~$700 | ✅ shipped, alembic 0063 |
| V12 corpus v2 + sysprompt v5c | ~$700 | ✅ PASS reclassified 98.9% |
| V13 4 fixes + dev DB rebuild | ~$600 | ✅ HALLU=0 maintained |
| Stream A Phase 0-4.5 + B + F + G + V + Y + W (this session) | ~$200-300 | ✅ Pipeline hardened |

**Pattern**: mỗi sprint major ship ~$600-1000 Opus tokens. Sustainable nếu cache hit >80%.

---

## Reference

- `scripts/cost_audit.py` 6 sub-commands
- `scripts/check_state_snapshot.py` drift check
- `CLAUDE.md` MODEL TIER POLICY section
- Stream X caveat: harness tier saving NOT VERIFIED
