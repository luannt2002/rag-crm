# Expert RAG Build — 2-Phase Plan

> Hợp nhất 3 mindset (AdapChunk + ekimetrics + RAG-Anything) → Expert RAG đạt **5 tiêu chí**
> (Nhanh · Faithfulness=100% · UX · Performance · Cost thấp) + **Coverage ≥95%**, đa-format,
> multi-tenant, log-center/CRM. Chi tiết verify: [`docs/EXPERT_RAG_BLUEPRINT.md`].
> Quy ước: **trả đúng 95% · refuse thật thà 5% · bịa 0%** (faithfulness là SÀN, không đánh đổi).

---

## "Đã expert chưa?" — câu trả lời thẳng

**CHƯA hoàn toàn — nền ~90% đã có, thiếu last-mile.** Hạ tầng 3 mindset đã dựng; "chuyên expert" cần
**bật/wire nốt các tầng coverage-booster đang OFF/missing** (HyDE, KG, narrate, cross-check) +
**đo bằng eval-gate 95%**. 2 bug T1 nền (parser flat, BM25 recall) đã fix phiên 2026-06-19.

### Hiện tại với mindset — ĐÃ CÓ GÌ (inventory verified)
| Mindset | Đã có | Còn thiếu |
|---|---|---|
| **AdapChunk** | 7 tầng ~90%: parser-md (fixed), block-atomic, profile 9/10, executor HDT/SEM/PROP/HYBRID, narrate-Port, eval-RAGAS | T5 cross-check OFF · T7 narrate OFF · T4 rule (chưa LLM) |
| **ekimetrics** | `ekimetrics_select` (metric-driven) có code | default OFF, chưa A/B vs rule-scorer |
| **RAG-Anything** | KG skeleton (`knowledge_graph.py` + `graph_retrieve`), hybrid+rerank+small-to-big | KG **disabled** · VLM image · graph-fusion OFF |
| **Retrieval funnel** | 6/8 tầng (chunk·hybrid·structural-filter·multi-query·small-to-big·rerank) | **HyDE** · **KG** (2 tầng cuối → +5-10% coverage) |
| **Đa-format** | pdf/word/excel/sheet/html/csv → structured | Sheet URL bug · VLM scan |

---

## PHASE 1 — CHUYÊN RAG (T1-Smartness) · mục tiêu Coverage ≥95% + Faithfulness=100%

**DoD Phase 1:** eval-gate trên bộ ground-truth (15-20 câu/doc × 3 loại: factoid/aggregation/structural)
đạt **Coverage ≥95% · Faithfulness=100% · HALLU=0**, đo bằng RAGAS, KHÔNG regression.

| # | Task | Tầng | Hiện trạng | Việc | Effort | Gate |
|---|---|---|---|---|---|---|
| 1.1 | **BM25 structural-OR** | retrieval | ✅ fixed (0→2 precise) | commit + integration test | done | — |
| 1.2 | **Wire HyDE** | retrieval | dead-stub | bật HyDE port (sinh đáp-án-giả→embed) cho intent abstract/structural; A/B coverage | M | A/B +recall, no latency-regress |
| 1.3 | **Cross-check (T5)** ON | chunking | code có, OFF | bật `adapchunk_layer5_cross_check` + đo strategy-accuracy | S | A/B no chunk-quality regress |
| 1.4 | **Narrate-then-embed (T7)** | chunking | code có, OFF | bật cho doc có bảng/công thức; A/B coverage table/formula-question | M | A/B +coverage, cost acceptable |
| 1.5 | **ekimetrics-select** A/B | chunking | code có, OFF | A/B metric-select vs rule-scorer; chọn winner per doc-type | M | strategy-accuracy ↑ |
| 1.6 | **KG (RAG-Anything)** bật | retrieval | disabled | set entity-extraction model + graph-fusion retrieval; A/B multi-hop/aggregation | **L** | +coverage aggregation, /plan riêng |
| 1.7 | `confidence_score` metadata | chunking | thiếu | thêm vào chunk metadata (spec 7.3) | XS | — |
| 1.8 | **Sheet URL fix** (xe-3) | parser | bug | `supports()` nhận `edit?gid=`→export CSV | S | no retry-storm |
| 1.9 | **Eval-gate 95%** | eval | RAGAS có | build ground-truth set + CI gate Coverage≥95%/HALLU=0 | M | **blocker để "expert"** |

**Thứ tự ROI:** 1.1(done) → 1.7/1.8 (rẻ) → 1.2 HyDE → 1.3 cross-check → 1.9 eval-gate → 1.4 narrate → 1.5 ekimetrics → 1.6 KG.
**Sacred:** mọi A/B giữ HALLU=0; app KHÔNG inject/override; fix đúng tầng (retrieval bug ≠ sysprompt).

---

## PHASE 2 — PLATFORM / CRM / INFRA (T2-CostPerf + T3) · mục tiêu Log-center chính xác + multi-tenant enforced + scale

**DoD Phase 2:** log-center capture 100% paid-call (token in/out/model/time/4-key) · dashboard rollup
chính xác bot/workspace/tenant/system theo time-range · RLS enforced · không data-loss.

| # | Task | Loại | Hiện trạng | Việc | Effort |
|---|---|---|---|---|---|
| 2.1 | **Log-center: streaming-gen** vào ledger | T2 correctness | gap (chỉ model_invocations) | thêm `_ledger.emit` trong `complete_runtime_stream` | M |
| 2.2 | **model_invocations + bot_id/channel** | T2 | thiếu cột | migration add 4-key → rollup per-bot được | S |
| 2.3 | **embed/rerank cost** trong ledger | T2 | NULL | thread cost_usd vào `emit_aux_usage` | S |
| 2.4 | **Per-workspace token endpoint** + index | T2 | phân mảnh 2 bảng | 1 endpoint rollup token-in/out theo workspace + index `(tenant,workspace,started_at)` | M |
| 2.5 | **RLS enforcement cutover** | T2 security | INERT | DSN→`ragbot_app`(NOBYPASSRLS) + set `DATABASE_URL_SYSTEM` + load-test gate | M (ops) |
| 2.6 | **upload_stream orphan** | T2 | data-loss | wire consumer HOẶC gỡ route | M |
| 2.7 | **SSE citation/refuse logging** | T2 UX | drop | log citations + flag soft-OOS=refused | S |
| 2.8 | **Worker tách process** | T3 scale | in-loop | tách container worker + replicas + PgBouncer | L |
| 2.9 | **Vector partition by tenant** | T3 scale | global HNSW | partition khi >10M chunks | L |

**Thứ tự:** 2.1-2.4 (log-center, đúng cốt CRM anh cần) → 2.7 → 2.5 RLS → 2.6 → 2.8/2.9 (khi scale thật).

---

## Ràng buộc chung (cả 2 phase)
- **EVOLVE không rewrite** — giữ khung Port+Adapter+DI+4-key+sacred; bật/wire/hoàn thiện.
- **Mọi đổi config/schema qua alembic** (không psql hot-fix); mọi đổi `src/` = Opus.
- **A/B trước default-on** cho mọi tầng quality (1.2-1.6) — không blind-flip.
- **Eval-gate (1.9) là cổng "expert"** — chưa đạt Coverage≥95%/HALLU=0 thì chưa gọi expert.
- Gate user-approve mỗi đầu task L (1.6 KG, 2.8/2.9 scale).

## Success = 5 tiêu chí (đo runtime, không cảm tính)
| Tiêu chí | Đo bằng | Target |
|---|---|---|
| Nhanh | P95, TTFT (`request_logs`) | P95 < ngưỡng/bot |
| Faithfulness=100% | HALLU-trap load-test | 0 fabricate |
| Coverage | eval-gate ground-truth | ≥95% |
| UX | refuse-rate, citation | refuse chỉ khi corpus thiếu |
| Cost | `token_ledger` rollup | giảm vs baseline |
| Performance | throughput, pool, cache-hit | scale 10× không vỡ |
