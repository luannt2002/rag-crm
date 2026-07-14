# Audit ON/OFF toàn bộ luồng — vì sao bot lỗi dù code tốt — 2026-07-13

> 4 agent read-only liệt kê MỌI feature-flag/toggle/action-mode theo 4 luồng
> (guard+generate · retrieval · router+failover · ingest), đọc giá trị mặc định
> THẬT từ `shared/constants/`. Main session (Opus) chấm chéo với bug QA thật.
>
> **rule#0 — cảnh báo diễn giải:** đây là **default trong CONSTANT**. Trạng thái
> LIVE của 1 bot = default NÀY *trừ khi* bị override trong `plan_limits` /
> `system_config` / `.env` / DB. Bằng chứng QA (#8, #13) xác nhận bot thật đang
> chạy ĐÚNG các default OFF này. Muốn biết state thật 1 bot cụ thể → đọc
> `plan_limits` của bot đó.

---

## 0. PHÁT HIỆN CỐT LÕI (đọc cái này trước)

**Bot lỗi KHÔNG phải vì code sai — mà vì các lưới an toàn + tính năng recall ĐÃ CÓ CODE nhưng bị TẮT / để "observe" mặc định.** Lưới an toàn deterministic tồn tại nhưng **chưa cắm điện**.

Ba nhóm công tắc TẮT giải thích trọn vẹn mọi bug QA:
- **Nhóm SAFETY (chống-bịa) TẮT** → HALLU lọt (#13 bịa giá), câu rỗng giao đi.
- **Nhóm RECALL (mở rộng tìm kiếm) TẮT** → thiếu coverage (#20 so-sánh, #2, spa listing).
- **Nhóm RESILIENCE (chịu lỗi provider) thực chất TẮT** → innocom lỗi không tự cứu.

**Vì sao TẮT?** Phần lớn là **CỐ Ý**: guard block ship "observe-first" để đo false-positive trước khi bật (constitution bắt buộc); recall features TẮT vì đánh đổi cost/latency. → Không phải "bật hết", mà **bật cái đã đo an toàn**.

---

## 1. 🔴 Nhóm SAFETY / chống-HALLU — hầu hết TẮT (observe)

| Công tắc | Default | file:line | Ý nghĩa | Bug liên quan |
|---|---|---|---|---|
| `numeric_fidelity_action` | **observe** | `_14:354` | Chặn số bịa/lệch — chỉ log, KHÔNG chặn | 🔴 **#13 bịa giá 260k** |
| `grounding_confirmed_action` | **observe** | `_14:327` | Judge xác nhận ungrounded vẫn giao | faithfulness phi-số |
| `brand_scope_gate_action` + phrases `()` | **observe + rỗng** | `_14:363,364` | Chặn "chưa phân phối hãng X" sai — **tắt kép** | brand denial sai |
| `claim_fidelity_action` + phrases `()` | **observe + rỗng** | `_14:388,385` | Chặn khẳng định sai phạm vi — **tắt kép** | over-claim |
| `empty_answer_guard_enabled` | **False** | `_14:374` | Câu rỗng → giao (chỉ WARN log) | 🔴 rỗng=success |
| `citation_marker_required` | **False** | `guard_output.py:633` | Grounding regex `[chunk_id]` không chạy | grounding yếu |
| **Degeneration/lặp guard** | **KHÔNG TỒN TẠI** | — | Không có detector nào chặn LLM lặp | 🔴 **#8 lặp vô hạn** |
| `frequency_penalty`/`presence_penalty` | **0.0 + KHÔNG wire** | `_07:19-20` | Chống lặp tại sampling — không tới litellm | 🔴 **#8** |

**Đang BẬT tốt:** `grounding_check_enabled=True` (judge chạy như detector), `grounding_failure_mode=fail_closed` (từ chối khi judge chưa wire), regex leak+secret luôn block, `stats_route_skip_grounding=False`.

⚠️ Nghịch lý: grounding judge **BẬT như detector nhưng TẮT như blocker** (`grounding_confirmed_action=observe`) → phát hiện được nhưng vẫn giao.

---

## 2. 🟡 Nhóm RECALL / coverage — nhiều cái TẮT

| Công tắc | Default | file:line | Bug coverage liên quan |
|---|---|---|---|
| `parent_child_enabled` | **False** | `retrieve.py:1867` | listing/enumeration cụt context |
| `neighbor_expand_enabled` | **False** | `_15:20` | câu trải nhiều chunk (bảng, list) mất dòng |
| `hyde_enabled` | **False** | `_00:160` | query ngắn/thưa under-retrieve |
| `bm25_substring_fallback_enabled` | **False** | `retrieve.py:1122` | tra mã/size chính xác bị miss |
| `multi_query_enabled_by_intent[factoid]` | **False** | `_16:140` | factoid single-shot, miss chunk đúng |
| `stats_brand_aware` | **False** | `_21:305` | 🔴 **#20 so-sánh**, brand-blind |
| `name_by_shape` (ADR-0008 A1) | **False** | `_21:297` | tên entity lấy cột vị trí (code) thay vì mô tả |
| `metadata_extraction/aware` | **False** | `retrieve.py:833`,`_12:41` | không narrow theo metadata |

**Đang BẬT tốt (bảo vệ coverage):** `multi_query_enabled=True` (master), MQ cho aggregation/comparison/multi_hop=True, `crag_lenient_grade_for_compound_intents=True`, `retrieve_fallback_enabled=True`, `metadata_fallback_relax=True`, `reranker_enabled=True`.

---

## 3. 🔴 Nhóm RESILIENCE (chịu lỗi innocom) — thực chất TẮT cho bot mới

| Công tắc | Default | file:line | Thực trạng |
|---|---|---|---|
| `DEFAULT_LLM_FAILOVER_ENABLED` | **True** | `_06:89` | Flag global BẬT nhưng… |
| `record_fallback_model_id` (per binding) | **None** | `model_runtime.py:83` | …**cần binding dự phòng, mặc định NULL** → `_failover_eligible` (router:644) = False → **bot mới KHÔNG có failover**, innocom lỗi = 503 |
| Empty-200 sync path | **không phòng thủ** | `router:785` | câu rỗng nhận im lặng, breaker không học |
| innocom timeout override (constant) | **không có** | — | constant 30s; *(DB row đã tuned 90s + concurrency 6 từ migration phiên trước — override runtime, không phải constant)* |
| Breaker per-provider chung | — | `router:410` | burst ingest có thể mở breaker chặn cả chat |
| TPM limit process-local | 200k×4 worker | `_06:97` | trần thực = 4× → vẫn overrun innocom |

**Đang BẬT tốt:** circuit breaker ON (fail_max=5), retry+backoff+jitter ON, bg lane tách (4) khỏi fg (16), rate-limit bypass OFF (đúng — paid tier).

---

## 4. 🟢 Nhóm COST — đúng vị trí (không lãng phí)

| Công tắc | Default | Tốt vì |
|---|---|---|
| `grade_use_batch` | **True** | grade = 1 LLM call thay vì N — đòn bẩy cost lớn nhất, ĐANG ĐÚNG |
| `structured_output_enabled` | True | parse rẻ |
| speculative_* | tất cả False | không đốt token thừa/turn |
| `cascade_routing_enabled` | False | (có thể bật để routing rẻ theo complexity) |

---

## 5. Ánh xạ BUG QA → công tắc TẮT gây ra

| Bug QA thật | Công tắc TẮT là nguyên nhân | Fix = |
|---|---|---|
| **#13 bịa giá "260k" từ "26"** | `numeric_fidelity_action=observe` + `name_by_shape=False` + raw-chunk không marker | Bật `numeric_fidelity_action=block` (FP đã đo 0/84) + `name_by_shape=True` |
| **#8 lặp vô hạn** | degeneration guard **KHÔNG TỒN TẠI** + penalty=0 không wire | **XÂY** detector lặp (mới) + wire penalty |
| **#20 so-sánh thiếu vế** | `stats_brand_aware=False` + `parent_child=False` + `neighbor_expand=False` | bật recall theo intent so-sánh |
| **#2 info công ty** | data chỉ ở sysprompt (không phải flag) | đưa data vào KB |
| **spa listing** | `parent_child=False` + `neighbor_expand=False` + MQ factoid False | bật recall cho listing |

---

## 6. ⚠️ Caveat rule#0 — constant vs deployed

- Đây là **default CONSTANT**. Ví dụ: constant `DEFAULT_EMBEDDING_PROVIDER="jina"` / `DIM=1024`, NHƯNG deploy thật = **ZeroEntropy zembed-1 / 1280** (qua `.env`/DB). → Đừng đọc constant rồi kết luận state live.
- Nhưng **bằng chứng QA (#8, #13) xác nhận** các guard safety đang chạy ở default OFF trên bot thật.
- Muốn biết chắc 1 bot: đọc `plan_limits` của bot đó (override per-bot).

---

## 7. Khuyến nghị bật/tắt (theo bằng chứng — KHÔNG bật mù)

**Bật NGAY (đã đo an toàn):**
1. `numeric_fidelity_action = block` — FP đo 0/84 trên 60Q → an toàn, chặn #13.
2. `name_by_shape = True` per-bot — sửa gốc typing entity.

**XÂY MỚI (chưa có code):**
3. Degeneration/repetition detector trong guard_output — chặn #8 (nghiêm trọng nhất, chưa có gì).
4. Wire `frequency_penalty`/`presence_penalty` vào GenerationParams→litellm.

**Bật CÓ ĐO (recall — đánh đổi cost/latency, cần A/B 60Q):**
5. `parent_child` + `neighbor_expand` cho intent listing/comparison.
6. `stats_brand_aware` cho bot có nhiều brand.

**Cấu hình vận hành (không phải code):**
7. Seed `record_fallback_model_id` per binding → bật failover thật cho innocom.

**Nguyên tắc:** mỗi lần bật 1 cái, đo lại 60Q (HALLU + coverage + FP), không gộp. Guard TẮT là CỐ Ý (observe-first) — bật khi đã đo, không bật mù.
