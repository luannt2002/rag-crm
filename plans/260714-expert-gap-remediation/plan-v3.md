Tất cả sự thật cốt lõi đã kiểm chứng trực tiếp (HEAD=5fd6ecd, 0.1 đã ship, 3 builder pipeline_config, IDOR fence hở ở HEAD, RLS inert, entity_synonyms 598/0, 3/6 bot rỗng oos_template, 264 config keys). Dưới đây là plan-v3.

> **CẬP NHẬT 2026-07-14 (sau /compact):** đã ship **6 task SHIP-NOW** (red→green TDD, 74 test pass, sacred guards sạch) — chi tiết verify ở `reports/CHANGELOG_VERIFY_20260714.md` NHÓM 3 (S1–S6): **0.5** degeneration · **1.4-U1** MMR test · **3.6** dense-NFC (bắt thêm path thứ 3 `_embed_batch_queries` plan bỏ sót) · **3.5** cache content-hash · **1.3a** CI wiring (advisory) · **SEC.1** IDOR fence. Chưa commit (đợi owner). Kế tiếp theo đường găng: B1 (0.2 CB atomic) → B2 (0.4→0.3 PII) → SEC.2 triage → B4 (2.2 hub re-ingest). 1.1 vẫn blocker CI `CREATE DATABASE`.

---

# PLAN-V3 — Expert-Gap Remediation (Ragbot)

## 1. Trạng thái & phạm vi

Plan này **thay thế** `plans/260714-expert-gap-remediation/plan.md` và `plan-v2.md` (cả hai tính trên baseline `71682a2`, đã lệch 2 commit). **HEAD thật = `5fd6ecd`** (đã kiểm chứng). Hai commit đã ship trước khi plan cũ tính: `a58ac8d` = **task 0.1 XONG** (revert `_COUNT_COL_TOKENS`, grep `src/ tests/` = 0 hit, test đã xóa); `5fd6ecd` = chore đã tiêu thụ sẵn vài mục 4.2/3.2/3.6 (docstring zeroentropy 2560→1280, scrub brand `llm_usage.py`, xóa dead Redis write ở `model_resolver`, và **gather-first refactor vùng `_prewarm_embedding_cache` trong `query_graph.py` — chính vùng task 3.6 phải sửa**). Mọi `file:line` trong INPUT 1 tính ở `71682a2`, phải re-anchor trước khi code.

Phạm vi: 18 task còn sống + **2 task security MỚI** mà cả 20-fix-verification lẫn plan cũ đều bỏ sót (write-side chưa fence). 1 task bị **CẤM** (3.1). 0.1 coi như đã ship.

---

## 2. LUẬT (mỗi luật gắn với thất bại đã sinh ra nó)

| # | Luật | Thất bại đã sinh ra nó |
|---|---|---|
| **L1** | **Khai báo CONSTANT-hay-DB** cho mọi thay đổi config. Chuỗi ưu tiên là `column > plan_limits > system_config > constant` — constant là fallback **THẤP NHẤT**, nên `DB != constant` là trạng thái **CỐ Ý** cho mọi key đã tuned. | 9f93804 nâng constant MMR 0.88→0.98 nhưng DB vẫn 0.88 ⇒ **runtime no-op**. Guard ngây thơ `system_config[k]!=constant[k]` sẽ đỏ trên ~16 override hợp lệ. |
| **L2** | **Khai báo ĐÃ-TỪNG-FIX-CHƯA** (`git log -S <symbol>` + `--grep revert`). Cùng một fix lần 3+ ⇒ dừng, đọc lý do revert cũ. | `_COUNT_COL_TOKENS` landing **lần 3** (4e83410→revert 6796cd9→5c4fdda→revert a58ac8d). Cliff floor 0.15→0.05→0.2 qua **6 migration**. |
| **L3** | **Không kết luận nếu không có runtime evidence.** Gắn nhãn **SỰ THẬT** (có `file:line`/DB row/output) vs **GIẢ THUYẾT**. Đọc DB, đừng suy từ seed migration. | Report suy "redact NEVER called" từ 1 seed row trong khi DB có **13 rule**; suy "mọi table refuse" trong khi **0/1683**. |
| **L4** | **Kiểm tra MẪU SỐ.** `request_logs`/`request_steps`/`chat_histories` là traffic **load-test bypass_cache**, KHÔNG phải production — `generate` chạy 1751/1778 req = **98.5%** (cache hit ~1.5%). Mọi "0 on live" ⇒ đọc là "0 trong mẫu load-test", hạ cấp từ bằng-chứng-an-toàn xuống quan-sát-không-phơi-nhiễm. | 3.1 dùng 741 thay 1778; 2.1 "brand 4-vs-28" không tái lập (đo lại 21/21); completeness gate xanh trên prod trong khi fresh-DB chết. |
| **L5** | **1 fix = 1 đo**, TRỪ khi atomicity cấm tách (khai báo rõ đơn vị nguyên tử). | 0.2 tách state-machine khỏi `reset()` ⇒ `reset()` chết câm; PII arm mà chưa siết redactor ⇒ hỏng retrieval. |
| **L6** | **Test LIVE dispatch, KHÔNG test internal function.** | 2.2 de89da8 "verified" trên path `sync.py` nhưng chưa từng chạy trên canonical worker; 2.3 flag live-TRUE nhưng dispatch không gọi; 4.1 "36 test pass" trên input bịa tay chứ không phải output `smart_chunk` thật. |
| **L7** | **Hỏi: test_chat và worker CÓ ĐI CÙNG ĐƯỜNG không?** Có **3 builder** `pipeline_config`; key mới phải vá đủ **mọi builder + mọi key-tuple**. | 0.4 (đề xuất gốc) vá builder worker, nhưng 23 dòng rò PII đến từ path SSE = builder test_chat. |
| **L8** | **Không đo được ⇒ không ship.** Nếu chưa có instrumentation, task instrumentation là tiền-điều-kiện. | 0.2 không có log event ở transition OPEN; 1.1 không verify được nếu CI thiếu `CREATE DATABASE`. |
| **L9** | **Mọi thay đổi DB content-state qua alembic/admin-UI-có-audit, TUYỆT ĐỐI không psql UPDATE.** Không "chạy 1 lần để test". | Sacred #7. 264 config keys hiện ra-khỏi-alembic (out-of-band); Wave-M3.6 "1tr499". |
| **L10** | **Fix ĐÚNG TẦNG.** Bug retrieval không fix bằng sysprompt; bug owner-domain-knowledge không fix bằng vocab baked trong engine. | `_COUNT_COL_TOKENS` = engine đoán thứ chỉ data-owner biết; 3 alembic sysprompt cho 1 bug retrieval (case study CLAUDE.md). |

---

## 3. 🚫 DANH SÁCH CẤM (mỗi mục kèm evidence)

1. **3.1 — back-fill chunk dưới floor `absolute_floor=0.2`.** REJECTED (FIX_WRONG). 30/30 case exit-D giữ đúng 2 chunk > floor; chunk thứ 3 back-fill sẽ < 0.2 = **HALLU surface**. Floor đã calibrate qua 6 migration (0.15→0.05→0.2), pin-test `test_cliff_floor_calibrated.py` ghi rõ *"floor above 0.20 restores the 0.15-era REFUSE_GAP regression"*. Lưu ý L4: "134/134 kept-1 status=success" **không** chứng minh "không lỗi" (`success ≠ correct`) — nhưng rủi ro HALLU vẫn đủ để cấm. Nếu muốn cứu recall, dùng `rerank_retrieval_safety_n` (rerank.py:483, rescue theo retrieval-rank, đo REFUSE_GAP trước).
2. **Re-land `_COUNT_COL_TOKENS` (lần 4).** Cite a58ac8d + 6796cd9. Nếu owner muốn ship default count-vocab thì phải lang-keyed trong `constants/_25_locale_structure_packs.py` + ADR sửa ADR-0006, **không bao giờ** đụng `_HEADER_EXACT_TOKENS`.
3. **3.2 standalone tại `_emit_usage_sink`.** Hàm không có `messages` ⇒ chỉ điền completion_tokens ⇒ vẫn thiếu ~98% (prompt token). Fold vào 3.4.
4. **3.3 gate `finish_reason=="length"`.** DEAD CODE: 0 occurrence trong DB + journal (chỉ `stop` 445×); `finish_reason` không được trả về repair loop. Gate trên **parse-error** thay vì finish_reason.
5. **0.4 option (a) phone-context words / (b) VN mobile-prefix 03/05/07/08/09 trong `src/`.** Vi phạm domain-neutral (numbering plan 1 nước baked trong platform code).
6. **0.4 gate qua `pipeline_config` trong `guard_input`.** `guard_input` không có `bot_cfg`; và vá 1 builder bỏ sót path còn lại (L7). Dùng HTTP boundary + `resolve_bot_limit(bot_cfg,...)`.
7. **4.1 chỉ strip `extract_structural_path`.** Đo được: HDT 0.00→0.9254 nhưng recursive/semantic **đứng yên 0.0465** (prefix `## heading\n` không đụng). Strip **CẢ HAI** prefix trong vòng lặp. Và **giữ observe-only**, không tune `tol` (HDT trần ~0.93; enforce = reject mọi doc HDT/legal).
8. **4.2(c) xóa node `condense_question`/`router`.** Là OFF-path của flag live `merge_condense_router`, pin bởi `test_graph_dead_node_removal.py`; xóa = crash LangGraph compile khi flag=False.
9. **4.2(e) xóa 3 comment "lying" (HNSW/idx_scan, row-dict side-channel, LocalGuardrail).** Đã kiểm chứng: **chính xác**, load-bearing memory. (Chỉ docstring `pgvector_store.py:4` m=16/ef=64 là sai → sửa thành m=32/ef_construction=200. Và bỏ luận điểm "idx_scan=18 chứng minh HNSW hoạt động" — 18 là counter tích lũy trên bảng 906 row, ~unused.)
10. **4.2(b) lý do "11ms × 1751 round-trip".** Sai: `resolve_runtime` hit L1 LRU cùng request (microsecond). Node chỉ là telemetry-only; lãng phí thật là 1 request_steps INSERT thừa.
11. **Value-guard `system_config[k] != constant[k]`.** Constant là fallback thấp nhất; đỏ trên mọi override cố ý (embedding_provider, embedding_dimension, feature flags). Target đúng = **SEED-vs-CONSTANT** (cả 2 trong git, không cần DB).
12. **1.4-UNIT2 collapse map MMR mà chưa A/B factoid.** DB global=0.88 ⇒ bỏ `by_intent` sẽ hạ aggregation 0.98→0.88, comparison 0.95→0.88 tại runtime = regression đúng thứ 9f93804 chống.
13. **Verify PII chỉ TURN-1.** 8109f83 đã mask turn-1 trên DB này ⇒ báo PASS giả. Lỗ thật = **dòng persist + turn-2** (`generate.py:697` replay history).
14. **60Q/load-test làm tín hiệu pass/fail cho 0.5, 3.6, 0.1.** No-op trên corpus hiện tại (0.5: 0/1683→0/1683; 3.6: 0 NFD; 0.1: byte-identical). Dùng unit/synthetic.
15. **psql UPDATE vào bot content-state** (`custom_vocabulary`, `plan_limits`, `system_config`, `guardrail_rules`, `ai_*`, `bots.oos_answer_template`). L9/Sacred #7.
16. **Advance ADR-0007 (rebuild `5db7922`) trước khi khôi phục lý do kill của `9416f4d`** (revert cùng ngày, body rỗng — không recover được từ git). Đây là "động cơ" của vòng lặp 0.1.

---

## 4. BATCHES

> **Quy ước block task:** Bug (file:line + số runtime) · CONST/DB · Đã-từng-fix · Fix (⚠ nếu đề xuất gốc sai) · Red-test-trước · Blast (reingest/reindex/migration/restart/test đỏ) · Đo-sau · Sacred · Tier·Effort.

---

### B-SEC — Write-side security (CHẠY TRƯỚC MỌI THỨ; block B5)

**Vì sao đi cùng:** cả 20-fix lẫn plan cũ chỉ hardening **query path**; toàn bộ write-side đang không fence. RLS inert (kiểm chứng: runtime role `postgres`, `rolsuper=t rolbypassrls=t`; `RAGBOT_ALLOW_SUPERUSER_RUNTIME` set; `DATABASE_URL_APP` **không** cấu hình). Fence tầng repo (bù cho RLS inert) **vắng mặt ở HEAD** nhưng đã viết+test sẵn trên branch `integ-260624-wave1` (đã kiểm chứng branch tồn tại).

#### SEC.1 — IDOR write-fence (merge từ `integ-260624-wave1`) — [T1·M]
- **Bug (SỰ THẬT):** `document_repository.py:113` chỉ check `document.record_tenant_id != tid` (tenant TỰ-KHAI của object incoming — attacker set = tid của mình, pass), rồi `:116 session.get(DocumentModel, document.id)` **chỉ theo PK**, rồi `:119-129` mutate `existing.*` + commit **không** check `existing.record_tenant_id == tid`. ⇒ clobber cross-tenant một `document.id` đã biết. `conversation_repository.py:158-168` cùng cấu trúc.
- **CONST/DB:** không (code). Không migration.
- **Đã-từng-fix:** chưa merge; fix đã tồn tại: `integ-260624-wave1:tests/unit/repositories/test_idor_write_fence.py` + diff `document_repository +114 / conversation_repository +108`.
- **Fix:** cherry-pick fence: nhánh UPDATE thành **một** statement lọc **cả PK VÀ `record_tenant_id`** (RETURNING). Hoạt động **bất kể RLS** — đây là lớp bảo vệ DUY NHẤT khi DSN chạy trên `postgres`.
- **Red-test-trước:** `test_idor_write_fence.py` (đã có trên branch) — cross-tenant `document.id` → `save()` phải **không** mutate; đỏ ở HEAD.
- **Blast:** restart. Test đỏ hợp lệ: các test giả định update-by-PK. Không reingest/reindex/migration.
- **Đo-sau:** chạy `test_idor_write_fence.py` xanh + drive re-upload path với `document.id` thuộc tenant khác → assert 0 row bị đổi (SQL `SELECT record_tenant_id FROM documents WHERE id=<victim>` không đổi).
- **⚠ Caveat honest (GIẢ THUYẾT):** chưa chứng minh entrypoint external nào cho caller truyền `document.id` cross-tenant (canonical create sinh UUID server-side; nghi ngờ path re-upload/idempotency/rechunk-by-id — chưa verify). Finding vững = **control tầng repo THIẾU ở HEAD + fix đã viết+test bị stranded** ⇒ đủ để merge.
- **Sacred:** 4-key/tenant isolation ✅ (khôi phục). L9 ✅ (code, không psql).

#### SEC.2 — Triage split-brain `integ-260624-wave1` (block 1.1) — [T2·M, quyết định]
- **Bug (SỰ THẬT):** `git diff fix-260623-ingest-expert...integ-260624-wave1 --shortstat` = **102 file, +5885/−416** stranded (RBAC, token_ledger_analytics, vocabulary, per-purpose route...). Migration `20260624_stats_index_entity_synonyms.py` đã ship vào HEAD (active) nhưng code populate ở lại branch.
- **Nhiệm vụ:** với mỗi file stranded, quyết merge/discard đối chiếu HEAD **TRƯỚC khi 1.1 đóng băng seed** — nếu không 1.1 sẽ bless vĩnh viễn schema-không-có-code lên **mọi** fresh DB.
- **Quyết định con — `entity_synonyms`:** DB đo được **598 total / 0 populated**; `stats_index_repository.bulk_insert` không ghi cột; query không ILIKE cột; comment `document_stats.py:198` ("aliases → entity_synonyms so query matches") **SAI ở HEAD**. GIN trigram index maintain trên mọi INSERT cho cột luôn-NULL. **Hai lựa chọn:** (A) cherry-pick `bulk_insert` write + query ILIKE + 2 test — đây là **fix thật** cho class notation-mismatch (`265/50ZR20` vs query `265/50R20`) mà 0.1/2.x cứ vòng quanh; HOẶC (B) revert migration + parser `aliases` như dead. **Khuyến nghị A** (recall thật + code đã viết+test), nhưng là quyết-định-owner. Thực thi code rides B4 (task 2.4).
- **Sacred:** L2 (đừng để split-brain lừa lớp verify kế tiếp). Owner sign-off bắt buộc.

**Đo đóng batch:** `test_idor_write_fence.py` xanh; bảng quyết-định-merge/discard cho 102 file có owner-approve; entity_synonyms có verdict A/B.

---

### B1 — Circuit-breaker resilience (0.2) — atomic 3 file

**Atomicity (KHÔNG tách):** state-machine + `_ResourceBreakerAdapter.reset()` + retune constant phải cùng 1 PR.

#### 0.2 — CB flap / illegal OPEN→CLOSED — [T2·M]
- **Bug (SỰ THẬT, repro thật):** `retry_policy.py:191` `record_success` set `state=CLOSED` **vô điều kiện** (bỏ qua cooldown 30s); `:200` chỉ clear window khi `prev is HALF_OPEN` ⇒ breaker mở lại chạy trên window "nhiễm độc"; router check `can_execute` (`dynamic_litellm_router.py:792`) **TRƯỚC** `async with sem` (:800) ⇒ call admit trước khi mở sẽ land sau. Repro: sem=16, 600 arrival, p=60% ⇒ 35 open + **34 illegal OPEN→CLOSED close**. `DEFAULT_CB_HALF_OPEN_MAX_CALLS` (=1) **không bao giờ import** ⇒ HALF_OPEN admit vô hạn (50/50), trái Port contract `circuit_breaker_port.py:14-15`.
- **Runtime hiện trạng:** **CHƯA LIVE** — uvicorn pid 889755 start 22:21:35, commit rate-mode 3006171 land 22:36:53 (process **predates** feature). Latent, kích hoạt lần restart kế.
- **CONST/DB:** CONST (`shared/constants/_08_sentry_otel.py`, compile-time — không psql).
- **Đã-từng-fix:** chưa; `CB_MODE_RATE` single-touch (3006171); `record_success` từ commit đầu cd08119. Defect gốc từ commit đầu (không phải regression rate-mode).
- **Fix (đề xuất gốc ĐÚNG hướng, nhưng THIẾU 2 phần):** (1) máy trạng thái tường minh: `_enter()` rotate window ở **mọi** transition; `record_success` while OPEN = **return** (call late không hủy cooldown); HALF_OPEN success→CLOSED; wire `DEFAULT_CB_HALF_OPEN_MAX_CALLS`; thêm `CircuitBreaker.reset()` tường minh. (2) **`_base.py:76-78`**: `reset()` hiện = `record_success()` → khi record_success thành no-op-while-OPEN thì `reset()`/`failover_orchestrator.reset_all()` **chết câm** → đổi sang `self._breaker.reset()`. (3) **BLOCKING** — retune `_08_sentry_otel.py:45-47` window/min_calls **20/10 → 50/50** (giữ threshold 0.5). Lý do: sau fix OPEN **giữ** đủ 30s; 38/46 binding chung provider `innocom` = **1 breaker** fast-fail mọi purpose; failover DUY NHẤT (`record_fallback_model_id`) là **dangling FK** (model không tồn tại trong `ai_models`). Spurious trip 1.386%/window @scatter 25% (binomial, tính chính xác) → 30s outage. Retune hạ xuống 0.012% @p=.25, detection @p=.60 cải thiện 0.872→0.943.
- **Prereq đo (L8):** thêm structlog event `cb_state_transition(from,to,provider,window_fail_rate,consec_open_fails)` trong `_enter()` — HEAD **không** log gì ở OPEN.
- **Red-test-trước:** `test_cb_rate_based.py::test_open_breaker_holds_against_late_inflight_success` (đỏ ở HEAD: state=CLOSED, can_execute=True) + `::test_half_open_admits_only_one_probe` (đỏ: admit 50) + ladder test (cooldown 30+15×4=90).
- **Blast:** restart (latent, đừng tìm trong log hiện tại). Test đỏ hợp lệ: value-guard 1.3 trên `DEFAULT_CB_WINDOW_SIZE/MIN_CALLS` — cập nhật cùng PR. 24/24 assertion pin ở 5 file CB đã replay PASS dưới fix.
- **Đo-sau:** fault-inject proxy (base_url qua env, **không** psql); load-test p=.25 & .60; assert trên event `cb_state_transition`: `count(OPEN→CLOSED)==0`; **assert trên `window_fail_rate` breaker QUAN SÁT ĐƯỢC, không phải p của proxy** (breaker gộp nhiều purpose ⇒ p bị pha loãng — measurement F8); pin `--workers 1` (state per-process); @p=.25 `count(CLOSED→OPEN)==0` trên ≥2000 call. **Báo completion-rate + count timeout/failed/running cạnh p95** (measurement F2: semaphore biến overload thành timeout bị right-censor; p95 phẳng khi availability sập). Baseline p50 success hiện = **22.6s** (không phải 3.3s report).
- **Sacred:** #10 ✅ (không đụng prompt/answer — đừng biến OPEN thành canned refusal). Zero-hardcode ✅ (wire constant chết). 4-key: N/A nhưng flag honest — coupling cross-tenant fairness (tenant A fail → fast-fail tenant B), pre-existing, out-of-scope.

**Đo đóng batch:** load-test fault-inject 0 illegal close + ladder fires + @p=.25 no spurious trip + completion-rate không giảm.

---

### B2 — PII boundary (0.4 + 0.3) — atomic (siết-rồi-arm)

**Atomicity (KHÔNG tách):** arm gate (seed provider + flip flag + restart) **KHÔNG được** chạy trước khi redactor đủ precision — nếu không `chinh-sach-xe` (bot xe có thật trong DB) hỏng retrieval. Precedent: `ed26e1b` bị revert vì "brand-conflation defect" (cùng class).

#### 0.4 — VnRegexPiiRedactor ăn SKU/plate/VIN + gate hở — [T1·M]
- **Bug (SỰ THẬT, chạy runtime):** `_default_patterns.py:144` `pii_vi_phone = (0\d{9,10}|\+84\d{9,10})` **không có anchor** ⇒ ăn substring: `"đơn hàng 202512010001"` → `"2[redacted]"`. `VnRegexPiiRedactor._PATTERNS` ăn `"Mã 123456789012"→[CCCD]`, `"xe 30A-12345"→[VN_PLATE]`, `"Số khung 4111111111111111"→[CARD]`. `guard_input.py:66` rewrite **vô điều kiện** (không per-bot gate). Hai redactor cùng `rule_id` `pii_vi_phone` khác pattern (DB unanchored vs Port `\b0\d{9,10}\b` anchored). **8 hit `pii_vi_phone` trong `guardrail_events` đều PRE-DATE commit rewrite 8109f83** (là flag, không phải corruption) — impact overstated, chưa có production instance nào bị corrupt.
- **CONST/DB:** pattern ở CẢ code (`_default_patterns.py`) VÀ DB (`guardrail_rules`, seed alembic). Toggle `DEFAULT_PII_REDACTABLE_RULE_IDS` (frozenset trong constants) = behavior-toggle hardcoded (vi phạm zero-hardcode #6) trong khi `plan_limits.pii_redaction_enabled` đã tồn tại.
- **Đã-từng-fix:** 8109f83 (24h tuổi, verify 1 chuỗi tay, không load-test). Không revert PII nào trong lịch sử.
- **Fix (⚠ đề xuất gốc a/b/c ĐỀU SAI):** (a) phone-context words / (b) VN prefix list = domain-neutral violation; (c) per-bot allowlist = phát minh config-surface thứ 3. **Đúng:** (1) siết pattern → `(?<!\d)(?:0\d{9,10}|\+84\d{9,10})(?!\d)` ở `_default_patterns.py:144` **VÀ** alembic UPDATE row platform-default (`record_tenant_id IS NULL`) — cả hai, không thì drift (2 nguồn: `check_input` đọc DB loader, `redact_pii` đọc code). (2) **Locale-scope tập redactable** (sacred F3): `VN_PLATE`/`CCCD`/`+84` là literal 1 nước trong path platform-wide; phải keyed theo language-pack trước khi arm — port policy BẢO THỦ (`DEFAULT_PII_REDACTABLE_RULE_IDS`: chỉ phone/email/ssn) VÀO Port, đừng kế thừa policy lỏng.
- **Red-test-trước:** `test_pii_redaction.py::test_does_not_mask_a_zero_padded_product_code` (`"Mã 0123456789"` → unchanged; đỏ ở HEAD) + `::test_never_masks_a_substring_of_a_longer_digit_run` (đỏ) + `test_pii_boundary_does_not_corrupt_catalog_queries` (3/4 param đỏ: CCCD/VN_PLATE/CARD).
- **Blast:** migration (guardrail_rules row) + restart. Test đỏ: `test_pii_redactor_strategy/extended/redact_method` pin behavior FP hiện tại (đỏ hợp lệ). `test_over_broad_cmnd_rule_is_not_in_the_allowlist` — pin frozenset, xóa.
- **Đo-sau:** `redact_pii("đơn hàng 202512010001 giao chưa")` == unchanged; psql `select pattern from guardrail_rules where rule_id='pii_vi_phone' and record_tenant_id is null` = form `(?<!\d)…(?!\d)`; **Coverage `chinh-sach-xe` không giảm** (load-test, gate ARMED) — Coverage <0.95 = blocker.
- **Sacred:** domain-neutral ⚠→✅ (locale-scope); zero-hardcode ✅ (gate qua config); L9 ✅ (alembic).

#### 0.3 — PII rò ở boundary (chuyển redaction ra HTTP boundary) — [T2·L]
- **Bug (SỰ THẬT):** DB: `chat_histories role=user` khớp regex phone = **23/4742** (mẫu load-test — L4). `generate.py:697` inject history vào prompt ⇒ egress turn-2. **BOTH cause report SAI:** (1) "redact NEVER called" — DB có **13 rule**, 4 action=redact enabled, runtime predicate True, redact fires (nhưng armed hay chưa: `system_config` 0 row `%pii%` ⇒ `NullPiiRedactor`; `plan_limits.pii_redaction_enabled` NULL cả 6 bot ⇒ hook **double-gated OFF**). (2) "no boundary hook" — hook `_maybe_redact_chat_query` (`payload.py:25`) đã tồn tại tại `pipeline.py:272`, nhưng worker là **quá muộn**: `answer_question.py` ghi raw `content` vào `messages`(:92)/`jobs.payload`(:111)/outbox `ChatReceived`(:130) **trong HTTP request** trước enqueue. **SSE path không có hook nào** — `chat_stream.py:187/317/437` toàn `req.content` raw; `:437` persist `req.content` (KHÔNG phải `final_state["query"]`) = **nguồn 23 dòng rò**.
- **CONST/DB:** DB (`system_config.pii_redactor_provider` — 0 row; `plan_limits.pii_redaction_enabled` — NULL). Cần alembic seed.
- **Đã-từng-fix:** clean, không churn PII.
- **Fix (⚠ đề xuất gốc "worker là boundary" SAI):** boundary đúng = **HTTP route**. (1) [prereq 0.4] siết redactor. (2) **XÓA** `guard_input.py:60-76` (block redact) + import infra `:14` (**vi phạm Strategy+DI: `from ragbot.infrastructure...` trong orchestration** — sacred F5); guard_input giữ **flagging**. (3) promote `_maybe_redact_chat_query` → `shared/pii_universal.py::redact_chat_query` (HTTP không được import từ worker package; giữ re-export). (4) **chat.py** sau `registry.lookup` trước `AnswerQuestionCommand`: `content = redact_chat_query(req.content, bot_cfg=..., pii_redactor=container.pii(), ...)`. (5) **chat_stream.py** thay `req.content` ở **:187 (hash), :317 (graph query), :437 (persist)** — cả ba. (6) **arm**: alembic seed `pii_redactor_provider='vn_regex'`. Flag per-bot giữ default-False (product decision — surface owner, **không** flip lén).
- **⚠ L7:** dùng `resolve_bot_limit(bot_cfg,...)` đọc `plan_limits` trực tiếp trên `bot_cfg` (path-agnostic, giống `degeneration_action` đã làm), **KHÔNG** gate qua `pipeline_config` — vì SSE dùng builder test_chat, worker dùng builder chat_worker (3 builder khác nhau, đã kiểm chứng).
- **Red-test-trước:** `test_chat_stream_pii_boundary.py::test_sse_route_never_persists_raw_pii` (assert **bound-param** của INSERT `:437`, không phải log; đỏ ở HEAD **cả khi 2 gate ON** — chứng minh SSE không có hook) + test catalog-không-corrupt (chung với 0.4).
- **Blast:** migration + **restart mọi process** (`bootstrap.py:452` Singleton cache provider ở first-build — miss restart = đo no-op tưởng fix fail). Test đỏ: `test_guard_input_rewrites_the_query_on_a_redact_hit` (assert `getsource` — weak test, viết lại behavioral). Cache-key đổi (hash text masked) — miss 1 lần, tự lành. **23 dòng cũ KHÔNG được dọn** bởi code fix — cần purge migration riêng (RỦI RO §8).
- **Đo-sau (L4 exposure-conditional):** drive **CẢ SSE và async** route với `"Tên Lan, sđt 0901234567, mail a@b.vn"` (bypass_cache), scope SQL theo trace_id: phone=0 AND email=0 trên `chat_histories/messages/jobs.payload/request_logs` cho row **có PII ở input**; **đo TURN-2** (gửi follow-up, assert raw phone không xuất hiện ở prompt `generate.py:697`) — turn-1-only = PASS giả.
- **Sacred:** Strategy+DI ✅ (xóa import infra); #10 ✅ (mask input user ở boundary — CLAUDE.md bless "PII redaction TẠI HOOK LAYER"); L9 ✅ (alembic seed + purge).

**Đo đóng batch:** SSE+async, phone=0 & email=0 trên 4 sink cho row-có-PII, turn-2 sạch, Coverage chinh-sach-xe không giảm.

---

### B3 — Structured LLM resilience + cost (3.4 folds 3.2 → 3.3)

**Atomicity:** 3.4 nguyên tử; 3.3 sau (cùng file, transport-seam vs validation-seam).

#### 3.4 — structured path bypass semaphore/breaker/retry — [T2·L]
- **Bug (SỰ THẬT):** `structured_output_helper.py:437` gọi thẳng `litellm.acompletion` — không sem/breaker/retry. Router **không có** attr `_litellm_module` (grep 0) ⇒ `query_graph.py:1352 getattr(...,None)` fallback `import litellm`. 4 purpose volume cao nhất (understand/grade/decompose/generate) chạy trần. `record_failure` không bao giờ gọi ⇒ breaker mù.
- **CONST/DB:** không. **Đã-từng-fix:** forward-only (213b3d2 thêm setdefault num_retries=0 nhưng bỏ sót sem/breaker/retry). Không revert.
- **Fix (đề xuất A ĐÚNG, B SAI):** B (copy sem+breaker vào node) = breaker **thứ 2** split-brain. **A:** refactor `_complete_runtime_one` → thêm `complete_runtime_raw(cfg, messages, *, purpose, **kw) -> ModelResponse` chạy CÙNG guarded core; thêm vào `LLMPort`; thay param `litellm_module` của `call_with_schema` bằng injected `guarded_completion: Callable`; `_invoke_structured_llm_node` (query_graph.py:1424) truyền closure `llm.complete_runtime_raw(...)`. Repair-loop và transport-retry **orthogonal** (đừng gộp budget). Breaker-open → LLMError → `_safe_acompletion` catch → None (giữ degrade-silent), nhưng `record_failure` fires **trong router trước khi** swallow.
- **Red-test-trước:** `test_structured_path_uses_router_guard.py`: inject litellm fail N lần → assert (a) breaker OPEN sau threshold, (b) transient được retry (acompletion >1), (c) sem bound (max_concurrent=1, 2 concurrent → peak 1). Cả 3 đỏ ở HEAD.
- **Blast:** restart. **13 test file** inject `litellm_module=` stub → đỏ hợp lệ, đổi sang stub `guarded_completion`/`complete_runtime_raw`. **Sizing:** 1 sem nay gánh ~4× traffic trên lane `innocom` — verify `DEFAULT_PROVIDER_MAX_CONCURRENT` đủ lớn (nếu nhỏ → 500 đổi thành latency).
- **Đo-sau:** load-test inject provider-500; count `cb_state_transition` open quy cho structured purpose >0 (HEAD=0); structured emit retry; peak concurrent ≤ max_concurrent; báo completion-rate (không claim gỡ 500 tới khi có số before/after).
- **Sacred:** Port+DI ✅ (khôi phục boundary — tốt hơn getattr duck-punch); #10 ✅ (transport, không content); no-version-ref (đặt tên `complete_runtime_raw`, **không** `_v2`).

#### 3.4-cost (fold 3.2) — token/cost structured = $0 — [gộp vào 3.4]
- **Bug (SỰ THẬT DB):** `understand_query` 1514/1514 zero-tok $0; `grading` 322/322 $0 = **49.7%** call luôn-$0. **⚠ đề xuất 3.2 gốc SAI site:** `_emit_usage_sink` không có `messages` (chỉ điền completion ~2% volume). **Đúng:** một khi 3.4 route qua router (đã sở hữu `estimate_tokens_fallback` @:851/:1118), token estimate tự chảy — **3.2 standalone thành thừa, DROP**. Nếu 3.4 defer, mới ship 3.2 tại `_capture_so_usage` (query_graph.py:1394, closure có `messages`).
- **Đo-sau:** `select purpose, count(*) filter(where prompt_tokens=0 and completion_tokens=0), sum(cost_usd) from model_invocations where purpose in ('understand_query','grading') and started_at>ship group by 1` → zero-tok từ 100%→~0, cost>0.

#### 3.3 — validation failure 3 class — [T2·M]
- **Bug (SỰ THẬT journal):** 4579 `validation_failed` + 3857 `repair_retry`. Class A (3949 `extra_forbidden` key `query`) = **đã ship 5c4fdda** (`_accept_query_alias`) nhưng **restart 22:21 rồi ZERO structured traffic** ⇒ **UNVERIFIED**. Class B (168 SlotSchema EOF-truncation) + C (357 bare-literal grade).
- **CONST/DB:** CONST (`DEFAULT_SLOT_EXTRACTOR_MAX_TOKENS=400`). **Đã-từng-fix:** A single-touch 5c4fdda.
- **Fix (⚠ B gate `finish_reason==length` DEAD, C thiếu):** A = chỉ load-test verify. **B:** gate repair trên **parse-error** (json_invalid/EOF), nâng `DEFAULT_SLOT_EXTRACTOR_MAX_TOKENS` — nhưng **UNMEASURABLE tới khi 3.4-cost land** (completion_tokens=0). **C:** `{'grade':v}` wrapper chỉ cover 15/357; 342 là `GradeBatchOutput` envelope `{'grades':[...]}` — collapse single-verdict tại **grade NODE** (grade.py:270, có chunk list) áp verdict cho mọi chunk; validator `GradeOutput` extract leading-token wrap **chỉ** khi yes/no/partial (garbage vẫn fail loud). C là **cost-fix, không phải HALLU** (degrade hiện đã HALLU-safe).
- **Red-test-trước:** `test_grade_output_accepts_bare_literal` (`'partial'`, `'**partial**\n...'`) + `test_grade_output_rejects_non_literal` (`'maybe'` vẫn raise) + `test_no_repair_on_truncated_json` (finish_reason='stop', acompletion gọi đúng 1 lần — chứng minh gate length sai).
- **Blast:** restart. Test đỏ: `test_llm_schemas.py`, `test_grade_batch_schema.py` (hợp lệ). Merge-collide 3.4 (cùng file) — 3.4 trước.
- **Đo-sau (⚠ L4):** baseline PHẢI là run load-test post-fix mới (không phải journal 06-26 pre-fix); đo **net `intent=fallback` rate**, không raw event count (repair inflation: 4050 event / 1530 call).
- **Sacred:** #10 ✅ (map output-của-model lên schema, generate bypass call_with_schema).

**Đo đóng batch:** breaker thấy structured, token>0 understand/grade, GradeOutput nhận bare-literal, net fallback-rate giảm.

---

### B4 — Ingest re-ingest cluster (2.2 → 2.1 → 2.3 → 2.4 → 4.1 → 0.1-parity)

**Atomicity:** MỘT re-ingest event cho toàn bộ. Đo qua **worker path** (đã instrumented — StepTracker 7 step U1-U7, `document_worker.py:312-378`; "7 row" là recency, không phải thiếu telemetry — missing-lens F5 refute "ingest no telemetry"). `sync.py`/`test_chat` truyền `step_tracker=None` ⇒ **đo qua worker path only**. Attribute bằng SQL-delta riêng biệt (mỗi task 1 signature): 2.2=`parser_preserve count`, 2.1=`lexeme count`, 2.3=`intro substring`.

#### 2.2 — worker không truyền raw_bytes (parser registry dead trên canonical path) — [T1·M]
- **Bug (SỰ THẬT):** `document_worker.py:668-681` gọi `ingest()` **không** `raw_bytes`, **không** `file_name`; `ingest_core.py:317` gate `if raw_bytes is not None`. DB: `chunking_strategy` = recursive 689 + hdt 217, **0 parser_preserve, 0 table_row**. de89da8 verify trên path `sync.py` (có raw_bytes) — chưa từng chạy trên **canonical worker** (async outbox).
- **CONST/DB:** không. **Đã-từng-fix:** LOW (parser_preserve forward-only b45cadf/a66fc13; wiring raw_bytes chưa từng add).
- **Fix (⚠ đề xuất "raw_bytes" ĐÚNG nhưng THIẾU; "blocks" SAI):** "blocks" không tới parser_preserve (driven bởi `parser_row_chunks`, không phải blocks). Truyền `raw_bytes=_raw` **VÀ `file_name=_doc_name` VÀ `mime_type`** — raw_bytes một mình chưa đủ: worker truyền `title` (không ext), `GoogleSheetsParser.supports()` cần `mime==text/csv OR ext==.csv`. Guard `_raw` chỉ tồn tại trên nhánh URL-refetch; `local://` upload dùng stored TEXT (không có bytes gốc) ⇒ parser_preserve bất khả cho local (0 local doc trong corpus, nhưng limitation thật).
- **Red-test-trước:** `test_document_worker_parser_first.py`: mock google-sheet/CSV qua `process_document`, assert `kwargs['raw_bytes'] is not None AND kwargs['file_name'].endswith('.csv') AND mime_type=='text/csv'`. Đỏ ở HEAD.
- **Blast:** **reingest** 5 CSV/sheet doc (canonical `rechunk_document_by_id`, **không** psql) + **re-embed ~2000 row** (ESTIMATE: 906→~2900) + worker restart.
- **Đo-sau (⚠ L4 scope):** SQL scope `record_document_id IN (<5 doc>)`: `parser_preserve > 0` (từ 0), `table_row` xuất hiện, `entity_name ~ '^col_[0-9]'` **giảm** (counted, không "inspect").
- **Sacred:** #10 ✅ (ingest chunking); L9 ✅ (rechunk canonical).

#### 2.1 — VN segmentation ingest = index bloat, recall ~0 — [T2·M] · atomic
- **Bug (SỰ THẬT DB):** trigger index `COALESCE(content_segmented,content)`; **436/906** chunk drift. **⚠ headline "brand 4-vs-28" KHÔNG tái lập** — đo lại seg=21/unseg=21, `seg_uniquely_helps=0` (structural: `_` luôn `blank` trong ts). Segmentation **mất 21 lexeme junk, thêm 477 welded compound dead** (`275/40r21_dx640`). Đây là **simplification + de-bloat + tiết CPU ingest**, KHÔNG phải recall rescue.
- **CONST/DB:** DB (flag `vi_compound_segmentation_ingest_enabled`, alembic/admin-UI). **Đã-từng-fix:** double-pin test (P22 Option B) — contentious, không revert visible.
- **Fix (atomic — phải drop query-side CÙNG trigger):** alembic: `CREATE OR REPLACE FUNCTION update_chunk_search_vector` → `to_tsvector('simple',COALESCE(NEW.content,''))` + **reindex trong cùng migration**; xóa `pgvector_store.py:409/417` `segment_vi_compounds` (query-side) — nếu không multi-word brand/SKU query weld vào `davanti_275/40r21` không match index unsegmented; update `bootstrap_ddl_only_tables.sql:60` (fresh DB, ties 1.1); flip flag ingest false.
- **Red-test-trước:** DB-invariant `SELECT count(*) FROM document_chunks WHERE search_vector <> to_tsvector('simple',COALESCE(content,''))` = 0 (HEAD=436) + source-pin `segment_vi_compounds(query_text)` NOT in `hybrid_search`.
- **Blast:** **migration + reindex + restart.** Test đỏ hợp lệ: `test_bm25_symmetric_segment.py` **VÀ `test_p22_whitespace_symmetric.py`** (đề xuất gốc bỏ sót cái thứ 2 — cùng pin).
- **Đo-sau (⚠ L4):** **recall delta ~0, KHÔNG đo được** — nói to. Đo win được: lexeme `4759→4298` (−9.7%, measured), 477 welded compound biến mất, latency ingest giảm 50-300ms/chunk.
- **Sacred:** domain-neutral ✅↑ (gỡ underthesea VN khỏi retrieval path); L9 ✅ (alembic). **Merge-collide 3.6** (cùng block `hybrid_search` query-prep) — sequence.

#### 2.3 — table intro/footer bị drop (flag live-TRUE nhưng INERT) — [T1·S]
- **Bug (SỰ THẬT):** live `table_strategy=table_dual_index`; `_chunk_table_dual_index` (csv_chunker.py:357) **không có param header_footer**; dispatch `__init__.py:514` gọi không truyền flag ⇒ `region.pre/post` inert. Flag `table_csv_emit_header_footer_chunks_enabled=true` chỉ tác dụng cho `table_csv` (không phải strategy đang chạy). Fix-refix: `010y` set flag cho table_csv → `0209` flip strategy dual_index 18 ngày sau, orphan flag.
- **CONST/DB:** DB config (seed). **Đã-từng-fix:** YES (010y→0209 orphan). Precedent 2026-06-17 key:value trial revert (measured neutral).
- **Fix (⚠ INCOMPLETE):** wire flag tại dispatch `__init__.py:514` (đọc `get_boot_config` như table_csv:503-511); prepend `region.pre` vào **group chunk đầu** + append `region.post` vào **cuối** (không synthetic chunk inflation). Reuse constant sẵn có (30/30/3/3), **0 constant mới**.
- **Red-test-trước:** dispatch-level `test_chunk_table_csv_header_footer.py`: `smart_chunk(doc=intro+CSV+footer, table_strategy=table_dual_index)` → assert intro AND footer substring trong ≥1 chunk. Đỏ ở HEAD.
- **Blast:** reingest (ride B4). Test dual_index hiện GREEN (assert presence không count).
- **Đo-sau (⚠ F5-refix):** đo **recall prose bao quanh (non-aggregation)**, KHÔNG justify bằng aggregation/numeric coverage (revert 06-17 đã settle tầng này miễn cho aggregation). SQL `content LIKE %intro%` ≥1 row. **Nếu pre/post rỗng ⇒ metric=0, no-op ⇒ re-point upstream parser.**
- **Sacred:** #10 ✅; L6 (test live dispatch). **⚠ 2.2 partial-moot:** sau 2.2, excel/google_sheets đi row-shaped bypass `smart_chunk` ⇒ 2.3 **re-scope** về CSV-text / parser NOT in `_ROW_PRESERVE_PROVIDERS`. Doc đo phải là CSV non-row-shaped chọn **sau** khi 2.2 land.

#### 2.4 — entity_synonyms code (CHỈ nếu SEC.2 verdict = MERGE) — [T1·S] · rides B4
- Cherry-pick `bulk_insert` write + query ILIKE + `test_stats_index_entity_synonyms.py`/`test_stats_keyword_synonym_expand.py` từ integ branch; sửa comment sai `document_stats.py:198`. Đo: notation-mismatch query `265/50R20` khớp entity `265/50ZR20`. Nếu verdict=REVERT: drop migration + parser `aliases`.

#### 4.1 — coverage gate = anti-signal (lossless doc chấm 0.00-0.05) — [T2·M]
- **Bug (SỰ THẬT repro):** HDT lossless → `coverage_ratio 0.0000` (chunk mang prefix `[Chapter One]\n`); recursive → 0.0465 (prefix `## heading\n`); genuine 50% loss → 0.4995. **ANTI-SIGNAL** (lossless < catastrophic). Gate observe-only.
- **Fix (⚠ đề xuất `extract_structural_path` INCOMPLETE):** chỉ fix bracket HDT→0.9254; recursive/semantic **đứng yên** (prefix `## heading\n` không đụng, hybrid double-prefix). **Đúng:** strip **CẢ HAI** prefix trong vòng lặp (md-heading `^#{1,6} .+\n` THEN bracket) trước `find_dropped_numbers`(:869)/`check_chunk_gaps`(:890); move `ctx.content=content` từ :939 lên trên :869. **GIỮ observe-only** (HDT trần ~0.93 — heading relocate vào bracket path; enforce tol=0.02 = reject mọi HDT/legal). Measured: recursive 0.0465→**1.0000**, HDT →~0.93.
- **Red-test-trước:** `test_coverage_gate_lossless.py`: doc heading-bearing qua `smart_chunk(hdt)` + `(recursive)`, chấm ≥0.90/≥0.98; control 50%-loss ≤0.55. Đỏ ở HEAD.
- **Blast:** reingest (ride B4) + restart. **KHÔNG** test đỏ hợp lệ (36 coverage test không feed prefixed output — GREEN giả). **⚠ metric ẩn:** `char_coverage_ratio` chỉ ghi trong `if not _cov.ok` + cần ingest step_tracker — move set_metadata ra khỏi guard để luôn ghi.
- **Đo-sau:** psql `metadata_json->>'char_coverage_ratio'` post-reingest (worker path) ~1.0 recursive, ~0.93 HDT.
- **Sacred:** #10 ✅ (observe-only, đừng enforce); domain-neutral ✅ (strip structural). **⚠ FALSE-edge dropped:** missing-lens F4 xác minh `coverage.py` (check_chunk_gaps) LIVE — không phải dead; edge "4.2 xóa coverage.py ⇒ 4.1 moot" **BỎ**. Dead thật = `infrastructure/chunk_quality/`.

#### 0.1-parity (đã ship, re-check trong B4) — [T1·S]
- Thêm guard test `test_stats_transposed_measure_row_not_dropped.py` (permanent, chống land lần 4). Parity: 0.1 **KHÔNG đo-cô-lập được** nữa (rides B4 re-ingest, 2.2 đổi chunk count) — dựa unit guard, **đừng quote parity delta**.

**Đo đóng batch:** MỘT re-ingest; sau đó: 2.2 parser_preserve>0, 2.1 lexeme −9.7%, 2.3 intro substring ≥1, 4.1 coverage ~1.0, 2.4 notation-match. Attribute bằng SQL-delta riêng (không đo whole-corpus count — measurement F5).

---

### B5 — Fresh-DB seed (1.1) — atomic; block bởi B2 + SEC.2

**Điều kiện chạy:** **SAU B2** (0.3/0.4 siết pattern `pii_vi_phone` — nếu 1.1 trước, bake pattern-bad thành platform default vĩnh viễn) **VÀ SAU SEC.2** (đừng bless schema-không-code như entity_synonyms).

#### 1.1 — fresh DB không ingest được (98% seed ngoài alembic) — [T1·L]
- **Bug (STATIC-proven + prod SELECT):** `grep -cE '^(INSERT|COPY)' squashed_baseline.sql` = 0 (schema-only); chain active seed **5 key**; prod = **264** (kiểm chứng). Root: 9d2fee9 squash archive 278 migration gồm `seed_system_config`(49)+`010f_guardrail`. DDL sống, DML seed mất. **⚠ BLOCKER report bỏ sót:** `chat_swap_to_innocom.py:45,65` seed `ai_providers/models` bằng `INSERT...SELECT FROM same-table WHERE id=<openai>` — row nguồn ở history đã discard ⇒ fresh DB `ai_providers=0/ai_models=0` (prod 4/7, kiểm chứng) ⇒ resolver collapse NullObject. Seed 264+rule **không đủ**. `.env.example:73-75` ship `text-embedding-3-small/1536` vs `vector(1280)` ⇒ INSERT hard-fail.
- **CONST/DB:** DB (4 migration seed). **Đã-từng-fix:** whack-a-mole (3 migration re-add từng row lẻ). `init_system_config.py` = seed thứ 3, SAI (`:62 embedding_dimension=1536`).
- **Fix (⚠ INCOMPLETE — thiếu ai_providers/models):** 4 migration head-position (final-state values): (1) 264 key ON CONFLICT DO NOTHING; (2) 13 guardrail rule từ `DEFAULT_GUARDRAIL_RULES` (resolve drift `classic_*` vs prod `legacy_*` TRƯỚC); (3) **ai_providers(4)+ai_models(7) literal column-list** (không SELECT-from-self; **UUID chính xác**: zembed-1 `770cc668...`, openai `2b771241`, gpt-4.1-mini `aa25f11d` — 1 UUID sai = swap migration no-op lần nữa); (4) 32 language_pack. **DO NOTHING** khắp (prod no-op). Generator `gen_seed_migration.py` đọc prod (read-only) emit .py tracked. **Companion bắt buộc:** fix `.env.example` (1280 + `EMBEDDING_PROVIDER=zeroentropy`) + delete/regen `init_system_config.py`. **Defence:** boot-time assert `resolved embedding_dim == pgvector typmod` (giết cả bug-class).
- **⚠ Sacred F4:** generator đọc prod-snapshot **launder** giá trị out-of-band thành "tracked". Cross-check mỗi value với code SSoT + archive seed; seed từ **code SSoT nơi có**, prod chỉ nơi không có; flag value không khớp nguồn nào. Domain-audit re-run tại generation-time (đã audit: 2 hit benign — `boilerplate_by_language` lang-keyed, `zeroentropy_api_url` public).
- **Red-test-trước:** `test_fresh_db_can_ingest.py`: scratch DB → `alembic upgrade head` → assert (1) `cfg_dim==col_typmod` (đỏ: 1536/1024 vs 1280), (2) ai_models>0 (đỏ:0), (3) guardrail=13 (đỏ:1), (4) completeness `--strict` exit 0 vs **fresh DB**, (5) create tenant→bot→POST create→`count(document_chunks WHERE embedding IS NOT NULL)>0`.
- **Blast:** migration. PROD **zero blast** (DO NOTHING, no-op). Test đỏ: `test_config_completeness_baseline.py` (baseline shrink = proof; re-point **fresh DB**). `test_migration_0048_round_trip` đã skipif (pre-existing).
- **Đo-sau (⚠ L8 BLOCKER):** **CHỈ** đo bằng `test_fresh_db_can_ingest.py` xanh — cần **`CREATE DATABASE` privilege trong CI** (author bị read-only boundary chặn, chưa runtime-verify). **Chưa có ⇒ ship UNVERIFIED = cấm.** Prod row-count = anti-signal (no-op). Completeness-vs-prod xanh hôm nay trong khi fresh DB chết (wrong-denominator).
- **Sacred:** L9 ✅ (đây LÀ antidote sacred #7); domain-neutral ✅ (audited); no-version-ref ⚠ (seed `_classic_*` không `_legacy_*`).

**Đo đóng batch:** `test_fresh_db_can_ingest.py` xanh trên scratch DB CI.

---

### B6 — Config guards (1.3)

#### 1.3a — wire `check_config_completeness` vào CI (SHIP-NOW) — [T2·S]
- **Bug (SỰ THẬT):** `grep -rn check_config_completeness .github/` = **0 hit**; `README_DEVOPS.md:43` hứa "required CI step". Gate tồn tại, guard 0.
- **Fix:** thêm job (fresh Postgres + `alembic upgrade head` + seed + script, baseline-aware, exit-gate).
- **Red-test:** `test_config_completeness_wired.py` assert ≥1 workflow chứa literal `check_config_completeness` (đỏ ở HEAD).
- **Blast:** near-zero (baseline absorb; xanh first run). **Đo-sau:** grep ≥1 + CI log in "contract/seeded/unseeded". **⚠ target FRESH DB** (không prod — wrong-denominator).

#### 1.3b — SEED-vs-CONSTANT value-drift guard (sau 1.4 + 1.1, advisory-first) — [T2·L]
- **⚠ đề xuất `system_config[k]!=constant[k]` SAI** (constant = fallback thấp nhất, đỏ trên ~16 override cố ý). **Đúng:** parse 186 `_pcfg(state,"key",CONST)` call-site → map key→constant; load `SEED_CONFIGS` + values trong `squashed_baseline.sql`; assert seed==constant TRỪ `CONFIG_DRIFT_ALLOWLIST{key,reason,date,owner}`. Hard-gate CHỈ key pure-technical; còn lại advisory. Re-point `test_seed_paths_agree.py` về `squashed_baseline.sql` (không phải archive `_archive.../0020` — dead reference).
- **Blast:** đỏ ~16 key tới khi allowlist. **⚠ Đã-từng-fix HIGH (MMR):** `0209` migration CỐ Ý pin 0.88 chờ A/B (task 1.4) — allowlist entry MMR không seal tới khi 1.4 rule. Co-update baseline khi 0.2 retune CB constant.
- **Sacred:** L9 ✅ (guard chống chính drift #7).

---

### B7 — Governance / cleanup (CHẠY CUỐI)

#### 4.3 — ADR-0009 cho app-override guard family (sau 0.5) — [T2·M]
- **Bug (SỰ THẬT):** **7** answer-substitution path (kiểm chứng: guard_output.py `:121,:167,:260,:341,:394,:682`+grounding block) — report đếm 6, thiếu `grounding_confirmed_action=block`. `grep docs/adr` = 0 ADR. `numeric_fidelity:block` + `brand_scope:block` live trên `chinh-sach-xe`.
- **⚠ Sacred F1 (LIVE #10.3 VIOLATION — dẫn đầu):** 3/6 bot **rỗng** `oos_answer_template` (kiểm chứng: `111`, `123`, **`huybot`**). `_resolved_oos_template` (query_graph.py:707) fall through → `language_packs.refuse_message` = **text VN platform-authored** = đúng thứ #10.3 cấm ("empty string nếu bot không set"). `DEFAULT_GROUNDING_FAILURE_MODE=fail_closed` là default **platform-wide** ⇒ trên huybot/111/123, grounder fail/unwired **thay answer bằng text platform, mặc định, HÔM NAY**.
- **Fix (doc-only + invariant):** viết ADR-0009 **scope 7 path**; 4 cam kết (substitute bot-own oos; per-bot opt-in safe-default; owner-approval ghi TRONG ADR — retro-fill f22a808/67b82de; measured-FP precondition). **THÊM invariant cứng report bỏ:** substitution resolve **CHỈ owner-text** (`bots.oos_answer_template` hoặc per-rule `response_message`); rỗng → return `""`, **KHÔNG** fall through `language_packs`/`constants`. **Xóa/viết lại comment tự-bào-chữa `guard_output.py:402`** (viện dẫn #10 làm khiên trong khi override answer 9 dòng trên).
- **Red-test:** `test_guard_output_override_pins.py`: mọi guard default==observe; `DEFAULT_GROUNDING_FAILURE_MODE=='fail_closed'`; behavioral: bot rỗng template → substituted answer == `""` (không phải language-pack string).
- **⚠ Đo-sau (measurement F10):** `numeric_fidelity 0/84` = CI FP **<4.3%** (rule-of-three) — ghi n+CI trong ADR, **không** "0/84 = safe"; re-run post-B4. **Depends 0.5** (measured-FP degeneration).
- **Sacred:** doc-only, no code literal; L9 (block-flip qua admin-UI+alembic).

#### 4.2 — dead-code / lying-comment (safe subset — LAST) — [T3·M]
- **CHỈ ship:** (a) xóa `grade.py:92-111` stats-branch unreachable (routing divert stats→generate trước rerank); (d) `bot_limits.py:171` inline `0.35` → `DEFAULT_RERANK_CLIFF_GAP_RATIO` (constant đã tồn tại unused, zero behavior change); (e) **CHỈ** docstring `pgvector_store.py:4` m=16/ef=64 → m=32/ef_construction=200. **F4-expand:** xóa 0-ref audited-dead registry (`self_rag_router`, `proximity_cache`, `multi_agent_review`, `chunk_quality`, `hyde/registry.py`) — DEAD-CODE NOTICE 2026-06-03.
- **CẤM (đã kiểm chứng):** (b) rationale 11ms (L1 hit); (c) xóa condense/router (live fallback + pin); (e) 3 comment accurate. `zeroentropy` docstring + `llm_usage` scrub đã do bởi 5fd6ecd.
- **Blast:** restart. (a/d/e-docstring) **KHÔNG** break test. **⚠ merge CUỐI** — collide 0.2 (exclude `DEFAULT_CB_HALF_OPEN_MAX_CALLS` — 0.2 wire live), 1.4/0.5 (constants file), 4.3 (guard_output line-refs).

---

### SHIP-NOW singletons (0-dep, xem §6)
0.5, 1.4-UNIT1, 3.5, 3.6 — chi tiết §6.

#### 0.5 — degeneration guard false-positive (tokenizer đếm `|`) — [T2·S]
- **Bug (SỰ THẬT repro):** `degeneration.py:57 answer.split()` đếm `|` là word; table 12×3 → `top_token_ratio=0.4262 ≥ 0.40` → is_degenerate=True. CJK 200× → n_words=1 → **guard no-op** (whitespace-less script). `top_token_ratio` recall **=0** (trên bug#8 ttr=0.167 < 0.40; dwr/dtr mới bắt).
- **Fix (⚠ "strip OR drop-ttr" false dichotomy — cần CẢ HAI):** strip-only chết feature-matrix (ttr=0.45); drop-ttr-only chết table 60-row (dwr=0.143). (1) `_tokens()` strip `[|`*#>~_=\[\]]` + filter alnum; (2) bỏ clause ttr khỏi is_degenerate OR (giữ RETURN key — guard_output:150 log + test assert); xóa `DEFAULT_DEGENERATION_TOP_TOKEN_RATIO_MAX`.
- **Red-test:** `test_markdown_pipe_table_not_flagged` + `test_feature_matrix_not_flagged` (discriminator, đỏ dưới strip-only) + `test_long_pipe_table_not_flagged` (discriminator, đỏ dưới drop-ttr-only) + recall guard bug#8. CJK **task riêng** (đừng bundle).
- **Blast:** restart. **0 test đỏ** (10 test hiện pass, replay fix vẫn pass). **Đo:** **KHÔNG đo được live** (0/1683→0/1683). Chỉ TDD + synthetic arming (bot block-mode, bypass_cache). CẤM 60Q làm signal.
- **Sacred:** #10 ✅↑ (giảm substitution); zero-hardcode ✅ (xóa constant).

---

## 5. ĐƯỜNG GĂNG (critical path)

**Chuỗi cứng dài nhất (depth 5):**
```
SEC.2 (triage) ─┐
                 ├─► 1.1 ──► 1.3b (value-guard)
0.4 ──► 0.3 ────┘
```
Mỗi cạnh là **correctness gate**: `0.4→0.3` (arm-an-toàn), `0.3/SEC.2→1.1` (đừng bake bug-pattern / schema-không-code lên mọi fresh DB), `1.1→1.3b` (đo đúng mẫu số). 1.1 mang **rủi ro ngoài cao nhất**: cần `CREATE DATABASE` trong CI (hiện **chưa có** — L8 blocker).

**Task unblock nhiều nhất: `2.2`** — root cause của symptom `col_N`/stats-fabrication mà 0.1/3.x/4.x quan sát downstream; gate cả cụm B4 (2.1, 2.3, 2.4, 4.1, 0.1-parity). Á quân: `3.4` (moots 3.2, gate 3.3); `SEC.2` (gate 1.1 + 2.4).

**Thứ tự chạy đề xuất:** Ship-now (§6) ∥ SEC.1 → B1 → B2 → SEC.2 → B4 → B5 → B3 (bất kỳ lúc sau B1) → B6 → B7.

---

## 6. CÓ THỂ SHIP NGAY HÔM NAY (0-dep, 0 measurement-risk)

| Task | Vì sao an toàn | Ship contract |
|---|---|---|
| **SEC.1** IDOR fence | Hoạt động **bất kể RLS**; fix đã viết+test trên branch. **Hành động giá trị nhất toàn plan.** | cherry-pick + `test_idor_write_fence.py` xanh |
| **0.5** degeneration | Pure function, 0/1683→0/1683 (0 runtime delta), TDD. **Land trước 4.2** (constants file). | 3 red-test (2 discriminator) xanh |
| **1.4-UNIT1** | Xóa `test_default_constant_aggregation_loosens_threshold` (1 RED ở HEAD) + fix comment MMR. 0 runtime. **KHÔNG** đụng threshold/map. | 43 passed / 0 failed |
| **3.6** dense NFC | Latent (0 live NFD), rebase trên prewarm-refactor 5fd6ecd. Ship `_embed_query` normalize **+ mirror `_prewarm`** cùng lúc. | `test_embed_query_nfc.py` (NFD→NFC) đỏ→xanh |
| **1.3a** CI wiring | Baseline-aware, near-zero blast, xanh first run. | grep ≥1 + CI job chạy |
| **3.5** cache-hash | Latent/dormant (không có runtime guardrail mutation). Append-only ⇒ key rule-less byte-identical. Hash **content-based**, KHÔNG loader counter (reset restart, desync). | `test_ruleset_change_moves_cache_version` |

**Đừng vội:** mọi thứ B2/B4/B5 (measurement-coupled), B7 (cuối), và **3.1** (CẤM).

---

## 7. CHƯA ĐO ĐƯỢC (không đo được ⇒ không ship — L8)

| Task | Vì sao chưa đo | Tiền-điều-kiện instrumentation |
|---|---|---|
| **1.1** | fresh-DB failure static-proven, không runtime (author bị read-only chặn `CREATE DATABASE`) | **CI `CREATE DATABASE` privilege** (hard blocker) |
| **0.2** | HEAD **không** log gì ở transition OPEN; Prometheus gauge mù flap ms | thêm event `cb_state_transition` (là PHẦN của fix) |
| **4.1** | `char_coverage_ratio` chỉ ghi trong `if not ok`; DB có 7 ingest row (recency, không phải thiếu telemetry — worker path CÓ StepTracker) | move set_metadata ra khỏi guard + đo **worker path only** (là phần của fix) |
| **3.3-B** | slot completion_tokens=0 (blind) | **3.4-cost land trước** (route qua router có estimate) |
| **0.5** | 0/1683 live (mẫu load-test không có table query) | chỉ TDD + synthetic arming; CẤM 60Q signal |
| **3.6** | 0 NFD trong 6527 row (mẫu ASCII/NFC harness; iOS/macOS IME = NFD) | synthetic NFD/NFC pair, bypass_cache |
| **3.5** | dormant (không có runtime guardrail CRUD) | inject ruleset delta cố ý |
| **2.1 recall** | seg_uniquely_helps=0 structural ⇒ recall delta ~0 không chứng minh được | chỉ đo lexeme/latency/bloat, **không** claim recall win |

**Lưu ý telemetry ingest (missing-lens F5 — REFUTE "ingest no telemetry"):** canonical worker path đã instrumented (7 step U1-U7); "7 row" là **recency** (traced ingest 07-07 SAU khi corpus ingest 07-06). B4 **đo được** qua worker path. `sync.py`/`test_chat` truyền `step_tracker=None` ⇒ đo worker-only. Granularity `metadata_json` mỏng (4.1 xử lý).

---

## 8. RỦI RO CÒN LẠI (plan này KHÔNG fix)

1. **RLS cutover chưa hoàn tất** (owner decision) — `DATABASE_URL_APP`→`ragbot_app` + drop `RAGBOT_ALLOW_SUPERUSER_RUNTIME`. SEC.1 (repo fence) là lớp bù; lớp 2 (RLS) là hardening riêng, cần owner.
2. **ADR-0007 = động cơ vòng lặp 0.1** (owner + recover git history). `9416f4d` revert `5db7922` **cùng ngày, body RỖNG** — lý do kill không recover được từ git; ADR-0007 (Proposed) muốn rebuild `document_service_index_numeric` (grep src/alembic = 0, chưa schedule). **CẤM advance tới khi khôi phục lý do kill.**
3. **23 dòng PII cũ** trong `chat_histories` — code fix không dọn; cần purge migration riêng (alembic/admin-UI, **không** psql DELETE). "phone=0 dòng mới" ≠ "PII đã gỡ khỏi system". PII đã gửi gateway turn cũ = ngoài tầm kiểm soát.
4. **CJK/Thai degeneration no-op** (task riêng, latent — 6 bot locale=vi) — guard câm cho whitespace-less script (multilingual-no-vocab violation). File riêng, không bundle 0.5.
5. **1.4-UNIT2 (MMR collapse)** — A/B factoid chưa chạy; giữ map. Collapse cần alembic bump DB global 0.88→0.98 + A/B reproduce methodology 9f93804 **trên factoid** (không reuse distinct-section N=10).
6. **Cross-tenant fairness coupling CB** (0.2 làm cắn mạnh hơn) — tenant A fail fast-fail tenant B (breaker keyed provider_code, không tenant-scoped). Pre-existing, task riêng.
7. **Shared-breaker / dangling failover FK** — 45/46 binding `record_fallback_model_id` NULL; 1 set = dangling FK. 0.2 làm OPEN "giữ" mà không có failover. Fix FK qua alembic (RỦI RO riêng).

---

## 9. ĐỊNH NGHĨA DONE (per-task contract)

Một task = DONE khi **đủ 6**:
1. **Red-test viết TRƯỚC**, chứng minh ĐỎ ở HEAD (quote output), XANH sau fix.
2. **Runtime evidence** (không unit-call/turn-1-log): DB row / injected-fault event / synthetic-input, theo cột "Đo-sau". Gắn nhãn SỰ THẬT vs GIẢ THUYẾT.
3. **Out-of-band step khai báo + thực hiện**: restart/seed/reingest nêu rõ (9/11 fix cần ≥1 mà code diff không chứa).
4. **Mẫu số đúng** (L4): không dùng count "live" cache-bypass làm bằng-chứng-an-toàn; scope query theo doc/trace/tenant.
5. **Sacred pass** 11/11 explicit (không "tổng quát đạt"); L9 (0 psql content-write).
6. **Không đo được ⇒ KHÔNG tuyên bố PASS** — trạng thái "CHƯA verify — cần [X]".

Batch = DONE khi query/load-test đóng-batch (cột "Đo đóng batch") cho số thật.

---

## 10. TRẠNG THÁI

| ID | Task | Batch | Tier | Effort | CONST/DB | Migration | Reingest | Restart | Trạng thái |
|---|---|---|---|---|---|---|---|---|---|
| 0.1 | drop `_COUNT_COL_TOKENS` | B0 | T1 | S | — | — | — | — | ☑ SHIPPED (a58ac8d) |
| — | guard test transposed | B4 | T1 | S | — | — | — | — | ☐ |
| SEC.1 | IDOR write-fence | B-SEC | T1 | M | code | — | — | ✅ | ☑ SHIPPED (2026-07-14 batch) |
| SEC.2 | triage split-brain 102-file | B-SEC | T2 | M | — | — | — | — | ☐ (block 1.1) |
| 0.2 | CB flap + retune (3-file atomic) | B1 | T2 | M | CONST | — | — | ✅ | ☐ |
| 0.4 | redactor precision + locale-scope | B2 | T1 | M | DB | ✅ | — | ✅ | ☐ |
| 0.3 | PII → HTTP boundary | B2 | T2 | L | DB | ✅ | — | ✅ | ☐ |
| 3.4 | structured → router (+fold 3.2) | B3 | T2 | L | — | — | — | ✅ | ☐ |
| 3.3 | grade validator + slot budget | B3 | T2 | M | CONST | — | — | ✅ | ☐ |
| 2.2 | worker raw_bytes+file_name | B4 | T1 | M | — | — | ✅ | ✅ | ☐ (hub) |
| 2.1 | drop VN segmentation (atomic) | B4 | T2 | M | DB | ✅ | reindex | ✅ | ☐ |
| 2.3 | table intro/footer (re-scope post-2.2) | B4 | T1 | S | DB | — | ✅ | — | ☐ |
| 2.4 | entity_synonyms code (nếu MERGE) | B4 | T1 | S | — | — | ✅ | ✅ | ☐ (SEC.2 gate) |
| 4.1 | coverage gate strip-both-prefix | B4 | T2 | M | — | — | ✅ | ✅ | ☐ |
| 1.1 | fresh-DB seed (4 migration +env) | B5 | T1 | L | DB | ✅ | — | ✅ | ☐ (CI blocker) |
| 1.3a | wire completeness CI | B6 | T2 | S | — | — | — | — | ☑ SHIPPED (2026-07-14 batch) |
| 1.3b | seed-vs-constant value-guard | B6 | T2 | L | — | — | — | — | ☐ (sau 1.4+1.1) |
| 1.4-U1 | delete stale MMR test + comment | now | T1 | S | — | — | — | — | ☑ SHIPPED (2026-07-14 batch) |
| 1.4-U2 | MMR collapse | §8 | T1 | S | DB | ✅ | — | ✅ | ☐ DEFER (A/B) |
| 0.5 | degeneration tokenizer | now | T2 | S | CONST | — | — | ✅ | ☑ SHIPPED (2026-07-14 batch) |
| 3.5 | cache-guard content-hash | now | T2 | M | — | — | — | ✅ | ☑ SHIPPED (2026-07-14 batch) |
| 3.6 | dense-query NFC (+prewarm mirror) | now | T1 | S | — | — | — | ✅ | ☑ SHIPPED (2026-07-14 batch) |
| 4.3 | ADR-0009 guard family (7-path) | B7 | T2 | M | — | — | — | — | ☐ (sau 0.5) |
| 4.2 | dead-code safe subset | B7 | T3 | M | — | — | — | ✅ | ☐ (LAST) |
| **3.1** | cliff back-fill | — | — | — | — | — | — | — | **🚫 CẤM (FIX_WRONG)** |
| 3.2 | structured cost standalone | — | — | — | — | — | — | — | **⊘ FOLD vào 3.4** |

**Files tham chiếu chính (re-anchor ở HEAD 5fd6ecd):** `src/ragbot/infrastructure/repositories/document_repository.py:98-130` · `src/ragbot/application/services/retry_policy.py:159-236` · `src/ragbot/infrastructure/resilience/_base.py:76-78` · `src/ragbot/orchestration/nodes/guard_input.py:14,60-76` · `src/ragbot/interfaces/http/routes/chat_stream.py:187,317,437` · `src/ragbot/interfaces/workers/document_worker.py:668-681` · `src/ragbot/shared/degeneration.py:57` · `src/ragbot/orchestration/nodes/guard_output.py:121,167,260,341,394,682,402` · `src/ragbot/orchestration/query_graph.py:707,1394,1424` · `alembic/versions/20260624_stats_index_entity_synonyms.py` · branch `integ-260624-wave1`.