# Phân tích chuyên sâu TẤT CẢ luồng — Ragbot (adversarially verified)

> **Ngày**: 2026-07-08 · **Phương pháp**: 2 workflow đa-agent (16 agent, ~1.26M token) — mỗi finding có **skeptic adversarial** re-mở file:line + re-chạy DB; corrections đã áp. Mọi claim gắn **SỰ THẬT** (verified) / **CHƯA VERIFY**. Đối chiếu code THẬT tại `/var/www/html/ragbot`, service live.
> Nguồn: workflow `wf_4775247a` (3 lỗi mở) + `wf_c3a25727` (truth-table pipeline) + trace inline.

---

## 0. ⚠️ CORRECTIONS — rule #0 đã sửa niềm tin sai (kể cả của tôi)

| Niềm tin trước | SỰ THẬT (verified) |
|---|---|
| spa "Diode Laser **của Hàn Quốc**" = world-knowledge HALLU | ❌ SAI — `content ILIKE '%Hàn Quốc%'` spa → **1 hit**, corpus có literal. **GROUNDED, không phải HALLU.** Vụ WK-sysprompt cả phiên chấm nhầm case grounded. |
| Brand-deny Rovelo = value-gate loại Rovelo price-NULL | ❌ REFUTED — `query_by_name_keyword('195/55R16', require_value=True)` → **3 rows GỒM Rovelo**; brand-aware offline **narrow ĐÚNG về Rovelo**. Cơ chế thật khác (xem §3.2). |
| Embedding = jina-v3 1024-dim (memory cũ) | ❌ Stale — thật là **zeroentropy zembed-1, dim 1280** (system_config `embedding_provider=zeroentropy`, 403/403 embedded). |
| Cần "build" CRM/cost-log (theo doc Cursor) | ❌ ĐÃ CÓ + đang ghi: **863 request_logs / 13.995 request_steps / 18.387 token_ledger** trong 24h. |

→ Bài học: eval-grade + trí nhớ đều phải verify sống. Adversarial-verify bắt được cả lỗi của người phân tích.

---

## 1. TRUTH-TABLE — INGEST (U0→U7)

Corpus DB-verified: **chinh-sach-xe** 403 chunk / 403 embedded / 242 DSI / 4 doc · **test-spa-id** 51 / 51 / 107 / 5 doc · dim 1280 · chunk_type table=631/text=275 · **parent_chunk children=0** (parent-child chưa chạy thật trên bot live).

| Stage | Status | Grade | Ghi chú |
|---|---|---|---|
| U0 identity 2-key + JWT tenant | LIVE | L2 | tenant JWT-only, 4-key sacred đúng (`documents.py:83-113`) |
| U0.5 bot-resolve 4-key | LIVE | L2 | `registry.lookup(4 key)` 404-miss (`documents.py:57-80`) |
| U1 validate + content_hash dedup | LIVE | L2 | sha256 + source_url idempotent (`ingest_core.py:420-453`) |
| **U2 parse (registry 7 parser)** | **PARTIAL** | L2 | ⚠️ **GAP**: registry path (docx/xlsx/sheets/pdf) trả row-dict **flat text**, `parsed_blocks=None`; block-native CHỈ ở OCR fallback (`document_worker.py:500`). Typed Block stream **mất** cho format registry → ingest re-analyze flat markdown. |
| U3 clean NFC/injection | LIVE | L2 | ⚠️ Tier-0 no-op nếu `_sanitizer` chưa DI-wire; legacy cleaner KHÔNG NFC (`ingest_stages.py:274-372`). CHƯA VERIFY sanitizer wired trong worker container. |
| U4 chunk (6-strategy + cross_check) | LIVE | **L2+cnt** | ↓ từ L3 — dựa chunk COUNT, chưa eval chunk-QUALITY (lossless-check observe-only) |
| U5 enrich contextual | **DEFAULT-OFF** | L1 | Tắt đúng — thay bằng `late_chunking=true` (0 LLM). Không dead-code. |
| U6 vn-segment underthesea | LIVE | L2 | async bounded, feed BM25 (`ingest_stages.py:954-1033`) |
| **U7 embed+store** | LIVE | **L3** | 403/403 embedded, dim 1280 zeroentropy, parent no-embed đúng, circuit-breaker |
| narrate-then-embed | **DEFAULT-OFF** | L1 | subsystem đầy đủ nhưng flag off |
| DSI stats build | LIVE | L3 | 242 (xe) / 107 (spa) rows |

**Ingest đánh giá**: khung LIVE + end-to-end verified (upload→chunk→embed→DSI). 2 điểm "đã-có-chưa-tốt": (a) **block-pipeline flat-text trên registry path** (mất cấu trúc block cho docx/xlsx/sheet), (b) U4 chunk-quality chưa eval-đo.

---

## 2. TRUTH-TABLE — QUERY + CROSS-CUTTING

### Query retrieval (trust cao)
- Stats-index route là path **chủ đạo**: `source=stats_index` fired **411/703**. Hybrid dense+BM25+RRF + rerank + CRAG grade LIVE.

### Query generate + guard (trust cao, 0 claim sai)
- **temp=0 enforced**; **Sacred #10 HOLDS** — app KHÔNG inject prompt text, KHÔNG author answer; chỉ (a) SysPromptAssembler append governed rules (wired cả 2 answer-path), (b) substitute **owner oos_answer_template** khi block/refuse. Không có hardcode-i18n refusal.
- **numeric-fidelity BLOCK** đang **BẬT** cho chinh-sach-xe (plan_limits DB-verified); brand-scope phrases seeded (observe).
- 38 guard unit test pass. + P0.1 empty-guard (default OFF) phiên này.

### 🔴 Cross-cutting — 1 GAP LỚN: RLS INERT
- **SỰ THẬT (proven live)**: DB build expert (24 bảng FORCE RLS + 24 policy + role `ragbot_app` NOBYPASSRLS đã provision) NHƯNG **runtime INERT** — app connect bằng **`postgres` superuser** qua `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` (`DATABASE_URL_APP` unset). Superuser **bypass toàn bộ RLS kể cả FORCE**. Session với `app.tenant_id` giả vẫn trả **tất cả rows**.
- → Tenant isolation hiện dựa **100% app-filter** (`record_bot_id`/`record_tenant_id` trong query), KHÔNG có DB-level enforcement. App-filter đang đúng (leak test pass) nhưng 1 query quên filter = leak. **Đây là gap bảo mật #1.**
- 4-key identity **L3** (unique constraint verified) · Observability **L3** (CRM tables ghi thật) · Cache L1 Redis + L2 semantic single-flight · Config resolve-chain LIVE.

---

## 3. 3 LỖI MỞ — root-cause 5-step (adversarially verified)

### 3.1 🔴 Non-numeric grounding gap → lỗi thật = xe "lốp xe tải" (STANDS)
- **Bug**: bảo hành corpus = "áp dụng lốp **xe du lịch (PCR)**" (`xe tải`=0 hit, `xe du lịch`=1, PCR=3). Bot nói "bao gồm lốp xe tải" = **extrapolate HALLU**.
- **Gốc rễ bất biến**: KHÔNG có deterministic gate cho **claim phi-số khẳng định**. `numeric_fidelity.py:86,102` chỉ soi SỐ; `brand_scope.py:32` chỉ soi **phủ định** brand; grounding-judge default **observe** (`_14:327`) + threshold **0.3** permissive + intent-gated.
- **Fix đúng tầng**: gate **claim-fidelity** mới (mirror numeric_fidelity) — Tier-1 deterministic (scope-affirmation token vs served-context, 0 LLM) + Tier-2 span-scoped NLI (per-bot block). **Observe-first**, đo FP. SOTA: RAGAS Faithfulness / FactScore / SelfCheckGPT / AIS. **KHÔNG sysprompt** (đã chứng minh vô hiệu 3/3).
- **CHƯA VERIFY**: verbatim answer câu-4 chưa pull từ eval-log lần này (chỉ corpus facts verified).

### 3.2 🟡 Brand-deny Rovelo → symptom thật, cơ chế 1 phần (value-gate REFUTED)
- **Bug**: "Rovelo 195/55R16 giá?" → "chưa phân phối Rovelo" (served LANDSPIDER). Rovelo CÓ trong DSI (price NULL). Đáp án đúng = "Rovelo 195/55R16 chưa có giá".
- **Đã loại**: value-gate KHÔNG loại Rovelo (query trả 3 rows gồm Rovelo); brand-aware offline **narrow ĐÚNG về Rovelo (keep=[2])**.
- **Cơ chế còn hở (CHƯA chốt line)**: stats path offline ra Rovelo nhưng live serve LANDSPIDER → nghi (a) `_parse_code_query` thắng dispatch → kw='195/55R16' (mất brand), (b) brand-aware dùng `state.original_query` — nếu bị condense mất "Rovelo" thì không narrow, hoặc (c) race-arm vector thắng. **Cần 1 live trace** original_query + entities tại bước brand-aware.
- **Fix interim (an toàn, ship được ngay)**: `brand_scope_gate_action=block` cho xe (B1 đã ship, gate detect denial + count(Rovelo)=51>0) → substitute oos_template thay vì denial sai. Root fix theo sau khi chốt cơ chế.

### 3.3 🔴 Perf slow-path (STANDS, mọi file:line verified) → giải thích timeout >120s
- **Số thật (request_steps)**: descriptive 41.5s (generate **30s TIMEOUT** out=27tok), superlative 48s (rewrite **29.8s timeout** + multi_query **2×**), heavy 40-136s, **factoid cũng ~20s**.
- **2 gốc rễ nhân nhau**: (a) **INFRA** — endpoint innocom (`ai.innocom.co/v1`) 3-30s/call, chạm cap 30s, latency **không tương quan output-token** (endpoint chậm, không phải sinh nhiều); (b) **ARCH** — heavy path 3-5 LLM call **tuần tự** + rewrite_retry chạy multi_query **2×** + `retry_policy.py:35` retry **×3** → slow node ×3 = tới 90s.
- **Fix**: infra (health-check/thay endpoint innocom) + orchestration (per-turn call-budget, parallel, tắt retry-loop cho heavy nếu không cần). T2/perf.

---

## 4. Ưu tiên hành động (đã hiệu chỉnh theo verified)

| # | Việc | Tầng | Risk | Trạng thái |
|---|---|---|---|---|
| 1 | **brand-scope BLOCK** cho xe (interim brand-deny) | config | 🟢 thấp | B1 đã ship, chỉ alembic bật + đo FP |
| 2 | **Perf**: health-check endpoint innocom + call-budget heavy path | infra+arch | 🟡 | ảnh hưởng UX rõ (timeout) |
| 3 | **claim-fidelity gate observe-mode** (non-numeric HALLU) | answer-guard | 🟡 (observe safe) | build mới, mirror numeric_fidelity |
| 4 | **RLS DSN flip** → ragbot_app (gap bảo mật #1) | infra | 🔴 (cần test kỹ) | role đã provision, chỉ đổi DSN + verify |
| 5 | **block-pipeline registry path** emit typed Block (docx/xlsx/sheet) | ingest | 🟡 | đã-có-chưa-tốt |
| 6 | Trace chốt cơ chế brand-deny (root fix sau interim) | retrieval | 🟡 | cần 1 live trace |

---

## 5. Điều CHƯA VERIFY (honest)
- Verbatim answer câu-4 chưa pull từ eval-log phiên này (chỉ corpus facts).
- Cơ chế brand-deny chưa chốt tới line cuối (interim block không phụ thuộc điều này).
- `_sanitizer` (U3 Tier-0 clean) có DI-wired trong worker container không.
- U4 chunk-quality + U6 segment-count chưa eval-đo (grade L2, không L3).
- "thang máy" (spa câu-9) = 0 hit corpus → fabrication THẬT nhưng chưa đo rate.

---

## 6. Chốt tổng
**Khung pipeline EXPERT + LIVE** — ingest end-to-end verified, query 12-stage chạy, sacred #10 giữ, observability L3, 4-key L3. Vấn đề = **"dây chưa nối hết" đúng như strategic stance**, KHÔNG phải khung sai:
- 🔴 **RLS inert** (bảo mật, app-filter gánh) · 🔴 **perf endpoint chậm** (UX) · 🟡 **non-numeric HALLU** (1 gate còn thiếu) · 🟡 **brand-deny** (interim block) · 🟡 **block-pipeline flat-text registry**.
Chất lượng answer core (price/factoid/trap/arrival/promo/process) ~75-83% đúng, lỗi còn lại đã định vị + có đường fix từng cái.

*Mọi claim dẫn file:line/DB-query đã verify hoặc gắn CHƯA VERIFY. Anti-hallucination: skeptic re-mở mọi file:line; corrections đã áp.*
