# [T3-Refactor + T1/T2] FIX-ALL 16 — Expert RAG + CRM hardening

> Ngày bắt đầu: 2026-06-15. Mandate: user "làm hết". Nguyên tắc: **mỗi việc verify xanh mới qua, không bỏ dở**; EVOLVE không REWRITE; sacred rules (HALLU=0, 4-key, zero-hardcode, no-app-inject) bất biến.
> Đo tiến độ: pytest xanh + service healthy + (god-file) mọi file ≤1.2k dòng.

## Trạng thái nền (đã xong trước plan này)
- ✅ chunking god-file (3192) → 6 module ≤1.2k + 32 test (`test_chunking_modules_split.py`)
- ✅ CRM analytics read-layer + dashboard (alembic 0219, `crm_analytics_service.py`, `crm.py`, `crm.html`)
- ✅ booking fix (5/5, HALLU=0) · retrieval_filter.py tách khỏi query_graph

---

## PHASE 1 — God-file refactor (T3, mechanical, test-gated). Mỗi file: convert package → carve dependency-order → re-export → pytest → restart → smoke.

- [ ] **1.1 document_service.py (4436)** → package:
  - `text_cleaning.py` (_fix_hyphenation/_strip_prompt_injection/_clean_document_text/canonicalize_embed_text + regex)
  - `chunk_typing.py` (chunk_type_for/should_skip_row_enrich/_BLOCK_TYPE_TO_CHUNK_TYPE)
  - `ingest_persistence.py` (_bulk_insert_chunks/_update_doc_progress/_maybe_redact_ingest_content/_maybe_validate_source_allowlist/_phase_d_step)
  - `ingest_result.py` (IngestResult dataclass)
  - `__init__` = DocumentService class; **decompose `ingest()` 2656-dòng** thành stage-helpers (validate/parse-clean/chunk/enrich/embed-store/finalize) — careful, core path.
  - Gate: test_enrichment_and_cleaning, test_source_allowlist, test_pii_wire_ingest_path, test_document_service_* xanh + ingest smoke.
- [ ] **1.2 test_chat.py (5354)** → đổi tên đúng bản chất (BE chat/admin API thật, KHÔNG phải test). Tách route modules theo nhóm: `chat_routes.py` / `bot_admin_routes.py` / `document_routes.py` / `admin_config_routes.py` / `token_routes.py` + `_shared.py` (helpers _sf/_require_owner/_find_bot_uuid). **Giữ URL `/api/ragbot/test/` ổn định** (đổi = vỡ client/demo). Gate: endpoint smoke + import.
- [ ] **1.3 chat_worker.py (1796)** → tách consume/handle/ack stages.
- [ ] **1.4 model_resolver.py (1230)** → tách resolve-binding / resolve-runtime / fallback.
- [ ] **1.5 dynamic_litellm_router.py (1038)** → tách router / pricing / call.

## PHASE 2 — query_graph.py (8020) — SACRED, cần golden-net (user approved)
- [ ] **2.1** chạy eval 42-câu (parallel) → snapshot golden baseline (answer+chunks+intent+latency).
- [ ] **2.2** convert package + sửa ~20 test path-guard.
- [ ] **2.3** carve `build_graph()` (~7000 dòng) → `stages/` (guard/cache/understand/retrieve/rerank/grade/generate/guard_output/persist), mỗi stage 1 module. Re-run golden-net sau MỖI stage = 0 diff deterministic + faithfulness không tụt.

## PHASE 3 — Multi-lang hardening (T1)
- [ ] **3.1** EN parity: bù 4 key thiếu trong language_packs (en 11→15) qua alembic.
- [ ] **3.2** VN-legal logic (Chương/Mục/Điều) + agg-keywords → config per-language (không bake cứng), bot EN/JP không bị bỏ rơi.
- [ ] **3.3** verify 1 bot EN end-to-end (answer + refuse).

## PHASE 4 — Hardcode sweep + comment rewrite (T2/T3)
- [ ] **4.1** grep-guard magic-number/literal toàn src → lift vào constants/config.
- [ ] **4.2** viết lại comment lan man → ngắn-gọn-WHY (theo file đã carve trước).

## PHASE 5 — Expert-RAG features + Postgres RLS (T1)
- [ ] **5.1** RLS end-to-end: wire `attach_rls_session_hook` + leak-test (gap 🔴).
- [ ] **5.2** condense topic-anchor cho follow-up ngắn (≤4 từ) — cần đo RAGAS.
- [ ] **5.3** LiteLLMReranker circuit-breaker (gap 🟡).

## PHASE 6 — Docs sync (T3, nhẹ)
- [ ] **6.1** README/STATE: chunking.py→package, alembic 0216→0219, CRM layer, retrieval_filter.

---

## Thứ tự chạy
Phase 1 (an toàn, mechanical) → Phase 6 (docs, nhẹ) xen kẽ → Phase 3/4 → Phase 5 → Phase 2 (sacred, cuối, có net). Mỗi task = 1 commit-block verify độc lập.
