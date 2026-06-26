# COMPLETE PROBLEM INVENTORY — 2026-06-26

> Ráp toàn bộ: 4 workflow (16 agent Opus) + code audit + DB checks + đối chiếu `_external_refs`.
> **Tổng ≈ 48 issue distinct** (10 lõi + 21 coverage + 20 remaining − overlap). Hầu hết **SỰ THẬT** (file:line/DB-row); diagnosis **TĨNH** — CHƯA load-test verify (psql 10.0.1.160 unreachable từ sandbox, READ-ONLY).
> Branch `fix-260623-ingest-expert`. Sub-reports: `MASTER_DIAGNOSIS` · `DEEP_ANALYSIS_MULTIBOT` · `DEEP_MULTIBOT_ARCH` · `FIXSPEC_N_PLUS_1` · `COVERAGE_SWEEP` · `REMAINING_FLOWS`.

---

## 0. TL;DR — sao cứ bug hoài + verdict

**Gốc rễ "bug hoài" (đối chiếu RAG-Anything + adaptive-chunking):** engine làm **EARLY-BINDING** — cố "hiểu" cấu trúc data lúc ingest bằng heuristic price-centric. Mỗi data-shape mới → đoán sai → bug → patch → shape sau lại vỡ → **lặp vô tận**. RAG product thật làm **LATE-BINDING**: giữ structure nguyên (Block Integrity), LLM đọc lúc answer → 0 bug per-shape, 0 config. **Các lần "handle trước" đều đi đường CONFIG (`column_roles`) = cái nạng → khách không khai → rơi inference fragile → bug.**

**Verdict:** Khung (Hexagonal/Port/DI/4-key/sacred) **ĐÚNG, GIỮ** (EVOLVE không REWRITE). Nhưng **~48 "dây chưa nối"** ở: ingest-table, provider-routing, **security-RLS**, multi-turn, observability. **N+1 = NO** (ingest/provider/security) → bot mới hiện vẫn phải đụng code/config.

---

## 1. P0 — phải xử trước (7)

| ID | Vấn đề | Tầng | Evidence | Fix |
|---|---|---|---|---|
| **RLS-1≡SB-1** | **RLS bypass HOÀN TOÀN runtime** — app connect role `postgres` (rolbypassrls=t), `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`, `ragbot_system` role không tồn tại → 24 policy cosmetic, tenant-isolation 0 defense-in-depth ở DB | security | `pg_roles` DB-row, cross-validated 2 agent | **ops**: wire `ragbot_app` DSN + health-check fail-loud (KHÔNG sửa khung) |
| **RLS-2** | `document_service_index` policy thiếu `missing_ok` → **CRASH ngay khi RLS-1 fix** | security | policy def | fix CÙNG RLS-1 |
| **SB-2** | `conversation_state.save_state` UPDATE chỉ `WHERE id=`, no tenant scope + plain session no-GUC → cross-tenant PII write | security | code | scope tenant + GUC session |
| **MT-1** | Multi-turn history TÁCH 2 kho theo transport: `chat_histories` (HTTP/SSE) vs `messages`/`conversations` (worker) → turn sau không thấy turn trước cross-transport | conversation | code, 2 store disjoint | reconcile 1 source-of-truth |
| **AG-A2** | **Grounding fail-OPEN** — grounder dead (`gpt-4.1-nano` 429) → `return None` → answer pass UNVERIFIED → **HALLU-net tắt thầm** (sacred breach) | answer/guard | code | `grounding_failure_mode=fail_closed` |
| **1 (provider)** | 4 binding query-path còn OpenAI dead → empty answer | provider | log 429 | re-point binding → innocom (alembic) |
| **5≡SB-3≡PLM-5** | qwen3 structured fail — routing substring `openai` ép strict json_schema, bỏ qua cột `supports_json_mode` | provider/llm | DB col + log | route theo CAPABILITY |

## 2. P1 — quan trọng (≈18)

**Ingest-table (đường late-binding sẽ giải gốc):**
- `2` PRICE_MIN_VND floor lọc mất số phi-giá (404<10000). `3` ParsedEntity PRICE-CENTRIC (legal 108 entity/0 price). `4` header 2-dòng→col_N (63/496). `8` cross-sheet không reconcile. `6` bịa-URL (ingest 55%). `ING-7` delete không purge stats.

**Retrieval-QUALITY (T1, ZE@1280 — "runs=TRUE nhưng coherence UNVERIFIED"):**
- `RQ-3` (measured) matryoshka-1280 **anisotropic** — xe random-pair cosine 0.91 → dense branch yếu → **platform implicitly BM25-dependent** → bot semantic-only/non-VN mới sẽ underperform. `RQ-1` sparse query VN-pinned → non-VN bot block. `RQ-2` article-filter pure-waste 2× round-trip. `RQ-5` chunk_quality dead.

**Fail-loud thủng (S5):**
- `OBS-1` empty answer = status=success no-warning. `OBS-2` (DB) 103/755=13.6% turn completion_tokens=0 qwen3 streaming → cost undercount. `DLC-1` idempotency không bao giờ 'done' (mark_done dead). `DLC-2` state=failed kẹt vĩnh viễn, transient 429=dark doc no-retry.

**Khác:**
- `CB-CLIENT-4XX` circuit-breaker coi client-4xx là provider-fail → 1 bot misconfig OPEN provider cho TOÀN platform. `PERSIST-CACHE-TASK` fire-and-forget cache-write GC-drop → hit-rate leak thầm. `SB-4` DNS-rebinding SSRF webhook. `SB-5` PII-redact vs slot-extractor loại trừ nhau. `FMT-1` upload `local://` bypass parser.

## 3. P2 — kiến trúc/dọn (≈16)
god-node retrieve.py 96KB · 120 config-key · 2 decomposer trùng · dead condense_question · DI-leak orchestration import infra · **FMT-3** app-inject caption tiếng-Việt hardcoded vào vision LLM (sacred#10+zero-hardcode+domain-neutral) · stale-entity 87% (GIẢ THUYẾT) · ADR-0007 schema-migration · RQ-4 cliff floor uncalibrated (GIẢ THUYẾT) · ...

---

## 4. Sức khỏe subsystem

| Subsystem | Verdict | Lý do |
|---|---|---|
| action-security-boundary | 🔴 **BROKEN** | RLS bypass + qwen3 mis-route + SSRF |
| ingest-table | 🔴 **chưa chuẩn** | early-binding price-centric (N+1 fail) |
| tenant-rls · cache-perf · multi-format · conversation · lifecycle · retrieval-quality · observability | 🟠 **AT-RISK** | "dây chưa nối", chạy được nhưng silent-gap |
| Khung Hexagonal/Port/DI/4-key/sacred | ✅ **ĐẠT** | grep-guard pass, app-no-override tôn trọng |

---

## 5. FIX GỐC — bỏ config, late-binding table (đúng vision: tạo bot→upload→sysprompt→test)

1. **Parser giữ structure** (table→markdown block nguyên, mỗi hàng=record gắn nhãn header tự động) — vùng được phép rewrite parser-adapter (tham khảo MinerU/enhanced_markdown của RAG-Anything).
2. **Table-aware chunking** (Block Integrity: KHÔNG cắt ngang bảng/hàng) — bỏ token-split flatten.
3. **Late-binding**: LLM đọc bảng-có-nhãn lúc answer → **KHÔNG cần ParsedEntity đoán role, KHÔNG cần column_roles**.
4. Bỏ/giảm stats-index early-binding (chỉ giữ nếu đo được lift).

→ Sau fix: **khách chỉ tạo bot → upload → sysprompt → kịch bản test. HẾT.**

---

## 6. Thứ tự xử lý
- **P0-ops (ngay, 0 code-khung)**: RLS-1+RLS-2+SB-2 (wire DSN role) · re-point 4 binding · grounding fail_closed · qwen3 capability-route · MT-1 reconcile.
- **P1-quality (re-ingest/wire, T1)**: **late-binding table flow** (giải 2/3/4/6/8/ING-7 cùng lúc) · fail-loud holes (OBS/DLC) · CB-4xx · retrieval-quality (RQ-1/2/3).
- **P2-arch (defer tới T1≥95%, T2/T3)**: god-node tách · config-key dọn · decomposer gộp · FMT-3 · ADR-0007.

## 7. Blind-spot thành thật (CHƯA verify được)
- **TẤT CẢ severity = baseline TĨNH** (code+DB). Runtime impact CHƯA load-test (psql unreachable + READ-ONLY).
- Chưa adversarial cross-tenant probe (runtime superuser) → "không thấy leak" mới verify bằng đọc-code.
- Chưa test: đa-format binary upload thật, multi-turn live, ZE retrieval-quality bằng eval, bot ngành-khác (N+1 thật).
- → Để chốt: wire `ragbot_app` role + load-test backward-verify + thêm 1 bot ngành-khác.
