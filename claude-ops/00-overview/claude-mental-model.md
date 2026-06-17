# Claude Mental Model — Opus / Sonnet / Haiku + Cache TTL

> **Mục tiêu**: hiểu **tại sao** tier policy của Ragbot là "Opus main + Sonnet subagent + Haiku banned". Không phải lý thuyết suông — số liệu thật từ `cost_audit.py` 30-day baseline.

---

## 1. Ba tier model — concrete pricing

| Tier | Model | $/1M input | $/1M output | $/1M cache_read | Use case |
|---|---|---|---|---|---|
| Top | `claude-opus-4-7` | **$15** | **$75** | $1.875 | Main session, deepdive, write code |
| Mid | `claude-sonnet-4-6` | $3 | $15 | $0.30 | Subagent read-only research |
| Low | `claude-haiku-4-5` | $1 | $5 | $0.10 | **BANNED ở Ragbot** (silent miss risk) |

**Why Haiku banned** — Haiku reasoning yếu cho deepdive multi-file, dễ silent miss → cascade vào parent decision → ship bug. Ragbot có HALLU=0 sacred → không tolerate silent failure mode. Sonnet 4.6 cũng cheaper hơn Opus 5× nhưng có reasoning đủ strong cho lookup.

---

## 2. Cache TTL = 5 phút (Anthropic prompt cache)

### Cơ chế

Anthropic prompt cache có **TTL 5 phút** từ lần access cuối. Cached tokens billed ở 10% giá gốc — Opus cache_read = $1.875/1M tokens vs $15/1M raw.

### Implication thực tế

**Cùng session, same prompt, < 5 phút giữa lần dùng**:
```
Turn 1: input 100K token raw  → $1.50 (Opus)
Turn 2 (sau 2 phút): input 100K token, 95% cache hit → $0.13
```

→ **89% saving** khi cache warm.

**Cách-tuần** (cold start mỗi lần):
```
Mỗi turn: input 100K token raw → $1.50 mỗi lần
```

→ **0% saving**, paying full price every time.

### Số thật từ `cost_audit.py` (Ragbot 30-day)

| Trạng thái | Cost/turn | Cache hit |
|---|---|---|
| Warm (V12 baseline) | $0.001619 | 89.7% |
| Cold (V13 fresh DB) | $0.000496 | 0% |

V13 cold cheaper raw vì lượt input ít hơn, nhưng **steady-state V13 sẽ converge về V12 sau cache warm**.

---

## 3. Decision rule — pick model nào cho task gì

### Decision tree (run mental model trước mỗi `Agent` invocation)

```
Task này có WRITE side-effect không?
  (Edit/Write/NotebookEdit; commit/push/gh-create; DB write; alembic; sysprompt; schema)
├─ CÓ → main session (Opus)
└─ KHÔNG → có phải pure lookup/research không?
   ├─ subagent_type=Explore (>3 grep/file)  → Agent({model:"sonnet"})
   ├─ WebFetch/WebSearch summary             → Agent({model:"sonnet"})
   ├─ grep/find/Read-only Bash               → Agent({model:"sonnet"})
   └─ ambiguous / có chance ghi              → main session (Opus, default an toàn)
```

### Anti-pattern PHẢI tránh

| Anti-pattern | Vì sao |
|---|---|
| Spawn Sonnet subagent để **ghi code** vào `src/ragbot/` | Sonnet history regex/schema/template/override-answer bug |
| Switch main session sang Sonnet để "save cost" | Pollution risk: 1 hot-edit mid-session = Sonnet ghi src/ |
| Spawn Haiku subagent | Silent miss → parent decision wrong |

---

## 4. Caveat — Ragbot harness tier saving CHƯA verified

**Empirical finding** (Stream X): trên Opus-1M variant Anthropic CLI, `Agent({model:"sonnet"})` invocations KHÔNG ghi sidechain entry vào JSONL. Có 2 khả năng:

1. **Inline simulation**: harness chạy subagent ở cùng main model = no real saving.
2. **Honored but not logged**: cost giảm thật nhưng `cost_audit.py model-mix` không đo được local.

→ Verify ground-truth: vào **console.anthropic.com → Usage** filter date+API key, check có line `claude-sonnet-4-*` không.

Cho tới khi verify, tier policy = **architectural intent** + forward-compat (khi Anthropic ship per-Agent model swap proper, code đã đúng).

---

## 5. Cache strategy thực tế giảm cost

| Lever | Saving expect | Effort |
|---|---|---|
| **Session granularity**: 1 work-block = 1 session ngắn (vs 980-call mega session) | Cache rebuild giảm | Coordination |
| **Stop bloat reads**: đừng load full STATE_SNAPSHOT/CLAUDE.md mỗi turn nếu memory đã giải | Memory đã giải 1 phần | 0 |
| **Test pollution fix** (Stream L Phase 4) | Mỗi session audit fail = +input tokens | 2-4h |
| **Sub-agent delegate** (KHI Anthropic enable proper) | 10-20% sau enable | đợi platform |

---

## Reference

- Anthropic prompt caching docs: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- Source pattern: `emtyty/claude-token-monitor` README
- Implementation: `scripts/cost_audit.py` ở Ragbot
- Memory: `feedback_v2_bug_lessons.md` (Sonnet bug history)
