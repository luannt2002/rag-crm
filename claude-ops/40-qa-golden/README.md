# 40-qa-golden — DEFERRED (mâu thuẫn stack)

> **Status**: ⏭ DEFERRED. Pattern hay nhưng `playwright` JS/browser KHÔNG match Ragbot backend stack (Python/FastAPI/LangGraph). Adapt thành `/golden-plan → /golden-gen → /golden-run → /golden-heal` cho golden test per-bot Ragbot là khả thi (~1 ngày), nhưng ROI thấp hơn Stream D PROPER.

---

## Source pattern

`hueanmy/qa-playwright-agent` Claude Code plugin với 4 slash command:

| Slash | Purpose |
|---|---|
| `/qa-plan <feature>` | Phân tích app profile, sinh test plan có cấu trúc (cases / regression / edge / error) |
| `/qa-generate` | Convert plan → Playwright TypeScript code |
| `/qa-sync` | Copy generated test vào app repo |
| `/qa-heal <spec> <error-log>` | Diagnose failed test + rewrite without weakening assertions |

Workflow chain rất hay — KHÔNG re-plan từ đầu khi test fail.

---

## Tại sao defer cho Ragbot?

### 1. Stack mismatch

| qa-playwright | Ragbot |
|---|---|
| Playwright (JS, browser) | Python/FastAPI/LangGraph backend |
| UI E2E test | API integration + RAG quality test |
| Browser harness | pytest |

Adapt cần rewrite 100% (chỉ giữ slash-chain pattern).

### 2. Existing manual coverage

Ragbot đã có:
- `tests/golden/test_golden_dataset.py` — pytest manual golden test
- `scripts/agent_d_loadtest.py` — 90Q load test
- `scripts/reclassify_loadtest.py` — Opus 6-verdict judge
- `scripts/eval_multi_hop.py` (Paper 14)
- `scripts/eval_vn_recall.py` (Paper 25)

→ ~80% golden test coverage đã có. Slash-chain chỉ wrapper convenience.

### 3. Effort vs ROI

- **Effort**: 1 ngày focused (4 slash command + per-bot golden config + heal logic)
- **ROI**: convenience cho dev iteration, không trực tiếp lift quality
- **Vs Stream D PROPER** (2-3 ngày, GA blocker fix): Stream D ưu tiên cao hơn

---

## Adapt plan (nếu sau này ship)

### `/golden-plan <bot_id>`

Read `bots.system_prompt` + `bots.custom_vocabulary` → sinh test plan:
- 5 happy-path queries
- 3 OOS queries
- 3 r60 traps (refuse-must-refuse)
- 2 multi-hop synthesis

Output: `tests/golden/<bot_id>/plan.md`

### `/golden-gen`

Generate pytest test cases từ plan. Per turn:
```python
def test_golden_<qid>():
    response = post_chat(bot_id, query)
    assert HALLU_marker not in response
    assert response.intent == expected_intent
    assert response.refuse_reason in {"oos", "no_context", "blocked"}
```

Output: `tests/golden/<bot_id>/test_golden.py`

### `/golden-run`

Run pytest cho bot specific:
```bash
.venv/bin/pytest tests/golden/<bot_id>/ -v
```

Output: pass/fail summary + per-turn detail.

### `/golden-heal <test_name> <error_msg>`

Debug failed test:
1. Read test code + error
2. Read bot sysprompt + corpus
3. Identify cause:
   - Sysprompt missing rule → suggest sysprompt edit
   - Corpus missing chunk → suggest doc add
   - Chunker bug → escalate Stream A
4. Output: 1-line root cause + suggested fix

Crucially: KHÔNG weaken assertion. Sysprompt/corpus fix preferred.

---

## Sacred contract preservation

Nếu ship sau này:
- Test code generated KHÔNG được inject text vào LLM runtime prompt — pure assertion code
- KHÔNG override LLM answer in test — only verify
- Bot owner data (sysprompt/corpus) only in test fixture/db, không hardcode trong test

---

## Revisit condition

Ship qa-golden khi:
- Stream D PROPER + Stream E shipped (GA unblock done)
- Multiple bot tenants live (>5 bot) → manual golden test bottleneck
- Owner request slash-command convenience cho golden test iteration

Until then, `tests/golden/` pytest manual đủ.

---

## Reference

- Source: https://github.com/hueanmy/qa-playwright-agent
- Existing pattern: `tests/golden/test_golden_dataset.py`, `scripts/eval_*`
- Memory: `reference_hueanmy_repos.md`
