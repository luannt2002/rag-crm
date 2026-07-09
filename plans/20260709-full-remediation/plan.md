# TECH-DEBT REGISTER + REMEDIATION PLAN — 18-flow audit · 2026-07-09

> Nguồn (mọi item có evidence `file:line`/SQL): `reports/AUDIT_500Q_PART1_ANSWERS.md`,
> `reports/AUDIT_500Q_PART2_ANSWERS.md` (18 section, verify 96/14/1), `reports/CURRENT_TRUTH_20260709.md`.
> Phân tích 5-dạng-1-gốc: xem cuối file. **6 fix do_now đã ship** (commit `764f559`) — KHÔNG lặp ở đây.

## Câu trả lời "đã đề xuất xử lý HẾT nợ kỹ thuật chưa?"

**Có — cho toàn bộ debt mà audit 18-luồng tìm ra** (register bên dưới: P0×2, P1×5, P2×11, P3×n, P4×2 + CHƯA-VERIFY×4). **Nhưng thành thật (rule#0)**: "tìm ra hết debt tồn tại" là điều KHÔNG chứng minh tuyệt đối được — đây là pass code-truth toàn diện nhất tới nay (point-in-time 2026-07-09). 3 khoảng cần lưu ý: (a) mục **CHƯA-VERIFY** chưa đo hết; (b) chương trình **ADR-0008 domain-coupling** chạy song song (P0-2 giao với nó); (c) debt tích lũy tiếp theo thời gian. → Register này là "sổ nợ" sống, cập nhật khi có finding mới.

## Method (mọi fix bắt buộc)
- **Red-test-first** → fix ĐÚNG TẦNG → verify (test + runtime/measure) → 0 regress. Không ship mù (đã revert 2 lần).
- **Sacred**: DB content chỉ qua alembic tracked (#7); KHÔNG app-inject/override answer (#10); domain-neutral; 0 secret literal; 4-key identity; HALLU=0.
- **Tier**: P0 nguy hiểm → P1 drift/reproducibility → P2 attic dead-code → P3 quality (measure_first) → P4 deferred-external.

## Phase 0 — anchor baseline (ĐÃ CHẠY 2026-07-09)
**`pytest tests/unit`: 49 failed / 6714 passed / 30 skipped.** Suite KHÔNG xanh 100% từ TRƯỚC.
- **2 fail là REGRESSION của 6-fix hôm nay → ĐÃ SỬA** (commit `e5913cb`): `test_cliff_floor_calibrated` (pin 0.05→0.2) + `test_document_worker_bytesniff` (mock get→stream). Xác nhận bằng worktree parent `d350dc0` (cả 2 pass ở parent). *(Bài học: verify targeted-only KHÔNG đủ — phải full-suite trước khi tuyên bố "no regression".)*
- **47 fail PRE-EXISTING** (xác nhận fail cả ở parent) = debt thật, map vào register:

| Failing test (count) | = Register item | Ghi chú |
|---|---|---|
| **`test_multibot_ingest_canary` (25)** | **⬆ NÂNG P0-3** | property-based: ingest **rớt dòng âm thầm** trên random domain = sparse-drop, ADR-0008 gốc. **Correctness bug thật** (mất data), không phải attic |
| `test_callback_delivery_client_reuse`+`callback_dispatcher`+`callback_retry`+`callback_negative_paths` (14) | **P1-6 (mới)** | webhook delivery (`infrastructure/delivery/callback_delivery.py`) — "expected N retry posts got 0". Cần điều tra: delivery client hỏng hay test drift |
| `test_domain_neutral_guard` (2) | P0-2 | price 138>127, brand |
| `test_pipeline_config_batch` (1) | **P1-7 (mới)** | `grade_chunk_preview` KeyError — commit `485ef25` comment dead-key nhưng quên sửa test |
| `test_no_version_ref_grep` (1) | **P1-8 (mới)** | ceiling vượt (`test_retrieval_tuning_z2.py:48` version-ref) |
| `test_generate_intent_max_tokens`·`test_chat_worker_config_batch`·`test_per_intent_caps`·`test_audit_pass2_repro` (4) | **P1-9 (mới, triage)** | chưa root-cause từng cái |

### P0-3 · Ingest sparse-drop — rớt dòng âm thầm (25 test đỏ)  🔴 correctness
- **Vấn đề**: `test_multibot_ingest_canary::test_invariant_random_domain_no_silent_row_drop[3..24]` FAIL — property-based invariant chứng minh ingest **drop dòng** khi cell trống trên domain bất kỳ (không chỉ 1 bot). Gốc: sparse-storage bỏ empty cell (`document_stats.py` — S1-A late-binding table, khớp ADR-0008).
- **Fix**: đây là **chương trình ADR-0008** đang chạy (`feedback_domain_neutral_structure_manifest`). Đúng tầng = data/ingest (không phải sysprompt). Manifest per-file + shape-typing thay sparse-drop.
- **Test**: `test_multibot_ingest_canary` xanh cho mọi random domain (N+1-th bot invariant).
- **Risk**: cao — đụng ingest core; measure_first (re-ingest + đối chiếu DSI).

---

## P0 — NGUY HIỂM (làm trước, evidence-verified)

### P0-1 · Injection guard EN-only → tiếng Việt LỌT  ✅ DONE (commit `87d55e9`)
> **ĐÃ FIX + VERIFIED**: rule `prompt_injection_vi` (non-classic, block, input) vào SSoT `_default_patterns.py` + alembic `seed_prompt_injection_vi_260710` (idempotent, platform-default NULL tenant). Runtime: pure-VN injection → `blocked` via prompt_injection_vi ALONE; normal VN ("hướng dẫn"+"bỏ qua bước") → `answered` 0 flag. Unit: 8 match / 7 no-FP + 92 guardrail pass. Chi tiết cách làm dưới đây (giữ lại làm reference):


- **Vấn đề**: `guardrail_rules` (DB, alembic 010f) regex prompt_injection **chỉ tiếng Anh**: `ignore\s+(previous|above|prior)\s+instructions?`, `you\s+are\s+now`, `system\s*[:>]`… Câu VN "bỏ qua hướng dẫn trước đó" / "quên hết chỉ dẫn phía trên" → **KHÔNG match**. Served qua `local_guardrail.py:121 get_default_compiled("prompt_injection")`, chạy `:697 detect_prompt_injection`.
- **Gốc rễ**: rule seed EN-only, chưa cover ngôn ngữ tenant (VN). Tầng = **guardrail rule (DB)**, KHÔNG phải code.
- **Fix (đúng tầng, sacred#7 + domain-neutral)**: alembic migration thêm **pattern VN** vào platform-default `prompt_injection` rule (pattern là NGÔN NGỮ, không phải brand → domain-neutral OK). Ví dụ: `bỏ qua|phớt lờ|quên (hết|đi)\s.*(hướng dẫn|chỉ dẫn|chỉ thị|lệnh)(\s+(trước|phía trên|bên trên))?`, `bạn (bây giờ|giờ) là`, `đóng vai`. ON CONFLICT giữ tenant override.
- **Test**: red-test `test_injection_vn_patterns.py` — compiled regex MATCH 6-8 câu VN injection + KHÔNG match 6-8 câu VN thường (false-positive gate). Rồi curl `/api/ragbot/chat` câu injection VN → guard block (runtime). Đo false-positive trên 1 batch câu hỏi VN thật.
- **Risk**: medium — regex quá rộng → chặn nhầm câu thường. → bắt buộc false-positive test.

### P0-2 · Domain-neutral guard ĐỎ (kiến trúc nhiễm ngành)  🔴 architecture
- **Vấn đề**: `pytest tests/unit/test_domain_neutral_guard.py` **2/2 FAIL**. price-coupling **138 > baseline 127** (11 ref "price" mới), brand **3 > baseline 0** (3 brand literal mới). HC02 REFUTED: brand literal rải nhiều file hơn khai (`query_graph.py:353/386/2445/2463/2498`, `retrieve.py:276-279`, `guard_output.py:213/292/293`, `constants/_21`).
- **Gốc rễ**: 2 nhánh. (a) brand = comment/docstring lẫn tên brand (scrub được ngay). (b) price-first-class = engine hardcode `price_primary|parse_money_vn|PRICE_BUCKETS_VND|query_by_price_range` — đây là **ADR-0008 territory** (structure-typing thay price-coupling).
- **Fix**:
  - Ngắn hạn (P0): **scrub 3 brand literal** khỏi comment/docstring (generic hóa) → brand về 0. Test: guard brand-check green.
  - Trung hạn (giao ADR-0008): 11 price-ref mới → generic hóa (field-by-shape) HOẶC nâng baseline CÓ JUSTIFY nếu là dùng hợp lệ. **KHÔNG nâng baseline mù.**
- **Test**: `test_domain_neutral_guard.py` 2/2 green (hoặc baseline điều chỉnh có lý do ghi rõ). Verify: grep brand = 0, price-ref audit từng cái.
- **Risk**: brand-scrub low; price-coupling = architectural, phối hợp ADR-0008.

---

## P1 — DRIFT / REPRODUCIBILITY

| ID | Vấn đề (evidence) | Gốc/tầng | Fix | Test | Risk |
|---|---|---|---|---|---|
| **P1-1** | **RLS INERT** — probe `postgres` + `SET app.tenant_id='0000'` thấy 6 bot; `ragbot_system` role MISSING; `DATABASE_URL_APP` unset; `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` (Part-1 T11/T16) | ops/DB role | Tạo role `ragbot_system`+`ragbot_app` (alembic/DDL) → set `DATABASE_URL_APP` → bỏ superuser-runtime. Cô lập thật thành 2 lớp (RLS + app WHERE) | probe app-role → cross-tenant BỊ CHẶN runtime | **cao** — cần cred ops; sai = app mất DB. Staged. |
| **P1-2** | comment-vs-code flag drift: parallel rewrite/cache comment "OFF" nhưng const True (`_11:289-290`); `pipeline_parallel_cache_understand` comment OFF, DB True (FL03 REFUTED: narrative sai nhưng value đúng) | comment stale | Sửa comment khớp code (WHY-only). KHÔNG đổi value | grep comment vs const | thấp |
| **P1-3** | `bypass_token_check` **6/6 TRUE** (Part-1 T14) | config intent | **Verify intent trước**: revenue-feature (paid bypass) hay lỗi? Nếu default nên false → alembic sửa per-bot | check bot config semantics | thấp (điều tra trước) |
| **P1-4** | max-chars 4-state drift: code `500_000` / HTTP-inline `2_000_000` (hardcode) / DB `2000000` / comment (UP03) | zero-hardcode | Reconcile 1 nguồn (constants) + seed alembic; gỡ inline `2_000_000` | grep 4-state = 1 value | thấp |
| **P1-5** | `length_limit 8000` **UNREACHABLE** — schema cap `content=2000`/test`4000` trước (TR02) → guard chết | dead guard | Hoặc hạ const về reachable, HOẶC gỡ guard + doc "schema là cap thật". Quyết định | test guard reachable hoặc removed | thấp |

---

## P2 — ATTIC (dead-code verified 0-caller · dọn dần, low-risk)

> Nguyên tắc: mỗi item **xác nhận lại 0-caller** (grep) → xóa/attic → `pytest` xanh → import OK. KHÔNG xóa test-harness (sacred). Vài item là **quyết định "wire hay xóa"** (T3).

| ID | Dead item (evidence) | Fix | Test |
|---|---|---|---|
| **P2-1** | `router`/`condense_question`/`decompose` nodes = **0 request_steps**/18,940 rows (TR12; `router.py:22`,`condense_question.py:79`,`decompose.py:30`) — chết sau merge | Xác nhận 0-caller runtime → gỡ node khỏi graph + file (hoặc attic) | graph assembles, pytest xanh, 1 câu/intent vẫn trả đúng |
| **P2-2** | `CragGraderPort` (Batch/PerChunk/Null+registry) wired `bootstrap.py:435` **KHÔNG BAO GIỜ invoked** (GR01) — grade node tự viết inline | **Quyết định**: wire Port vào grade node (dùng) HOẶC gỡ wiring+registry dead | grade node vẫn chạy; nếu wire → parity test |
| **P2-3** | `smart_chunk_atomic` def `chunking/__init__.py:653`, **0 call-site** (DC02) | Xóa def + comment "Wave B2 will wire" | pytest chunking xanh |
| **P2-4** | `text_normalizer/registry.py` **toàn bộ commented** (TR03); `DEFAULT_TEXT_NORMALIZER_PROVIDER="null"` | Xóa module chết. **Nếu** accent-VN-norm có giá trị retrieval → measure_first rồi mới revive | import OK; retrieve 1 câu VN không regress |
| **P2-5** | `check_token_cap` (`tenant_token_meter.py:276`) **0 caller** → monthly cap KHÔNG enforce (TR03/AU) | **Quyết định**: WIRE (enforce cap — đây là FEATURE gap) HOẶC gỡ nếu cap không phải yêu cầu sản phẩm | nếu wire → test cap chặn khi vượt |
| **P2-6** | Cascade routing **no-op**: `low==high==default=="openai/claude"` (TR13; `generate.py:403`) | Gỡ cascade code HOẶC cấu hình model khác nhau (cost tiering thật) | flag off = no-op verified |
| **P2-7** | Greeting fast-path: 3 config seed (`skip_understand_for_greeting`…) reader `query_graph.py:649` **0 caller** (TR14) | Gỡ 3 orphan config (alembic) HOẶC wire reader | grep reader; heuristic classifier vẫn là fast-path |
| **P2-8** | HyDE infra stub `infrastructure/hyde/*` dead; live path = `application/services/hyde_generator.py` cho 1 bot legal (QT11) | Gỡ stub chết, giữ live path | hyde bot legal vẫn chạy |
| **P2-9** | AdapChunk block-pipeline flag ON nhưng **no-op** (cả 2 nhánh về `smart_chunk(flat)`, CURRENT_TRUTH) | Hoàn thiện B1-B4 wiring (giá trị thật) HOẶC gỡ flag. measure nếu wire | ingest 1 doc, block giữ heading/table |
| **P2-10** | `knowledge_edges` 0 rows · `docling` 167L dead · `neighbor_expand` no-op · `reflect` 0% prod dormant (CURRENT_TRUTH §2) | Audit từng cái: gỡ hoặc doc-as-dormant | pytest xanh |
| **P2-11** | `cache_hit` bool dead trên hit-path (routing key off `cache_status`, TR04); `answer_type` KHÔNG phải DB column (TR05 REFUTED) | Gỡ field `state.py:233 cache_hit` chết | routing hit vẫn đúng |

---

## P3 — QUALITY (measure_first · nơi tạo giá trị T1)

> Đã có ở `plans/20260709-remediation-donow/plan.md` Phase 3. Mỗi cái = load-test đo trước/sau, flag riêng, không ship mù.
- #9a comparison G-097/098 (unique-leg-id + SKU-atomic decomposer) · #4 CRAG mixed-branch top-1 rescue (S-039/046/075) · #2 rrf_round_robin wire · #3b MMR 0.88→0.98 flip · #10 grounding 30s→8s per-bot.
- GAP-A arrival-link (G-063/067) · GAP-B claim-fidelity Tier-1b (S-005 contact non-numeric).

## P4 — DEFERRED-EXTERNAL (phụ thuộc bên ngoài)
- **innocom 5xx failover** — nghẽn LỚN NHẤT (p50 45.6s/p95 110s, 7/7 fail rows). Cần provider/cred backup → anh quyết.
- **embedder CB 488×/30d** — zembed-1 flaky. Cùng lớp → failover/backup embedder.

## CHƯA-VERIFY (khoảng trống thành thật — cần đo mới kết luận)
- Cross-tenant leak runtime (chỉ 1 tenant, chưa exercise) · Recall@10 · cache hit-rate xu hướng · cost embed/rerank (ledger KHÔNG emit → leak, AU).

---

## Thứ tự thực thi đề xuất (gate từng bước)
1. **P0-1 injection VN** (security, nhanh, đo false-positive) → **P0-2 brand-scrub** (guard green).
2. **P2 attic dọn dần** (low-risk, mỗi cái test xanh) — giảm nợ bảo trì, làm rõ "tầng-chạy-thật".
3. **P1 drift** (P1-2/4/5 nhanh; P1-1 RLS cần ops cred → staged).
4. **P3 measure_first** (load-test cycle) — nhích chất lượng thật.
5. **P4** khi anh quyết provider.

## Phân tích nền (5 dạng · 1 gốc)
- **5 dạng lỗi**: A refactor-orphan (dead-nhưng-trông-sống) · B config-drift 4-state · C guard-gap (an-toàn-giả) · D dead-safety-net · E domain-coupling accretion.
- **1 gốc bất biến**: **vibe-coding + refactor dở dang** → codebase 2 tầng: "thiết kế" (port/registry) vs "thật-sự-chạy" (inline). Khoảng cách = luận điểm "đã CÓ ≠ đã BẬT". Config-drift gốc = psql out-of-band không back-port seed.
- **Kết luận**: bot đúng 95.9%/95.3% BẤT CHẤP dead-code (tầng-chạy-thật ổn). Dead = nợ chi phí/bảo trì, KHÔNG phải nợ đúng-sai. Chỉ **P0 (2 cái) thật sự nguy hiểm**; phần lớn là attic vô hại.
