# PLAN SOI TẤT CẢ LUỒNG — PASS-2 (đã phản biện + hiệu chỉnh)

> Tổng hợp của MAIN (Fable 5) sau 2 pass: PASS-1 = 27 agent tìm ~150 finding; PASS-2 = 5 agent
> **đọc lại source độc lập để kiểm chứng** (2 deep L1/L2, 2 deep L3/X, 1 adversarial critic) +
> 3 probe chạy thật (langgraph 1.2.4, kreuzberg 4.9.7, Starlette middleware) + query DB thật.
> Mỗi finding nặng bị **đọc lại source ≥2 lần độc lập**. Nhãn FACT/HYPOTHESIS theo rule#0.
> Report nguồn: `reports/DEEPDIVE2_20260703/pass2-*.md`. Stance: **EVOLVE không REWRITE**.

---

## PHẦN A — TƯ DUY / MINDSET (cách đọc toàn bộ vấn đề)

### A.1 Ba mệnh đề tổ chức toàn bộ ~150 finding
Không phải 150 bug rời rạc. Là **3 lỗi HỆ THỐNG** — mỗi cái có 1 guard cấu trúc diệt cả class:

| Class | Bản chất kỹ thuật (đã probe) | Số instance | Guard cấu trúc |
|---|---|---|---|
| **S1 — State-key drop** | langgraph 1.2.4 drop MỌI key không khai trong `GraphState` TypedDict (input + node-return + in-place). AST walk: **22 key** dùng-mà-không-khai (pass-1 nói 11 → **undercount**) | 22 | **AST pin-test** (prototype đã CHẠY, bắt đủ 22) |
| **S2 — Last-mile DI wiring** | Feature có Port+Registry+Null+test-xanh vẫn = 0 production vì bootstrap không có provider / ctor thiếu param / kwarg sai tên. Test mock strategy, không test wiring | ≥9 | **Wiring-audit 1 trang + integration test chạy class THẬT un-mocked** (cấm AsyncMock) |
| **S3 — Happy-case box** | Extractor **vocabulary-gated** (header phải khớp frozenset vi/en), ngoài box degrade về **0 entity** (không graceful). Comment trong code tự thừa nhận | (mọi corpus lạ) | **Shape-only header fallback + canary corpus** (đổi silent-zero → graceful + đo được) |

### A.2 Insight tư duy quan trọng nhất (git-forensic — pass-2 mới phát hiện)
**2 bug CRITICAL không phải "chưa xây" mà là "đã xây ĐÚNG rồi bị merge sau ÂM THẦM ghi đè":**
- Stats HALLU-net: `062d6fa` vá breach "stock number leaked from history" → `3097755` ("fix(phaseN): integrate…") revert. Pin test còn, đang FAIL.
- Re-export CRAG/cliff/threshold: `24f2451` xóa, để comment cũ nói dối → **5** test pin gãy (pass-1 nói 7 — sai, 2 cái kia là FastAPI env drift).

→ **Đổi khung giải pháp:** đòn bẩy CAO NHẤT không phải "re-implement feature" mà là **process-control: 1 blocking-pin + merge-gate chặn collection-error** — vì pattern "integrate merge nuốt fix" **SẼ tái phát**. Rẻ nhất, chặn cả class.

### A.3 Vì sao EVOLVE không REWRITE (bằng chứng)
Cả 5 agent pass-2 độc lập kết luận: khung expert-grade (Hexagonal · Port/Registry/DI · 4-key · RLS-design). **0/45 finding nói "khung sai"**. Mọi defect = (a) dây chưa nối, (b) vòng lặp chưa đóng, (c) key bị drop, (d) fix bị revert, (e) happy-case gap. Đập cái đã chuẩn = lỗi nặng nhất.

### A.4 Sự trung thực của audit (pass-2 tự bắt lỗi pass-1)
Critic + 2 deep agent **hạ cấp 5 chỗ pass-1 nói quá** (xem Phần D). Không tìm thấy false-positive thứ 2 kiểu RETR-F1 trong nhóm CRITICAL. **Danh sách P0 pass-1 đáng tin, thứ tự không đổi** — chỉ siết ngôn ngữ.

---

## PHẦN B — CASE-STUDY TỪNG LUỒNG (vấn đề → gốc rễ → expert solution → chuẩn chưa → trade-off → ảnh hưởng)

Chỉ liệt kê các case CONFIRMED nặng nhất. Đầy đủ 45 case ở report con.

### LUỒNG 1 — INGEST (13 CONFIRMED / 2 REFINED / 0 REFUTED)

**CS-L1.1 · Worker B2B là parse-path SONG SONG, không phải thin-adapter (root của multi-format)**
- **Vấn đề**: `POST /documents/create` (đường khách B2B thật) → XLSX/CSV/Sheets mất row-per-chunk (col_N, cross-row conflate). Fix xe-bot 01/07 chỉ bảo vệ Path-A (test-UI).
- **Gốc rễ (chain)**: mất atomicity ← `parser_preserve` không fire ← `parser_row_chunks=None` ← `if raw_bytes is not None` gate (`ingest_core.py:317`) ← worker flatten `"\n\n".join(c.content)` + gọi `ingest()` **không truyền raw_bytes** (`document_worker.py:464,613`). `blocks=` chỉ observability (docstring `ingest_core.py:204` tự nói). **Immutable cause = worker là path song song, không tái dùng luồng canonical.**
- **Expert solution**: worker truyền row-shape signal / dùng seam `insert_content_list`-style (RAG-Anything pattern) để **1 funnel sau parser** — KHÔNG flatten. Short: truyền `raw_bytes`/`parser_row_chunks` qua worker. Mid: hợp nhất 2 path thành 1 (parse tách khỏi index tại 1 seam công khai).
- **Đã chuẩn expert?** Short = patch (nối 1 param); **hợp nhất path = expert** (diệt cả class "2 path lệch hành vi"). Đúng CLAUDE.md "1 luồng canonical".
- **Trade-off**: hợp nhất path đụng worker contract → cần regression re-ingest. Short-patch rẻ nhưng vẫn 2 path.
- **Ảnh hưởng**: **Correctness** multi-format cho MỌI khách B2B. Blast radius = đường production chính.

**CS-L1.2 · OCR fallback trả 0 block MỌI doc (runtime-verified 2 lần)**
- **Vấn đề**: ảnh/scan/.doc/.xls/.ppt → ingest ra 0 block → "empty document text" → DLQ.
- **Gốc rễ**: 0 block ← `getattr(coroutine, "elements")=None` ← `extract_bytes(...)` gọi SYNC trong `def` (không await) ← kreuzberg 4.9.7 `extract_bytes` là **coroutine** (probe: `iscoroutinefunction=True`), sync variant là `extract_bytes_sync`. Test mock SYNC API nên xanh giả.
- **Expert solution**: đổi `extract_bytes`→`extract_bytes_sync` (hoặc await đúng) + truyền `ExtractionConfig(ocr_language)` + **un-mock contract test** chạy kreuzberg thật.
- **Đã chuẩn expert?** Fix 1 dòng = patch; **un-mock contract test = expert** (chặn mock-drift class — đây là F6 lesson).
- **Trade-off**: contract test cần lib thật trong CI (đã có sẵn). Gần như 0 rủi ro.
- **Ảnh hưởng**: **Correctness** multi-format. .doc/.xls/.ppt cũng cần thêm parser (không có OLE2 branch).

**CS-L1.3 · PII redaction chết ở 2 tầng độc lập (pass-2 mới thấy tầng 2)**
- **Vấn đề**: bot bật `pii_redaction_enabled=true` + operator bật `recap_pii_enabled=true` → CCCD/sđt vẫn lưu thô vào messages/chunks/embeddings.
- **Gốc rễ**: passthrough ← (tầng 1) config gate F2 đọc default OFF; **(tầng 2) DI singleton `bootstrap.py:447-449` đóng băng `provider="null"` compile-time**, knob `pii_redactor_provider` **0 reader** (grep empty). Comment nói "per-call resolution" = SAI.
- **Expert solution**: `providers.Callable(build_pii_redactor, get_boot_config("pii_redactor_provider"))` (mẫu `crag_grader_factory` đã đúng ở `bootstrap.py:435`). 1 dòng.
- **Đã chuẩn expert?** Có — đúng tầng (DI container), đúng mẫu sẵn có, thuộc S2-class → phải kèm wiring-audit.
- **Trade-off**: bật PII thật tăng latency ingest nhẹ (per-bot opt-in nên bounded).
- **Ảnh hưởng**: **Correctness/compliance**. Blast radius = mọi bot bật PII (hiện = 0 vì trơ).

**CS-L1.4 · Happy-case box degrade về 0 (canary 25/25 fail = spec)**
- **Vấn đề**: raw-CSV header lạ vocab (Khmer/Thai/vi-synonym) → 0 entity → stats/count/list/superlative chết im lặng cho corpus đó.
- **Gốc rễ**: 0 entity ← `_is_noise_entity` xóa row chỉ có col_N ← header không khớp frozenset vi/en → thành col_N ← **header detection vocabulary-gated, không shape-gated** (`document_stats.py:156`). Runtime probe pass-2: `31.12.2026→price 31122026`, `'1 600 000'→1`, bảng năm→doanh thu vào price index.
- **Expert solution**: **shape-only header fallback** (skill `table-header-detect-structural`): khi không khớp vocab → nhận header theo FORM (hàng toàn label, không value-cell, contrast với hàng dưới) → gán role positional. Currency → config `language_packs`, thoát VND-baked. + canary corpus CI.
- **Đã chuẩn expert?** Có — đổi failure mode từ silent-zero → graceful + đo được; domain-neutral (shape not vocab); sacred #10 an toàn (retrieval-tier).
- **Trade-off**: shape-fallback có thể nhận nhầm header ở doc rất lạ → cần canary chặn regression 2 chiều.
- **Ảnh hưởng**: **Coverage** mọi tenant ngoài locale demo — đúng lo "mới support happy case" của owner.

**CS-L1.5 · Coverage gate phát hiện mất chữ nhưng KHÔNG vá** (CONFIRMED observe-only) — `uncovered_spans` tính rồi vứt (`ingest_stages.py:864`). Expert: append span thiếu thành tail-chunk (~15 dòng, reference `postprocessing.py:128` vá+assert). Ảnh hưởng: mất-chữ-im-lặng = bot mù (đúng class Coverage).

**CS-L1.6 · AdapChunk chưa phải AdapChunk** (CONFIRMED) — bake-off đo `adaptive==oracle 0/8, lift +0.001`; selector chọn TRƯỚC chunk, Ekimetrics tính metric trên chunk giả lập. Expert: đóng vòng evaluate-then-select (bake-off offline → `oracle_best` per-doc override). Trade-off: CPU re-chunk offline (đã có script). **Đây là fix "smartness" thật, không phải thêm rule PROPOSITION.**

### LUỒNG 2 — QUERY (12 CONFIRMED / 2 REFINED / 0 REFUTED)

**CS-L2.1 · S1 state-drop — casualty nặng nhất + 2 casualty pass-1 bỏ sót**
- **Vấn đề repro**: bot mua `extra_output_tokens=2048` → không nhận (paid feature chết). + **NEW**: `rerank_score_mode` drop (rerank→grade) → grade luôn dùng relative-gate, floor tuyệt đối chết. + **NEW pass-2**: `raw_user_message` drop → **âm thầm revert fix slot 2026-06-15** (bug "Tên Lan"→OOS refuse 5/5).
- **Gốc rễ (immutable)**: đọc-ra-0/None ← key vắng khỏi state ← key set ở input-dict/node-return ← **`GraphState` (58 key) không khai** ← langgraph reducer drop (probe). Cause bất biến = **schema omission (L4)**.
- **Expert solution**: khai 12-13 key cross-node vào `GraphState` + đổi 6 in-place-write→return + **AST pin-test** (prototype đã chạy, bắt 22). Đây là mẫu SOTA "make illegal states unrepresentable at boundary".
- **Đã chuẩn expert?** Khai key = patch (lần 3 sau M17); **AST guard = expert** (biến silent-drop thành collection-time failure).
- **Trade-off**: guard cần allowlist `_ALLOWED_NON_STATE_KEYS` (vd `action_state` DB-backed = cosmetic, `_uq_cache_hit` scratch) để không false-positive — chi phí 1 lần.
- **Ảnh hưởng**: Correctness (paid feature + loop-safety + slot booking) + Cost (redundant resolve). Blast = mọi turn mọi bot.

**CS-L2.2 · Stats route = 0 verification + HALLU-net bị REVERT (git-forensic)**
- **Vấn đề**: câu stats → bypass rerank+grade+**grounding** (chunk synthetic score=1.0); LLM trả số leaked-from-history không ai kiểm.
- **Gốc rễ**: skip vô điều kiện `guard_output.py:105` ← commit `3097755` revert `062d6fa` (git blame). Knob `stats_route_skip_grounding` còn (default False = grounding ON) nhưng node **không đọc**. Pin test đang FAIL.
- **Expert solution**: restore `_pcfg(state, "stats_route_skip_grounding", DEFAULT)` gate (HOẶC owner chốt xóa knob+comment); + **merge-gate chặn collection-error/pin-fail** để pattern không tái phát.
- **Đã chuẩn expert?** Restore gate = patch đúng; **merge-gate = expert** (diệt class "integrate merge nuốt fix").
- **Trade-off**: bật grounding cho stats tốn 1 LLM-judge call/stats-turn (P95↑). Cân bằng: grounding observe-only hoặc gate per-bot.
- **Ảnh hưởng**: **HALLU sacred**. Với corpus pháp quy ngân hàng = trả sai số/điều luật.

**CS-L2.3 · GraphRAG gãy CẢ 2 CHIỀU (kwarg naming)**
- **Vấn đề**: bật `graph_rag_mode=adaptive` → ingest đốt token extract triples rồi **vứt**; query trả 0 chunk; feature hiện "on" trong config.
- **Gốc rễ**: 0 edge/0 chunk ← `TypeError` mọi call ← `bot_id=` vs signature `record_bot_id` (`graph_retriever.py:61`, `ingest_core.py:802`) ← `except Exception` nuốt ← test mock `AsyncMock` nhận mọi kwargs. Cause = **naming-convention ở kwarg layer** (class memory đã cảnh báo 3 lần).
- **Expert solution**: rename kwarg `record_bot_id=` (HOẶC gate OFF resolver nếu chưa ưu tiên — thôi đốt token). + **un-mock integration test** (fail hôm nay với TypeError) + narrow `except Exception`→fail-loud-once metric.
- **Đã chuẩn expert?** Rename = patch instance; **un-mock test + narrow except = expert** (diệt class + hết nuốt lỗi).
- **Trade-off**: un-mock cần real service + fixture session. GraphRAG gate-off đánh đổi capability (đang = 0) lấy cost honesty.
- **Ảnh hưởng**: Correctness multi-doc (owner care) + Cost.

**CS-L2.4 · Grounding gate NGƯỢC** (CONFIRMED) — judge đo "bịa"→ship (warn/hitl 0 consumer); judge không chạy được→refuse (fail_closed). Expert: escalate confirmed-ungrounded thành refuse per-bot-opt-in HOẶC rename judge thành observability + đo metric. **Đã chuẩn?** Cần owner chốt hướng (sacred #10: nếu block phải qua bot config). Ảnh hưởng: HALLU.

**CS-L2.5 · ai_keys query schema KHÔNG tồn tại** (DB-verified) — `ragbot.ai_keys` không có (table thật = `public.ai_keys`, 0 row). `POST /admin/ai/keys`→500. Expert: bỏ prefix `ragbot.` (5 dòng) + integration test insert/read. Ảnh hưởng: cả feature key-rotation chết, chỉ .env hoạt động.

**CS-L2.6 · Cascade routing no-op** (CONFIRMED) — `resolved_answer_model` set rồi 0 reader (+ undeclared S1). Expert: wire vào `_invoke_llm_node` HOẶC xóa wire+helper. Trade-off: xóa = honest (đang tốn resolve+log vô ích). Ảnh hưởng: Cost/T1.

### LUỒNG 3 — OBSERVABILITY / EVAL / TEST (CONFIRMED + 2 overclaim đính chính)

**CS-L3.1 · Test suite hỏng cửa trước** (re-run byte-for-byte: 67 fail/6439 pass/8 collection-error) — `pytest -q` abort ngay. 8 error = 2 root (5 re-export `24f2451` + 3 FastAPI env 0.135 vs ≥0.137). 6 REAL bug, **chỉ E1 (stats-grounding) là HALLU-critical**, còn lại cost/config-debt. Expert: (a) restore re-export + feature-detect FastAPI shim → CI xanh cửa trước; (b) merge-gate blocking-pin. Ảnh hưởng: CI mù toàn bộ + ~25-30% test file không bắt behavioral break.

**CS-L3.2 · Tầng verification sau generate = trục thiếu lớn nhất** — ragbot: `generate→guard_out(shingle)→HẾT`. SOTA (4 refs + 2 blog): thêm numeric-fidelity + hard-citation-coverage + citation-ID-validate + completeness-check + why-these-sources, **observe-only** (sacred #10 an toàn). Expert deliverable: Agent-Grader RAGAS-parallel (asyncio.gather N=8-10) + ground-truth `{question, expected_answer, expected_source_chunk_ids, question_type∈6-loại}` + %sample human-review. Ảnh hưởng: đo được Coverage + Faithfulness per question_type.

**CS-L3.3 · Redis-Streams recovery không re-dispatch** (CONFIRMED) — XCLAIM rồi vứt payload → 0 retry → DLQ sau 5 lần claim; comment 3 chỗ nói "retry until success" = fiction. Expert: dispatch `claimed` qua `_dispatch_one` hoặc XAUTOCLAIM + PEL re-read. Ảnh hưởng: doc kẹt DRAFT khi embed-API 429 transient.

**CS-L3.4 · InvocationLogger finally-INSERT không guard** (CONFIRMED) — DB blip lúc audit → mất turn LLM đã thành công (vi phạm "observability không được giết money-path"). Expert: try/except quanh INSERT (như Prometheus emit ngay dưới). Ảnh hưởng: 5xx user khi pool spike.

---

## PHẦN C — SECURITY / TENANT (probe + DB verified)

- **SEC-1 Middleware order** (probe): CORS + 3 rate-limiter chạy TRƯỚC tenant-bind → `tenant=None` → per-tenant CORS whitelist inert + BotRL/SourceRL bypass mọi request. Expert: add `TenantContextMiddleware` **cuối** nhóm auth (wrap ngoài cùng) + regression test boot `create_app()` thật. Ảnh hưởng: Security cross-origin + fairness đa tenant.
- **SEC-2 RLS chết runtime** (DB): role `postgres` `rolsuper=t rolbypassrls=t`; `ragbot_app` (bypassrls=f) không dùng. Isolation 100% dựa app-WHERE `record_bot_id`. + **soft-delete resurrection** (fallback stage thiếu `deleted_at IS NULL`) = bug thật. Expert: (ops) provision `DATABASE_URL_APP`→ragbot_app **sau khi fix SEC-4** (nếu không worker insert fail WITH CHECK); (code) thêm `AND d.deleted_at IS NULL` 3 fallback stage.
- **SEC-3 ai_keys** = CS-L2.5.
- **SEC-4 Idempotency ingest thiếu bot_id** — `for_ingest_document` = sha256("ingest"|tenant|source_url|corpus_version), không bot/workspace (`idempotency_key.py:40`). ⚠️ **CẦN ĐỐI CHIẾU**: critic nói key là `X-Idempotency-Key` header partner (chỉ nuốt nếu partner tái dùng header); X-agent nói key dựa source_url. → **có thể 2 cơ chế idempotency khác nhau** — phải trace runtime trước khi kết luận severity. Expert: thêm bot+workspace vào key (mirror `for_chat_message` đã đúng) — an toàn bất kể.

---

## PHẦN D — PASS-2 ĐÍNH CHÍNH PASS-1 (trung thực)

| Pass-1 nói | Pass-2 verdict | Đính chính |
|---|---|---|
| S1 "≥11 key drop" | **UNDERCOUNT** | thực 22 key; `rerank_score_mode` NEW cross-node; `action_state` cosmetic (DB-backed) |
| Re-export gãy "7 test" | **SAI SỐ** | 5 (re-export) + 3 (FastAPI env) — 2 root khác nhau |
| XML-wrap "100% chết" | **PARTIALLY-WRONG** | chỉ date-default-on chết; `plan_limits.xml_wrap_enabled=True` VẪN chạy |
| `int(_price)` "HALLU multi-currency" | **VND-null hôm nay** | mọi bot đang VND (int no-op); chỉ cắn bot USD/EUR |
| Idempotency "multi-bot data-loss" | **CONDITIONAL** | phụ thuộc partner tái dùng key + cơ chế cần đối chiếu |
| "có mấy X ≠ liệt kê X" | **HẸP** | chỉ corpus notation-variant (lốp/SKU) |
| re-ingest "xóa 99 entity" | **OVERCLAIMED** | delete per-`doc_id`, guarded — không cross-document wipe |
| RLS "leak fallback stage" | **REFINED** | `record_bot_id` fence giữ; bug thật = soft-delete resurrection |
| stats "fabricated tenant uuid" | **REFINED** | không có trên production write; = demo `UUID(int=1)` + docstring GUC-name drift |
| `rerank_score_mode`→"HALLU↑" | **HYPOTHESIS** | chỉ CRAG all-irrelevant fallback; chưa đo breach |
| CI "không có eval gate" | **OVERCLAIM** | có `eval-gate.yml` + 4 sibling |
| streaming "log $0" | **REFUTED** | OBS-F6 (fix của mình) đã meter streaming cost |

---

## PHẦN E — PLAN THỰC THI (đòn bẩy cao nhất trước)

### E.0 BA GUARD CẤU TRÚC — làm TRƯỚC (diệt cả class, rẻ nhất)
1. **AST pin-test** `test_graphstate_key_pin.py` (prototype đã chạy, bắt 22) → chặn vĩnh viễn S1.
2. **Wiring-audit `docs/dev/WIRING_AUDIT.md` + `test_di_wiring_smoke.py`** (un-mocked, cấm AsyncMock) → chặn S2.
3. **Merge-gate + blocking-pin** (chặn collection-error + pin-fail vào main) → chặn class "integrate merge nuốt fix".

### E.1 TUẦN 1 — P0 (đa số effort S, đúng CLAUDE.md, có test verify)
| P0 | Việc | Guard đi kèm | Effort |
|---|---|---|---|
| P0-1 | Khai 12-13 state-key + đổi in-place→return (paid-token, rerank_score_mode, raw_user_message, embedding_column) | **AST pin-test** | S |
| P0-2 | Restore stats grounding gate + re-export CRAG/cliff/threshold → 5 test pin sống + un-break FastAPI shim | **merge-gate** | S |
| P0-3 | OCR `extract_bytes_sync` + un-mock contract test; PII bootstrap Callable 1-dòng | wiring-audit | S |
| P0-4 | Coverage repair (append uncovered_spans) + persist page_number + ai_keys bỏ prefix `ragbot.` | — | S |
| P0-5 | GraphRAG kwarg `record_bot_id=` (hoặc gate off) + soft-delete `deleted_at IS NULL` 3 fallback stage + idempotency thêm bot_id | un-mock test | S-M |

### E.2 TUẦN 2-3 — Escape happy-case box + verification tier
- Worker Path A/B parity (row-shape signal qua canonical seam) · shape-only header fallback + canary corpus · currency→config
- Tầng verification observe-only: numeric-fidelity(VN) + hard-citation-coverage + citation-ID-validate + why-these-sources
- Sentinel rerank gate (hết recalibrate threshold) · middleware-order fix + regression test · expose retrieved_chunk_ids+scores trong response

### E.3 TUẦN 4+ — Eval end-to-end + đóng vòng AdapChunk
- Agent-Grader RAGAS-parallel + ground-truth 6-config ablation (spec §8.3) · bake-off→feedback-loop (oracle_best override) · embeddings A/B arms harness · modality probe questions
- Ops: provision `ragbot_app` NOBYPASSRLS (sau SEC-4) → bật RLS thật

### E.4 RỦI RO NẾU KHÔNG SỬA (thẳng)
- **HALLU sacred vỡ**: stats-route (P0-2) + grounding-gate-ngược + int-price(bot phi-VND) → corpus pháp quy ngân hàng trả sai điều luật/lãi suất.
- **Doanh thu rò**: paid-token=0 (P0-1). **Multi-format thất hứa**: .doc/.xls/ảnh/scan không ingest (P0-3).
- **Silent data loss**: coverage không vá + happy-case-zero (P0-4 + E.2).
- **Regression tái phát**: không merge-gate → fix P0-2 sẽ bị "integrate merge" nuốt lần nữa.

### E.5 CÒN THIẾU DỮ LIỆU (rule#0 — không đoán)
1. 3 screenshot/trace gốc (33s ingest, original_content với chunk TABLE, 77 chunks Thông tư).
2. Trace runtime 1 ingest qua `POST /documents/create` (psql/runtime session này không cho) → xác nhận Path-B degrade thực tế.
3. Cơ chế idempotency ingest (source_url-based vs header-based) — 2 agent nói khác nhau, phải trace.
4. Load-test số thật cho mọi P0 (mọi "fix X%" phải đo trước commit).

---

## PHẦN F — TUÂN THỦ CLAUDE.md (mọi fix)
Sacred #10: verification node = observe-only, không override answer. Sacred #7: ai_keys=code fix, RLS=ops+alembic, không psql. Domain-neutral: shape-fallback theo FORM, currency→config. Zero-hardcode: `cross_doc_reconcile_enabled` inline True + `_autocut` gap là nit còn lại. 4-key: SEC-4 restore bot+workspace. HALLU=0: int-price + grounding-gate + stats-skip = retrieval/guard tier, không text bịa. EVOLVE không REWRITE: 3 guard + nối dây + đóng vòng lặp, KHÔNG đập khung.
