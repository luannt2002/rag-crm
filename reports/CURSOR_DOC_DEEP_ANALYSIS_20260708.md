# Phân tích chuyên sâu: `z_luannt_deubg.txt` (Cursor synthesis) vs code Ragbot THẬT

> **Ngày:** 2026-07-08 · **File phân tích:** `z_luannt_deubg.txt` (8631 dòng, +6548 dòng working-tree chưa commit)
> **Phương pháp:** đọc toàn bộ + đối chiếu từng claim file:line với codebase THẬT bằng grep/ls/read (rule #0). KHÔNG tin claim của doc — verify.

---

## 1. File này là GÌ — 3 lớp khác chất lượng

`z_luannt_deubg.txt` KHÔNG phải 1 tài liệu — là **nhiều output session Cursor ghép nối**:

| Dòng | Nội dung | Bản chất | Chất lượng |
|---|---|---|---|
| 1–1144 | `EXPERT_RAG_ANALYSIS.md` — AdapChunk theory, luồng U0-U7/Q1-Q7, 8-step debug, 5-tiêu-chí, CRM/cost-log schema, roadmap Gate 0-4 | **DESIGN/THEORY doc** (generic RAG) | ✅ Tốt như mental-model, KHÔNG phải mô tả code này |
| 1146–1694 | `RAGBOT_AUDIT_QUESTIONS.md` — ~120 câu bắt agent "khai sự thật code" + 15 trap + red-flags | **AUDIT FRAMEWORK** | ✅ Kỷ luật tốt (khớp rule #0) |
| 4441–5911 | Agent Cursor **TRẢ LỜI** audit, claim file:line | **CODE AUDIT (pass 1)** | 🔴 **Bịa nhiều** — nguồn ghi *repo `luannt2002/rag-crm`* (repo KHÁC) |
| 6019–6925 | "PROMPTS FIX NGAY" P0/P1 (lặp 2 lần) | Fix prompts | ⚠️ Generic, 1 phần sai tiền đề |
| 6926–7990 | "PHÂN TÍCH SÂU Phần 11-16" | **CODE AUDIT (pass 2)** | 🟡 Chính xác hơn (đọc file thật: semantic_cache, ragas_metrics…) |
| 8143–8631 | "MONG ĐỢI SAU FIX" — target 5-tiêu-chí, checklist | Aspirational | ⚠️ Kỳ vọng, chưa đo |

**Điểm mấu chốt:** doc tự khai `Nguồn: repo luannt2002/rag-crm` (dòng 4445) — agent Cursor audit **1 repo generic/khác**, KHÔNG phải `/var/www/html/ragbot` này. Nên phần "sự thật code" (pass 1) phần lớn là **suy từ spec + generic RAG knowledge**, không phải đọc code này.

---

## 2. Verification (rule #0) — claim của doc vs code THẬT

Đối chiếu từng claim bằng grep/ls/read trên repo này:

| # | Claim của agent Cursor | Code THẬT (verified) | Verdict |
|---|---|---|---|
| 1 | Upload endpoint = `POST /bots/{bot_id}/documents` | `documents.py:92` **`/documents/create`** + `:193 /documents` (canonical BE-to-BE) | 🔴 **SAI** |
| 2 | 5 Port: `EmbedderPort/RerankerPort/VectorStorePort/ParserPort/LLMPort` | `application/ports/` có **~60 port files** (`embedder_port`, `reranker_port`, `narrate_port`, `token_ledger_port`, `crag_grader_port`…) | 🟡 Đúng ý (Hexagonal thật) nhưng **hiểu thiếu 12×** |
| 3 | Worker = `document_service/ingest_consumer.py` | File **KHÔNG TỒN TẠI** | 🔴 **Bịa path** |
| 4 | `narrate.py` **"File KHÔNG TỒN TẠI"** (dòng 7681) | Narrate là **subsystem đầy đủ**: `narrate_port.py` + `narrate_dispatch.py` + `narrate_service.py` + `infrastructure/narrate/{null,llm}_narrate.py` + `narrate/{table,formula}_narrator.py` + locale-pack `_26`. Flag `DEFAULT_NARRATE_THEN_EMBED_ENABLED=False` | 🔴 **SAI hoàn toàn** (có, chỉ default OFF) |
| 5 | `LLMGateway` @ `infrastructure/llm/gateway.py` bắt buộc mọi call | File **KHÔNG TỒN TẠI**; thật = `dynamic_litellm_router.py` + `token_ledger_port` | 🔴 **Bịa** (nhưng ý "log mọi call" CÓ qua token_ledger) |
| 6 | CRM cần **build** 3 bảng `request_trace/pipeline_step_log/token_ledger` + `stats_*_daily` | Đã có: `token_ledger` (port+infra+analytics repo+`admin_metrics` route+migration `20260619`), `request_logs`+`request_steps` (`models_monitoring.py:78,169`), `audit_log` (`models.py:681`) | 🟡 **Đã tồn tại** dưới tên khác — doc "phát minh lại" |
| 7 | DSN = `ragbot:ragbot@postgres` superuser → RLS inert | `.env` `DATABASE_URL=postgresql+asyncpg://<role>` (role khác literal doc). Gap RLS-superuser **plausible đúng** (khớp STATE) nhưng literal **bịa** | 🟡 Kết luận đúng, evidence bịa |
| 8 | Block pipeline "chưa wire, no-op" | Flag `DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED=True` (`_12:185`) tồn tại; parser chưa emit block-list = **đúng phần** | 🟢 Đúng |
| 9 | Test = 6104 unit | THẬT: **829 file test**, **6208 tên `def test_` unique** | 🟢 Sát (đúng bậc) |

**Tỉ lệ**: pass-1 audit ~**50% claim file:line bịa/sai**; pass-2 (Phần 11-16) chính xác hơn vì có đọc file thật.

---

## 3. Nghịch lý — audit tự bác bỏ chính nó

Bộ câu hỏi (dòng 1675) liệt kê **"red flags = agent đọc README thay vì code"**:
> *"Trả lời không có file path · nói 'theo thiết kế' · claim tests pass nhưng không chạy lệnh · nói RLS enforce nhưng DSN superuser · nói AdapChunk full nhưng block pipeline no-op."*

→ Chính agent Cursor **trả lời** rơi vào đúng bẫy đó: đưa file:line **tự tin nhưng bịa** (`ingest_consumer.py`, `gateway.py`, narrate "không tồn tại", endpoint sai). **Doc phải bị verify lại trên repo này, KHÔNG được tin làm nguồn sự thật.** Đây là minh hoạ sống của rule #0: "EXISTS ≠ đúng"; claim không evidence = GIẢ THUYẾT.

---

## 4. Đánh giá chiến lược — vs CLAUDE.md T1>T2>T3

Các đề xuất LỚN của doc (build CRM cost-log center, `pipeline_step_log`, `stats_*_daily` rollup, L1 exact cache, partition-by-month, Prometheus metrics) **gần như toàn bộ là T2 (cost/perf/observability) + T3 (infra)** — KHÔNG phải **T1 (bot trả lời thông minh)**.

- Theo CORE MVP order (**T1 > T2 > T3**) và `[[feedback_no_premature_observability]]` (aggregate qua structlog + audit event, KHÔNG dựng rollup/Prometheus sớm) → dựng full CRM rollup lúc này là **premature**.
- **Doc MÙ với T1 gaps thật** vừa đo hôm nay (`fail_verify_analysis_20260707.md`): **B4 stats-suppress false-deny = 10 stable · world-knowledge HALLU = 5 stable**. Doc nhìn hệ thống qua lăng kính **cấu trúc/infra**, không thấy bug **retrieval-quality** đang thật sự hại câu trả lời. Đó chính là khác biệt giữa "audit generic" và "đào gốc rễ có evidence".
- Nhiều "CRITICAL gap" doc nêu **đã build** (3-bảng observability, token_ledger) hoặc **đã track** (RLS DSN flip, narrate default OFF, block pipeline) trong `STATE_SNAPSHOT.md`. Doc phát hiện **ít cái MỚI**.

---

## 5. Cái ĐÁNG lấy từ doc (giá trị thật)

Bỏ phần "sự thật code" bịa, doc vẫn có giá trị **mindset**:

1. **Verify-first, không nhảy gate** (Gate 0→4, mỗi gate = verify script + gold Q&A) — khớp rule #0.
2. **8-step layer-attribution debug** (INGEST/QUERY/EVAL → fail ở layer nào) — đúng cách em trace B4/WK bug.
3. **Happy-case input contract** (template + normalizer + checker score) — pragmatic hơn universal parser.
4. **Trap questions + red-flags** — kỷ luật chống agent "đọc README".
5. **Xác nhận 3 gap đã biết là thật** (RLS DSN flip pending · narrate default OFF · block pipeline chưa emit block-list) — đáng đóng, nhưng là **T2/T3**, sau T1.
6. **5-tiêu-chí Expert RAG** (Latency/Cost/Accuracy/UX/Perf) — khung cân bằng hợp lý.

---

## 6. Khuyến nghị

1. **KHÔNG merge/commit** `z_luannt_deubg.txt` vào repo như "audit chính thức" — nó là ghi chú ngoài (Cursor, repo khác), file:line ~50% bịa. Giữ ngoài git hoặc để `docs/_archive/external/` kèm disclaimer "unverified — external Cursor session".
2. **KHÔNG ưu tiên CRM/cost-log/rollup ngay** (T2/T3 premature) — token_ledger + request_logs/request_steps + audit_log đã đủ observability cho giai đoạn này.
3. **Giữ ưu tiên T1 hiện tại**: đóng B4 stats-suppress (10 stable) + world-knowledge HALLU (5 stable) theo `plans/20260707-cross-bot-systemic-gaps/plan.md`. Đây là cái doc KHÔNG thấy nhưng lại hại câu trả lời nhất.
4. **Lấy phần mindset** (8-step attribution, happy-case, gate-discipline) làm checklist review — đã phần lớn áp dụng.
5. Nếu muốn dùng bộ 120 câu audit: **chạy lại trên repo NÀY** với đúng file:line (không dùng câu trả lời bịa của Cursor). Em có thể trả lời đầy đủ template với evidence thật nếu anh cần.

---

*Mọi verdict dẫn từ grep/ls/read trên `src/ragbot/` + `alembic/` + `.env` (rule #0). Claim của doc = GIẢ THUYẾT tới khi verify; bảng §2 là kết quả verify thật.*
