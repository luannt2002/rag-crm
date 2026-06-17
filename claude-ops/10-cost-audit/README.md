# 10-cost-audit — Token monitor cho Claude Code session

> **Status**: ✅ APPLIED variant. `scripts/cost_audit.py` đã ship 6 sub-cmd (Stream `8fe2934`). Pattern adapted từ `emtyty/claude-token-monitor`. 4 advisor rule còn lại (`many-reads`, `day-spike`, `low-cache-hit`, +1 TBD) chưa port — TODO list ở `advisor-rules-todo.md`.

---

## Why

Anh chạy MEGA campaigns 450-900 turn × nhiều sub-agent Opus, mỗi ngày $700-1000. Cần dashboard cost/turn local để:
- Verify CLAUDE.md "Sonnet usage = 0%" rule
- Detect anomaly (day spike, cache breakdown)
- Plan saving lever (session granularity, cache hit)

`emtyty/claude-token-monitor` upstream có 9 advisor rule. Em port 5/9 + thêm 1 đặc thù Ragbot (`sonnet-leak`) = 6 rule trong `cost_audit.py`.

---

## Run

```bash
# 1. Daily kickoff
python scripts/cost_audit.py today

# 2. 7-day trend
python scripts/cost_audit.py weekly --days 7

# 3. 30-day baseline
python scripts/cost_audit.py weekly --days 30

# 4. Top sessions by cost
python scripts/cost_audit.py sessions --top 10

# 5. Tier policy check
python scripts/cost_audit.py model-mix --days 7

# 6. Sonnet leak (CI gate, exit 1 nếu phát hiện)
python scripts/cost_audit.py sonnet-leak

# 7. What-if replay (T-A1/T-A2 + Sonnet pricing)
python scripts/cost_audit.py tier-replay --date 2026-05-05 --top 10

# 8. Advisor rules
python scripts/cost_audit.py advise
```

---

## Data source

```
~/.claude/projects/-var-www-html-ragbot/*.jsonl
```

Mỗi file = 1 session. Mỗi line = 1 event (user msg / assistant msg / tool_use / tool_result).

**Dedupe**: `(sessionId, message.id)` per assistant turn.

**Schema** (mỗi assistant event):
```json
{
  "type": "assistant",
  "sessionId": "...",
  "message": {
    "id": "msg_xxx",
    "model": "claude-opus-4-7",
    "usage": {
      "input_tokens": 100,
      "cache_read_input_tokens": 12329,
      "cache_creation_input_tokens": 7199,
      "output_tokens": 691
    }
  },
  "timestamp": "2026-05-06T20:51:30.405Z"
}
```

---

## 6 sub-cmd shipped

| Sub-cmd | Purpose | Use case |
|---|---|---|
| `today` | Cost hôm nay grouped by model | Daily kickoff |
| `weekly --days N` | Cost trend per day | Spike detect |
| `sessions --top N` | Top expensive sessions | Identify mega-session bloat |
| `model-mix --days N` | Opus/Sonnet/Haiku ratio + write-leak | Tier policy compliance |
| `sonnet-leak` | Exit 1 nếu Sonnet xuất hiện | CI gate |
| `tier-replay --date YYYY-MM-DD` | What-if T-A1/T-A2 + Sonnet pricing | Optimize ratio |
| `advise` | Cache-hit / fragmentation / opus-routine rules | Anomaly alert |

---

## Detail mỗi sub-cmd

Đọc `how-to-read.md` cho output format + interpretation.

Đọc `advisor-rules-todo.md` cho 4 rule chưa port.

---

## Reference

- Source: https://github.com/emtyty/claude-token-monitor
- Implementation: `/var/www/html/ragbot/scripts/cost_audit.py`
- Pricing: `PRICING_USD_PER_MTOK` dict ở đầu script (update khi Anthropic đổi giá)
- Memory: `project_cost_audit_shipped.md`
