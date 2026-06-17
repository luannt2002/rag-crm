# 00 — DECISION REGISTER (backlog cho Phase 3)

> Danh sách DECISION cần ra ADR. Phase 3 mới viết ADR đầy đủ; đây chỉ là đề bài + nguồn gốc.
> Trạng thái: BACKLOG → RESEARCHING → ADR-DRAFT → APPROVED/REJECTED. Chỉ APPROVED vào Phase 4.

## Nhóm ENGINE (khung gốc D1–D10)

| ID | Decision | Nguồn gap | Wave dự kiến |
|---|---|---|---|
| D1 | AdapChunk hoàn thiện đến đâu (B1 emit-blocks→B2 route→B3 narrate→B4 atomic ON) hay thay engine | spec AdapChunk + dead-code chunking.py | W3 |
| D2 | Workspace slug→entity thật + RBAC workspace-scope + quota cascade tenant→ws→bot | P1-C §4 (slug-only, RBAC global) | W2 |
| D3 | RLS end-to-end: wire `attach_rls_session_hook` + leak test CI, cách ít xâm lấn nhất | P1-C §1 (0 callsite) | W1 |
| D4 | Semantic cache L2: invalidation matrix + bot-delete purge + BotLifecycleService | P1-C §2 (orphan on soft-delete) | W1 |
| D5 | Retrieval determinism trên corpus pháp lý dày (rerank tie-break) | git: tie-break đã revert (commit 2f5ed41) | W4 |
| D6 | Numeric aggregation: disclaimer vs extract-then-compute ngoài answer-path (KHÔNG vi phạm sacred #5) | reports graded fail multi-fact | W4 |
| D7 | Grounding judge ≤5 câu: nâng coverage không tăng p95 | P1-E domain | W4 |
| D8 | Noisy neighbor: fair queuing ingest + per-tenant rate limit nhẹ nhất | P1-C §3 (1 stream, Semaphore(5) global) | W2 |
| D9 | Cost: ma trận purpose×model — chỗ nào xuống nano/cache. **Haiku contradiction** chốt (xóa khỏi constants hay amend ban) | constants còn claude-haiku vs CLAUDE.md ban | W5 |
| D10 | Embedding versioning: chặn đổi model khi có chunks / re-embed flow | P1-C §5 (no guard) | W5 |

## Nhóm APPLICATION + AdapChunk-fix (bổ sung sau review khung 2026-06-10)

| ID | Decision | Nguồn | Wave |
|---|---|---|---|
| D11 | SLO + alerting + backup/DR PostgreSQL + secrets rotation + **Nghị định 13 (PDPD)** compliance — bắt buộc B2B VN, guard_output đã chạm PII | review khung §1 | W6 |
| D12 | Production feedback loop: thumbs up/down → vòng học; analytics câu refuse / câu user hỏi mà corpus thiếu | review khung §1 (eval synthetic) | W6 |
| D13 | Human ground-truth process: ai gán nhãn đáp án chuẩn, ai review nhãn — AdapChunk §9.3 (người gán nhãn không biết hệ thống để tránh thiên vị). Agent KHÔNG tự verify đáp án của chính nó | review khung §1 (Achilles heel Phase 5) | trước W5 eval |
| D14 | AdapChunk: strategy selection **per-section** (theo cây heading HDT) thay vì per-document | review AdapChunk §2 (granularity thô) | W3 |
| D15 | AdapChunk: **proposition verification** — entailment check proposition vs đoạn gốc, hoặc luôn giữ original_content. Tránh hallucination tại ingest (mâu thuẫn HALLU=0) | review AdapChunk §3 | W3 |
| D16 | AdapChunk: **large-table policy** — hòa giải "atomic tuyệt đối" (spec) vs `table_csv` row-as-chunk (Ragbot). Bảng nhỏ=atomic, bảng lớn=cắt nhóm-hàng+lặp-header+1 chunk tóm tắt | review AdapChunk §5 (2 triết lý sống chung) | W3 |
| D17 | AdapChunk: **incremental re-chunk** — update 1 doc thì re-chunk gì (cả doc/diff), doc mới có đổi strategy đã chọn không | review AdapChunk §6 (im lặng vòng đời) | W3/W6 |

## Known-limitation (ghi nhận, chưa cần ADR riêng)
- AdapChunk context-binding heuristic "1–2 câu" trượt tham chiếu xa ("Như Hình 2.1...") — accept v1.
- LLM selector confidence < 0.6 dựa self-reported, **chưa calibrate** — gộp vào D1/D14.
- SEMANTIC chunking cost chưa biện minh — để **ablation Phase 5** trả lời (giữ hay bỏ).

## Verdict AdapChunk (sơ bộ, chốt ở Phase 3)
- **Mindset = chuẩn** (structure-aware · atomic+context-binding §2.4 · narrate-then-embed+original_content ·
  rule cross-check · eval-by-type §8). Làm xương sống.
- **Engine = chuẩn một nửa**. 4 chỗ phải sửa: LLM-selector (Ragbot đã tự sửa→rule ✅) · granularity per-doc (D14) ·
  proposition no-verify (D15) · atomic tuyệt đối với bảng lớn (D16). → khẳng định nguyên tắc "giữ mindset, thay engine có ADR".
