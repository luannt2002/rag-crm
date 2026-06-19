# CLAUDE.md — Ragbot Project Instructions

> Rules for Claude Code on this project. Backup of expanded version: `docs/_archive/full_pre_trim_20260506/CLAUDE_full.md`.

---

## MINDSET nền — always-on guard

### CẤM ĐOÁN — TUYỆT ĐỐI (rule #0, đứng trên mọi rule khác)

**KHÔNG được phát biểu BẤT KỲ kết luận / nguyên nhân / dự đoán kết quả nào mà không có EVIDENCE thật.** Mọi câu khẳng định PHẢI kèm bằng chứng kiểm chứng được: log/trace, `psql` query result, test/load-test output, code `file:line`, DB row. Đặc biệt:

- **CẤM** nói "sẽ fix được", "kỳ vọng X%", "chắc là do Y", "có vẻ ổn" khi CHƯA chạy test/query đối chiếu. Muốn nói tác động → **chạy đo trước**, dẫn số thật.
- **CẤM** report "đã fix / work / pass" khi chưa có output kiểm chứng (pytest/load-test/curl/DB). Diagnosis xong ≠ fix xong; fix xong ≠ verified — phải TEST mới được tuyên bố.
- Khi chưa đủ evidence: nói rõ **"CHƯA verify — cần [chạy gì]"**, KHÔNG lấp bằng phỏng đoán.
- Phân biệt rạch ròi: **SỰ THẬT (có evidence)** vs **GIẢ THUYẾT (chưa kiểm chứng)** — luôn gắn nhãn.

Vi phạm rule này = nghiêm trọng nhất. Khớp với [[feedback_no_guess_must_measure]] + [[feedback_root_cause_expert_solution]].

### Karpathy 4 principles

1. **Think Before Coding** — KHÔNG đoán mò. State assumptions explicit. Unclear → hỏi. Multi-interpretation → present alternatives.
2. **Simplicity First** — Minimum code giải bài toán. Không feature ngoài request, không abstraction cho code dùng 1 lần, không error handling cho case không xảy ra.
3. **Surgical Changes** — Mỗi line thay đổi PHẢI trace ngược về user request. KHÔNG drive-by refactor. Match existing style.
4. **Goal-Driven Execution** — Strong success criteria > weak. Bug fix = "viết test reproduce → make it pass → verify no regression".

Trivial task (typo, đổi tên var) → bypass full rigor.

### Pocock skill mindsets

- `/grill-me` — non-trivial: phỏng vấn user trước khi code, đọc codebase trước khi hỏi câu trả lời được trong code.
- `/diagnose` — Phase 1 = build feedback loop (failing test / curl / replay trace). KHÔNG vibe-debug.
- `/tdd` — failing test FIRST, code SAU. Bug fix: test PHẢI fail trước khi sửa.
- `/zoom-out` — không hiểu code thì đọc calling sites + module boundaries trước khi propose change.
- ADR (`docs/adr/`) — chỉ ghi khi đủ 3 điều kiện: hard-to-reverse + surprising-without-context + real-trade-off. KHÔNG spam ADR.

### claude-mem patterns

- **3-layer search** — index → context → full. Fetch full only after filter.
- **PII redaction** TẠI HOOK LAYER (boundary), trước khi data tới worker/DB.
- **Graceful degradation** — transport error (timeout, 5xx) → degrade silent. Client bug (4xx, TypeError) → fail loud. Aux dependency KHÔNG được làm chết app chính.
- **Two-ID decoupling** — external invariant (e.g. `bot_id` slug) vs internal restart-rotated UUID (`record_bot_id`).

### Workflow non-trivial task

`/grill-me` → plan (`plans/YYMMDD-xxx/plan.md`) → `/tdd` failing test → minimum code → surgical diff → verify (test pass + grep self-verify zero-hardcode + naming + 4-key) → bug? `/diagnose` phase 1.

---

## MODEL TIER POLICY — Opus main · Sonnet subagent · Haiku BANNED · ship từng cái

**Data-driven** (replay 30 ngày 2026-04-07 → 2026-05-06, 13,420 calls / $11,072):
- 98.6% calls trong main session đụng hot-path (`src/ragbot/`, `alembic/`, sysprompt, `CLAUDE.md`) → MUST Opus
- Chỉ 1.4% calls là pure-research (savings nếu Sonnet = 0.4%, không đáng)
- **Savings thật** đến từ **delegate research sang Sonnet subagent**, KHÔNG phải downgrade main session

### Tier matrix

| Tier | Model | Áp dụng | Lý do |
|---|---|---|---|
| **T-A MAIN** | `claude-opus-4-7` | Parent session, mọi Edit/Write/commit/DML/sysprompt/schema, deepdive analysis cross-file (audit, trace flow, root-cause), bug fix, plan/ADR, math/HALLU adjudication, golden tests | Hot path = zero-bug guarantee. Deepdive cần reasoning sâu Sonnet hay miss. |
| **T-B SUBAGENT** | `claude-sonnet-4-6` | `Agent({subagent_type:"Explore", model:"sonnet"})` cho >3 grep / >3 file research; `Agent({subagent_type:"general-purpose", model:"sonnet"})` cho WebFetch/WebSearch summary | Subagent có context riêng, return summary cho parent → giảm cache parent. ~5× rẻ trong scope subagent. |
| **T-X BANNED** | `claude-haiku-4-5` | KHÔNG task nào. Lookup Haiku miss file silent → cascade vào parent decision. Risk > saving. | Quality không đảm bảo cho HALLU=0 sacred. |
| **T-X BANNED** | `claude-sonnet-*` cho **main session** | KHÔNG dùng Sonnet ở parent. Pollution risk: 1 hot-edit là cả session lệch pha. | Bug history regex/schema/template/override answer. |

### Quy tắc "ship từng cái" — the only way to actually save

Trước mỗi work-block, hỏi 3 câu:

1. **Cần research >3 grep / >3 file đọc?** → `Agent({subagent_type:"Explore", model:"sonnet"})`
2. **Cần WebFetch/WebSearch summary?** → `Agent({subagent_type:"general-purpose", model:"sonnet"})`
3. **Edit / commit / decide / deepdive analysis?** → main session (Opus, KHÔNG delegate)

**Cấm**: subagent với `model:"sonnet"` ghi code vào `src/ragbot/` — subagent là **pure read-only research** only. Subagent muốn ghi → bắt buộc `model:"opus"`.

### Deepdive = Opus, KHÔNG lệch pha

Audit cross-file, trace luồng data, root-cause bug, design review, security audit, sysprompt eval → **không delegate Sonnet**. Sonnet miss nuance ở task >5 file = parent ship sai. Chấp nhận chi phí Opus cho chất lượng deepdive.

### Decision tree

```
Task type?
├─ Edit/Write/commit/DML/sysprompt/schema     → Opus (main session)
├─ Deepdive analysis (audit, trace, root-cause, security)
│                                              → Opus (main session)
├─ Multi-query research (>3 grep/file Read)   → Agent(Explore, model="sonnet")
├─ WebFetch/WebSearch summarize URL/doc        → Agent(general, model="sonnet")
└─ 1-2 file Read + simple grep                 → inline main session (Opus, OK)
```

**Nguyên tắc "ship từng cái"**: 1 work-block = 1 mục đích rõ. Đừng gộp research + edit + commit vào 1 monolithic session 900 calls — split research ra subagent Sonnet, main session chỉ làm edit + decide.

### Per-task workflow

1. **Pick scope**: research / write / deepdive — pick MỘT mỗi work-block.
2. **Pick model**: T-A main Opus / T-B subagent Sonnet — đúng decision tree.
3. Score complexity 1–5 (track effort only).
4. Audit-as-go: 11-item Quality Gate REAL-TIME.
5. Output: `STATUS / SCOPE / MODEL / COMPLEXITY / QUALITY_GATE n/11 / FILES_CHANGED / TESTS`.

### Quality Gate (PHẢI pass trước commit)

1. Logic correct + edge cases (null, empty, concurrent).
2. Zero-hardcode (no magic numbers / model names / brand).
3. Strategy + DI pattern (Port + Registry preserved).
4. Tenant isolation — DB scoped `record_tenant_id`.
5. RBAC — `require_min_level(...)` correct numeric level.
6. **4-key bot identity** — never less.
7. Tests — real assertions, NOT `assert True`.
8. Domain-neutral — no brand / industry literal.
9. T1/T2/T3 tier declared (CORE MVP — bot smartness/cost/refactor).
10. **Application KHÔNG inject text/template/rule vào answer LLM, KHÔNG override answer**. `system_prompt` + bot config = single source of truth.
11. **Model tier match** — main session 100% Opus; Sonnet chỉ trong subagent (sidechain), KHÔNG ghi `src/ragbot/`/`alembic/`; Haiku = 0.

Verdict: APPROVED / APPROVED WITH FIX / REJECTED (rewrite).

### Application MINDSET — Bot owner owns everything

1. **Application KHÔNG inject text vào LLM prompt**. KHÔNG prepend platform/docs-only rules, context-tag instructions, citation hints. Bot owner's `system_prompt` is THE single source of truth.
   **Exception DUY NHẤT (governed, ADR-W1-S10)**: `SysPromptAssembler` được APPEND (không bao giờ prepend/chèn giữa) platform-default rules từ `language_packs[locale].sysprompt_default_rules` — với đủ 4 điều kiện: (a) text seed/update CHỈ qua alembic tracked, (b) domain-neutral, (c) per-bot opt-out qua `plan_limits.sysprompt_rules_disabled`, (d) owner xem được prompt lắp-ráp-cuối qua `GET /admin/bots/{id}/effective-prompt`. CẤM thêm key/cơ chế append mới tương tự không qua ADR. Invariants khóa bởi `tests/unit/test_sysprompt_assembler_pin.py` — vỡ bất kỳ pin nào = exception này hết hiệu lực, phải gỡ assembler khỏi answer-path. Ablation Phase 5 đo đóng góp rules; lift=0 → chuyển rules vào bot-creation template + gỡ append.
2. **Application KHÔNG override LLM answer**. KHÔNG `math_lockdown` regex check + replace, KHÔNG `_lang_pack.blocked_answer` / `oos_answer` fallback. LLM trả gì = user thấy nấy.
3. **Refusal text origin**: `bots.oos_answer_template` (DB column) hoặc per-rule `response_message` trong guardrail config. KHÔNG fallback i18n.py hardcoded text — empty string nếu bot không set.
4. **Math safety / hallu prevention** — bot owner viết rule trong `system_prompt`. LLM tự kiểm. Application KHÔNG regex-check + override.
5. **HALLU=0 sacred** + Anti-HALLU 4-loại-số (fabricate / misinterpret / extrapolate / conflate). Refusal traps must be honored; ground-truth numbers only.
6. Hardcoded constants chỉ cho pure technical (timeout, retry, batch). KHÔNG cho response template, refusal phrase, behavior toggle default — those go in `system_config` DB hoặc `bots` per-bot column.
7. **CẤM HOT-FIX qua psql UPDATE** vào `bots.system_prompt`, `bots.oos_answer_template`, `language_packs.content`, `system_config.value`, `ai_models.*`, `ai_providers.*`, `bot_model_bindings.*`, `bots.plan_limits`. Mọi thay đổi DB content state CHỈ qua: (a) alembic migration tracked trong git, HOẶC (b) admin UI có audit_log trail. Lý do: psql UPDATE thủ công → out-of-band drift, không reproduce được trên DB khác, không rollback được, gây bug khi clone DB (vd Wave M3.6-L2 sysprompt edit 2026-05-20 không có alembic → bug K1 aggregation "1tr499" 2026-05-25). Anti-pattern lịch sử: backup file `/tmp/wave_*_sysprompt_backup_*.txt` + psql script chạy 1 lần là **CẤM TUYỆT ĐỐI**.

### Quality target (zero-tolerance · measurable)

| Metric | Target | Verify |
|---|---|---|
| Haiku usage | **0** (sacred ban — quality risk) | `cost_audit.py model-mix` |
| Sonnet usage trên main session (parent) | **0** (pollution ban) | `cost_audit.py model-mix` (parent = `isSidechain:false`) |
| Sonnet usage trên subagent (sidechain) | 0–80% OK | `cost_audit.py model-mix` |
| Sonnet write-leak (Sonnet ghi `src/ragbot/` hoặc `alembic/`) | **0** | `cost_audit.py model-mix` |
| Opus usage trên main session | 100% | `cost_audit.py model-mix` |
| HALLU fabricate | **0** sacred | load-test gate |
| App-inject in LLM prompt | 0 | code review (Quality Gate #10) |
| App-override LLM answer | 0 | code review (Quality Gate #10) |

**Expected savings**: 10–20% trên ngày trung bình (research-shipping mix), 0% ngày pure-shipping (như 2026-04-29 V4 GA = 100% T-A1), 25–30% ngày research-heavy. Honest baseline: 30 ngày replay = 0.4% nếu KHÔNG đổi pattern; cần áp dụng "ship từng cái" + subagent delegate để thực sự tiết kiệm.

**⚠ Harness caveat (Stream X finding 2026-05-06)**: trên Opus-1M variant, `Agent({model:"sonnet"})` invocations KHÔNG ghi sidechain entry vào JSONL — `cost_audit.py model-mix` luôn report 100% Opus dù có spawn Sonnet subagent. Hai khả năng (không phân biệt được local): (a) harness inline subagent vào main session = cost = Opus thực; (b) harness honor model param nhưng log không capture = cost giảm thật nhưng không đo được. **Verify ground-truth**: Anthropic Console → Usage → filter date+API key → check có line `claude-sonnet-4-*` không. Until verified, tier policy = architectural intent, saving claim chưa verified.

**Rollback rule**: nếu (a) HALLU > 0 trong load test sau khi enable Sonnet subagent, hoặc (b) Sonnet write-leak > 0 (subagent ghi `src/ragbot/`), hoặc (c) deepdive subagent miss file (parent phải re-research) → revert về Opus 100%, post-mortem.

Verify từ Claude Code session logs:
```bash
python scripts/cost_audit.py today                       # cost + breakdown by model
python scripts/cost_audit.py model-mix --days 7          # Opus/Sonnet/Haiku ratio + write-leak
python scripts/cost_audit.py tier-replay --date YYYY-MM-DD  # what-if: T-A1 vs T-A2 mix + saving
python scripts/cost_audit.py sonnet-leak                 # (now: ONLY checks main session pollution)
python scripts/cost_audit.py weekly --days 7
python scripts/cost_audit.py advise                      # cache-hit / fragmentation
python scripts/cost_audit.py sessions --top 10
```

Đọc trực tiếp `~/.claude/projects/-var-www-html-ragbot/*.jsonl`, dedupe `(sessionId, message.id)`. Pattern adapted từ `hueanmy/claude-token-monitor`.

---

## CORE MVP PRIORITY ORDER — TUYỆT ĐỐI

3-tier ordering. KHÔNG đảo, KHÔNG bỏ qua tầng dưới khi tầng trên chưa đạt.

- **T1 — RAGBOT TRẢ LỜI THÔNG MINH (highest)**: faithfulness ≥ 0.9, grounded ≥ 0.8, không bịa, không refuse khi có docs match. "Cái này có làm bot trả lời chính xác hơn không?" — no → defer.
- **T2 — COST + PERF + UX (medium)**: token/turn, LLM call count, cache hit rate, P95, TTFT, refuse rate, citation clarity. Pipeline node không đóng góp quality + làm chậm → cắt hoặc gate per-bot.
- **T3 — DESIGN PATTERN, SOLID, DI, OPEN-CLOSED (lowest)**: refactor abstraction, port + adapter. KHÔNG ưu tiên hơn T1.

**Anti-patterns CẤM**: refactor abstraction khi bot vẫn refuse 89%; thêm Strategy registry mới khi LLM call/turn chưa profile; ship strategy stubs orphan trong khi retrieve trả 0 chunks; bump axis 7 (code) trong khi axis 1 (retrieval) đứng yên.

**Decision rule**: PR/commit/plan PHẢI tự đánh dấu tầng. Plan title prefix: `[T1-Smartness]` / `[T2-CostPerf]` / `[T3-Refactor]`. Plan không rõ tầng → reject.

---

## MANDATORY: /plan + Honest verification

- **/plan before non-trivial code**: any task >3 files or >1h → create `plans/YYMMDD-description/plan.md` (phases, files, checklist) → get user approval → implement phase by phase → update plan status as you go.
- **Honest code verification**: ALWAYS grep-verify claims. "I implemented X" → show actual code line. "Tests pass" → show `pytest` output. KHÔNG report STUB/FAKE features as REAL.

---

## BUG INVESTIGATION MANDATE — TUYỆT ĐỐI (TRIGGER ANY-BUG)

**Khi report bug, lỗi, regression, edge case** — em PHẢI tự đào tới gốc rễ TRƯỚC KHI propose fix. KHÔNG patch nhanh, KHÔNG nói nhảm, KHÔNG fix sai tầng.

### 5-step bug investigation protocol (BẮT BUỘC mọi bug non-trivial)

Mỗi bug có 5 mục PHẢI trả lời rõ ràng, evidence-driven, trước khi ship fix:

**1. Bug gì? — Reproduce concrete**
   - Câu hỏi/input cụ thể (verbatim)
   - Đáp án đúng (literal trong source-of-truth: corpus / spec / doc)
   - Đáp án bot/system trả về (verbatim từ log/test)
   - Diff: đúng vs sai khác nhau đâu (1-2 câu)
   - **Evidence required**: log JSON, test run, curl output, screenshot. KHÔNG bằng "em đoán".

**2. Nguyên nhân TRỰC TIẾP — Trace 1 layer ngược lên**
   - Layer fail: data / retrieval / orchestration / LLM / sysprompt / test design
   - Số liệu cụ thể: `chunks_used=0`, `top_score=0.04`, `CB OPEN`, `503`, `latency 60s retry`
   - Vị trí code/data fail: `file:line` HOẶC `chunk_id` HOẶC `alembic XXXX`
   - **Evidence required**: trace, EXPLAIN ANALYZE, journalctl error, DB query result

**3. Gốc rễ — Trace tới root cause (KHÔNG dừng layer 1)**
   - Continue trace cho tới khi tìm được nguyên nhân BẤT BIẾN (immutable cause)
   - Multiple layers: vấn đề L1 do L2 + L2 do L3. PHẢI liệt kê đầy đủ chain
   - Ví dụ chain: `bot refuse` ← `chunks=0 retrieve` ← `BM25 pipe tokenize fail` ← `websearch_to_tsquery default tokenizer + corpus có symbol notation`
   - **Evidence required**: code path + config + data sample đối chiếu

**4. Expert solution — Đúng tầng + best practice 2024-2025**
   - Fix tầng nào: phải khớp với layer gốc rễ (KHÔNG fix sai tầng — e.g. retrieval bug KHÔNG fix bằng sysprompt rule)
   - Pattern paper / SOTA: reference paper hoặc industry pattern (Anthropic CR, RAPTOR, ColBERT, multi-vector retrieval, semantic chunking, graceful degradation...)
   - 2-3 level: ngắn-hạn patch / trung-hạn architectural / dài-hạn governance
   - **Evidence required**: 1-2 sentence "tại sao expert solution này đúng cho case này"

**5. CLAUDE.md compliance check — Tự audit role**
   - Sacred-rule 11/11: từng item check ✅/❌ với reason
   - Quality Gate: pre-commit grep guard, RBAC, tenant isolation, zero-hardcode, domain-neutral, no app-inject text, no app-override answer, 4-key identity, no version-ref, no broad-except, T1/T2/T3 declared
   - Application MINDSET: bot owner self-service preserved? schema-driven KHÔNG hard-code per-bot?
   - Model tier policy: main Opus / Sonnet subagent / Haiku=0
   - **Evidence required**: list explicit từng rule check, KHÔNG "tổng quát đạt"

### Bug-investigation template (paste vào response)

```
## Bug investigation: <bug-name>

### 1. Bug gì
- Câu hỏi: <verbatim>
- Đáp án đúng: <literal corpus/spec>  
- Bot trả: <verbatim từ log>
- Diff: <1-2 câu>

### 2. Nguyên nhân trực tiếp
- Layer fail: <data/retrieval/llm/...>
- Số liệu: chunks_used=X, top_score=Y, ...
- Evidence: <log/trace line>

### 3. Gốc rễ (chain)
- L1 ← L2 ← L3 ...
- Immutable cause: <root>
- Evidence: <code path + config + data>

### 4. Expert solution
- Tầng fix: <khớp gốc rễ>
- Pattern: <SOTA paper / industry>
- Levels: short / mid / long-term

### 5. CLAUDE.md compliance
- Sacred rule X/11: ✅/❌ với reason mỗi item
- Quality Gate Y/Z items
- Application MINDSET check
- Model tier check
```

### Anti-pattern CẤM khi report bug

❌ **Patch sai tầng** — fix sysprompt rule khi retrieval miss; fix LLM model khi tokenizer bug
❌ **Frame sai để xoa dịu** — gọi "honest refuse" khi corpus có đáp án mà retrieval miss = bot REFUSE SAI, không honest
❌ **Nói nhàm** — "có vẻ ổn", "khá tốt", "tạm chấp nhận" KHÔNG kèm số liệu/evidence
❌ **Đoán nguyên nhân** — "có thể do X" KHÔNG kèm trace; "khả năng L2" KHÔNG kèm log
❌ **Fix nhanh trước, hiểu sau** — patch xong rồi mới đào root cause = lặp lại bug
❌ **Bỏ qua compliance check** — ship fix mà không tự audit CLAUDE.md = vi phạm tích lũy
❌ **Tổng quát "đạt sacred-rule"** mà không liệt kê từng item explicit

### Lessons learned từ 2026-06-03 session (case study)

Em đã ship 3 alembic (0154 / 0156 / 0158) cho bug `spa-07` mà KHÔNG đào gốc rễ:

- alembic 0154 — rule 21.B negative examples → **sysprompt layer**
- alembic 0156 — rule 21.C heading disambiguation → **sysprompt layer**  
- alembic 0158 — rule 23 exact name match → **sysprompt layer**

**Cả 3 đều SAI TẦNG**. Gốc rễ thật ở **retrieval layer** (corpus narrate English + cosine cross-lingual mismatch). Rule sysprompt chỉ dặn LLM cách chọn — nhưng chunk literal "700.000" KHÔNG vào top-K thì LLM có muốn quote cũng không thấy.

→ **Cost lesson**: 3 alembic × ~30 phút mỗi cái = ~1.5h wasted vì không đào tới gốc rễ. Ship Phase 0 `/diagnose` + `/zoom-out` TRƯỚC sẽ tiết kiệm 1.5h này.

Em đã frame sai khi gọi "bot honest refuse" — corpus CÓ đáp án nhưng retrieval miss, bot refuse là SAI (silent failure), không phải honest. **HALLU=0 ≠ Quality=100%**. Faithfulness 1.0 không đủ — phải care Coverage rate.

### Coverage rate — metric mới TUYỆT ĐỐI

Bên cạnh **Faithfulness** (bot không bịa), TRACK thêm **Coverage** = % câu hỏi mà corpus CÓ đáp án và bot trả lời ĐÚNG:

```
Coverage = answer_correct_when_corpus_has_answer / total_corpus_has_answer
```

- Faithfulness 1.0 + Coverage 1.0 = quality 100%
- Faithfulness 1.0 + Coverage 0.5 = bot không bịa nhưng MÙ 50% — vẫn FAIL UX

Mỗi load test PHẢI report cả 2 metric. Coverage <0.95 = blocker để ship.

**CẤM tuyên bố 1 flow/step "đạt ≥X/100" (hoặc "đã fix / work / pass") khi CHƯA có (a) debug-trace backward-verify cho step đó (chunk có ingest→retrieve→topK→prompt→answer đúng không) VÀ (b) load-test/eval output số thật.** Baseline TĨNH (chấm theo code-evidence) ≠ VERIFIED (chấm theo runtime số thật) — phải gắn nhãn rõ cái nào. Khớp rule #0 CẤM ĐOÁN.

---

## Domain-neutral rule — TUYỆT ĐỐI

**Code hệ thống KHÔNG support riêng bất kỳ khách hàng, ngành, hay lĩnh vực nào.**

- KHÔNG hardcode tên dịch vụ, bảng giá, tên thương hiệu, domain abbreviations.
- Domain data → `system_config` hoặc per-bot `custom_vocabulary`.
- Application = ragbot platform, KHÔNG phải 1 bot cụ thể.
- Golden test questions → file riêng per bot, KHÔNG trong code chung.

### Tenant-identifier / secret literals — CẤM HOÀN TOÀN trong file tracked

Forbidden in any tracked `.py / .md / .json / .yml / .yaml / .sh / .toml / .cfg / .ini`: brand hostnames / customer subdomains, credentials (password, API key, DSN, bearer), tenant-internal hostnames/IPs, tenant-specific DB usernames.

```python
# WRONG
BASE_URL = "https://backendsg.<brand>.vn:3004"
dsn = "postgresql://postgres:<brand>.vn123@10.0.1.160:5432/ragbot_v2_dev"

# RIGHT
import os
BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
dsn = os.getenv("DATABASE_URL")
if not dsn: raise RuntimeError("DATABASE_URL env var required")
```

Docs / plans / reports markdown: dùng placeholder generic (`<server-host>`, `<prior-project>`) thay vì tên thật. Historical reports với tên thật → redact tại chỗ.

**CẤM**: vague/disguised commit messages khi scrub, "quiet" scrub để tránh lộ, thêm literal mới cho tenant khác kể cả khi user yêu cầu.

Full scrub workflow + grep helpers → `docs/dev/SECRET_SCRUB_WORKFLOW.md`.

---

## No version-ref rule — TUYỆT ĐỐI

**Code KHÔNG được chứa version-ref** (`v1 / v2 / v3 / v4 / _legacy / _new / _old`) trong: column DB, file source, function/class/variable, constant, config knob, comment/docstring nhắc Sprint/Round/post-V/alembic-numbered, **URL path prefix** (`/v1/`, `/v2/`, `/api/v1/`), **Pydantic schema class name** (`FooV1`, `BarV2`), **router module name** (`xxx_v1.py`, `yyy_v2.py`).

Tên reflect **PURPOSE**, không reflect **VERSION**: `embedding`, `reranker`, `parser` — KHÔNG `embedding_v3`, `LegacyParser`, `migrate_to_v4.py`. Comment rule: WHY only — no temporal/version context.

```python
# WRONG
DEFAULT_EMBEDDING_COLUMN_V3: Final[str] = "embedding_v3"
def _pick_embedding_column(spec_dim): return "embedding_v3" if spec_dim == 1024 else "embedding"
# Sprint S9-P0: previously this hard-deleted; new default UPSERT post alembic 0042.

# RIGHT
DEFAULT_EMBEDDING_COLUMN: Final[str] = "embedding"  # dim lifted from spec at runtime
# UPSERT semantics: incoming source_url replaces matching docs; omitted leaves untouched.
```

### Schema/API versioning — header-based, NOT URL-based

REST best practice: versioning qua **header `X-Schema-Version`** thay vì URL prefix `/v1`. URL stable forever (1 canonical path), backward-compat khi schema evolve done qua header negotiation.

```python
# WRONG — URL contains version
POST /api/v1/documents/ingest
class IngestRequestV1(BaseModel): ...
src/ragbot/interfaces/http/routes/documents_v1.py

# RIGHT — URL purpose-named, version in header
POST /api/ragbot/documents/ingest
Headers: X-Schema-Version: 1
class IngestRequest(BaseModel):
    schema_version: int  # validated against SUPPORTED_SCHEMA_VERSIONS
src/ragbot/interfaces/http/routes/documents_ingest.py
```

Khi schema-version V2 lên: field `schema_version: 2` added, code branches on header value bên trong handler. **KHÔNG tạo route file mới** `documents_v2.py`.

Exception: alembic migration history files (immutable; DDL `RENAME COLUMN x TO y` legitimately references both names).

Pre-commit grep:
```bash
# Code-level version-ref
grep -rnE "(_v[0-9]|_legacy|EMBEDDING_COLUMN_(V[0-9]|LEGACY))" src/ scripts/ tests/ \
  | grep -v __pycache__ | grep -v "alembic/versions/"
grep -rnE "Sprint S?[0-9]+|V[0-9]+\.[0-9]+\.[0-9]+|Round V[A-Z]|post-V[0-9]+" \
  src/ scripts/ tests/ | grep -v __pycache__ | grep -v "alembic/versions/"
# URL + schema class version-ref
grep -rnE "/v[0-9]+/|\"v[0-9]+\"|\"/api/v[0-9]\"" src/ragbot/ | grep -v __pycache__
grep -rnE "class\s+\w+V[0-9]+\b|documents_v[0-9]|chat_v[0-9]" src/ragbot/ | grep -v __pycache__
# All four expect: 0 hits.
```

---

## Zero hardcode rule — TUYỆT ĐỐI

**KHÔNG MỘT CON SỐ NÀO inline trong code ngoài `shared/constants.py`.** ALL config from `system_config` DB (Redis-cached). ALL thresholds via `pipeline_config`. ALL defaults declared in `shared/constants.py` and imported. NO hardcoded role strings — use `shared/rbac.py` numeric levels (`require_min_level(60)` for admin). `0` / `0.0` literals OK (zero = disabled/none, not magic).

```python
# WRONG
def chunk(text, chunk_size=1024): ...
timeout = 30; max_tokens = 450

# RIGHT
from ragbot.shared.constants import DEFAULT_CHUNK_SIZE, DEFAULT_LLM_TIMEOUT_S
def chunk(text, chunk_size=DEFAULT_CHUNK_SIZE): ...
timeout = DEFAULT_LLM_TIMEOUT_S
```

**Whitelist allowed inline**: `0`/`0.0` (disabled/none), `1`/`1.0` (identity/init), `100` (percentage), indices (`items[0]`, `lines[:5]`), `range(10)` in tests, alembic migration files.

Common violation patterns + grep verification + sweep guidance → `docs/dev/ZERO_HARDCODE_DETAIL.md`.

---

## Strategy + Dependency Injection mindset — TUYỆT ĐỐI

**KHÔNG hard-code provider/implementation TRỰC TIẾP trong orchestrator/business logic.** Mọi swap-able thing (LLM, embedder, reranker, parser, tokenizer) PHẢI qua:

1. **Port** at `application/ports/<thing>_port.py` — Protocol/ABC contract.
2. **Strategy** at `infrastructure/<thing>/<provider>_<thing>.py` — one provider per file. Add provider = thêm 1 file, KHÔNG sửa orchestrator.
3. **Registry** at `infrastructure/<thing>/registry.py` — `_REGISTRY: dict[str, type[Port]]`. Caller: `build_<thing>(provider=cfg.<thing>_provider, **kwargs)`.
4. **Null Object** at `infrastructure/<thing>/null_<thing>.py` — default OFF, KHÔNG raise.
5. **DI container** at `bootstrap.py` — `providers.Singleton(build_<thing>, provider=cfg.<thing>_provider)`.
6. **Config-driven** — `<thing>_provider` key in `system_config` DB. Đổi 1 dòng = đổi behavior. KHÔNG redeploy.

```python
# WRONG — hard-code class trong orchestration
from infrastructure.reranker.litellm_reranker import LiteLLMReranker
reranker = LiteLLMReranker(model="cohere/rerank-v3.5")

# WRONG — if/elif provider trong business logic
if cfg.reranker_provider == "cohere": reranker = CohereReranker(...)
elif cfg.reranker_provider == "viranker": reranker = ViRankerLocal(...)

# RIGHT — DI + Port, provider là config string
from application.ports.reranker_port import RerankerPort
def rerank_node(state, *, reranker: RerankerPort):  # injected
    if isinstance(reranker, NullReranker): return  # bypass
    out = await reranker.rerank(query, chunks)
```

Apply to: LLM router, reranker, embedder, document parser, tokenizer, guardrails, prompt cache. Reasons: Open-Closed, test-friendly (inject Mock/Null), ops-friendly (config flip vs redeploy), domain-neutral compatible.

```bash
# Pre-commit checks (both expect 0 hits):
grep -rnE 'if.*provider.*==|provider == "(cohere|openai|anthropic|jina)"' \
  src/ragbot/orchestration/ src/ragbot/application/services/
grep -rnE "from ragbot\.infrastructure\.(reranker|embedding|llm)\." \
  src/ragbot/orchestration/   # only Port imports allowed there
```

---

## Broad-except sweep policy

**KHÔNG `except Exception:` ngoài 3 trường hợp được phép**:

1. **Top-level entrypoint** (worker, request handler, background driver) — MUST `exc_info=True` + `error_type=type(exc).__name__` + structured event + (a) reraise OR (b) explicit recovery.
2. **`finally` cleanup** — resource release.
3. **Background task wrapper** — log + continue / log + reraise.

Mọi case khác: narrow lib-specific types — `(SQLAlchemyError, RedisError, httpx.HTTPError, OSError, ValueError, TypeError)`. When broad-except is genuinely required, add `# noqa: BLE001 — <reason>` on the `except Exception:` line.

Narrow exception classes available in `src/ragbot/shared/errors.py`: `AuditEmitError`, `RetrievalError`, `EmbeddingError`, `IngestError`. Decreasing-only metric across sprints; regression guard at `tests/unit/test_narrow_exception_hierarchy.py::test_broad_except_count_decreases`.

---

## Architecture & key files

Stack: Python 3.12+ / FastAPI / LangGraph / pgvector / Redis Streams / structlog / Docker Compose. 2-tier cache: exact hash + semantic pgvector.

- `RAGBOT_MASTER.md` → table of contents.
- `docs/master/` → 13 sub-files (A-M).
- `shared/constants.py` → all defaults (SSoT).
- `shared/bot_limits.py` → bot-limit resolve chain (column > plan_limits > system_config > schema default).
- `plans/` → implementation plans.

## Test rules

New feature = new tests with real behavioral assertions. WEAK (NOT acceptable): `assert True`, `assert is not None`. STRONG: real assertions on values/behavior.

---

## Naming Convention — EXTERNAL vs INTERNAL keys

**EXTERNAL** keys (passed in from outside, NO prefix): `bot_id` (VARCHAR slug), `channel_type` (VARCHAR — `web` / `zalo`...), `connect_id` (VARCHAR external user ID), `message_id` (BIGINT upstream ID), `tenant_id` (INT upstream legacy bridge), `trace_id` (VARCHAR distributed trace).

**INTERNAL** keys (our DB UUID PKs, prefix `record_`): `record_bot_id`, `record_document_id`, `record_tenant_id`, `record_conversation_id`, `record_request_id`, `record_model_id`, `record_binding_id`, `record_provider_id` — each is the UUID PK of the matching table.

Rule: `record_` prefix = internal UUID FK. No prefix = external value passed in.

### IDENTITY RULE — TUYỆT ĐỐI 4-KEY REQUIRED

**Định danh 1 bot trên platform = 4 keys** — split between wire body and JWT bearer:

- **HTTP body 2-key**: `(bot_id: str, channel_type: str)` REQUIRED + `workspace_id: str | None` OPTIONAL.
- **JWT bearer claim**: `record_tenant_id: UUID` REQUIRED, lifted by `TenantContextMiddleware` onto `request.state.record_tenant_id`. Body NEVER carries tenant UUID (defence vs caller-spoofed claims).
- **Internal 4-key bot identity**: `(record_tenant_id: UUID, workspace_id: str, bot_id: str, channel_type: str)`.

**Workspace pass-through**: platform validates slug FORMAT only, NEVER manages workspace lifecycle. Slug `^[a-zA-Z0-9-]+$`, length 1-64. Missing/null body field falls back to `str(record_tenant_id)`. Invalid format → 422 `WORKSPACE_ID_INVALID`. Tenant-level/forensic rows write `WORKSPACE_SYSTEM_SLUG = "system"`.

**Schema** — 4 NOT-NULL columns on `bots`: `record_tenant_id` UUID FK, `workspace_id` VARCHAR(64), `bot_id` VARCHAR, `channel_type` VARCHAR. Unique constraint `uq_bots_record_tenant_workspace_bot_channel`. Resolve via `BotRegistryService.lookup(record_tenant_id, workspace_id, bot_id, channel_type)` — never optional.

**Why 4 REQUIRED**: two tenants × two workspaces can independently set `bot_id="support"` + `channel_type="web"` (slug is tenant-defined). Missing any key → cross-tenant / cross-workspace leak; nullable column → unique constraint can't enforce uniqueness.

**Layer-by-layer key usage**:

| Layer | Keys |
|---|---|
| HTTP request body | `(bot_id, channel_type)` REQUIRED + `workspace_id` OPTIONAL |
| JWT bearer claim | `record_tenant_id: UUID` REQUIRED |
| External resolve (`bots` lookup) | `(record_tenant_id, workspace_id, bot_id, channel_type)` — ALL 4 |
| Redis registry cache key | `ragbot:bot:{record_tenant_id}:{workspace_id}:{bot_id}:{channel_type}` |
| DB unique constraint | `uq_bots_record_tenant_workspace_bot_channel(...)` — 4 cột NOT NULL |
| Internal queries (pgvector, documents, conversations, semantic_cache) | `record_bot_id` ONLY |
| Composite index on data tables | `record_bot_id` (+ `workspace_id` for forensic scoping) |
| Tenant-level / forensic rows | `workspace_id = WORKSPACE_SYSTEM_SLUG ("system")` |

Once `record_bot_id` is resolved, internal queries use it alone (it is unique). The 4-key tuple is required only at the external resolve boundary.

Resolve flow diagram + full anti-pattern catalogue → `docs/dev/IDENTITY_RULE_DETAIL.md`.

---

## Async Performance Mindset

### Rule 1 — gather-first
When writing or reviewing async code, ALWAYS ask first: **"Are these awaits independent?"**
- YES → `asyncio.gather()` is mandatory, not optional.
- NO → sequential is correct — document WHY (data dep / ordering / transaction boundary).

### Rule 2 — Draw the DAG before refactoring
Before touching any async function, draw its dependency graph:

```
cfg_model ─┐
cfg_vocab  ├──► asyncio.gather() ──► build_prompt() ──► llm_call()
cfg_prompt ─┘
```

Never parallelize what has data dependency. The first arrow is parallel-safe; the chain after `build_prompt` must stay sequential.

### Rule 3 — Measure, don't guess
Every async optimization MUST have baseline + new timing. Shared helper: `src/ragbot/shared/perf.py::timer(label, log_threshold_ms=0.0)`. structlog event `perf_timer`.

### Rule 4 — Layered gather pattern
```python
# Layer 1: independent config reads
cfg_a, cfg_b, cfg_c = await asyncio.gather(get_a(), get_b(), get_c())

# Layer 2: processing (depends on layer 1)
result_x, result_y = await asyncio.gather(
    process_x(cfg_a, cfg_b),
    process_y(cfg_c),
)

# Layer 3: final (depends on layer 2)
final = await combine(result_x, result_y)
```

### Rule 5 — Error handling in gather
- Side-effects (cache write, audit, invalidation): `return_exceptions=True`, log failures
- Required outputs (resolver, repo create): let exceptions raise (fail-fast)

### Rule 6 — Bounded concurrency for loops
`asyncio.gather(*[fn(x) for x in items])` over a large list spikes DB/Redis pool. Bound with semaphore:

```python
sem = asyncio.Semaphore(DEFAULT_CONCURRENCY_N)
async def _bounded(x):
    async with sem:
        return await fn(x)
results = await asyncio.gather(*[_bounded(x) for x in items], return_exceptions=True)
```

### Rule 7 — Don't gather across transaction boundaries
SQLAlchemy `AsyncSession` is NOT safe for concurrent ops on same session. Two repo calls sharing session MUST stay sequential. Use separate session factories or `async with session.begin():` outside gather.

### Rule 8 — Don't gather audit / ordering-sensitive ops
- Audit log inline = compliance-required sync (fail-loud)
- Stream consume → process → ACK = exactly-once requires sequential ACK after process
- Outbox publish per-row holds row lock → sequential by exactly-once design

Reference scan + ship plan: `reports/ASYNC_BOTTLENECK_SCAN_20260518.md`.

---

## DAILY_REPORT — Quy trình báo cáo công việc

**Khi user prompt từ khoá `DAILY_REPORT`** (hoặc tương đương: "báo cáo công việc session/ngày", "daily report", "tổng kết phiên"), em PHẢI làm 4 bước theo thứ tự:

### Bước 1 — Tóm tắt công việc đã làm
1. Đọc `git log --since="<phiên trước>"` để liệt kê commits của phiên
2. Tóm tắt theo nhóm: bug fix / feature ship / docs / refactor / tests
3. Số liệu cụ thể: commit count, file changed, line added/removed, test count delta
4. Anchor cuối: `git log -1 --oneline`
5. Format ngắn gọn (≤ 30 dòng) — không lan man

### Bước 2 — Verify outdate state của 6 file truth-of-record
Check mtime + last commit của các file:
- `README.md` — stack + intro
- `RAGBOT_MASTER.md` (TOC) + `docs/master/01-A` → `16-P` (16 sub-files)
- `RAGBOT_STEP_PIPELINE.md` — pipeline canonical (step count flexible)
- `reports/RAG_Master_of_Masters_DeepDive_Report.md` — long-form report
- `STATE_SNAPSHOT.md` — always-updated current state (PRIMARY)
- `STATE_SNAPSHOT_HISTORY.md` — append-only historical sessions

File nào commit date < ngày của phiên hiện tại → MUST update.

### Bước 3 — Update file outdated (additive only)
Quy tắc:
- **STATE_SNAPSHOT.md** = always-updated PRIMARY. Update anchor commit, ship status, latest score, latest test count. **Prepend** new session ở đầu, **giữ legacy sections** bên dưới.
- **STATE_SNAPSHOT_HISTORY.md** = append-only. Move section cũ của STATE_SNAPSHOT > 30 ngày vào đây.
- **README.md** = chỉ update khi stack thay đổi (model version, framework, key dependency). Không spam mỗi session.
- **RAGBOT_MASTER.md** + `docs/master/*` = chỉ update khi architecture thay đổi (new port, new pipeline node, schema migration).
- **RAGBOT_STEP_PIPELINE.md** = chỉ update khi pipeline thêm/bớt step (tăng giảm bao nhiêu kệ, miễn là document chính xác step hiện tại).
- **RAG_Master_of_Masters_DeepDive_Report.md** = chỉ update khi có deep audit cross-file mới.

**Surgical rule**: phiên KHÔNG đụng stack/architecture/pipeline/schema → CHỈ update STATE_SNAPSHOT.md. Không drive-by refactor 6 file.

### Bước 4 — Commit + push
1. Run pre-commit grep guard (`scripts/audit_agent_diff.sh` nếu có)
2. Commit format:
   ```
   docs: DAILY_REPORT <DATE> — <one-line summary>

   <Multi-line summary từ Bước 1>

   Anchor: <commit_sha>
   Updated files: STATE_SNAPSHOT.md, [other if any]

   Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
   ```
3. `git push origin <current_branch>`
4. Báo cáo lại user: anchor commit + danh sách file updated + push status

### Bước 5 — Nếu phiên KHÔNG có git commit (chỉ research/analysis)
- Skip Bước 4 (không cần commit code)
- Vẫn update STATE_SNAPSHOT.md với section "Research-only session"
- Liệt kê output: reports/, plans/ docs đã viết
- Đề xuất next step

### Quy ước "phiên" (session)
- 1 phiên = từ lần user prompt đầu tới khi DAILY_REPORT triggered
- Boundary mặc định: `git log --since="24 hours ago"` nếu user không nói rõ
- User có thể override: "DAILY_REPORT từ commit X" hoặc "DAILY_REPORT 2 ngày qua"

### Anti-pattern KHÔNG được làm
- ❌ KHÔNG update 6 file cùng lúc mọi phiên (drive-by refactor)
- ❌ KHÔNG xóa nội dung cũ trong STATE_SNAPSHOT (append-only)
- ❌ KHÔNG commit nếu pre-commit guard fail
- ❌ KHÔNG inject text vào file mà không có evidence từ git log
- ❌ KHÔNG bịa số liệu test count (phải chạy pytest verify hoặc đọc CI log)

---

## ACTIVE PROGRAM — Ragbot Expert Build (multi-agent, chốt 2026-06-10)

Đang chạy chương trình 6-phase biến Ragbot thành RAG expert-grade multi-tenant. Khi làm việc trong luồng này:

- **`program/` = bộ nhớ chương trình** (KHÔNG phải context): `program/00-charter.md` (6-trục DoD + ràng buộc), `program/00-inventory.md`, `program/decisions/00-DECISION-REGISTER.md` (D1–D17 + Wave 6), `program/context/P1-*.md` (Phase 1 reports), `program/gaps/`, `program/waves/`, `program/eval/`. Phiên chết → phiên mới đọc `program/` là tiếp tục được. Mọi tri thức nằm trong file, KHÔNG giữ raw report trong context.
- **Gate discipline**: 5 gate đều cần user approve. Orchestrator KHÔNG tự trôi qua gate. Phase 1–3 = READ + REPORT ONLY; chỉ Phase 4 sửa code theo ADR đã duyệt. Mọi phát hiện = evidence `file:line`/`commit-hash`.
- **STRATEGIC STANCE — EVOLVE, KHÔNG REWRITE** (binding): strangler fig. Khung đã expert (Hexagonal/DDD · Port+Adapter+DI · 2 graph · config chain · 9 sacred). Vấn đề = "dây chưa nối hết", KHÔNG phải "khung sai". GIỮ khung + 4-key + sacred; WIRE/HARDEN (RLS, cache scope, worker GUC); HOÀN THIỆN AdapChunk B1–B4; MIGRATE schema (workspace slug→entity, backward-compat null→default ws); REWRITE cục bộ chỉ parser adapter (Kreuzberg flat-text → emit block list); SWAP engine qua ADR. Đập cái ✅ ĐÃ CHUẨN = lỗi nặng nhất.
- **Model override cho program này**: subagent dùng **Fable 5** (user chốt 2026-06-10, "đốt token lấy độ sâu"). Đây là override CÓ CHỦ ĐÍCH của model-tier policy ở trên — chỉ áp trong scope program Expert Build; ngoài program, tier matrix (Opus main / Sonnet subagent / Haiku ban) vẫn nguyên hiệu lực. Subagent Fable 5 vẫn READ-ONLY ở Phase 1–3 (chỉ ghi `program/*.md`, KHÔNG ghi `src/`).

## Startup rules

- Source `.env` before starting: `set -a && source .env && set +a`.
- All pydantic sub-settings have `env_file=".env"`.
- Plan → Code → Test → Push (mandatory workflow).
- Each feature has its own plan in `plans/`.
