# PLAN — Query-flow best-practice remediation (nhận câu → trả lời)

> Nguồn evidence: 2 workflow audit (intent-taxonomy 4 agent + query-flow 7 agent = ~1M tokens Opus) + funnel workflow + RAG deep-test chinh-sach-xe (20 QA). Report: `reports/QUERY_FLOW_BESTPRACTICE_AUDIT_20260703.md`, `reports/RAG_DEEP_TEST_chinh-sach-xe_20260703.md`.
> Nguyên tắc: T1(smartness) > T2(cost/perf) > T3(refactor). Rule#0: đổi routing/threshold PHẢI load-test. Sacred: HALLU=0, no app-inject/override, no-psql-hotfix (DB content qua alembic/admin), domain-neutral.
> **Trạng thái: PLAN ONLY — chưa code. Working tree đang giữ Q7-Q12 + nhóm A/B + I15 (chưa commit).**

---

## 0. TÓM TẮT

Pipeline = **khung SOTA-grade** (hybrid+RRF+rerank+CRAG+MMR+LITM+multi-query+HyDE). 2/7 stage đã chuẩn; 5/7 PARTIAL. **4 issue HIGH** (2 HALLU-sacred, 1 correctness, 1 observability) + drift taxonomy + refinements.

**19 vấn đề** chia 5 nhóm → 4 phase theo rủi ro + tầng T1/T2/T3.

---

## 1. VẤN ĐỀ — phân loại + root cause + giải pháp + trade-off

### NHÓM A — HALLU-CRITICAL (sacred, T1, ưu tiên tuyệt đối)

#### A1 · Grounding gate NGƯỢC ⭐ (guard_output)
- **Vấn đề:** LLM grounding judge xác nhận answer BỊA (ratio>threshold) → chỉ `severity=warn/hitl` + flag, KHÔNG block → answer bịa vẫn ship.
- **Evidence:** `guard_output.py:503-519`; runtime q20 bịa "1.250.000đ + 25 lốp" cho Neoterra (chunk không có giá/tồn).
- **Root cause:** gate bất đối xứng — judge-dead→refuse (fail_closed) nhưng judge-confirms-bịa→ship+flag.
- **Giải pháp:** confirmed-ungrounded (trên threshold calibrated) → refuse bằng `oos_answer_template` (giống nhánh regex-block), gate đối xứng, config-driven per-bot.
- **Trade-off:** Chặn bịa (T1↑ HALLU) NHƯNG có thể tăng refuse-oan nếu threshold sai → cần calibrate + có thể giảm coverage. Đây là quyết định **sacred #10** (app KHÔNG override answer — nhưng refuse-bằng-template KHÔNG phải override, là từ chối). Owner phải chốt: block vs observe.
- **Rủi ro:** CAO (đổi hành vi answer). **Verify:** load-test HALLU=0 + PASS không tụt + đo refuse-rate trước/sau.

#### A2 · Semantic cache KHÔNG có safety floor (input+cache)
- **Vấn đề:** per-bot `semantic_cache_threshold` clamp chỉ [0,1]; operator set 0.0 → cosine match BẤT KỲ cached row → trả answer SAI câu (HALLU vector).
- **Evidence:** `bot_limits.py:468` (per-bot win outright), `:151-152` (clamp [0,1]); hằng `SEMANTIC_CACHE_THRESHOLD_MIN_RECOMMENDED=0.95` định nghĩa nhưng UNUSED (`_04_jwt_auth.py:152`).
- **Root cause:** floor đã có nhưng không enforce.
- **Giải pháp:** enforce floor — `resolve_semantic_cache_threshold` = `max(resolved, FLOOR)` + warn khi stored < floor; đổi schema-min từ 0.0 → floor trong `validate_plan_limits`.
- **Trade-off:** Chặn HALLU-vector (T1↑) NHƯNG giới hạn A/B threshold xuống (không thể test dưới floor). Floor 0.90-0.95 vẫn đủ room A/B.
- **Rủi ro:** THẤP (chỉ chặn giá trị nguy hiểm). **Verify:** unit test floor enforce; không cần load-test (chỉ siết bound).

### NHÓM B — CORRECTNESS BUGS (T1, an toàn-đến-trung)

#### B1 · Char-cap chạy SAU LITM reorder (generate)
- **Vấn đề:** char-cap cắt từ ĐUÔI list NHƯNG chạy SAU `reorder_for_lost_in_middle` (đã đẩy chunk relevant vào GIỮA) → cắt mất chunk relevant nhất.
- **Evidence:** `generate.py:586-595` (cap loop post-reorder).
- **Root cause:** thứ tự sai — cap phải trước reorder.
- **Giải pháp:** cap/filter theo score-desc TRƯỚC `reorder_for_lost_in_middle`, rồi reorder chunk sống sót.
- **Trade-off:** Đúng chunk relevant tới LLM (T1↑), gần như 0 downside. Chỉ đổi thứ tự 2 bước.
- **Rủi ro:** THẤP. **Verify:** unit test thứ tự (cap giữ top-score, reorder sau); regression.

#### B2 · Comparison decompose fuse 1 global RRF pool (query-transform) — q27 miss
- **Vấn đề:** "so sánh A và B" decompose thành sub-query nhưng fuse TẤT CẢ vào 1 RRF pool rồi truncate top_k → entity yếu (notation lệch) bị đè, không lọt LLM.
- **Evidence:** `retrieve.py:1378-1382` (mq_rrf_merge → chunks[:top_k]); runtime q27 miss 205/65R16.
- **Root cause:** global RRF không đảm bảo share per-entity.
- **Giải pháp:** balanced merge — mỗi sub-query đảm bảo share tối thiểu trong final top_k (round-robin interleave per_query_chunks).
- **Trade-off:** Cứu multi-entity (T1↑ coverage) NHƯNG có thể đẩy 1-2 chunk chung ra để nhường entity yếu → cần đo không giảm single-fact. 
- **Rủi ro:** TRUNG. **Verify:** load-test q27-class (comparison) + không tụt factoid.

#### B3 · MMR chạy TRƯỚC grade (rank+filter)
- **Vấn đề:** MMR dedup dùng rerank-score làm relevance, drop chunk mà CRAG grade sẽ giữ (thứ tự rerank→mmr→neighbor→grade).
- **Evidence:** graph order; `mmr.py`.
- **Giải pháp:** MMR SAU grade (grade→mmr→litm), HOẶC MMR non-destructive trước grade (chỉ reorder, defer hard-cut).
- **Trade-off:** Đúng relevance signal (T1↑) NHƯNG đổi thứ tự graph node (cần test regression). Chi phí nhỏ.
- **Rủi ro:** TRUNG. **Verify:** regression + đo coverage.

### NHÓM C — OBSERVABILITY (T2)

#### C1 · Không có "chunk survival trace" (rank+filter)
- **Vấn đề:** chunk đáp án chết ở 6 stage độc lập (rerank cap/cliff floor/cliff gap/mmr/grade/max cap) mà không có 1 trace nói chết ở đâu → khó debug q27-class.
- **Evidence:** filter stacking rerank.py + retrieval_filter.py.
- **Giải pháp:** 1 trace keyed by chunk_id ghi stage drop mỗi candidate → request_steps metadata.
- **Trade-off:** Debug tốt hơn (T2↑) NHƯNG thêm log overhead nhỏ. Gate per-bot/debug-mode.
- **Rủi ro:** THẤP (chỉ thêm observability, không đổi logic). **Verify:** unit test trace emit đúng stage.

### NHÓM D — TAXONOMY DRIFT (T3 consistency, phần lớn an toàn)

#### D1 · Dead parallel taxonomy QUERY_INTENT_* + query_router package
- **Vấn đề:** 2 họ enum; `QUERY_INTENT_*` (_17) + package `query_router` DEAD (commented-out từ 2026-06-03), 0 live consumer, chỉ 2 unit test giữ.
- **Evidence:** `query_router/registry.py` DEAD-CODE NOTICE; `_17_pipeline_audit.py:36-62`; 0 hit bootstrap/graph.
- **Giải pháp:** XÓA package `query_router/*` (5 file) + `QUERY_INTENT_TYPES`/`QUERY_INTENT_{STRUCTURED_REF,SMALLTALK,HALLU_TRAP,SEMANTIC}` + QueryIntent Literal mirror + 2 unit test dead. Giữ `QUERY_INTENT_FACTOID/COMPARISON` chỉ nếu còn ref (kiểm).
- **Trade-off:** Xóa drift + ~4 file dead (T3↑ clean) — 0 behavioral risk (dead code). Downside = 0.
- **Rủi ro:** THẤP (dead code, verify 0 live ref trước xóa). **Verify:** grep 0 live import + full test pass.

#### D2 · Intent 9 → 6 canonical (over-split social + feedback misplaced)
- **Vấn đề:** greeting+vu_vo+chitchat = 3 nhãn 1 hành vi (skip retrieve); feedback không phải retrieval-intent.
- **Evidence:** taxonomy audit — best-practice 6 {factoid, multi_hop, aggregation, comparison, out_of_scope, chitchat}.
- **Giải pháp:** gộp greeting/vu_vo → chitchat trong `llm_schemas.py` Literal (9→7); giữ greeting làm heuristic fast-path map→chitchat downstream; chuyển feedback ra trục signal riêng.
- **Trade-off:** Taxonomy chuẩn + đơn giản (T3↑) NHƯNG đổi enum = chạm classifier + 8 map by_intent + heuristic + alembic seed → **đổi routing MỌI bot**.
- **Rủi ro:** CAO (routing change). **Verify:** load-test HALLU=0 + PASS không tụt. **← owner-gated + measure.**

#### D3 · DB config drift — key chết (rule#7 reproducibility)
- **Vấn đề:** DB seed (archived migration) có key classifier KHÔNG emit: `range_query, cross_compare, yes_no, summary_doc, promo, sale, voucher` → config chết. FRESH DB (squash chain) không có seed → lệch production.
- **Evidence:** archived 0130/0138 seed; squashed_baseline.sql = 0 by-intent seed.
- **Giải pháp:** (a) alembic mới re-seed by-intent maps VÀO squash chain (fix fresh-DB drift) + (b) bỏ key chết khỏi seed. Đồng bộ tất cả map về đúng canonical set.
- **Trade-off:** Reproducible clone-DB (T2/T3↑) NHƯNG cần alembic + đảm bảo không đổi giá trị đang chạy production. 
- **Rủi ro:** TRUNG (DB content, phải alembic không đổi live value). **Verify:** clone-DB test + fresh-DB = production behavior.

### NHÓM E — BEST-PRACTICE REFINEMENT (T2/T3, cần đo)

| ID | Vấn đề | Giải pháp | Trade-off | Rủi ro |
|---|---|---|---|---|
| E1 | "BM25" là `ts_rank_cd` không phải BM25 thật (không IDF/TF-sat) | ParadeDB pg_search/pg_bm25 sau LexicalRetrieval Port, HOẶC đổi tên "lexical" cho đúng | retrieval quality↑ NHƯNG thêm dependency/infra | CAO (infra) |
| E2 | Candidate width nông (top_k*2=40) | tách `retrieve_candidate_pool` 80-150 khỏi final top_k | recall↑ (cứu q27-class) NHƯNG rerank cost↑ nhẹ | TRUNG |
| E3 | CRAG grader quá lenient (giữ ambiguous) | neutral prompt, giữ "relevant" mặc định, ablation đo | precision↑ NHƯNG có thể tăng refuse | TRUNG (đo) |
| E4 | Injection chỉ regex | thêm ML-moderation strategy (Llama Guard) sau regex, config-gated | security↑ NHƯNG cost/latency + dependency | TRUNG |
| E5 | HyDE poison cache (embed hypothetical) | cache probe dùng embed RAW query | cache hit↑ NHƯNG tách embed path | THẤP |
| E6 | Condense gated >2turn+100char | condense khi có BẤT KỲ prior turn | coreference↑ (follow-up ngắn) NHƯNG cost↑ | THẤP |
| E7 | context_cap char (2900) không phải token | token-budget theo model context window | context↑ cho model lớn NHƯNG đổi cap logic | TRUNG |
| E8 | Chitchat hard-drop `<documents>` | không drop khi có graded chunks | tránh mất context khi misclassify | THẤP |
| E9 | Unicode exact-hash cache | NFC normalize + collapse space | hit-rate↑ VN | THẤP |
| E10 | RRF weight flat 0.5/0.5 untuned | A/B per-corpus HOẶC xóa adaptive dead branch | quality↑ nếu tune | THẤP-TRUNG |
| E11 | Threshold chưa calibrate (cache 0.97, cliff 0.05, grounding 0.3, min_score 0.3) | calibration harness theo score histogram model hiện tại | đúng distribution NHƯNG cần harness + đo | TRUNG |
| E12 | router_select_model misnomer/dead-routing | rename telemetry + centralize cost-routing | rõ ràng T3 | THẤP |
| E13 | Citation = attribution không phải entailment | tách id-validation vs entailment judge | citation đúng NHƯNG thêm judge | TRUNG |

---

## 2. PHASING (thứ tự thực thi)

### Phase 1 — HALLU + correctness AN TOÀN (làm sớm, ít/không load-test)
- **B1** char-cap trước reorder (test) · **A2** cache floor (test) · **C1** chunk-survival trace (test) · **D1** xóa dead query_router (test).
- Rủi ro thấp, verify bằng unit test + regression. KHÔNG cần load-test.

### Phase 2 — HALLU-sacred cần load-test + owner-chốt
- **A1** grounding gate block (sacred #10 decision + load-test HALLU=0).
- **B2** q27 balanced-merge decompose (load-test comparison).
- **B3** MMR sau grade (regression + coverage đo).

### Phase 3 — Taxonomy chuẩn hoá (owner-gated, routing change)
- **D2** intent 9→6 (load-test routing) · **D3** DB drift re-seed (alembic + clone-DB test).

### Phase 4 — Refinement (đo từng cái)
- E1-E13 theo ưu tiên T1(E2,E3) > T2(E4-E11) > T3(E12). Mỗi cái A/B đo trước.

---

## 3. TRADE-OFF TỔNG (đọc nhanh)

| Nhóm | Được | Mất/Rủi ro | Cần đo? |
|---|---|---|---|
| A (HALLU) | HALLU=0 vững | refuse-oan nếu threshold sai | A1 CÓ, A2 KHÔNG |
| B (correctness) | đáp án đúng chunk | đổi thứ tự node | B1 KHÔNG, B2/B3 CÓ |
| C (observability) | debug được | log overhead nhỏ | KHÔNG |
| D (taxonomy) | clean + reproducible | D2 đổi routing mọi bot | D1 KHÔNG, D2/D3 CÓ |
| E (refinement) | chất lượng↑ | cost/infra/dependency | CÓ (A/B từng cái) |

---

## 4. VERIFICATION GATE (rule#0)

- **Không cần load-test** (unit + regression đủ): B1, A2, C1, D1, E5, E6, E8, E9, C1.
- **Cần load-test** (HALLU=0 + PASS không tụt): A1, B2, B3, D2, E2, E3, E7, E11.
- **Cần alembic + clone-DB test:** D3.
- **Cần owner chốt (sacred #10):** A1 (block vs observe).

## 5. KHUYẾN NGHỊ

Bắt đầu **Phase 1** (4 fix an toàn, có test, không load-test) để thấy kết quả nhanh + giảm rủi ro. Phase 2+ chờ owner chốt A1 (sacred) + bố trí load-test. Giữ nguyên **không commit** tới khi owner duyệt.
