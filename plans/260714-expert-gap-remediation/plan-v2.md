> # ⚠️ ĐÃ BỊ THAY THẾ bởi plan-v3.md
> Tính trên baseline `71682a2`. Chứa các claim đã bị L5 bác (structured '2 round-trip', '2271/ngày', RBAC 13 route, MMR alembic no-op).
> Dùng: [plan-v3.md](plan-v3.md). Giữ làm lịch sử.

---

# [T1-Smartness + T2-CostPerf] PLAN v2 — Expert-gap remediation

**Ngày**: 2026-07-14 · Nhánh `fix-260623-ingest-expert` · HEAD `71682a2`
**THAY THẾ** `plan.md` (v1) — v1 **lỗi thời**: nó chưa biết (a) DB không tái tạo được, (b) structured-output tốn 2 round-trip, (c) 2,271 LLM call bỏ qua router, (d) 13 route ghi/xóa không RBAC.

**Evidence**:
- `reports/TRUTH_VERIFICATION_20260713.md` — 29 mục vs code/git/DB (**12/29 cáo buộc SAI**)
- `reports/CONFIG_FLAG_HISTORY_AUDIT_20260714.md` — flag · config drift · lịch sử · structured-output

---

## 0. LUẬT (vi phạm = reject)

| # | Luật | Vì sao |
|---|---|---|
| **L1** | Khai **`CONSTANT hay DB?`** | DB **thắng** constant. 78/171 constant đã **CHẾT** ở runtime |
| **L2** | Khai **`ĐÃ TỪNG FIX CHƯA?`** | F7 build+revert body rỗng · cliff floor 3 lần · stats-serve 7 patch/12 ngày · coverage ship-mất-vớt |
| **L3** | Số liệu hành vi **PHẢI từ runtime** | B4: đọc constant ra "57.7%", runtime **0.0%** |
| **L4** | **Không đo được ⇒ không ship** | B2 thiếu telemetry · F4 thiếu failing test |
| **L5** | **1 fix = 1 lần đo** | Gộp = không quy được nhân quả |
| **L6** | **Tái dùng pattern đã có** | B1 back-fill = pattern `mmr_filter` 002-D |
| **L7** | **CẤM p95 trên mẫu cắt cụt** | `5c4fdda` thất bại vì survivorship bias |
| **L8** | Grep theo **METHOD/SYMBOL**, xử lý **dotted key** | A4 (`_idem`) · `decomposer.enabled` |
| **L9** | ⭐ **Hỏi: route test có đi cùng đường prod không?** | 3 chỗ đã lệch: `raw_bytes` · `heuristic_intent_enabled` · `guard_output_parallel_enabled` |
| **L10** | ⭐ **Flag live=true ≠ code đọc nó** | ~30 flag **TRƠ** |
| **L11** | ⭐ **Migration `UPDATE` KHÔNG phải seed** | 9 migration no-op im lặng trên DB rỗng |

---

## 1. 🚫 DANH SÁCH CẤM — đừng đào lại

| Mục | Vì sao CẤM | Bằng chứng |
|---|---|---|
| **B4** gỡ `factoid` khỏi rerank skip-list | no-op (0/741) + **vỡ ~10 assertion**/3 file | `intent_skip_set = 0` |
| **A4** xóa/wire `IdempotencyService` | **Đang chạy đủ** cả chat + ingest. Xóa = **phá retry-safety BE-to-BE** | 6 call site `self._idem` |
| **A9** bật `reflect` | **Tái tạo regression ĐÃ ĐO: 3.57s/turn** | `routing.py:201` — **GIỮ NGUYÊN COMMENT** |
| **A9** bật `graph_retrieve` | Tắt 3 tầng, **không có KG**. Bật = LLM call **per chunk lúc ingest** | `graph_rag_entity_extraction_model = ""` |
| **A8** bật `critique_parse` bằng flag | **KHÔNG BẬT ĐƯỢC BẰNG FLAG** — cần owner thêm rule vào `system_prompt`. **Sacred #10 CẤM app inject** | `critique_parser.py:1-21` |
| **F1d** sửa `test_crossdoc_reconcile.py:68` | Test **ĐÚNG** — ghim chống brand-conflation (ADR-0008 B5) | 2 row = 2 sản phẩm khác nhau |
| **F1c** khôi phục `superseded_by`/`authority_score` | **Giàn giáo chưa từng có logic.** Recency đã có `documents.updated_at` + `version` | migration 0010: *"drops **wired-but-unused** columns"* |
| **C1** segment query-side (theo audit cũ) | **GIẢM RECALL** (`&` → `<->` phrase) | index **KHÔNG** segmented |
| **ING-F1** stock-as-price allowlist | **Owner ĐÃ revert (`6796cd9`)**, chấp nhận là **KNOWN limitation**, workaround per-bot `custom_vocabulary["column_roles"]` | commit body |
| **22 flag Class-B2** (`neighbor_expand`, `self_rag_critique`, `parent_child`, `autocut`…) | **Wiring ĐÚNG, chỉ OFF.** Xóa flag = **xóa tính năng đã ship**. Node **CÓ** trên LangGraph, gate nằm **TRƯỚC** span → trông chết trong `request_steps` | |
| **`decomposer_enabled`** | Trông orphan. **KHÔNG.** Key live **dotted** `decomposer.enabled`, live `true` | `query_decomposer.py:151` |

---

## 2. 🔴 ĐỢT 0 — CHẶN (làm ngay, song song)

### T0.1 🚨 SEED COVERAGE — **DB KHÔNG TÁI TẠO ĐƯỢC** ⭐ **CHẶN MỌI PHÉP ĐO**

> **Nếu không làm cái này trước, MỌI A/B đều vô nghĩa** — dev/CI và prod chạy **hai hệ thống khác nhau**.

| | |
|---|---|
| **Đo thật** | `CREATE DATABASE` trắng → `alembic upgrade head` (40 revision) → **5 row** `system_config`. PROD: **264**. **Thiếu 259** |
| **Cơ chế** | `squashed_baseline.sql` = `pg_dump --schema-only`, **0 INSERT**. 9/12 migration dùng `UPDATE … WHERE key=…` → **no-op im lặng trên bảng rỗng** |
| **Hệ quả** | DB fresh: `jina`/1024/`jina-reranker-v3` vs PROD `zeroentropy`/1280/`zerank-2` → **INSERT 1024-dim vào `vector(1280)` = HARD FAIL**. `chunking_policy` vắng → chunk sai **im lặng**. `guardrail_rules`: **1 rule thay vì 13** |
| **Sacred** | 🔴 **VI PHẠM RULE #7 — 98%** (259/264 key ngoài alembic) |
| **Đã biết lúc ship** | `STATE_SNAPSHOT.md:675` dưới **"🔴 RISKS"**: *"Squash là SCHEMA-ONLY — không có DATA"*. **Ship anyway** |
| **Fix** | **1 alembic** `INSERT … ON CONFLICT DO NOTHING` **toàn bộ 264 key** với giá trị LIVE + **12 guardrail rule** từ archive `20260516_010f`. **No-op trên prod theo cấu tạo**, parity đầy đủ trên DB mới |
| ⚠️ | **PHẢI `ON CONFLICT DO NOTHING`** — không thì **đè mất override của operator trên prod** |
| **Kèm** | Cho `scripts/init_system_config.py` **nghỉ hưu** (seed 158 key, **không có trong deploy doc** → **thứ tự deploy quyết định model stack**). Trỏ `test_seed_paths_agree.py` **khỏi `_archive_pre_squash`** (nó đang canh migration **không bao giờ chạy** = **niềm tin giả**) |
| **Test đỏ trước** | dựng DB trắng → `alembic upgrade head` → assert `count(system_config) == count(prod)` |

### T0.2 🚨 RBAC — **13 ROUTE GHI/XÓA KHÔNG CÓ GATE**

```
PUT    /admin/config/{key}                     ← ĐỔI BẤT KỲ system_config NÀO
PUT    /admin/api-keys/{provider_code}         ← GHI API KEY
DELETE /admin/api-keys/{provider_code}/{label}
POST   /bots · PATCH /bots/{id} · DELETE /bots/{id}
PUT    /bots/{id}/{ch}/max-history · PATCH /bots/{id}/vocabulary
POST   /bots/{id}/{ch}/documents · POST .../documents/upload
DELETE /documents/{doc_uuid}
POST   /reinit-bots · POST /validate-link
```
`require_min_level = 0` trên **cả 5 file**. Chỉ `bot_insights_routes.py` có gate (và nó **không có route ghi**).

| | |
|---|---|
| **Mỉa mai** | `PUT /admin/config/{key}` chính là con đường sacred rule #7 gọi là *"admin UI có audit_log"* — **nhưng KHÔNG có RBAC** |
| **Giảm nhẹ** | CLAUDE.md: `test_chat` **không expose external**, chặn ở **gateway/network**. Bảo vệ hiện tại = **tầng mạng**, KHÔNG phải tầng app |
| ⚠️ **ĐÃ TỪNG FIX** | **`cc9880c`** (nhánh `worktree-agent-a98b47eb8ed705bb5`) = **RBAC cho đúng mấy route này + test 229 dòng** → **CHƯA MERGE** |
| **Fix** | **Khôi phục `cc9880c`**, đừng viết lại. `git show cc9880c` |

### T0.3 🚨 STRUCTURED OUTPUT — 2 ROUND-TRIP CHO MỌI REQUEST

| | |
|---|---|
| **Root cause** | Gateway **PHỚT LỜ `response_format`** → call 1 trả **văn xuôi** → validate fail → repair retry **đưa schema vào prompt** → **3/3 valid**. Cả `json_object` lẫn strict `json_schema` đều no-op |
| **Chi phí đo được** | `understand_query`: **1,530 call · avg 10,314ms** · 112 repair-retry + 122 validation-failed/ngày |
| **Grade** | latency thật **min 2799ms** (conc 1) / **min 3852ms** (conc 8) → **cap 2.0s nằm DƯỚI cả min** → 100% timeout là **tất yếu số học**, không phải treo. Cap 3.0s cứu **0/30** |
| ⚠️ **ĐÃ TỪNG FIX** | 🚨 **`5c4fdda` (hôm qua) THẤT BẠI** — "p95 2.56s" tính trên **mẫu chỉ gồm những lần THẮNG cap 2.0 cũ** (survivorship bias). Dữ liệu thật: **max thành công = 1996ms**, sát rạt trần |
| **Fix #1** ⭐ | **Đưa schema vào prompt NGAY CALL ĐẦU** cho provider không cưỡng chế `response_format`. `_build_repair_messages` **đã làm đúng thế này rồi** ở vòng 2 — chỉ là làm sớm hơn 1 vòng. → cắt **~1,530 round-trip/ngày**; `understand` 10.3s → ~3.5s |
| **Fix #2** | `ai_models.supports_json_mode = false` cho `openai/claude` (**đang khai SAI**) — qua **alembic**, không psql |
| 🔒 **Sacred #10** | **AN TOÀN** — đây là prompt **nội bộ pipeline**, không phải prompt answer của bot owner |
| **KHÔNG** | **CẤM nâng `DEFAULT_GRADE_TIMEOUT_S` tiếp.** Đó là **fix sai tầng**. Kể cả 5s cũng chỉ mua 1 node tốn 2 round-trip (~7s) để **không sinh ra gì** |

### T0.4 🚨 2,271 LLM CALL/NGÀY BỎ QUA ROUTER

```
structured_output_helper.py:437  →  litellm_module.acompletion(...)   ← GỌI THẲNG
dynamic_litellm_router.py:442/447/28  →  semaphore · circuit-breaker · retry
                                          ↑ nằm trong _complete_runtime_one — structured KHÔNG đi qua
```

| step | n | qua router? |
|---|---|---|
| `generate` | 1751 | ✅ (cost $4.61, token ghi đủ) |
| `understand_query` | **1530** | ❌ (cost **$0**, token **0**) |
| `grade` | **741** | ❌ |
| `rerank` | **741** | ❌ |

**Semaphore 6 + rate-CB + retry budget em ship tuần này CHỈ bảo vệ `generate`.**
`understand` + `grade` **nã gateway không giới hạn** → nhiều khả năng **chính chúng gây ra** 94 `InternalServerError` + p50 3.3s→5.8s dưới tải.

**Fix**: cho structured path **đi qua router**.
**Kèm**: `_emit_usage_sink` gọi `estimate_tokens_fallback` → thu hồi **3,012 step** đang có `cost_usd = 0`.

### T0.5 🔧 E1 — cache hit bỏ qua `guard_output` (lỗ hổng an ninh live)

`_cache_route` (`routing.py:58-59`) → `persist` → `END`. `guard_output` chỉ nằm trên `critique_parse → guard_output` (`:3027`). Cache key (`cache_port.py:90`) **không có guardrail**. TTL **3600s**.
→ Owner thêm rule BLOCK → nội dung bị cấm vẫn phục vụ **1 tiếng, không qua guard**.
**Fix**: (1) route cache hit **QUA `guard_output`** (guard bỏ qua được thì không phải guard) · (2) hash ruleset vào `_compute_bot_cache_version`.

### T0.6 🔧 Test đỏ tại HEAD

`test_per_intent_caps.py::test_default_constant_aggregation_loosens_threshold` → `assert 0.98 > 0.98` **FAIL**. Xử lý cùng T2.2.

---

## 3. ĐỢT 1 — CỨU CÔNG VIỆC ĐÃ MẤT (rẻ nhất, giá trị cao nhất)

> **Đừng viết lại thứ đã có.** `git show <sha>` là lấy được.

### T1.1 🔥 F7 / ADR-0007 — **TÌM VÌ SAO BỊ REVERT, TRƯỚC KHI BUILD LẠI**

```
5db7922  06-29  feat(stats): F7 attribute-generic stats index — every numeric column queryable
                5 file, 312 insertion, + test 176 dòng
9416f4d  06-29  Revert "..."     body: [This reverts commit 5db7922...]   ← RỖNG
```
`docs/adr/0007` **đề xuất CHÍNH XÁC cái đã bị vứt**, status **Proposed, CHƯA LÀM**.

> 🔥 **"Fix đi fix lại" ở dạng thuần khiết nhất.** Team **sắp build lại từ đầu** thứ đã build và vứt **không lý do**.
> **HÀNH ĐỘNG GIÁ TRỊ NHẤT TOÀN BÁO CÁO**: hỏi owner **vì sao revert** → nếu không có lý do chính đáng → **`git cherry-pick 5db7922`**, đừng viết lại.

### T1.2 Triage `integ-260624-wave1` — **5,885 dòng mắc kẹt**

| nhánh | chứa gì | HEAD |
|---|---|---|
| `cc9880c` | **RBAC route ghi/xóa** + test 229 dòng | ❌ **MẤT** → **T0.2** |
| `4b94c28` | **IDOR write-fence 4 repo** + test 261 dòng + RLS force-parity | ❌ **MẤT** |
| `be94f58` | reranker fix · retrieve fan-out · BM25 soft-delete · **6 file test (600+ dòng)** | ❌ **MẤT** |
| `5d6fb6d` | token-ledger rollup + admin-metrics RBAC (3 file test) | ❌ **MẤT** |
| `548e1c5` | stats `entity_synonyms` + **4 file test** | ⚠️ **SPLIT-BRAIN** |
| `dcdc55a` | B-FORMAT table row-split: **7 file, 489 dòng, 2 file test (270 dòng)** | ❌ → thay bằng `7e8dd38` **1 file, 8 dòng, 0 test** |

🔴 **SPLIT-BRAIN**: `alembic/versions/20260624_stats_index_entity_synonyms.py` **CÓ trên HEAD, sẽ chạy trên mọi DB** — nhưng **nhánh sinh ra nó mắc kẹt, 4 file test biến mất**. **Schema ship, code+test thì không.**

🔴 **`dcdc55a`**: bản hiện tại **YẾU HƠN 60×** bản đã tồn tại. `git show dcdc55a`.

**Fix**: triage từng nhánh **TRƯỚC KHI XÓA**. Ưu tiên: `cc9880c` (RBAC) → `4b94c28` (IDOR) → `548e1c5` (test cho migration đang chạy) → `dcdc55a` (B-FORMAT).

---

## 4. ĐỢT 2 — A1 & CORPUS (đổi chunking → **vô hiệu mọi phép đo trước nó**)

> ⚠️ 3 fix này **đều cần re-ingest/reindex → GỘP 1 LẦN**. Xong phải **ĐO LẠI BASELINE**.

### T2.1 🔥 A1 — worker không truyền `raw_bytes` → parser registry **CHẾT trên prod**

| | |
|---|---|
| **Chain** | `ingest_core.py:317` gate `if raw_bytes is not None` · worker `document_worker.py:514` **tự parse rồi flatten** `"\n\n".join(...)`, gọi `ingest(content=full_text)` — **KHÔNG raw_bytes** |
| 🎯 **Vì sao trốn lâu** | **UI test nội bộ TRUYỀN `raw_bytes`. API production B2B thì KHÔNG.** → dev thấy đúng, khách nhận chunking phẳng. **(Pattern L9)** |
| **Runtime** | **0/583 chunk** được row-parse. 5 doc CSV đều `recursive` |
| ⚠️ **ĐÃ TỪNG FIX** | `de89da8` (07-01) fix `col_N` gate trên `_parser_row_shaped(parser_row_chunks)` — **luôn `None` trên worker → fix ĐÓ CŨNG CHẾT.** 3 doc ingest **5 ngày SAU** fix vẫn `recursive`. Doc **nêu đích danh trong commit message** vẫn chưa fix |
| **Tác động T1** | `col_N` corruption = **lớp bug bịa số** mà ADR-0008 đang đuổi |
| **Fix** | worker truyền `raw_bytes=_raw`, **bỏ flatten** |
| **Blast** | 5 doc / 583 chunk. Re-ingest idempotent. Chunk count tăng (63 hàng → 63 chunk) |

### T2.2 F5 — intro/footer bảng: flag **LIVE-TRUE nhưng TRƠ** + **drift DB**

Strategy live = `table_dual_index`, mà `_chunk_table_dual_index` **không nhận** `header_footer_enabled` và cắt `lines[header_idx : last_data_idx+1]` → **`pre`/`post` bị loại trừ VỀ CẤU TRÚC**.
⚠️ **Fix aggregation recall (`20260612_0209`) đã ÂM THẦM regress feature này** — quên port logic pre/post.
🔴 **6/6 test XANH** vì chúng gọi **thẳng** `_chunk_table_csv_with_context`, **không qua dispatch live**.
**Fix**: port `region.pre`/`post` vào `_chunk_table_dual_index` (~10 dòng) · **test phải chạy qua DISPATCH LIVE** · seed `chunking_policy` (đã gộp vào **T0.1**).

### T2.3 C1 — **GỠ segmentation ở INGEST** (🔴 ngược 180° so với audit cũ)

| | |
|---|---|
| 🔴 **Audit cũ SAI** | Postgres coi `_` là **`blank` = phân cách** → **XÓA** underscore. `to_tsvector('simple','chăm_sóc da mặt')` → `'chăm' 'da' 'mặt' 'sóc'`. **ZERO lexeme từ ghép VN trong index.** Query hiện tại **ĐANG ĐÚNG** |
| **Underscore chỉ sống khi** | token có kèm `.` hoặc `/` (Postgres đổi `asciiword` → `file`). VD `dr._x`, `265/60r18_at` |
| 🔥 **Bug THẬT (ngược chiều)** | Đo live: 1 brand token → **28 chunk chứa nó** · index hiện tại tìm ra **4** · không segment thì **28**. **24/28 BẤT KHẢ TRUY CẬP** |
| **Đối chứng ngược** | chunk mà segment **GIÚP** tìm ra (không segment thì mất) = **0**. → **segmentation = lỗ ròng tuyệt đối** |
| **Quy mô** | **436/906 chunk** lệch tsvector |
| **Fix** | (1) trigger index `NEW.content` thay `COALESCE(NEW.content_segmented, …)` · (2) **xóa** 2 call `segment_vi_compounds` query-side (`pgvector_store.py:409,417`) · (3) **nghỉ hưu** `test_bm25_symmetric_segment.py` (nó **ghim bug**) |
| **Blast** | ⚠️ **REINDEX tsvector toàn corpus**. **Không** cần re-ingest |
| ⚠️ | **KHÔNG merge gate của `be94f58`** — gate cho call sắp xóa là vô nghĩa |

### T2.4 ⚠️ **RE-INGEST + REINDEX 1 LẦN → ĐO LẠI BASELINE**

---

## 5. ĐỢT 3 — QUERY-SIDE (**1 fix = 1 lần đo**)

| # | Task | Tóm tắt |
|---|---|---|
| **T3.1** | **B1 cliff back-fill `min_keep`** | **18.1%** query → LLM chỉ 1 chunk (134/741). `min_keep` chỉ gác **1/3 lối ra**, và là lối bắn **0.4%**. ⚠️ **FIX-REFIX: floor tune 3 lần** (0.15→0.05→0.2), `test_cliff_floor_calibrated.py` **đang canh trần 0.20** → 🔒 **KHÔNG ĐỘNG SỐ**. Fix = **đổi THỨ TỰ** (back-fill), **tái dùng pattern `mmr_filter`** (002-D) |
| **T3.2** | **B3 ALEMBIC MMR 0.88 → 0.98** | 🔴 **Constant ĐÃ ĐÚNG (0.98)** — sửa `constants.py` = **0 tác dụng**. DB ghim 0.88 bằng alembic **ĐÃ APPLY**. Số 0.98 **ĐÃ ĐƯỢC ĐO** (`9f93804`). **PHẢI update CẢ 2**: global **và** `by_intent.factoid` (map per-intent **thắng**). Runtime: factoid **4.77→3.19 (−33%)** |
| **T3.3** | **C2 NFC ở `_embed_query`** | Dense query **không normalize** → query NFD (macOS/iOS) embed lệch không gian. Fix **trước cache lookup**. **KHÔNG** nhét vào embedder adapter (vi phạm domain-neutral) |
| **T3.4** | **F1 multi-doc provenance** | `_key = (_name, price)` → **cùng giá GỘP, khác giá CẢ HAI SỐNG** = ngược conflict-resolution. 💡 **Fix RẺ**: `_DOC_LIVE_JOIN` **ĐÃ CÓ** ở `stats_index_repository.py:57`, mọi SELECT **đã trả `record_document_id`** → **provenance cách 1 CỘT SELECT, 0 migration, 0 re-ingest**. + phát `stats_price_conflict` event (hôm nay **im lặng hoàn toàn**) + bỏ `score: 1.0` (**ADR-0008 B4**). ⚠️ **FIX-REFIX CAO NHẤT: 7 patch/12 ngày** → **PHẢI đóng khung là mở rộng ADR-0008**. 🔒 **CẤM hardcode "mới nhất thắng"** — app cấp **DỮ LIỆU**, LLM **QUYẾT ĐỊNH** |

---

## 6. ĐỢT 4 — FLAG & CONFIG CLEANUP (theo CLASS, **không quét mù**)

### T4.1 ☠️ Lớp **INERT** — sửa hoặc xóa (**đây là lớp GIẤU BUG**)

| flag | LIVE | vấn đề |
|---|---|---|
| **`circuit_breaker_enabled`** | `true` | 🔴 **KILLSWITCH GIẢ** — chỉ có trong **docstring**; `FailoverOrchestrator(` **không bao giờ khởi tạo**. **Với tay lấy lúc sự cố = không có gì xảy ra** |
| **`embedding_text_strategy`** | `"auto"` | 🔴 **"auto" KHÔNG có trong registry** {`prefix_plus_raw`,`raw_only`,`field_selective`,`null`} → **luôn rơi về NullObject**. Toàn bộ registry **bất khả tiếp cận** |
| `table_csv_emit_header_footer_chunks_enabled` | `true` | TRƠ → **T2.2** |
| `adapchunk_layer5_cross_check_enabled` | `true` | `apply_cross_check` gọi **vô điều kiện**; flag chỉ đọc khi `strategy is None` |
| `tenant_rate_limit_enabled` · `docs_only_strict_enabled` · `understand_query_cache_enabled` · `cache_stampede_singleflight_enabled` · `robust_json_parser_enabled` · `callback_ssrf_guard_enabled` · `parser_heading_detection` · `parser_table_detection` | | **0 reader** hoặc reader không bao giờ chạy |

### T4.2 🔴 **TEST-HARNESS ≠ PROD** (pattern lần 2 & 3)

```
heuristic_intent_enabled       →  worker=0   test_chat=2
guard_output_parallel_enabled  →  worker=0   test_chat=2
```
→ Override per-bot cho 2 key này **bị PROD BỎ QUA IM LẶNG**.
**Fix**: thêm vào `workers/chat_worker/pipeline_config.py`. **Và thêm 1 test ghim: 2 whitelist phải KHỚP NHAU.**

### T4.3 Flag **sai TÊN KEY**

| constant | key code thật sự đọc |
|---|---|
| `cr_prompt_cache_enabled` | `contextual_retrieval_prompt_cache_enabled` |
| `enriched_prefix_persist` | `enriched_prefix_persist_in_content` |
| `self_rag_enabled` | `self_rag_critique_enabled` |
| `rerank_intent_whitelist_enabled` | DTO lồng `rerank_intent_whitelist.enabled` |
| `diff_reingest_enabled` | `diff_based_reingest_enabled` (chỉ log `not_implemented`) |

### T4.4 CLASS A — **INLINE 42 flag** (live ON, 0 override → nhánh OFF chết)

Ưu tiên theo lượng code xóa được:
1. **5 flag structured-output** (`structured_output_enabled` + `grade_/understand_/reflect_/decompose_use_structured_output`) → xóa nhánh free-text-parse ở **5 node** (`generate.py:726`, `grade.py:172`, `reflect.py:86`, `understand.py:205`, `decompose.py:45`). ⚠️ **GIỮ `generate_use_structured_output`** — live `false` **và** force-disable trên SSE → **2-mode THẬT**
2. **5 flag `pipeline_parallel_*`** → xóa 5 nhánh serial + **gỡ đăng ký node `condense_question`/`router`** (`query_graph.py:2923`). ⚠️ **KHÔNG xóa `_router_route`** — vẫn sống

### T4.5 CLASS B1 — **XÓA 15 orphan constant + code chết**

`api_key_failover_enabled` · `embedding_failover_enabled` · `embedding_semantic_chunk_enabled` · `multi_vector_enabled` · `auto_merge_retrieval_enabled` · `retrieval_bm25_fallback_enabled` · `grounding_numeric_overlap_enabled` · `grounding_check_truly_parallel` · `cag_mode_enabled` · `proposition_llm_decomp_enabled` · `proposition_use_llm` · `tenant_bypass_rate_limit` · `blocks_api_enabled` · `modality_rerank_enabled` · `mmr_use_cosine`
→ xóa được luôn `shared/auto_merge_retrieval.py` (~273 dòng, **không import ở đâu**) + `infrastructure/reranker/_modality_boost.py`

### T4.6 🗑️ **BỀ MẶT CODE CHẾT ĐÃ KIỂM CHỨNG SẴN**

```
66 file có "DEAD-CODE NOTICE — 2026-06-03"  ·  6,477 dòng
12 registry comment 100%  (cag · chunk_quality · convo_summary · hyde · proximity_cache ·
                           self_rag_router · sentence_similarity · tenant_model_tier ·
                           text_normalizer · tokenizer · tools · multi_vector)
```
Header mỗi file: *"AST import-graph reachability scan … **Safe to delete physically; defer to operator decision**"*
⚠️ **GIỮ `application/services/hyde_generator.py`** — HyDE **THẬT** (`bootstrap.py:598`). Chỉ `infrastructure/hyde/*` là bản trùng chết.

### T4.7 **DRIFT GUARD** (Phase 1 của config)

**24 key L1 DRIFT** · **54 constant SHADOWED (đã chết)** · **72 row DB rác** · **87 constant còn chịu tải**
🔴 **KHÔNG có guard nào canh giá trị**: `grep "== DEFAULT_" tests/ scripts/` → **0 hit**

**Fix**: CI/startup guard `system_config[k] != constant[k]` → **FAIL LOUD**, trừ khi có trong `CONFIG_DRIFT_ALLOWLIST` **kèm lý do + ngày**.
⚠️ **CI sẽ ĐỎ NGAY trên 24 key** — **đó là mục đích**. Phải **phân xử từng cái** (0.88 hay 0.98?), **không đóng dấu cho qua**.

> **Chỉ SAU T0.1 (seed) + T4.7 (guard), phương án "xóa hẳn constant" mới KHẢ THI.**
> Nếu chọn nó → **phải sửa CLAUDE.md** (rule zero-hardcode đang bắt buộc constants.py là SSoT).

---

## 7. ĐỢT 5 — DỌN + QUYẾT ĐỊNH

| # | Task |
|---|---|
| **T5.1** | **D1 XÓA 3 comment NÓI DỐI** (`pgvector_store.py:4,226-238,257`) — root cause HNSW **KHÔNG phải** opclass/cột/filter (agent **phản chứng**: bỏ hẳn filter, planner **vẫn** Seq Scan). Thật là **cost model** (906 row: seq 285 vs HNSW startup 5475). **Planner ĐÚNG. Không mất recall.** ⚠️ `plans/20260709` **đã triage đúng** nhưng **quên xóa comment** → chẩn đoán sai sống mãi. + `SET LOCAL hnsw.iterative_scan='relaxed_order'` (no-op hôm nay, lan can cho ~17k chunk) |
| **T5.2** | **D3 dim-guard per-vector** — dim check **chỉ ở `health_check` (warmup)**, không ở hot path. Wire dim = **ctor 1280**, `spec.dimension` **không bao giờ đọc**. ⚠️ **audit `ai_models.dimension` TRƯỚC** |
| **T5.3** | **D2 cột `embedding_model`/`embedding_dim`** — `_check_embed_model_consistency` so **config với config** → sau swap **chúng KHỚP** → **bất lực về cấu trúc**. Thiệt hại live **REFUTED** (906/906 sau swap) nhưng **chỉ biết nhờ MAY MẮN**. Fix: so model query với **model GHI TRÊN ROW** |
| **T5.4** | **F2/F3 xóa nhánh + node chết** — 2 coroutine vô dụng (**2 row `request_steps` + 1 resolver round-trip/query**, không phải 3 row). `condense_question`+`router` **chết trên MỌI query >2 tháng**. ⚠️ **GIỮ `_router_route`** |
| **T5.5** | **D4 sửa locator coverage gate** — 🔴 thủ phạm **KHÔNG phải `proposition`** (không live). Là **`_chunk_hdt` (217 chunk live)** prepend `[path]\n` → `find()==-1` → **ratio 0.0000 dù không mất gì**. ⚠️ **CẤM tune `tol`** — ratio **vô nghĩa về cấu trúc**. Fix: trả prefix **qua METADATA** |
| **T5.6** | **E3 — 1 ADR, 0 code, ~1-2h.** 🔴 Cáo buộc **REFUTED — vu oan code TỐT**. Default là `observe`, không phải block. Commit bị vu oan (`c0c0dea`) để lại comment **đúng chuẩn CLAUDE.md**. **Phát hiện THẬT: 8 commit guard / 7 ngày, 0 ADR** — bề mặt app-override **nới rộng từng bot một**, "owner-approved" **chỉ trong commit message**. Fix: **1 ADR** phủ cả họ |
| **T5.7** | **E2 — ADR-0009 + 🔒 GIẾT gate-theo-NGÀY-SINH.** 4/6 bot đang bị XML-wrap, **không owner nào set** — bật **chỉ vì ngày tạo row bot**. ⚠️ đổi default = đổi prompt 4 bot live → **PHẢI A/B**. 🔒 **KHÔNG THƯƠNG LƯỢNG: prompt của bot KHÔNG BAO GIỜ được phụ thuộc NGÀY SINH** |
| **T5.8** | **B2 — telemetry TRƯỚC, fix seam SAU.** Strategy **chỉ log structlog**, `metadata_json` null 902 row → **không đo được**. **Bước 1: persist `chunk_strategy`. Bước 2: đo. Bước 3 mới fix** |
| **T5.9** | **A7 A/B `neighbor_expand`** (**CHỈ SAU T2**) — **+0 LLM call**, chi phí **1 SQL round-trip batched**. Thí nghiệm T1 tốt nhất |
| **T5.10** | **A2 `rrf_round_robin`: ĐO rồi WIRE hoặc XÓA.** Rule#0: **chưa có bằng chứng runtime** nào cho thấy entity-starvation đang hại answer. **Không wire mù** |
| **T5.11** | **A3 XÓA `null_embedder`** — registry **đã degrade an toàn** (`registry.py:93` → embedder THẬT). Bản commented **RAISE** → **vi phạm hợp đồng Null-Object của CLAUDE.md**. **Wire nó vào = CÓ HẠI** |
| **T5.12** | **F4 failing test TRƯỚC** rồi mới flip atomic protect |

---

## 8. 🛡️ COMMENT PHẢI BẢO VỆ — **CẤM "dọn dẹp"**

Git bị xóa (repo re-init 17/06, project bắt đầu 15/04). **Một số quyết định CHỈ còn sống trong comment.**

| file:line | Ghi lại |
|---|---|
| `nodes/routing.py:201` | reflect bắn **2×/turn, phí 3.57s** (req 9cf611b5) |
| `ingest_stages_enrich.py:232` `:445` | **bão O(n²)**; **TỒN TẠI 2 bản CR**; *"ĐỪNG bật lại vì tưởng 'nhiều context hơn'"* |
| `ai_config_repository.py:39` | hardcode `None` → **LiteLLM âm thầm fallback `OPENAI_API_KEY` cho MỌI provider** |
| `dynamic_litellm_router.py:469` | **236 fail, ZERO lần CB mở** → CB phải dùng **rate** mode |
| `_10_rbac.py:54` | grounding async chiếm hết slot → **p95 24-37s** |
| `query_graph.py:2679` | append raw chunk → **COVERAGE 1.00→0.90** |
| `_06_llm_defaults.py:131` | `pii_vi_cmnd` **cố ý loại** — pattern khớp **GIÁ** (9 chữ số) |
| `ingest_stages.py:751` | preserve path → **1 chunk 74KB** → **cụm over-refuse V13** |
| `_00_app_env_taxonomy.py:218` | `ef_search` 100→64; **dòng `= 100` comment lại là LỊCH SỬ, không phải rác** |

*(~40 comment — danh sách đầy đủ ở `reports/CONFIG_FLAG_HISTORY_AUDIT_20260714.md` §9.)*

> **Một PR "dọn comment" sẽ xóa sạch 2 tháng đo đạc.**

---

## 9. ĐỊNH NGHĨA DONE

Mỗi task ✅ khi đủ **4 điều**:
1. **Red test trước** — fail tái hiện bug, **trước** khi sửa
2. **Fix tối thiểu** — mọi dòng trace ngược về task
3. **Đo sau** — số runtime thật, **1 fix = 1 lần đo**
4. **Khai đủ 3 cột** — `CONSTANT hay DB?` · `ĐÃ TỪNG FIX CHƯA?` · `Blast radius`

**Quality Gate 11/11 + sacred rule** trước mỗi commit.

---

## 10. TRẠNG THÁI

| Đợt | Task | Status |
|---|---|---|
| **0 CHẶN** | T0.1 seed(264+13rule) · T0.2 RBAC · T0.3 schema-in-prompt · T0.4 router · T0.5 cache-guard · T0.6 test đỏ | ⬜ |
| **1 CỨU** | T1.1 F7 hỏi-vì-sao-revert · T1.2 triage 6 nhánh mắc kẹt | ⬜ |
| **2 CORPUS** | T2.1 A1 raw_bytes · T2.2 F5 table · T2.3 C1 unsegment · T2.4 re-ingest+baseline | ⬜ |
| **3 QUERY** | T3.1 B1 · T3.2 B3 · T3.3 C2 · T3.4 F1 | ⬜ |
| **4 FLAG/CFG** | T4.1 INERT · T4.2 test≠prod · T4.3 sai-tên-key · T4.4 inline 42 · T4.5 xóa 15 · T4.6 6477 dòng chết · T4.7 drift-guard | ⬜ |
| **5 DỌN/QĐ** | T5.1–T5.12 | ⬜ |
