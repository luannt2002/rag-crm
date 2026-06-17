# Plan: Fix tất cả "chưa-expert" + verify — 2026-06-15

> Chuẩn CLAUDE.md: evidence-driven (file:line), T1/T2/T3 declared, TDD (test fail trước),
> surgical, domain-neutral, no per-bot, HALLU=0 sacred, verify = test pass + đo số thật.
> Scope: UI bỏ khỏi đánh giá (sản phẩm = API). Streaming endpoint `/chat/stream` đã có → API-complete.

---

## PHẦN A — TỔNG HỢP DEBUG HÔM NAY (consolidation, evidence)

### A1. Đã FIX (code còn trong file, app chạy — git history đã reset)
| # | Fix | Evidence | Tier |
|---|---|---|---|
| 1 | Latency background-grounding lane | `dynamic_litellm_router.py:613` `::background` cap 4 · đo 26.8s→3.3s | T2 |
| 2 | UI workspace 404 (list_documents + /chat) | `find_by_3key_unique` (bot_repository:191, test_chat:180/2952) | T1 |
| 3 | Perf narrate gather | ingest 6-7min→57s | T2 |
| 4 | Model cleanup (haiku/full/gpt-5/gemma/qwen + orphan providers) | alembic 0216, constants repoint, seed clean | T3 |
| 5 | Source + git + journal cleanup, STATE/README rewrite | — | T3 |
| 6 | API key incident (scope mất → key mới) | sk-proj-wXsi7q, 42/42 lại | ops |

### A2. Verify QUALITY (load test 20260615b, key restored)
- **42/42 đúng thực chất, HALLU=0/6 sacred**, 0 lỗi pipeline. legal 10/10 · spa 18/18 · xe 14/14.
- Cost: chat ~$0.0064/câu, upload ~$0.013/ingest. $200 = dev/eval (full-model window + overnight matrix).

### A3. BUG đo được (confirmed)
| Bug | Evidence | Root cause | Tier |
|---|---|---|---|
| **Booking bare-slot refuse** | đo **5/5** ("đặt lịch"→"Tên Lan"→refuse) | `query_graph.py:6006` refuse-SC `not graded and not _is_chitchat` — KHÔNG check đang booking | T1 |

### A4. Verify SAI claim của AI ngoài (rule#0 — đều bác bỏ)
history=10 (≠5) · chunk có type (≠flat) · embedding sống · numeric sum đúng · coref WORK · **hybrid BM25+RRF + reranker + grounding-NLI + citation + streaming + embed-cache ĐỀU ĐÃ CÓ**. → Mọi "Expert RAG roadmap" của AI ngoài = xây lại đồ đã có. Bug thật duy nhất = booking (A3).

### A5. Chunking 3-chiều (AdapChunk doc vs Ekimetrics paper vs code)
Ragbot = AdapChunk structure-driven (rule-scorer thay LLM Selector, production) + Ekimetrics 5-metric (lexical proxy, OFF). Fidelity: SC cao, BI TB, ICC/DCC/RC thấp.

---

## PHẦN B — PLAN FIX "CHƯA-EXPERT" (5 task, ưu tiên T1→T3)

### TASK 1 — [T1-Smartness] Fix bug booking bare-slot refuse  🔴 ƯU TIÊN 1
**5-step bug investigation (CLAUDE.md mandate):**
1. **Bug**: luồng đặt lịch, turn = slot trống ("Tên Lan") → refuse "chưa có thông tin" (5/5). Slot vẫn capture (turn sau nhớ tên).
2. **Trực tiếp**: intent=factoid, 0 graded chunk → refuse-short-circuit bắn.
3. **Gốc rễ**: `query_graph.py:6006` `if _refuse_sc_enabled and not graded and not _is_chitchat:` — không biết bot đang giữa booking flow (`_action_enabled` + có captured slot).
4. **Expert solution (Tier-B generic)**: thêm `_booking_in_progress` vào điều kiện. Khi action_config bật + đang slot-fill → KHÔNG refuse, để LLM tiếp tục hỏi slot. Domain-neutral (mọi bot booking), no per-bot.
5. **Compliance**: sacred-10 (không inject/override) ✓ · zero-hardcode (flag từ pcfg) ✓ · 4-key ✓ · T1 declared ✓.

**Files**: `src/ragbot/orchestration/query_graph.py` (~6000-6024 refuse-SC block).
**TDD**: test reproduce bare-slot mid-booking → refuse (FAIL trước) → sửa → 0/5 refuse + refuse-bẫy vẫn đúng (out-of-corpus vẫn refuse).
**Verify**: load test booking 5/5 → 0/5; HALLU vẫn 0; refuse-trap legal q09/q10 vẫn refuse.

### TASK 2 — [T1-Smartness] Multi-turn coref test suite (quality guard)
**Vấn đề**: coref WORK (đo rồi) nhưng KHÔNG có test → regress âm thầm. CLAUDE.md: load test single-turn, thiếu multi-turn.
**Files**: `tests/scenarios/multiturn_coref_*.json` (mới) + harness multi-turn (same connect_id).
**Nội dung**: turn1 hỏi A → turn2 "còn B?" → turn3 "cái nào rẻ hơn" → assert đúng entity. Biến thể: "chi tiết", "thêm", "đầu tiên".
**Verify**: chạy suite, assert pass; thành CI guard.

### TASK 3 — [T2-CostPerf] Cost-persistence durable billing
**Vấn đề**: `request_logs` bị wipe (CASCADE xóa bot) → không audit cost/ngày (vụ $200). Code ghi cost CÓ (`finalize_request_log`) nhưng data mất.
**Expert solution**: bảng `billing_ledger` append-only, KHÔNG FK CASCADE tới bots (chỉ lưu record_bot_id text + tokens + cost + date). Ghi song song finalize_request_log.
**Files**: alembic mới (create table) + `request_log_repository.py` (dual-write) + query API.
**Verify**: chạy vài chat → query ledger thấy cost/bot/ngày; xóa bot → ledger CÒN.

### TASK 4 — [T1/T3] Ekimetrics ICC/DCC fidelity bằng zembed-1
**Vấn đề**: ICC/DCC dùng Jaccard proxy (fidelity thấp, đo khác paper). zembed-1 đã có → tính cosine THẬT, không cần Jina (no vendor mới).
**Expert solution**: trong `intrinsic_metrics.py`, thêm path tính ICC/DCC bằng embedding (zembed-1) khi `ekimetrics_embed_mode_enabled` (flag, default OFF). Giữ Jaccard làm fallback ($0).
**Scope**: chỉ bot legal (doc dài) — corpus CSV không cần (table_csv tối ưu).
**Verify**: A/B legal 30Q với ekimetrics ON (embed mode) vs OFF → đo Coverage/Faithfulness; chỉ default ON nếu lift>0, HALLU=0.

### TASK 5 — [T3-Refactor] RLS non-superuser DSN (defense-in-depth)
**Vấn đề**: 24 RLS policies tồn tại nhưng app connect superuser → bypass. Isolation chỉ app-filter `record_bot_id`.
**Expert solution**: DSN non-superuser + GUC `app.record_bot_id` set per-request → RLS enforce ở DB layer (backstop cho app-filter).
**Files**: bootstrap DSN + middleware set GUC + alembic grant.
**Verify**: query cross-bot với GUC sai → 0 rows (RLS chặn); regression: tất cả query hiện tại vẫn chạy.
**Lưu ý**: cẩn thận — đây là thay đổi hạ tầng, test kỹ regression; KHÔNG gấp (app-filter đủ an toàn).

---

## PHẦN C — THỨ TỰ + GATE
1. **TASK 1** (booking) — nhanh, an toàn, bug thật → làm trước, verify load test.
2. **TASK 2** (coref test) — guard, không đụng prod code.
3. **TASK 3** (cost-persist) — observability, alembic + dual-write.
4. **TASK 4** (ekimetrics fidelity) — A/B đo, chỉ ON nếu lift.
5. **TASK 5** (RLS) — cuối, cẩn thận regression, không gấp.

**Gate mỗi task**: TDD test fail→pass · grep self-verify zero-hardcode/domain-neutral · load test HALLU=0 giữ · đo số thật (rule#0). Mỗi task = 1 commit (git init lại nếu cần version control).

**Definition of Done (expert đủ 5 tiêu chí, UI out)**:
- Faithful: booking fix + coref guard → conversational correctness expert
- Cost: cost-persist → auditable
- Performance/Latency: streaming endpoint đã có (API), background lane fixed
- (RLS + ekimetrics = bonus hardening)
