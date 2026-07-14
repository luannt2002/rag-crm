# Best-Practice Audit — tất cả luồng Ragbot vs SOTA RAG/LLM 2024–2025

> **Ngày:** 2026-07-13 · **Nhánh:** `fix-260623-ingest-expert`
> **Phương pháp:** 6-agent code review + 4-agent flag audit + perf audit + đối chiếu
> bug QA thật (2 sheet: bot xe 17/20, bot pháp lý 17/20). Mỗi mục anchor bằng
> `file:line` hoặc grep evidence — không claim suông (rule#0).
>
> **Ký hiệu:** ✅ đạt · 🟡 có code nhưng TẮT/một phần · ❌ thiếu hẳn
>
> **Anchor các best-practice đã verify tồn tại:** MMR (`shared/mmr.py`,
> `orchestration/nodes/mmr_dedup.py`) · Contextual Retrieval (`narrate_service.py`) ·
> RAGAS (`ragas_metric_adapter.py`) · prompt-cache Anthropic (`dynamic_litellm_router.py:88`
> `cache_control ephemeral`) · cross-encoder rerank (zerank-2) · query decompose
> (`query_decomposer.py`). RAPTOR / self-consistency: grep = 0 hit → xác nhận THIẾU.

---

## Bối cảnh — vì sao audit này quan trọng

Bug QA thật cho thấy bot lỗi **KHÔNG phải vì code sai** mà vì **lưới an toàn + tính năng đã có code nhưng bị TẮT/observe mặc định**. Audit best-practice này trả lời: so với SOTA, Ragbot **dẫn đầu ở đâu, tụt ở đâu**, và gap nào là "chưa làm" vs "làm rồi chưa bật".

---

## A. Ingest & Indexing

| Best practice (SOTA) | Ragbot | Evidence |
|---|---|---|
| Multi-format canonical pipeline (Port+Strategy) | ✅ | parser registry, 1 API `/documents/create`; thêm format = 1 adapter |
| Structure-preserving chunk (table/heading/atomic) | ✅ | `table_csv` strategy, footer-preserve, atomic-block protect |
| Contextual Retrieval (Anthropic 2024 — situate chunk) | ✅ | `application/services/narrate_service.py`, chunk-context enrich |
| Late chunking (Jina) | ✅ ON | `DEFAULT_JINA_EMBEDDING_LATE_CHUNKING=True` (`_02:45`) |
| Semantic (embedding-based) chunking | 🟡 OFF | `DEFAULT_EMBEDDING_SEMANTIC_CHUNK_ENABLED=False` (`_06:183`) |
| Incremental re-embed (content-hash diff) | ✅ | hash-diff skip unchanged (`ingest_core.py:628-640`) |
| Idempotent ingest | ✅ | `X-Idempotency-Key`, TTL 24h |
| ADR-0008 shape-name typing | 🟡 OFF | `DEFAULT_STATS_NAME_BY_SHAPE=False` (`_21:297`) — tên entity lấy cột vị trí |

---

## B. Retrieval

| Best practice | Ragbot | Evidence |
|---|---|---|
| Hybrid dense + sparse (BM25 + vector) | ✅ | `pgvector_store.hybrid_search` |
| Cross-encoder reranking | ✅ | zerank-2, RerankerPort DI, NullReranker bypass |
| MMR / diversity | ✅ | `shared/mmr.py`, `orchestration/nodes/mmr_dedup.py`, per-intent threshold |
| Query transform (rewrite / multi-query / decompose) | ✅ | (nhưng `multi_query[factoid]=False` → factoid single-shot) |
| HyDE | 🟡 OFF | `DEFAULT_HYDE_ENABLED=False` (`_00:160`) |
| Small-to-big / parent-child | 🟡 OFF | `parent_child_enabled=False` (`retrieve.py:1867`) |
| Metadata filtering | 🟡 OFF | `metadata_aware_retrieval_enabled=False` (`_12:41`) |
| RAPTOR (hierarchical summary tree) | ❌ thiếu | grep = 0 |
| Multi-tenant isolation (RLS) | ✅ **xuất sắc** | `pgvector_store.py:258` WHERE + null-guard `:310` + RLS `session_with_tenant` + 4-key |
| Semantic caching (2-tier) | ✅ | exact-hash + pgvector, threshold 0.97 |
| BM25 substring fallback (exact/code match) | 🟡 OFF | `bm25_substring_fallback_enabled=False` (`retrieve.py:1122`) |
| Neighbor-window expansion | 🟡 OFF | `neighbor_expand_enabled=False` (`_15:20`) |

---

## C. Generation & Grounding

| Best practice | Ragbot | Evidence |
|---|---|---|
| Structured output | ✅ | `structured_output_enabled=True` (`_14:83`) |
| Low temperature cho faithfulness | ✅ | `DEFAULT_GENERATION_TEMPERATURE=0.0` (`_10:200`) |
| Citation grounding (validated vs chunk_id) | ✅ | citation filter (`generate.py:817-847`) |
| Single source of truth (no app-inject/override) | ✅ **sacred #10** | SysPromptAssembler append-only (`:141`); refuse từ `oos_answer_template` DB |
| Self-RAG / reflection | ✅ | `reflect.py` keep/rewrite |
| Deterministic faithfulness verify (numeric) | ✅ **dẫn đầu**, 🟡 observe | numeric-fidelity gate (`numeric_fidelity.py`); action=observe (`_14:354`) |
| LLM-judge grounding | ✅ detector, 🟡 observe-blocker | grounding judge ON; `grounding_confirmed_action=observe` (`_14:327`) |

---

## D. Safety / Reliability

| Best practice | Ragbot | Evidence |
|---|---|---|
| Degeneration / repetition handling | ❌ **THIẾU** | không detector (grep=0); `frequency/presence_penalty=0.0` (`_07:19-20`) + **không wire tới litellm** → **bug #8 lặp vô hạn** |
| Refusal on low confidence | ✅ | `grounding_failure_mode=fail_closed` (`_14:314`), `refuse_short_circuit_enabled=True` |
| Empty / truncation detection | 🟡 | log-only (`router:804-811`); sync path không phòng thủ; giao `ok:true` |
| Prompt-injection defense | ✅ | chặn tiêm lệnh VN ở input guard (commit `87d55e9`) |
| Output leak / secret guard | ✅ | 24-word shingle + secret scanner (`local_guardrail.py`) |
| SSRF trên callback | ✅ | re-resolve lúc giao (`callback_delivery.py:93`) |

---

## E. LLM Ops

| Best practice | Ragbot | Evidence |
|---|---|---|
| Multi-provider failover | 🟡 | `DEFAULT_LLM_FAILOVER_ENABLED=True` nhưng cần `record_fallback_model_id` (null mặc định) → bot mới **0 failover** |
| Circuit breaker | ✅ | per-provider, `fail_max=5`, adaptive cooldown 30–120s |
| Retry + backoff + jitter | ✅ | `retry_policy.py`, 3 attempt, 0.5–1.5× jitter |
| Prompt caching (Anthropic) | ✅ | `cache_control: ephemeral` (`router:217-258`), metric `prompt_cache_hits_total` |
| Cost / token tracking | ✅ | `cost_audit.py`, TPM limiter (nhưng process-local → 4× dưới 4 worker) |
| Rate limiting | ✅ | per-tenant 600/min, per-IP 300/min, bypass=paid tier |
| Observability (structured, per-step) | ✅ | structlog + `request_steps` table (FK request_logs) |
| Eval harness (RAGAS) | ✅ | `ragas_metric_adapter.py` |

---

## F. Async / B2B integration

| Best practice | Ragbot | Evidence |
|---|---|---|
| Transactional outbox | ✅ | `FOR UPDATE SKIP LOCKED` (`outbox_publisher.py:97`) |
| Idempotency — submission | ✅ | `IdempotencyService` (`answer_question.py:68`) |
| Idempotency — consume-side | ❌ | handler 2-arg, no inbox_tx → crash-before-ACK **dup cả turn** (`pipeline.py:97`) |
| Dead-letter — outbox / callback | ✅ / ❌ | outbox có DLQ; callback thất bại **drop im lặng** (`callback_delivery.py:150`) |
| Exactly-once | 🟡 | crash-before-ACK dup (cửa sổ hẹp) |
| Webhook security | ✅ | HMAC + SSRF re-resolve |
| Orphan event | ❌ | `chat.answered.v1` produce, 0 consumer (`callbacks.py:120`) |

---

## G. Kiến trúc & Engineering

| Best practice | Ragbot | Evidence |
|---|---|---|
| Hexagonal/DDD, Port+Adapter+DI | ✅ **xuất sắc** | 56 Port, 52 `providers.Singleton`, 0 provider if/elif |
| Config-driven / 12-factor | ✅ | `system_config` (Redis-cached), zero-hardcode (sweep 10/12 sạch) |
| Measured rollout (observe-first feature flags) | ✅ **đúng BP** | guard block ship observe trước, đo FP rồi mới enforce |
| Broad-except discipline | ✅ | 0 un-annotated `except Exception` |
| Typed contracts | 🟡 | `state: Any` untyped trên answer-path (63 `Any` trong query_graph) |
| God-file / SRP | 🟡 | `query_graph.py` 3071 dòng, `generate()` 924 dòng |

---

## 🎯 Ragbot DẪN ĐẦU vs SOTA (mạnh hơn phần đông RAG production)

1. **Deterministic numeric-fidelity gate** — verify từng con số bằng thuật toán (không chỉ LLM-judge). Rất ít hệ RAG có lớp này; đây là anti-HALLU đúng bài (deterministic > LLM-obedience).
2. **Multi-tenant RLS + 4-key identity** — production-grade isolation, test 381 pass.
3. **Observe-first measured rollout** — đúng best practice: đo false-positive trước khi enforce (constitution bắt buộc).
4. **Full bộ SOTA retrieval**: hybrid + cross-encoder rerank + MMR + Contextual Retrieval + late chunking + 2-tier cache.
5. **RAGAS eval + prompt caching + Port/DI** — swap provider = 1 dòng config.

## 🔴 Ragbot TỤT vs SOTA (gap thật, xếp theo mức độ)

1. **[Cơ bản] Degeneration handling THIẾU HẲN** — `repetition_penalty` là chuẩn từ Holtzman 2019 ("Neural Text Degeneration"). Ragbot không có detector, penalty=0 và **không wire tới litellm** → **bug #8 lặp vô hạn**. Đây là gap best-practice cơ bản nhất.
2. **[Quy trình] "Vòng lặp best-practice chưa đóng"** — observe-first là đúng, NHƯNG best practice đòi hỏi **measure XONG → promote sang enforce**. Ragbot đã đo (numeric-fidelity FP 0/84 trên 60Q) nhưng **chưa promote observe→block** → guard vô hiệu trên bot thật → **bug #13 bịa giá lọt**.
3. **[Reliability] Consume-side idempotency + callback dead-letter thiếu** → dup turn (dup cost + dup webhook) + mất câu trả lời khi giao thất bại.
4. **[Advanced] RAPTOR / self-consistency thiếu** — cho bot high-stakes (pháp lý, y tế) nên có hierarchical retrieval + majority-vote để giảm sai số quan trọng (liên quan bug ngưỡng MFA của bot pháp lý QA cũ).
5. **[Cấu hình] Failover chưa seed binding dự phòng** — cơ chế có sẵn (`router:605`), chỉ thiếu 1 binding thứ 2/bot → innocom lỗi = 503 thay vì tự cứu.

---

## Kết luận best-practice (1 câu)

> **Ragbot ở TRÊN trung bình SOTA về kiến trúc + anti-HALLU deterministic + multi-tenant, nhưng lỗi vận hành vì "vòng lặp best-practice chưa đóng":** các guard đã-đo-an-toàn vẫn kẹt observe, degeneration handling (BP cơ bản) thiếu hẳn, và failover/idempotency chưa cấu hình đủ. Gap phần lớn là **"làm rồi chưa bật/chưa đóng vòng"**, không phải **"chưa làm"**.

---

## Việc nên làm (theo giá trị × độ khó, có bằng chứng)

| Ưu tiên | Việc | Loại | Vì sao |
|---|---|---|---|
| 1 | Xây **degeneration detector** trong guard_output + wire penalty | Code mới | BP cơ bản đang thiếu; chặn #8 (nghiêm trọng nhất) |
| 2 | Promote `numeric_fidelity_action=block` (FP đã đo 0/84) | Bật flag | Đóng vòng observe→enforce; chặn #13 |
| 3 | `name_by_shape=True` per-bot | Bật flag | Sửa gốc typing entity |
| 4 | Consume-side ON CONFLICT + callback dead-letter | Code | dup + mất câu |
| 5 | Seed `record_fallback_model_id` per binding | Cấu hình | failover innocom thật |
| 6 | Bật recall (parent_child/neighbor_expand) có A/B 60Q | Bật flag (đo) | coverage listing/so-sánh |

**Nguyên tắc:** mỗi lần bật/sửa 1 cái → đo lại 60Q (HALLU + coverage + FP) → không gộp. Guard TẮT là cố ý observe-first; bật khi đã đo, không bật mù.

---

# PHẦN II — PHÂN TÍCH CHUYÊN SÂU + SUY LUẬN

> Phần I là bảng chấm. Phần II là **suy luận**: vì sao mỗi thiết kế được chọn,
> trade-off gì, chuỗi lỗi ra sao, SOTA làm khác thế nào & tại sao, và hàm ý khi
> đưa vào production. Mỗi lập luận neo vào evidence đã thu thập.

## II.1 — Meta-insight: "Vòng lặp best-practice chưa đóng" (quan trọng nhất)

**Quan sát:** Ragbot có gần đủ bộ guard SOTA, nhưng bug HALLU thật (#13) vẫn lọt. Nghịch lý này chỉ giải được khi hiểu **quy trình rollout**, không phải code.

**Chuỗi suy luận:**
1. Constitution của dự án bắt guard-block phải **ship "observe-first"** — chạy ở chế độ chỉ-đo (không chặn) để thu thập false-positive rate trên tập cố định TRƯỚC khi cho phép nó sửa câu trả lời. Đây là best practice **đúng** (measured rollout, tránh guard mới over-refuse hàng loạt).
2. Numeric-fidelity gate đã qua bước đo: FP = 0/84 trên 60Q (report step 14-15). → Về mặt dữ liệu, nó **đủ điều kiện promote sang block**.
3. NHƯNG không có bước 3 (promote): `DEFAULT_NUMERIC_FIDELITY_ACTION` vẫn = `observe` (`_14:354`), và bot LIVE không override trong `plan_limits`. → Guard chạy như một **máy ghi log đắt tiền**: phát hiện đúng số bịa nhưng vẫn giao cho user.
4. **Hệ quả suy luận:** HALLU=0 mà em báo trên 60Q là **đo dưới điều kiện block-ON** (harness bật block để đánh giá). Bot thật chạy observe → cùng câu hỏi, cùng code, nhưng **kết quả khác** vì một dòng config. Đây là lý do "code review nói tốt" mà "QA nói lỗi" — cả hai đều đúng, khác nhau ở flag.

**Vì sao đây là gap best-practice chứ không phải bug:** Observe-first là nửa đầu của vòng lặp "measure → enforce". Best practice đòi hỏi **đóng vòng**: khi FP đã dưới ngưỡng, PHẢI promote. Ragbot dừng ở nửa đầu. → Fix không phải "viết code" mà "đóng vòng vận hành" (bật flag + đo lại). Đây là bài học tổ chức, không phải kỹ thuật.

**Hàm ý production:** BẤT KỲ bot mới nào cũng thừa hưởng `observe` → cùng lỗ. Muốn dùng thật phải có **quy trình bàn giao bot**: checklist promote các guard đã-đo sang enforce, không để mặc định observe.

## II.2 — Degeneration (#8): vì sao đây là gap "cơ bản" nghiêm trọng nhất

**Suy luận về mức độ:** Trong 2 bug nặng, #13 (bịa giá) là **lọt lưới có sẵn** (guard tồn tại, chỉ chưa bật). #8 (lặp vô hạn) là **không có lưới nào cả** — nghiêm trọng hơn về mặt best-practice vì nó là kỹ thuật cơ bản đã chuẩn hoá từ 2019 (Holtzman et al., "The Curious Case of Neural Text Degeneration").

**Chuỗi lỗi (5 tầng, có file:line):**
```
LLM sinh loop "công ty bảo hiểm xã hội bắt buộc/tự nguyện..." ×hàng trăm
 ← tại SAMPLING: không có lực cản lặp — frequency_penalty=0.0, presence_penalty=0.0 (_07:19-20)
 ← tại WIRING: GenerationParams chỉ mang {temperature, top_p, max_tokens} (model_runtime.py:107-111);
   router complete_runtime chỉ nhận temperature+max_tokens (:570). → penalty ĐỊNH NGHĨA trong
   bot_config schema nhưng KHÔNG BAO GIỜ tới litellm. Cú lừa: owner set penalty cũng vô ích.
 ← tại GUARD: không có detector lặp nào trong generate/guard_output/local_guardrail (grep=0)
 → max_tokens=450 chỉ giới hạn thành 450 token rác, VẪN giao ok:true
```

**Vì sao SOTA không dính:** hai lớp phòng thủ độc lập — (a) `frequency_penalty`/`presence_penalty` tại sampling (giảm xác suất token lặp), (b) detector deterministic ở tầng ứng dụng (đếm tỷ lệ n-gram lặp, vượt ngưỡng → cắt/từ chối). Ragbot thiếu **cả hai**.

**Suy luận về nguyên nhân gốc-tổ chức:** vì sao một hệ tinh vi (numeric-fidelity gate deterministic) lại thiếu cái cơ bản này? Giả thuyết: đội tập trung vào **anti-HALLU nội dung** (số bịa, sai brand) — loại lỗi "nói sai sự thật" — mà bỏ qua **anti-degeneration** — loại lỗi "sinh văn bản hỏng". Đây là hai họ lỗi khác nhau; guard cho họ này không che họ kia. Bài học: **taxonomy lỗi phải đủ rộng** — faithfulness=1.0 không đảm bảo output well-formed.

**Fix đúng tầng (suy luận):** wire penalty là phòng ngừa tại nguồn NHƯNG phụ thuộc provider (innocom/LM Studio có thể bỏ qua param) → không đủ tin. Detector deterministic app-side là **lưới an toàn không phụ thuộc provider** → đúng mindset "deterministic > obedience". → Làm cả hai; detector là bắt buộc.

## II.3 — Retrieval: vì sao recall features TẮT lại hợp lý, nhưng gây bug

**Suy luận về trade-off:** parent_child, neighbor_expand, HyDE, bm25_substring_fallback đều **mở rộng recall** nhưng có giá: mỗi cái thêm chunk vào context (→ nhiều token, chậm hơn, và **loãng** — chunk thừa có thể đẩy chunk đúng ra khỏi top-K rerank). Vì vậy TẮT mặc định là **quyết định cost/precision hợp lý** cho câu hỏi tên trực tiếp (đường dễ, đã đạt L3).

**NHƯNG chuỗi suy luận cho bug coverage:**
1. Câu "so sánh 205/65R16 vs 235/40R18" (#20) là **comparison intent** — cần lấy ĐỦ cả 2 vế.
2. Với recall TẮT: retrieval single-shot cho mỗi size; nếu 1 size nằm ở chunk không lọt top-K (vì bảng lớn, chunk bị cắt) → vế đó "không tìm thấy" dù CÓ trong KB.
3. parent_child ON sẽ kéo cả bảng cha về; neighbor_expand ON sẽ kéo dòng liền kề. → 2 flag này chính là thứ cứu #20.
4. **Suy luận ngược:** vì sao factoid MQ=False mà comparison MQ=True? Vì factoid (1 thực thể) không cần đa dạng hoá query; comparison (nhiều thực thể) thì cần. Thiết kế **đúng ý định** — nhưng #20 lộ ra rằng comparison còn cần recall-expansion (parent/neighbor), không chỉ MQ.

**Hàm ý:** đừng bật recall toàn cục (loãng + đắt). Bật **theo intent**: listing/comparison → parent_child + neighbor_expand ON; factoid → giữ lean. Đây là lý do phải A/B 60Q — để tìm điểm cân bằng precision/recall theo intent, không bật mù.

## II.4 — Resilience: vì sao "failover=True" lại là bẫy nhận thức

**Suy luận về cái bẫy:** đọc `DEFAULT_LLM_FAILOVER_ENABLED=True` (`_06:89`) → tưởng failover đang bảo vệ. Đây là **false sense of safety**. Sự thật cần đọc thêm 2 tầng:
1. Router `_failover_eligible` (`router:644-657`) đòi HỎI binding có `record_fallback_model_id` + `fallback_provider` + `fallback_wire_model_id` — **tất cả** phải populated.
2. Cột đó nullable, DTO default `None` (`model_runtime.py:83`). → Bot mới/mặc định: flag global ON nhưng eligibility=False → **exception gốc re-raise → 503**.

**Suy luận về hậu quả kết hợp với innocom:** innocom bất ổn (cụt/503) là **chắc chắn xảy ra** dưới tải. Failover là cơ chế duy nhất biến "mất câu" thành "câu từ provider dự phòng". Vì failover thực chất TẮT → mọi lỗi innocom detect-được đều thành 503. → **Đòn bẩy #1 để production-ready không phải sửa code mà seed 1 binding dự phòng/bot.**

**Vì sao empty-200 nguy hiểm hơn 503:** 503 là exception → được đếm vào circuit breaker → breaker học được provider hỏng → mở → fast-fail. Empty-200 (`router:785`) KHÔNG phải exception → breaker **không học** → provider hỏng âm thầm, mỗi request đều thử lại từ đầu, user nhận câu rỗng "thành công". → Empty-200 là **lỗi câm** (silent failure) — loại nguy hiểm nhất vì không có tín hiệu.

## II.5 — Anti-HALLU: vì sao Ragbot vừa dẫn đầu vừa lọt lưới

**Suy luận về điểm mạnh:** hầu hết RAG chống HALLU bằng **LLM-judge** (hỏi LLM "câu này có grounded không?"). Yếu điểm: judge cũng là LLM → cũng có thể sai/bịa, và tốn 1 round-trip. Ragbot có thêm lớp **deterministic numeric-fidelity**: trích mọi token số trong câu trả lời, đối chiếu với số trong context/DB bằng thuật toán. → Không phụ thuộc LLM, không tốn round-trip, bắt được đúng loại lỗi nguy hiểm nhất (số bịa). **Đây là thiết kế vượt SOTA phổ thông.**

**NHƯNG chuỗi suy luận cho #13 (vì sao lớp mạnh này vẫn lọt):**
1. Số bịa "260.000" đến từ số rác "26" (cột date1) **NẰM TRONG chunk** được serve.
2. Gate kiểm "số trong câu có xuất hiện trong context không". "26" CÓ trong context → "tồn 26" pass. "260.000"→260000 KHÔNG có → đáng lẽ flag.
3. Nhưng action=observe → chỉ log. → **Ngay cả lớp deterministic mạnh nhất cũng vô hiệu nếu để observe.**
4. Sâu hơn: kể cả block, gate có điểm mù — nếu LLM chỉ lặp lại "26" nguyên văn (không nhân 10000), gate coi là grounded (số có trong chunk) dù ngữ nghĩa sai (26 là ngày, không phải giá/tồn). → Đây là giới hạn bản chất của "số-trong-context": không hiểu **vai trò cột**. Fix gốc = đừng serve số rác (null-price marker + loại cột date khỏi chunk narration + route price-ask sang stats-synthetic có cấu trúc).

**Suy luận tổng:** anti-HALLU của Ragbot mạnh về **cơ chế** nhưng yếu về **hai chỗ**: (a) chưa bật (observe), (b) tầng dữ liệu vẫn để số rác lọt vào chunk. Lớp guard giỏi không cứu được nếu dữ liệu đầu vào đã nhiễm. → Nguyên tắc: **chặn ở nguồn (ingest/serve) rẻ và chắc hơn chặn ở cuối (guard)**.

## II.6 — Suy luận tổng hợp: bức tranh nguyên nhân-hệ quả

Nối tất cả lại thành một luận điểm:

> **Ragbot được xây bởi đội giỏi kiến trúc + giỏi anti-HALLU nội dung, nhưng chưa đóng 3 vòng lặp vận hành.** Ba vòng hở:
> 1. **Vòng measure→enforce** (guard đo xong chưa promote) → HALLU lọt (#13).
> 2. **Vòng taxonomy lỗi** (chống sai-sự-thật nhưng bỏ chống văn-bản-hỏng) → degeneration (#8).
> 3. **Vòng cấu hình chịu lỗi** (cơ chế failover có, binding chưa seed) → innocom lỗi = 503.

Cả 3 đều **không phải "code sai"** mà là **"chưa hoàn tất chu trình"**. Đây vừa là tin tốt (khung không cần đập) vừa là cảnh báo (cần kỷ luật vận hành + checklist bàn giao bot, không chỉ code).

**Dự đoán có điều kiện (GIẢ THUYẾT, cần đo):** nếu (a) bật numeric block, (b) thêm degeneration detector, (c) seed fallback binding → suy luận là 2 bug nặng (#8, #13) bị chặn và innocom-503 giảm mạnh. Coverage (#20, #2) cần bước riêng (bật recall theo intent). **Chưa được phép tuyên bố % cho tới khi chạy 60Q A/B từng bước.**

---

## Tài liệu liên quan (cùng phiên)
- `reports/FLAG_ONOFF_AUDIT_20260713.md` — chi tiết mọi công tắc ON/OFF theo luồng
- `reports/CODE_DEEPDIVE_REVIEW_20260711.md` — review code 5 flow + SOLID
- `reports/PERF_AUDIT_20260711.md` — hotspot performance
- `reports/PERF_LATENCY_INNOCOM_CONTROL_20260711.md` — độ trễ + xử lý lỗi innocom
- `reports/RELIABILITY_FIX_20260710.md` — cap 16→6 + clean re-verify
