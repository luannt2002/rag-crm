# P2-GAPMAP — Tổng hợp 7 audit Phase 2 → đề bài Phase 3

> Synthesis của `gaps/P2-A…G.md` (7 auditor, đều evidence `file:line`/commit/psql/EXPLAIN/link).
> Date 2026-06-10 · branch `fix-260604-action-slotmachine-dead-key` · HEAD `7dd1f84` · alembic head `0195`.
> STANCE = **EVOLVE, không rewrite**. Nhãn: ✅ ĐÃ CHUẨN · 🕰 LỖI THỜI vs SOTA-2026 · ↔️ LỆCH (doc≠code≠plan≠DB) · 🐛 SAI/HOLE.
> Mục đích: 1 bản đồ duy nhất để DỪNG ở **GATE 2**. Phase 3 mới ra ADR + quyết "build nốt vs thay engine".
> **Đây là TỔNG HỢP — không thêm phát hiện mới; mọi item trace ngược về 1 trong 7 file P2.**

---

## 1. BẢNG TỔNG — domain × nhãn

| Domain (auditor) | ✅ | 🕰 | ↔️ | 🐛 | Tổng | Headline 1 dòng |
|---|---|---|---|---|---|---|
| **A** RAG query-graph orchestration | 18 | 2 | 7 | 3¹ | 30 | Khung 21-node/33-step CHUẨN + sacred-clean; nợ = tie-order nondeterminism (OPEN post-revert) + 7 ↔️ doc/DB drift |
| **B** Chunking & AdapChunk | 11 | 5 | 5 | 4 | 25 | Narrate-then-embed LIVE (211/211 DB-verified); nợ = `_is_table_line` misclassify ~163 prose chunk + Block-feed flatten giết L2/L6 |
| **C** Multi-tenancy & security | 9 | 2 | 2 | 6 | 19 | 4-key identity SOTA-grade (đừng đụng); **RLS 100% INERT runtime (psql-proven)** = #1 leak risk |
| **D** Retrieval & ranking | 6 | 7 | 1 | 2 | 16 | Cliff/safety-net/CRAG-mode = vết-sẹo-production quý; nợ = tie-break content-aware + true-BM25 + ef_search hardcode |
| **E** LLM-ops & anti-hallu | 7 | 2 | 2 | 3 | 14 | Sacred no-inject/no-override CLEAN + 9 lock-test; nợ = grounding ≤5-câu cap + 3 temp-0 bypass + silent-degrade vô hình |
| **F** Data / cache / event | 7 | 2 | 2 | 9² | 20 | Version-bust passive CHUẨN; nợ = **exactly-once → at-most-once (message DROPPED)** + orphan family (cache/chunk/reaper) |
| **G** Platform / config / cost | 5 | 2 | 1 | 4 | 12 | 5-tier resolve + 33-step cost-capture CHUẨN; nợ = config-drift 2 path + dead validate guard + ingest cost = $0 |
| **TỔNG** | **63** | **22** | **20** | **31** | **136** | Khung expert; vấn đề = "dây chưa nối hết" + recalibrate, KHÔNG phải "khung sai" |

¹ P2-A: 1 🐛 nặng (tie-order) + 2 🐛 nhẹ (graph_retrieve transport divergence, zero-hardcode `_pcfg` defaults).
² P2-F: 9 🐛 trong đó 3 low-sev (understand_query bust, corpus_version dead `invalidate()`, dead `build_response_cache_key`).

**Đọc bảng**: ✅63 (46%) = khung đã chuẩn, chứng cứ STANCE EVOLVE đúng. 🐛31 (23%) tập trung ở **C (security) + F (data/event)** = tầng "wire/harden" chưa nối. 🕰22 (16%) tập trung ở **D (retrieval) + B (chunking)** = engine-swap-qua-ADR. ↔️20 (15%) = doc/DB/plan drift, fix rẻ (sửa doc + alembic DELETE row mồ côi).

---

## 2. TOP 15 ISSUE — theo (tác động 6 trục × effort)

> Trục: **ĐÚNG** (HALLU=0/faithfulness) · **ĐỦ** (recall/coverage) · **AN TOÀN** (RLS/0 cross-tenant) · **NHANH** (p95) · **RẺ** (cost/query) · **KIỂM SOÁT** (log+lý do).
> Effort: S (<2h) · M (½–1 ngày) · L (>1 ngày). Sắp theo (severity × leverage), không phải effort.

| # | Issue | Kéo trục | Nhãn | Effort | Evidence | → Dn |
|---|---|---|---|---|---|---|
| 1 | **RLS 100% inert runtime** — app connect `postgres` (rolsuper=t, rolbypassrls=t); hook 0-callsite; `app.workspace_id` GUC never SET. Bypass đo thật: bogus tenant → 21 bot rows | **AN TOÀN** (P0) + KIỂM SOÁT | 🐛 | M | P2-C RLS-1/2/3 (psql 2026-06-10); P2-G #12 | **D3** + D2 |
| 2 | **Exactly-once = at-most-once** — dedup `SET NX` TRƯỚC handler; handler raise → XCLAIM redeliver → dedup-skip + XACK = **message DROPPED** | **ĐÚNG/ĐỦ** (data loss) + KIỂM SOÁT | 🐛 | M | P2-F H-EO (`redis_streams_bus.py:198-215`) | D8-adj (**new**) |
| 3 | **`_is_table_line` misclassify VN legal prose** — ~163/211 "TABLE" chunk là văn xuôi (chỉ 9 có pipe) → LLM-narrate-rồi-embed oan, override âm thầm `raw_only` | **ĐỦ** (coverage) + RẺ (LLM hop oan) | 🐛 | S | P2-B 🐛-B (`chunking.py:253-256`, DB 211/211) | **D1** |
| 4 | **Bot/tenant soft-delete purge nothing + semantic_cache no FK** — orphan chunk/cache/corpus_version vô hạn; FK RESTRICT chặn hard-delete | **AN TOÀN** + KIỂM SOÁT | 🐛 | M | P2-F H-BOT/H-TEN/H-FK (`bot_management_service.py:242-269`, alembic 0014) | **D4** |
| 5 | **Tie-order nondeterminism OPEN** post-revert `2f5ed41` — không có stable tie key; UUID-key đã chứng minh −13pp legal; variance gốc ở LLM temp-0 upstream | **ĐÚNG** (flip) + KIỂM SOÁT | 🐛 | M | P2-A 🐛-1 + P2-D §3 (`7dd1f84`) | **D5** |
| 6 | **Grounding ≤5-câu cap** — `sentences[:5]` cắt cứng, câu 6+ KHÔNG vào judge; answer aggregation/`sub_answers` dài → tail-claim unverified | **ĐÚNG** (HALLU observe) + KIỂM SOÁT | 🐛 | S→M³ | P2-E 🐛-1 (`local_guardrail.py:413,445`) | **D7** |
| 7 | **Stuck-doc reaper mù `active`+0-chunk** — ingest INSERT `state='active'` trước embed; reaper chỉ quét `state='DRAFT'` → worker crash = active doc 0 chunk vô hình | **ĐỦ** (coverage) + KIỂM SOÁT | 🐛 | S | P2-F H-REAP (`document_recovery_worker.py:155`) | D4-adj / **D17** |
| 8 | **Ingest LLM spend = $0 unledgered** — narrate/enrich/metadata hardcode `cost_usd=0.0`, không parent request_logs; + 0 read-query sum `cost_usd` per-step | **RẺ** (cost mù) + KIỂM SOÁT | 🐛 | M | P2-G §3 (`document_service.py:3881`) | **D9** / Q21 |
| 9 | **temp-0 coverage gap** — `multi_query`/`grounding`/`decompose` gọi `llm.complete` KHÔNG truyền `temperature=` → phụ thuộc cột DB; trong DETERMINISTIC set nhưng bypass override `:1274` | **ĐÚNG** (determinism) | 🐛 | S | P2-E 🐛-2 (`query_graph.py:2797/1041/7858`) | D5-adj |
| 10 | **Config drift init_system_config ≠ alembic 0020** — `max_tokens` 1024 vs 450 (2.3×), `rerank_top_n` 10 vs 5 (2×) → fresh-DB vs migrated-DB khác answer budget | ĐÚNG/RẺ + KIỂM SOÁT | 🐛 | S | P2-G DRIFT-1 (`init_system_config.py:30/38`) | **D9** |
| 11 | **Block stream flattened (Block→str)** — `document_worker.py:295` join làm phẳng → L2 atomic-protect + L6 `smart_chunk_atomic` + context-buffer 0 input (root của dead engine AdapChunk) | **ĐỦ** (chunk ceiling) | 🐛/↔️ | L | P2-B 🐛-A (`:295`, `document_service.py:1920`) | **D1/D14** |
| 12 | **Grounding silent-degrade vô hình** — judge timeout/empty/except → `None` = "grounded"; KHÔNG có `grounding_degraded_total` metric phân biệt PASS thật vs judge chết | **ĐÚNG** + KIỂM SOÁT | 🐛 | S | P2-E 🐛-3 (`local_guardrail.py:505-555`) | **D7** |
| 13 | **Ingest fairness = 0** — 1 global stream + `Semaphore(5)` chia chung mọi tenant → noisy-neighbor ingest | **NHANH** (p95 ingest) | 🐛/🕰 | M | P2-C + P2-F (`redis_streams_bus.py:153-170`) | **D8** |
| 14 | **math_lockdown dead DB rows + doc 04-D drift** — `math_lockdown_enabled=true` có 0 code reader; doc 04-D vẽ "math lockdown" trong GENERATE → operator tưởng sacred violation ON | KIỂM SOÁT (đọc sai sacred posture) | ↔️ | S | P2-A ↔️ + P2-E ↔️ (`cad52dc`/`6e9041d`) | D6-adj |
| 15 | **validate_constants.sh dead guard** — trỏ `shared/constants.py` đã xóa (split `1446fef`) → exit 0 im lặng, version-ref/temporal check KHÔNG chạy | KIỂM SOÁT (governance no-op) | 🐛 | S | P2-G DRIFT-2 (`validate_constants.sh:18`) | D9-adj |

³ #6: counter-only (degraded metric) = S; bỏ cap → claim-level NLI judge qua Port = M (đụng 🕰-A, cần ADR).

**Bên-rìa-TOP-15 (ghi nhận, không drop):** proposition connector-deletion (P2-B 🐛-D, S, D15) · narrate × raw_only override (P2-B 🐛-C, S, D1) · graph_retrieve worker-vs-stream transport divergence (P2-A 🐛-2, S, latent-mine) · embedding-model-change 0-guard (P2-F/P2-A, D10) · HNSW post-filter recall-cliff **scale caveat** (P2-D §4.5 — chưa reproduce, corpus 560 rows quá nhỏ, GIẢ THUYẾT) · `rerank_input_pool` ≠ `rerank_top_n` two-stage gap (P2-D mục 16, D-retrieval).

**Phân bố TOP-15 theo trục** (1 issue kéo nhiều trục): AN TOÀN ×3 (1,4,13) · ĐÚNG ×7 (2,5,6,9,12 + 3,8 phụ) · ĐỦ ×5 (2,3,7,11 + 6 phụ) · RẺ ×3 (3,8,10) · NHANH ×1 (13) · KIỂM SOÁT ×11 (gần như mọi issue). → **KIỂM SOÁT** là trục yếu nhất hệ thống (đo/log/governance), **AN TOÀN** có 1 P0 đơn lẻ nhưng nặng nhất.

---

## 3. AdapChunk mindset — VERDICT SƠ BỘ (chốt ở Phase 3, KHÔNG quyết ở đây)

> Charter §AdapChunk: GIỮ mindset (structure-aware · atomic+context-binding · narrate-then-embed · rule cross-check · eval-by-type) — được phép THAY ENGINE qua ADR. Đây là verdict sơ bộ từ P2-B + P2-D, **đề bài cho Phase 3 quyết**.

### 3a. Mindset = CHUẨN (làm xương sống, KHÔNG đụng)
- **Narrate-then-embed LIVE** (P2-B §1, DB-verified 560/560 meta · 211/211 TABLE narrated≠raw): embed narration, answer cite raw `content`, degrade 3 tầng → raw. Ngay cả 🐛-B misclassify cũng KHÔNG đẩy chữ bịa vào answer (chỉ vào vector). **Settle mâu thuẫn P1-SYNTHESIS §5: P1-E đúng, P1-B "DEAD" sai.**
- **Deterministic rule selector** (no per-doc LLM judge) — `select_strategy:663-792`, vindicated bởi e86c0f6 legal-LLM-branch revert. Giữ rule-based.
- **VN legal hierarchy (HDT) + L5 cross-check** flag-ON, DB-verified 90 HDT chunk có citation path. Giữ.
- **table_csv row-as-chunk** production-grade (48 TABLE label đúng). Giữ.

### 3b. Tầng đáng BUILD NỐT (HOÀN THIỆN dead-code đã thiết kế)
- **[Cao nhất] Block feed end-to-end** — un-flatten `document_worker.py:295` → `ingest(blocks=)` → `smart_chunk_atomic` survivor path. Một wire này: (a) cho L2 atomic-protect + context-buffer + L6 input thật; (b) thay regex-on-flattened-text (đã chứng minh hỏng qua 🐛-B) bằng `Block.type` truth từ parser; (c) charter-blessed "REWRITE cục bộ 1 module = parser adapter Kreuzberg flat→Block list". Effort L. → **D1/D14**.
- **Block-native atomic survive consolidation** — giữ `smart_chunk_atomic`, bỏ `_smart_chunk_with_atomic_protect` elif-ladder trùng (đã drift `:2488-2499` vs `:2628-2637`). `_split_into_blocks_with_atomic` giữ CHỈ làm fallback cho block-less source (direct-text API). → **D1**.
- **Large-table rule** (P2-B Q15): "bảng atomic ở mức HÀNG không phải ký tự; header đi theo mọi fragment; chỉ FORMULA/IMAGE atomic-tuyệt-đối". Hợp nhất table_csv + atomic-protect vào 1 helper `_emit_table_rows`. → **D16**.

### 3c. Tầng đáng FIX-TẠI-CHỖ (rule patch, độc lập rewire — ship được NGAY Phase 4)
- `_is_table_line` comma rule (🐛-B/T1) — yêu cầu ≥2 dòng CSV liên tiếp cùng field-count, hoặc loại VN điểm/khoản `^[a-zđ]\)\s`/kết `;`. **Độc lập Block-feed, hết mis-narration prose hôm nay.** → D1.
- narrate × raw_only ordering (🐛-C) — narrate CHỈ trên TABLE/FORMULA/IMAGE thật, assert anchor survive. → D1.
- proposition connector retention (🐛-D) — giữ token `nếu/khi/vì`, store `source_sentence` metadata. → D15.

### 3d. Tầng đáng THAY ENGINE / XÓA (qua ADR + ablation Phase 5)
- **SEMANTIC chunking per-sentence cosine → DELETE** `_chunk_semantic_embed` + SentenceSimilarityPort scaffold. SOTA 2026 (arXiv 2410.13070 NAACL, 2606.00881): gain không nhất quán, recursive thường tốt hơn trên doc thật — chính cost zembed-1-per-sentence là cái paper lên án. Live lexical `semantic` (93 chunk) free nhưng kế thừa quality non-result → demote/fold recursive. → **ablation Phase 5** (D1 known-limitation).
- **Per-section granularity** (D14) thay per-document — HiChunk (arXiv 2509.11552) +18-25% retrieval. Route qua Block feed.
- **Proposition LLM-upgrade** (nếu có) BẮT BUỘC entailment gate per DnDScore (arXiv 2412.13175) chống fabrication-at-ingest. Live regex-only = an toàn, đừng "upgrade" vô gate. → **D15**.
- **Ekimetrics selector** — WIRE-FOR-ABLATION có **kill-date**: rule-based, free runtime, là consumer DUY NHẤT cho dead L3 DocumentProfile entity (1 wire giải 2 dead layer). A/B 13 GRADED_* corpora Phase 5 → không lift Coverage/Correctness thì XÓA (~600 dòng). → D1/D14.

### 3e. Verdict 1 dòng (sơ bộ)
**Mindset chuẩn 100% → giữ xương sống. Engine chuẩn ~½: 1 tầng build-nốt (Block feed, leverage cao nhất) · 3 fix-tại-chỗ rule · 2 thay/xóa qua ADR+ablation.** Khẳng định nguyên tắc charter "giữ mindset, thay engine có ADR" — **KHÔNG rewrite chunking.py**. Quyết cuối + thứ tự wave ở **Phase 3**.

---

## 4. MAP ISSUE → DECISION (D1–D17 + Wave 6 + new)

| Dn | Decision (đề bài) | Wave | Issue P2 map vào (evidence) |
|---|---|---|---|
| **D1** | AdapChunk build-nốt vs thay engine | W3 | TOP#3 (`_is_table_line`), TOP#11 (Block feed), 🐛-C narrate×raw_only, semantic-delete, Ekimetrics-wire (P2-B §3d/§7) |
| **D2** | Workspace slug→entity + RBAC ws-scope + quota cascade | W2 | TOP#1 (workspace GUC never set, P2-C RLS-3); P2-C Q6/Q7/Q8 (no `workspaces` table, RBAC global-per-tenant, quota tenant-only) |
| **D3** | RLS end-to-end (wire hook + leak test CI) | **W1** | **TOP#1** (P2-C RLS-1 superuser DSN + RLS-2 hook 0-callsite; leak-test phải assert `rolbypassrls=false`) |
| **D4** | Semantic cache invalidation + bot-delete purge + BotLifecycleService | **W1** | TOP#4 (H-BOT/H-TEN/H-FK orphan family), TOP#7 (H-REAP stuck-doc); P2-F §5 Q23/Q24 design |
| **D5** | Retrieval determinism (rerank tie-break) | W4 | TOP#5 (tie-order, content-aware key `score→bm25_rank→chunk_index` KHÔNG uuid), TOP#9 (temp-0 bypass upstream variance); P2-D §3 experiment |
| **D6** | Numeric aggregation (disclaimer vs extract-then-compute, no sacred #5 vi phạm) | W4 | P2-E Q20 (tool-use-TRƯỚC-answer = ĐƯỢC; post-hoc replace = CẤM); TOP#14 math_lockdown dead-row cleanup |
| **D7** | Grounding judge ≤5-câu coverage không tăng p95 | W4 | TOP#6 (sentence-cap tail), TOP#12 (silent-degrade counter); P2-E 🕰-A claim-level NLI/MiniCheck qua Port |
| **D8** | Noisy neighbor: fair-queue ingest + per-tenant rate limit | W2 | TOP#13 (ingest fairness), TOP#2 (exactly-once — **D8-adjacent NEW**: inbox-pattern marker placement, P2-F §4) |
| **D9** | Cost: ma trận purpose×model + Haiku contradiction chốt | W5 | TOP#8 (ingest $0 + no read-query), TOP#10 (config-drift seed), TOP#15 (dead validate guard); P2-G Q21 `request_llm_calls` Option A · P2-A/E Haiku KHÔNG vi phạm (2 governance scope) |
| **D10** | Embedding versioning (chặn đổi model khi có chunks) | W5 | P2-F embedding-model-change guard (no purge, no corpus bump on binding swap); P2-D §2c zembed-1 vs Qwen3/Voyage swap qua ADR |
| **D11** | SLO + DR + Nghị định 13 (PDPD) + secrets rotation | W6 | (chưa có 🐛 trực tiếp Phase 2 — Wave 6 application layer; guard_output đã chạm PII per charter) |
| **D12** | Production feedback loop (thumbs → eval; analytics refuse/miss) | W6 | P2-B §2 eval-loop 🕰 (no harness keyed `chunking_strategy_selected`); P2-G #5 eval harness CHUẨN làm nền |
| **D13** | Human ground-truth process (người không biết hệ thống gán nhãn) | trước W5 eval | (đường găng Phase 5 — agent KHÔNG tự verify đáp án của chính nó; P2-E Q19 "load test HALLU=0 ≠ judge tốt") |
| **D14** | AdapChunk per-section strategy selection | W3 | TOP#11 (Block feed tiền đề), P2-B 🕰-3 (HiChunk per-section +18-25%) |
| **D15** | AdapChunk proposition verification (entailment / giữ original) | W3 | 🐛-D connector-deletion, P2-B Q13 (regex-now no-fab; LLM-prop cần entailment gate) |
| **D16** | AdapChunk large-table policy (atomic-row + header-travel) | W3 | P2-B Q15 `_emit_table_rows` hợp nhất (atomic ở HÀNG, FORMULA/IMAGE atomic-tuyệt-đối) |
| **D17** | AdapChunk incremental re-chunk (lifecycle) | W3/W6 | TOP#7 (H-REAP reaper extend = first lifecycle hook); P2-F Q24 DRAFT-state unify |

### Decision MỚI cần thêm vào register (Phase 3)
- **D8b (exactly-once inbox)** — move dedup mark từ Redis-`SET NX`-before-handler → Postgres `inbox(msg_id PK)` trong CÙNG tx handler, XACK sau commit; Redis `SET NX` giữ làm fast-path optimisation. WIRE/HARDEN trong event-bus Port hiện có, KHÔNG swap Redis Streams. (P2-F §4, Wave dự kiến W1/W2 — data-loss nên ưu tiên cao). **Đề xuất ghi vào DECISION-REGISTER ở Phase 3.**
- **D-true-BM25** — `ts_rank_cd` thiếu IDF-saturation/k1/b; VectorChord-BM25 2.4-6.5× ES (P2-D mục 15). Engine-swap candidate qua LexicalRetrievalPort + A/B 91Q trước ADR. (gộp được vào D1-nhóm-engine hoặc tách riêng W4).

### Trục chưa có 🐛 Phase 2 (gap-of-evidence, không phải "đã chuẩn")
- **D11 (SLO/DR/PDPD)** + **D13 (ground-truth process)**: 0 audit Phase 2 đụng vì Phase 1–3 read-only + scope engine. Đây là **đường găng con-người** (charter §critical-path) — không có nghĩa OK, mà là **chưa điều tra**. Phase 3 phải mở.

---

## 5. "ĐÃ CHUẨN — ĐỪNG ĐỤNG" — hợp nhất 7 shortlist (đập = lỗi nặng nhất)

> Charter mandate praise-first. 8 keystone xuất hiện ≥1 lần trong 7 report, evidence-backed:

1. **4-key identity + JWT-only tenant claim + Redis 4-key registry** (P2-C §6.1-3) — SOTA-shaped, anti-spoof, DB unique constraint. Slug-in-identity là lựa chọn ĐÚNG.
2. **Sacred no-inject / no-override + 9 lock-test** (P2-A §5.2, P2-E §5.1-2) — sysprompt verbatim, grounding warn-only, math_lockdown override đã xóa sạch. `test_generate_no_app_injection.py` = regression guard, giữ.
3. **Narrate-then-embed dual-content safety** (P2-B §6.1) — embed narration, answer raw, degrade 3 tầng. Misclassify cũng không vào answer. ĐỪNG "simplify" overwrite `content`.
4. **Version-stamped passive bust** (corpus_version dual-bump `GREATEST(updated_at,deleted_at)` + bot_version sha256) (P2-F §6.1) — purge-free invalidation, đập = stale-answer bug.
5. **Semantic-cache 4-key scope TRƯỚC cosine** (P2-C §6.6, P2-F §6.2) — app-WHERE belt giữ cả khi RLS off. Giữ làm defence-in-depth.
6. **Cliff filter + retrieval safety-net + CRAG score-mode-aware + bot-filter-PRE exact-sort** (P2-D §6.1-9) — vết-sẹo-production forensic, EXPLAIN-verified recall 100% small-corpus. Tune THAM SỐ qua A/B, đừng đập CẤU TRÚC.
7. **5-tier resolve + 33-step request_steps + per-step cost-capture wired** (P2-G §6.1-3) — LaunchDarkly-class config, cost substrate ĐÃ populated (Q21 EVOLVE không replace).
8. **Graph singleton + identity-OFF node pattern + flag-flip qua alembic `_ab`** (P2-A §5.1,5,7) — multi-tenant-safe, opt-in không nhiễm default, governance no-psql-hotfix điểm sáng.

---

## 6. CHỐT GATE 2 — đề nghị user approve

**Trạng thái**: Phase 0 ✅ · Phase 1 ✅ (GATE 1) · **Phase 2 ✅ DONE** (7/7 audit + GAPMAP này).

**Sự thật đã verify Phase 2** (evidence-driven, không đoán):
- RLS **PROVEN INERT** (psql bypass-proof, không còn "cần thực nghiệm") — TOP#1, P0 AN TOÀN.
- Narrate-then-embed **LIVE** (DB 211/211) — settle mâu thuẫn P1 §5.
- structured_subanswer **ĐÃ FLIP ON** (alembic 0192) — sửa claim sai P1-A.
- Tie-order nondeterminism **OPEN** post-revert — fix-direction = content-aware key, KHÔNG uuid.
- Exactly-once **= at-most-once** (re-verified line-by-line) — message DROPPED, data-loss thật.
- HNSW recall-cliff **chưa reproduce** (corpus 560 rows quá nhỏ) — GIẢ THUYẾT scale, đo lại Phase 3.
- math_lockdown override **đã chết** (0 reader) nhưng DB row + doc còn → cleanup.

**Đề nghị Phase 3** (research + ADR, **chờ approve mới sang**):
1. Ưu tiên Wave: **W1** = D3 (RLS) + D4 (purge/reaper) + **D8b (exactly-once)** — 3 issue P0/data-loss, "code sửa ĐẦU TIÊN" đúng charter.
2. Viết ADR cho D1/D5/D7/D8b + 2 decision mới (D8b inbox, D-true-BM25) → ghi vào DECISION-REGISTER.
3. AdapChunk: chốt verdict §3 (build Block-feed vs ablation-delete semantic) — Phase 3 quyết, có A/B gate.
4. Mở D11/D13 (SLO/DR/PDPD + ground-truth) — trục chưa điều tra.

**KHÔNG tự sang Phase 3. Dừng ở GATE 2.**

---
*P2-GAPMAP tổng hợp 7 file P2-A…G. 0 src/alembic/tests chạm. Chỉ file này + 7 P2 được ghi trong `program/gaps/`. program/ vẫn UNTRACKED — chờ user duyệt trước khi commit.*
