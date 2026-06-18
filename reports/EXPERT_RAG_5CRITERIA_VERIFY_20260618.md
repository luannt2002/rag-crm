# EXPERT RAG — VERIFY 5 TIÊU CHÍ (sau luồng mới nhất) — 2026-06-18

> Câu hỏi: *"đã đủ RAG expert chưa?"* — 5 tiêu chí: **Nhanh · Đúng/Faithfulness=100% · UX · Performance · Cost thấp**.
> Nguyên tắc rule#0: mỗi verdict gắn evidence. **Code-verified ≠ load-test-verified** — phân biệt rạch ròi.

## TRẢ LỜI THẲNG: CHƯA đủ Expert — nhưng đã gần hơn 1 bước

Session này **fix + test xong** 3 luồng bug thật + 2 drift, nhưng các đòn bẩy Nhanh/Cost/Coverage là **cơ chế ĐÃ DỰNG, còn TẮT mặc định** (chờ load-test calibrate). Chưa đo lại Coverage/HALLU/p95 ⇒ **chưa được tuyên bố "100%"**.

## SCORECARD — baseline (master plan) vs sau session

| Tiêu chí | Baseline | Sau session | Delta | Trạng thái evidence |
|---|---|---|---|---|
| **Đúng/Đủ** | 6/10 | 6/10 | = | Faithfulness/HALLU=0 (by-design, chưa re-measure). Coverage lever (H2 synonym) DỰNG nhưng OFF/chưa owner-vocab |
| **UX** | 6/10 | **7/10** | ▲ | **SSE booking slot FIXED** (unit-test verified) — real UX win |
| **Performance** | 5/10 | **6/10** | ▲ | SSE action fixed; nhưng **RLS vẫn CHẾT** (ops) → trần cứng |
| **Nhanh** | 6/10 | 6/10 | = | MQ auto-gate DỰNG nhưng default 0.0 (OFF); sysprompt 2400 tok chưa nén |
| **Cost** | 7/10 | 7/10 | = | MQ gate OFF; sysprompt chưa nén. Cache 96% prefix vẫn tốt |

**Tổng**: cải thiện thật ở **UX + Performance** (có unit-test chứng minh). Nhanh/Cost/Coverage: **lever sẵn sàng, chưa kích hoạt** → trung thực không cộng điểm khi chưa đo.

## ĐÃ LÀM (code-complete + unit-tested session này)

| Việc | Tiêu chí | Test | Trạng thái |
|---|---|---|---|
| C2 — SSE booking slot persist (`chat_stream.py` wire resolver) | UX·Perf | 4/4 | ✅ verified |
| MQ auto-gate complexity (Adaptive-RAG, default OFF) | Nhanh·Cost | 4/4 | ✅ cơ chế, chờ calibrate |
| H2 — synonym OR-expand stats LIST route | Đúng/Coverage | 5/5 | ✅ cơ chế, chờ owner-vocab + đo |
| Fix `NullGuardrail.check_output` drift (`leak_min_match_count`) | ổn định | 15/15 | ✅ verified |
| Đăng ký `multi_query_complexity_min` pcfg parity (4 chỗ) | ổn định | 3/3 | ✅ verified |

## CÒN CHẶN "Expert/100%" (chưa làm — cần quyết/đo)

| Chặn | Tiêu chí | Hành động | Ai |
|---|---|---|---|
| **RLS chết** (superuser DSN) | Performance | đổi `DATABASE_URL_APP` + gỡ escape flag | ops |
| **Coverage chưa 0.95** | Đúng/Đủ | Phase 4: populate `entity_category` + self-query (alembic) | code+owner |
| **MQ threshold chưa calibrate** | Nhanh·Cost | 1 load-test chọn floor (band 0.3–0.5 đã đo) rồi flip | đo |
| **Sysprompt 2400 tok** | Cost·Nhanh | nén → ~1000-1200 tok (alembic) | code |
| **Chưa re-measure** Coverage/HALLU/p95 | tất cả | 1 load-test parallel (gather N=8) sau khi bật lever | đo |

## ĐIỀU KIỆN ĐỦ "Expert RAG 5/5" (DoD)
1. **Đúng=100%**: Faithfulness 1.0 + HALLU=0 (load-test trap) **+ Coverage ≥0.95** (Phase 4 xong).
2. **Nhanh**: p95 < target (ablate pipeline nặng + MQ gate ON + sysprompt nén) — đo.
3. **Cost thấp**: token/turn ↓ (sysprompt nén + cache warm) — đo so n8n ~4k.
4. **UX**: booking đa-turn OK (✅ đã fix) + list đủ + refuse duyên dáng (✅).
5. **Performance**: RLS sống (ops) + rerank sống (✅) + isolation defense-in-depth.

→ **Gate**: KHÔNG tuyên bố Expert cho tới khi 1 load-test parallel đo đủ Coverage≥0.95 + HALLU=0 + p95 + token, SAU khi bật lever + ops RLS + Phase 4.

## Kết luận
- **Trung thực**: hệ THỐNG KHUNG đã expert-grade (Hexagonal/DDD, Port+DI, rerank sống, 4-key, cache scope đúng, chunking adaptive, 33-step observability). **Vấn đề là "dây + đo"**, không phải "khung sai" — đúng stance EVOLVE.
- Session này đóng 5 lỗ thật (2 bug production + 3 drift/cơ chế), 0 regression unit.
- **Chưa đủ Expert** vì 5 việc chặn ở trên — phần lớn là **bật lever + 1 load-test + 1 ops + 1 alembic**, không phải đại tu.
