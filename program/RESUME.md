# RESUME · Ragbot Expert Build Program — trạng thái để tiếp tục phiên mới

> Đọc file này + `program/00-charter.md` là tiếp tục được. File = bộ nhớ, không phải context.
> Cập nhật: 2026-06-10, **Phase 2 DONE — đang ở GATE 2 chờ user approve**.

## Model & quy tắc
- Subagent = **Fable 5** (override có chủ đích, user chốt 2026-06-10). Phase 1–3 READ-ONLY (chỉ ghi `program/*.md`).
- 5 GATE đều cần USER approve. Orchestrator KHÔNG tự trôi qua gate.
- STANCE binding: **EVOLVE, KHÔNG rewrite** (strangler fig). Xem `00-charter.md` §STRATEGIC STANCE.
- Mọi phát hiện = evidence `file:line`/`commit-hash`. Chạy theo cặp 2 agent (kỷ luật appendix).

## Phase status
| Phase | Trạng thái | Output |
|---|---|---|
| **P0 Setup** | ✅ DONE | `program/00-charter.md`, `00-inventory.md`, `decisions/00-DECISION-REGISTER.md` (D1–D17 + Wave 6), `context/P1-C-PRESEED-multitenancy.md` |
| **P1 Hấp thụ context** | ✅ DONE · **GATE 1 APPROVED** | `context/P1-A…G.md` (7 file) + `context/P1-SYNTHESIS.md` (ma trận component×trạng thái, 25 câu hỏi mở, 3 mâu thuẫn) |
| **P2 Debug-all (gán nhãn)** | ✅ DONE (engine **+ application**) · **GATE 2 APPROVED** (user 2026-06-10: "tiếp tục làm việc cho đến khi rag + application expert") | engine: `gaps/P2-A…G.md` + `gaps/P2-GAPMAP.md`. application: `gaps/P2-H…K.md`. Hợp nhất: `EXPERT-STATE-REPORT.md` (226 item, scorecard 6-trục) + `EXPERT-PLAN.md` (6 wave + luồng làm) |
| **P3 Research+ADR** | ✅ W1 DONE | deep-research `wf_38df9b83` (10 findings 3-0). 6 ADR W1: `ADR-W1-D3-rls` · `D8b-exactly-once` · `D4-lifecycle` · `KEY-api-key-encryption` · `DI-transport-parity` · `S10-sysprompt-append`. |
| **P4 Build (Wave 1)** | ✅ 6/6 code SHIPPED (gate chờ ops) | main: `1b06a46` DI · `18a31f5` S10 · `c2bf270` D3 · `83fee63` KEY · `6121de8` D4 · `e85f2b8` D8b. +W3 fix-tại-chỗ: `2f89c46` table-misclassify · `e23b80a` proposition-connector. Ops-side gate: `waves/W1-OPS-CHECKLIST.md` (KEK + ragbot_app DSN → leak-test + graded). |
| **P4 Build (Wave 2)** | ✅ core shipped | `cc39346` D2c quota-wire (IQ-1 closed) · `b77dc6a` D2a workspaces entity (alembic 0199) · `8f91839` D8 per-tenant fairness semaphore. CÒN (defer): D2 quota ws-cascade · drop role_definitions.scope (cần psql reconcile) · D8c burst rate-limit. |
| **P4 Build (Wave 4 quick-wins)** | 🔄 2 shipped | `811d716` D5b temp-0 router choke-point (3 direct-call flip source) · `85dc70b` D7a grounding-degraded counter (HALLU observe). CÒN: tie-break content-aware key (D5a) · grounding claim-judge (D7b) · math_lockdown dead-row cleanup (D6). |
| **W2 ADR** | ✅ | `ADR-W2-D2-workspace-entity` (APPROVED WITH FIX) · `ADR-W2-D8-ingest-fairness` |
| P5 Eval+ablation | ⏳ chưa | — |
| P6 Vận hành | ⏳ chưa | — |

## ➡️ NEXT ACTION (làm ngay khi resume)
**W1 ✅ code 6/6 (gate runtime chờ ops). W2 đang chạy: D2c + D2a shipped.**
Anchor: `9ae81f7`. Branch `fix-260604-action-slotmachine-dead-key`. Alembic head **0199**. Suite ~5880 pass 0-fail.

Tiếp W2 (implement inline ở main HEAD để alembic numbering đúng — KHÔNG worktree stale-base):
1. **D2 quota ws-cascade** — thêm `workspace_id` predicate vào `IngestQuotaService` + cascade `min(tenant,ws)` headroom; degrade tenant-only khi ws-limit chưa set (ADR-W2-D2 §c·2).
2. **D2 drop `role_definitions.scope`** — TRƯỚC đó reconcile drift (0036 seed INSERT vs psql 0 rows; cần psql verify — ADR "APPROVED WITH FIX").
3. **D8 ingest fairness** — per-tenant `asyncio.Semaphore` thay global `Semaphore(5)` trong `redis_streams_bus.py:170` (wrap NGOÀI khối dedup-INSERT-XACK của D8b — đọc ADR-W2-D8 compat note).
4. Rồi **W3** (Block-feed lớn D1/D14 + large-table D16), **W4** (tie-break D5 + grounding D7), **W5** (cost D9), **W6** (feedback-loop D12 + ops D11).

**Ops chờ (W1 gate)**: KEK + `DATABASE_URL_APP=ragbot_app` → leak-test + graded — `waves/W1-OPS-CHECKLIST.md`.
**Commit discipline**: code commit TÁCH khỏi `program/` docs; implement inline ở main (tránh worktree stale-base alembic-race).

## Phát hiện Phase 2 đã có (pair 1)
**P2-A pipeline** (✅18 · 🕰2 · ↔️7 · 🐛1+2):
- 🐛 Tie-order nondeterminism vẫn OPEN sau revert `2f5ed41`. Fix đúng = stable tie key (score DESC, chunk_index, content_hash) — **KHÔNG dùng uuid** (đã chứng minh −13pp legal). Gốc variance phụ = LLM temp-0. → D5.
- 🐛 GraphRAG transport divergence: `chat_stream.py:330` hardcode `kg_service=None` vs `chat_worker.py:1357-1360` wire nó. Inert hôm nay (0 `graph_rag_mode` row) nhưng mìn nếu flip.
- 🕰 Speculative streaming Phase-2 stream draft chưa verify (`:1299-1333`, code tự nhận HALLU-risk). SOTA = Speculative RAG verify-before-emit (arXiv:2407.08223). DB OFF.
- ↔️ **CONTRADICTION CHỐT: CODE là sự thật** — 21 node/33 step, no-override (`cad52dc`+`6e9041d`). Doc 04-D "24-step + math lockdown" stale. **Orphan**: DB row `math_lockdown_enabled=true` có **0 code reader** → cần alembic DELETE.
- structured_subanswer **IS ON** (alembic `0192_..._ab`) · critique_parse swap **COMPLIANT** Gate#10 (template bot-owned, default "") · double-decompose triple-cost **bất khả** (route mutually-exclusive) · closures+singleton **KHÔNG block** plan split 260609.

**P2-B chunking** (✅11 · 🕰5 · ↔️5 · 🐛4):
- ↔️ **CONTRADICTION CHỐT: narrate-then-embed LIVE** — trace `document_worker.py:322-354,383` → `document_service.py:2891-2902` (`texts_to_embed = narrated_texts`) × DB thật: 560/560 chunk có narrate metadata, 211/211 TABLE chunk narrated≠raw và narration là text được embed; `content` giữ raw → answer cite raw, no answer-side HALLU. **P1-E đúng, P1-B sai.**
- 🐛 **BUG MỚI (quan trọng)**: chỉ 9/211 "TABLE" chunk là bảng thật. Classifier `_is_table_line` (`chunking.py:253-256`) bắt nhầm dòng điều khoản luật VN `a) …, …;` (CSV-comma rule) → ~163 chunk văn xuôi bị LLM-summarise-rồi-embed, override âm thầm strategy `raw_only` (`document_service.py:2838-2843`). Repro test có trong report.
- 🕰 SEMANTIC chunking (per-sentence cosine) → **drop/demote** (arXiv 2410.13070: gain không nhất quán, recursive thường tốt hơn trên doc thật). `_chunk_semantic_embed` → DELETE sau ablation Phase 5.
- Large-table rule đề xuất: **bảng atomic ở mức HÀNG, không phải ký tự; header đi theo mọi fragment; chỉ FORMULA/IMAGE atomic-tuyệt-đối**. Hợp nhất table_csv + atomic-protect vào 1 helper `_emit_table_rows`.
- atomic_protect (`62a1a05`) ship-dark **KHÔNG có A/B** · Block-native atomic survive consolidation (bug misclassify chứng minh regex-on-flattened-text hỏng) · proposition regex-only (no fabrication) nhưng cần entailment gate nếu LLM-proposition đổ bộ · Ekimetrics → wire-for-ablation có kill-date.

## File map program/
```
program/00-charter.md · 00-inventory.md · RESUME.md(this)
program/decisions/00-DECISION-REGISTER.md   (D1–D17 + Wave 6)
program/context/P1-A…G.md · P1-SYNTHESIS.md · P1-C-PRESEED-multitenancy.md
program/gaps/P2-A…G.md (7 auditor) · P2-GAPMAP.md (synthesis · GATE 2)
program/{decisions,waves,eval,sandbox}/  (chưa dùng)
```

## Phát hiện application (P2-H…K, 2026-06-10)
- alembic head = **0195** (verify `alembic heads`; claim "0260" của P2-H = false-match date-prefix — đã đính chính).
- 🔴 **API keys plaintext** `api_keys.value_plain` (P2-J, P0; encryption machinery có nhưng skip).
- 🟠 **Sacred #10 tension CONFIRMED**: `sysprompt_assembler.py:126` app append ~6KB rule platform (15-19, từ `language_packs`) SAU `bot.system_prompt`; engine audit không bắt vì test ở `generate` (đã-lắp). → Phase 3 ADR phân xử.
- 🟠 **Feedback-loop CỤT**: ghi live (`message_feedback`), đọc/học chết (`aggregate_per_bot` 0-caller · `FAQCandidateService` 0-callsite · `admin_refuse_suggestions` không-trong-router=404).
- 🟠 **get_graph DI singleton order-dependent**: stream thiếu 4 DI, có thể None toàn platform (chưa verify).
- 🟡 workspace RBAC/quota dead (`role_definitions` 0 rows) · ingest-quota orphan · DR RPO≈24h · eval self-bias · **PDPD stale** (NĐ13→Luật 91/2025).
- ✅ App layer chín hơn dự kiến: hash-chain audit + PII-redact + GDPR-erase GA-grade · no-version-ref PASS · 4-key API boundary PASS.

## Lưu ý CLAUDE.md
- Đã thêm section "ACTIVE PROGRAM — Ragbot Expert Build" vào CLAUDE.md (program memory + gate + EVOLVE stance + Fable-5 override). KHÔNG revert.
