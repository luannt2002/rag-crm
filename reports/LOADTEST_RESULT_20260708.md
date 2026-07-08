# Load-test Report — Ragbot 200 câu (xe + spa) · 2026-07-08

> **Phương pháp**: gate100 (xe, 100q) + spa100 (spa, 100q), fresh connect_id, bypass_cache. Answer **agent-graded** (10 LLM-judge song song, so corpus `expect`+`note`), **KHÔNG dùng digits-match**. Perf đo từ `request_logs.duration_ms`. **Tính đúng/sai và tốc độ TÁCH RIÊNG** (một answer bị timeout cắt = PERF, KHÔNG tính là sai).
> Chạy trên code sau phiên fix: empty-guard + claim-fidelity + brand-scope BLOCK + innocom timeout 30s→90s.

---

## 0. Executive summary

| | ĐÚNG | HALLU | Perf-cut |
|---|---|---|---|
| **xe** (100q) | **91/97 gradeable = 94%** | **0** | 3 |
| **spa** (100q) | **91/99 gradeable = 92%** | **1** | 1 |
| **TỔNG** (200q) | **~93%** | **1/200 (≈0)** | 4 |

- ✅ **Chất lượng logic CAO** — ~93% đúng, **HALLU gần 0** (xe tuyệt đối 0).
- 🔴 **Perf KÉM** — **p50 = 45.6s, p95 = 110s, max = 185s** (endpoint innocom chậm). Tách riêng, không tính vào sai.
- 14 fail thật gom vào **2 tầng**: orchestration (comparison + coref, 7) + retrieval (arrival + coverage, 6) + 1 HALLU-phone.

---

## PHẦN 1 — TÍNH ĐÚNG/SAI (agent-graded, đáng tin)

### 1.1 Tổng verdict
| Verdict | xe | spa |
|---|---|---|
| CORRECT (trả đúng) | 76 | 74 |
| REFUSE_OK (từ chối trap đúng) | 15 | 17 |
| WRONG (sai/miss) | 6 | 7 |
| HALLU (bịa) | **0** | **1** |
| PERF (bị cắt, không chấm được) | 3 | 1 |

**ĐÚNG = CORRECT + REFUSE_OK** (từ chối bẫy đúng cũng là đúng). xe 91/97 = **94%**, spa 91/99 = **92%**.

### 1.2 Per-FLOW correctness
| Bot | Flow | OK/n | Ghi chú |
|---|---|---|---|
| xe | **price_lookup** | **42/42** ✅ | hoàn hảo — tra giá chính xác |
| xe | inventory / policy / existence | 8/8·6/6·2/2 ✅ | tồn kho, bảo hành, tồn tại |
| xe | trap_no_price / oos_brand / oos_domain | 7/7·5/5·3/3 ✅ | **bẫy 15/15, 0 bịa** |
| xe | price_inventory / multi_variant | 11/12·4/5 | 1 perf-cut mỗi cái |
| xe | **arrival_date** | **3/6** ⚠️ | intermittent — 3 trả đúng, 3 refuse oan |
| xe | **comparison** | **0/4** 🔴 | flow yếu nhất — miss vế-2 |
| spa | s2_gia_le / s7_trap / s8_ux | 15/15·10/10·10/10 ✅ | giá lẻ, bẫy, UX hoàn hảo |
| spa | s3_buffet / s4_quytrinh / s6_lapluan | 14/15·13/15·9/10 | 1-2 miss mỗi cái |
| spa | s1_congty | 9/10 | 1 **HALLU** (bịa hotline) |
| spa | **s5_followup** | **11/15** ⚠️ | coref — 4 sai referent |

### 1.3 14 FAIL chi tiết → map TẦNG gốc rễ
| Fail-class | Câu | Tầng | Chi tiết |
|---|---|---|---|
| **Comparison** | xe G-095/097/098 (3) | 🟡 ORCHESTRATION | "so sánh A và B" — corpus CÓ cả 2 nhưng decompose/retrieve vế-2 miss → refuse oan |
| **Coref follow-up** | spa S-057/060/064/068 (4) | 🟡 ORCHESTRATION | "quy trình của NÓ" → không resolve referent, hỏi lại / trả sai service |
| **Coverage false-refuse** | spa S-039/046/075 (3) | 🟡 RETRIEVAL | corpus CÓ (Peel gói / 16 bước / Ultherapy) nhưng retrieval miss → refuse oan |
| **Arrival intermittent** | xe G-063/064/067 (3) | 🟡 RETRIEVAL | ngày về "28-thg 11" flaky (G-065/066/068 trả ĐÚNG) |
| **HALLU** | spa S-005 (1) | 🔴 GENERATE | bịa hotline "0909.999.999" (không có corpus) — 1 ca duy nhất |

### 1.4 Anti-HALLU đánh giá
- **HALLU = 1/200 (0.5%)** — xe **0 tuyệt đối**. Bẫy (trap_no_price/oos_brand/oos_domain/s7_trap) **honored 100%, 0 bịa số**.
- Các gate anti-HALLU (brand-scope BLOCK + numeric-fidelity + claim-fidelity observe) **hoạt động tốt** — Rovelo price-NULL → defer đúng, Michelin không-stock → refuse đúng.
- 1 HALLU còn lại (S-005 bịa phone) = generate-layer, phi-số → cần non-numeric gate mở rộng (claim-fidelity Tier-1b).

---

## PHẦN 2 — PERFORMANCE (đo riêng, KHÔNG tính vào đúng/sai)

### 2.1 Latency (request_logs, 301 request cửa sổ load-test)
| Metric | Giá trị |
|---|---|
| **p50** | **45.6s** |
| **p90** | 90.0s |
| **p95** | 110.0s |
| **max** | 185.3s |
| requests >30s | **237/301 (79%)** |
| requests >60s | 90 |
| requests >90s | 30 |

→ **Rất chậm** — nửa số câu >45s, 79% >30s. UX kém.

### 2.2 Timeout-truncation (đã đỡ nhờ raise 90s)
- **Trước** (timeout 30s): 200q → **16 empty + ~9 truncated = 25 bị cắt** (endpoint chưa trả kịp 30s → hủy → answer rỗng/cụt). spa nặng hơn (14 empty).
- **Sau** (timeout **90s**, alembic `innocom_timeout_90s_260708`): re-run 25 câu → **chỉ 1 còn rỗng** (S-048). → 90s **đóng gần hết perf-truncation**.
- Empty-guard không kịp fire khi timeout raise exception TRƯỚC guard_output → 90s là fix đúng tầng cho truncation.

### 2.3 Root cause perf (verified)
- **INFRA (gốc chính)**: endpoint LLM `innocom` chậm **3-30s/call, không tương quan output-token** (endpoint chậm, không phải sinh nhiều). Là **external** → đổi provider = quyết định ops.
- **ARCH (phụ)**: heavy path (comparison/aggregation/followup) chạy **3-5 LLM call tuần tự** + rewrite_retry chạy multi_query 2× + `retry_policy max_attempts=3` stack → 1 call chậm ×3.
- p50 45s = endpoint chậm × nhiều call/turn.

### 2.4 Đã làm + khuyến nghị perf
- ✅ **Raise innocom timeout 30s→90s** (alembic tracked) — giảm truncation 25→1.
- ⚠️ **Đánh đổi**: 90s × retry 3 = worst-case 270s/call; heavy-path 5 call × 90s. → khuyến nghị **giảm retry 3→2** (worst 180s) nếu latency quan trọng hơn completeness.
- 🔴 **Đòn bẩy thật = endpoint nhanh hơn** (ops) HOẶC **giảm số LLM-call/turn** (orchestration call-budget, hot-path, cần đo N≥10).

---

## 3. Kết luận

**Sự thật hiện tại (đo được, đáng tin):**
- **ĐÚNG/SAI: bot TỐT** — **~93% đúng, HALLU ≈ 0** (1/200). Retrieval mạnh (price_lookup 42/42), anti-HALLU vững, bẫy 15/15.
- **14 fail thật** = 2 tầng: **orchestration** (comparison-decompose 3 + coref-resolution 4 = 7) + **retrieval** (arrival flaky 3 + coverage-miss 3 = 6) + 1 HALLU-phone.
- **PERF: KÉM (riêng)** — p50 45s, p95 110s, gốc = endpoint innocom chậm (external). 90s timeout đã đóng truncation nhưng latency vẫn cao.

**2 chiều tách bạch:** chất lượng-logic ≠ tốc độ. Bot trả **đúng** nhưng **chậm**. Fix đúng/sai = orchestration (comparison/coref); fix tốc độ = endpoint (external) / call-budget.

*Mọi số dẫn từ agent-grade verdict (`wtbvzdlbc`) + `request_logs`/`request_steps`. Perf tách khỏi correctness (một answer cắt-do-timeout = PERF, không phải sai).*
