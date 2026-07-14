# TRẠNG THÁI TẤT CẢ LUỒNG + DANH SÁCH ĐẦY ĐỦ ĐIỂM CHÊ — 2026-07-13

> Audit trung lập 6 luồng (mỗi luồng 1 agent read-only chấm từng stage OK/WEAK/BROKEN/OFF/ABSENT)
> + verify-S1 + completeness-critic. Mọi grade & flaw kèm evidence `file:line` hoặc dòng log thật
> (`reports/loadtest_innocom_20260713/server_log_window.jsonl`). rule#0: không assert khi không có bằng chứng.
> Bổ trợ cho [CRITIQUE_VERIFICATION_20260713.md](CRITIQUE_VERIFICATION_20260713.md) (kiểm chứng critique).

---

## 0. CHỐT TỔNG (đọc trước)

**KHÔNG có S1-blocker tuyệt đối ở mức code-default** — nghĩa là không request nào *chắc chắn* bịa/leak/crash mỗi lần. NHƯNG cả 6 luồng chung 1 chủ đề: **"tính năng CÓ CODE nhưng đang TẮT / observe / inert / dead-path"** — đúng khớp hiến chương EXISTS ≠ WORKS ≠ VERIFIED. Lỗi mang tính **xác suất** (lưới tắt → HALLU lọt khi xui) và **điều kiện** (isolation/delivery hỏng khi gặp cảnh cụ thể), KHÔNG phải sập hệ thống.

**4 điểm chê "S2 nhưng leo lên S1 theo điều kiện" (nguy hiểm nhất):**
| # | Điểm chê | Leo S1 khi | Evidence |
|---|---|---|---|
| **CS1-a** | Cột số KHÔNG-phải-giá (số lượng/khối lượng) bị đọc NHẦM thành GIÁ | row nhiễm được phục vụ qua `stats_index_route` (live 40/60) → **bịa số cho user** — cùng lớp bug #13 nhưng **cơ chế MỚI** | `document_stats.py:731-761` (`elif _is_pure_money(col)`), suppression cần owner-declare (`:727`, default OFF) |
| **CS1-b** | TẤT CẢ lưới chống-HALLU (numeric/brand/claim/grounding) = **observe**, empty-guard off, degeneration KHÔNG có | provider xui → answer bịa/rỗng/lặp giao thẳng, không lưới chặn | `_14:354,327,363,388`; grep degeneration=0 |
| **CS1-c** | **RLS inert** — app connect bằng superuser `postgres` → 21 policy bị bypass, cô lập đa-tenant còn **1 lớp** (app filter) | 1 query quên `WHERE record_tenant_id` → **leak chéo tenant** im lặng | `.env` DATABASE_URL_APP unset + `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`; `engine.py:67-81` |
| **CS1-d** | **PII "redact" là no-op im lặng** — số ĐT/email/CMND bị *flag* nhưng query gửi nguyên | có PII thật + ràng buộc privacy → PII rò sang gateway bên thứ 3 + lưu DB | `local_guardrail.py:165-168,845-846`; `guard_input.py:44-56` (không mutate `state['query']`) |

---

## 1. BẢNG TRẠNG THÁI 6 LUỒNG

| Luồng | Câu hỏi chất lượng | Phân bố grade | Verdict 1 dòng |
|---|---|---|---|
| **1. Input + Retrieval** | Bot tìm đúng chunk? | 9 OK · 4 WEAK · 1 OFF | 🟢 **Lõi tốt** (hybrid+rerank+mmr+cache chuẩn); WEAK ở rìa: PII no-op, 0-chunk không retry, MQ timeout 45% |
| **2. Grade + Generate + Safety** | Trả lời không bịa? | 4 OK · 4 WEAK · 4 OFF · 1 ABSENT | 🔴 **Yếu nhất** — gần như mọi lưới chống-HALLU tắt/observe; generate OK nhưng "trần" |
| **3. Ingest + Data** | Corpus dựng tốt? | 7 OK · 3 WEAK · 1 OFF | 🟡 **Lõi tốt** (parser registry/chunk/embed OK); WEAK ở stats-index: re-upload xoá, cột nhầm giá, coverage observe |
| **4. Provider + Resilience** | Sống sót lỗi provider? | 2 OK · 4 WEAK · 1 OFF · 2 ABSENT · 1 BROKEN | 🔴 **Danh nghĩa** — CB/failover/TPM tồn tại nhưng inert cho bot mới; retry-under-lock gây nghẽn |
| **5. Interface + Delivery** | Giao đáp án exactly-once? | 1 OK · 3 WEAK · 1 OFF · 1 BROKEN | 🔴 **Rủi ro cấu trúc** — streaming chết giao câu cụt im lặng, redeliver nhân đôi, callback không dead-letter |
| **6. Config + Tenant + Isolation** | Cô lập đa-tenant thật? | 8 OK · 1 WEAK · 1 OFF | 🟢 **Thiết kế chuẩn** (4-key, RLS 3-lớp, cache-scope) NHƯNG **RLS đang TẮT** trong deploy này |

**Đọc bảng:** luồng **1 & 3 & 6** = khung/thiết kế TỐT (đúng "EVOLVE không REWRITE"). Luồng **2 & 4 & 5** = "dây chưa nối hết" — cơ chế có nhưng chưa cắm điện / chưa cover failure mode thật.

---

## 2. DANH SÁCH ĐẦY ĐỦ ĐIỂM CHÊ (dedup, xếp theo mức)

### 🟠 S2 — ảnh hưởng chất lượng / coverage / reliability (~24 mục)

**Luồng 2 (Safety) — nhóm nặng nhất:**
1. LLM grounding judge **non-functional dưới tải** (timeout/degrade 51/60) — backstop chống-HALLU không chạy. *(known)*
2. `grade_timeout_s=2.0s` **THẤP HƠN p95 của chính grader (2.56s)** → grade tự timeout 17/60 → force-pass mọi chunk. `grade.py:204-205` *(NEW)*
3. `grounding_failure_mode=fail_closed` **KHÔNG cover case timeout** — `_grounder_dead` chỉ bật khi `llm_fn=None`; wired mà timeout → fail-OPEN. *(NEW)*
4. `grounding_async_pass` = **trấn an giả** — `query_graph.py:904` log "pass" chỉ vì chạy async. *(NEW)*
5. `grounding_confirmed_action=block` chỉ enforce ở **nhánh parallel guard** (`guard_output.py:806-822`), nhánh serial thì không. *(NEW)*
6. `claim_fidelity` cờ nhầm **stopword/hư-từ** thành claim vô căn cứ → false-positive. *(NEW)*
7. **Mặc định KHÔNG lưới nào STOP answer** — numeric/brand/claim/grounding_confirmed đều observe. *(known → CS1-b)*
8. **understand→intent cascade** hạ 93% câu về intent=FACTOID fallback → **retrieval + context budget CHẶT NHẤT** (khi UnderstandOutput fail). `understand.py:311`, `_01:239`, `_16:41-49` *(NEW, critic)*
9. **Context char-cap mặc định 2900** — chính code ghi "quá chặt"; drop chunk điểm-thấp. `_16:27`, `generate.py:575` *(NEW, critic)*

**Luồng 1 (Retrieval):**
10. **PII "redact" no-op** — PII gửi nguyên sang gateway + lưu DB. *(NEW → CS1-d)*
11. **retrieve 0-chunk → nhảy thẳng generate**, bỏ vòng `rewrite_retry` recovery → refuse-oan. `routing.py:223-225` *(NEW)*
12. `multi_query` ON nhưng **timeout ~45% (27/60)** dưới provider chậm → về single-query (0 biến thể recall) mà vẫn tốn 5s/câu. `retrieve.py:1361-1368` *(known)*
13. `stats_index_route` (40/60) **thay hoàn toàn hybrid semantic** bằng SQL lookup — đúng khi có stats, nhưng bỏ rerank/grade. *(known)*

**Luồng 3 (Ingest):**
14. **Re-upload 1 phần XOÁ stats-index của mọi entity KHÔNG đổi** — đổi 3/500 chunk → 497 entity mất giá/tên khỏi stats path (vector còn, SQL mất). `ingest_core.py:656-659`, `ingest_stages_final.py:442,497`, `stats_index_repository.py:260` *(NEW, borderline-S1)*
15. **Cột số không-phải-giá đọc nhầm thành giá** → bịa số. `document_stats.py:731-761` *(NEW → CS1-a)*
16. Giá đúng ngoài `[10k, 500M]` VND **bị drop im lặng** (xe 800M, BĐS tỷ) — coupling tiền tệ. `_21:85,90` *(known)*
17. Invariant lossless-coverage **observe-only** — số nguồn bị drop chỉ log, không chặn/re-chunk. `ingest_stages.py:864-905` *(NEW)*
18. CSV gắn nhãn `text/plain` → **route sang parser prose**, mất cấu trúc row. `parser/registry.py:172-179`, `markdown_parser.py:84-87` *(NEW)*
19. Gate T012 **drop entity không-giá của comma-CSV**. `document_stats.py:1215-1221` *(NEW)*

**Luồng 4 (Provider):**
20. **TPM limiter chỉ wire vào `_complete_via_llmport` — 0 caller live** → toàn bộ answer+ingest bỏ qua pacing. `router:1161` vs live `complete_runtime` *(NEW)*
21. **Router resilience chỉ bọc 1 phần** — structured-output + enrich gọi litellm trực tiếp, **bỏ qua CB/semaphore/retry/TPM**, fail → None im lặng. `structured_output_helper.py:429` *(NEW)*
22. **CB không bao giờ mở** dưới lỗi rải rác (~91% success reset counter). `retry_policy.py:162,177` *(known)*
23. `connect_timeout_ms(5000)` + `max_retries(2)` DB-knob **dead**, không forward. `model_runtime.py:30-31` *(NEW)*
24. **Semaphore giữ slot suốt retry+backoff** → 6 call treo bão hoà lane cap=6 → head-of-line blocking. `router:743-744` (duration tới 91260ms) *(NEW)*
25. **Retry amplification** — 3 lần của mình × litellm internal (244 "Retrying request") ≈ 9× đập gateway đang 500. *(known)*
26. **Failover tắt cho bot deployed** — cần fallback binding mà bot 1-provider không có. `router:653-657` *(known)*
27. **Empty-200 trên sync path trả về như success** (record CB success), trong khi stream path chặn 0-token. `router:785` vs `1027-1034` *(NEW)*

**Luồng 5 (Delivery):**
28. **Streaming provider chết → câu CỤT, frame `done`, KHÔNG error signal, không retry** (bytes đã gửi). `_sse_helper.py:180-297` *(NEW, BROKEN)*
29. **Recovery đòi lại chat đang chạy** (>30s idle, budget 60s) → **xử lý trùng** → double cost/message/callback. `redis_streams_bus.py:504,572-591` *(NEW)*
30. **Consume-side non-idempotent** — crash giữa chừng → redeliver nhân đôi row + callback. `pipeline.py:290`, `message_repository.py:75` *(known)*
31. **Callback không dead-letter/async-retry** — 3 lần sync fail → terminal `delivery_failed`; outbox `ChatAnswered` **0 consumer**. `callback_delivery.py:150`, `callbacks.py:314` *(NEW)*
32. `pipeline_timeout` **không enforce trên sync HTTP path** (đúng path load-test). `chat_routes.py:474` *(known)*

**Luồng 6:**
33. **RLS inert** (superuser runtime). *(known → CS1-c)*

**Critic bổ sung:**
34. **Booking slot-filling chạy LIVE trên bot Q&A xe** — 23 call SlotSchema_booking, 11 fail 500, bỏ qua resilience. `slot_extractor.py`; log `SlotSchema_booking` *(NEW)*
35. **Server hoàn tất + persist request client đã bỏ** (0b372e10 chạy 201s quá 180s). *(NEW)*

### 🟡 S3 — nợ kỹ thuật / nhỏ (~15 mục)

36. **KHÔNG có degeneration/repetition detector** trong grade→generate→guard (bug #8). *(NEW)*
37. `secret_scanner` chỉ bắt 3 dạng key (`sk-`/`ghp_`/`AKIA`) — **lọt Google `AIza`, JWT, bearer**. *(NEW)*
38. Regex citation-marker **thoả mãn quá dễ** (match mọi `[...]`). *(NEW)*
39. `numeric_fidelity` **log tên event 'observe' NGAY CẢ KHI block** — audit-integrity. `guard_output.py:163-176` *(NEW)*
40. `brand_scope` **cùng bug** — log `brand_scope_observe` khi action=block. `guard_output.py:265` *(NEW, critic)*
41. Grounding intent gate **loại hallu_trap/oos/chitchat** khỏi kiểm chứng. *(NEW)*
42. `guardrail provider` **hardcode 'local' trong DI** — vi phạm Strategy+DI. *(NEW)*
43. `DEFAULT_RERANKER_PROVIDER` **stale = 'jina'** (live = zeroentropy). *(NEW)*
44. `embedding_model_mismatch` warning **false-positive** (so tên prefixed vs bare). *(NEW)*
45. **embed không có guard dim per-vector** (chỉ per-batch count). *(NEW)*
46. **Duplicate constant price-band** (SSoT drift). *(NEW)*
47. **Unclosed aiohttp ClientSession leak** dưới bão retry (fd/socket). log `0b372e10` *(NEW)*
48. **Callback HMAC signing bỏ qua im lặng khi không có secret**. `callback_delivery.py:39,110` *(NEW, security)*
49. **semantic-cache preflight dead check** firing 100% (60/60 `no_embedding_column`). `query_graph.py:2877` *(NEW)*
50. **outbox ChatAnswered 0 consumer**; **precedence config ghi ngược code**; **bảng `tenants` ngoài 21 policy RLS**; **bg-lane concurrency hardcode 4**. *(NEW)*

---

## 3. ĐỐI CHIẾU 1 MÂU THUẪN (vì sao cross-check quan trọng)

Agent Luồng-1 chấm **understand = OK** và gán lỗi repair là *"innocom-gateway JSON corruption"*. **SAI.** Bằng chứng cứng workflow #1 + adversarial + critic: **56/60 (93%)** trace fail là **lỗi hợp đồng schema BÊN MÌNH** (`UnderstandOutput` cấm field `query`, xem CRITIQUE_VERIFICATION §1-A). → **Reclassify understand = WEAK.** Critic tự bắt lỗi này ("FLOW 1 understand OK → should be WEAK"). Đây là lý do phải chạy critic + đối chiếu, không tin 1 agent đơn.

---

## 4. GÓC CHƯA AUDIT (giới hạn phạm vi — honest)

Critic chỉ ra 7 góc chưa luồng nào chấm — cần vòng sau:
1. **Structured-output/JSON-repair subsystem** (`structured_output_helper.py`) — driver lỗi live lớn nhất mà không luồng nào chấm như 1 stage.
2. **Intent blast-radius** — intent sai kéo theo 4 hành vi hạ nguồn (decompose/rewrite/MQ/budget).
3. **Context/token-budget packing** (rerank→generate) — char-cap funnel.
4. **Cost/LLM-call amplification** (T2) — ~10 call/câu, 62 repair.
5. **Action/slot-filling** (`slot_extractor.py`) chạy live trên bot Q&A.
6. **SSE keepalive/heartbeat** — sống sót idle-timeout.
7. **Schema-drift vs DB deployed** (`no_embedding_column` 60/60).

---

## 5. THỨ TỰ FIX ĐỀ XUẤT (mỗi bước đo lại 60Q)

**Nhóm A — sửa rẻ, chặn HALLU/coverage (làm trước):**
1. `UnderstandOutput` nhận `query` alias (`AliasChoices`) — bỏ 1 round-trip + hết cascade demote 93% (fix #8-cascade). *(luồng 1+2)*
2. Nâng `grade_timeout_s` ≥ 3s (trên p95 2.56s) — hết force-pass 17/60. *(luồng 2)*
3. Cột nhầm-giá (CS1-a): chỉ đọc price khi header ∈ role-tokens HOẶC owner-declare — chặn bịa số. *(luồng 3)*
4. Sửa audit-integrity: log đúng action (observe vs block) cho numeric + brand. *(luồng 2)*

**Nhóm B — chống fail-open dưới tải:**
5. grounding + degeneration guard **deterministic** (không gọi LLM) — vì LLM-guard timeout dưới tải. *(luồng 2)*
6. Enforce `pipeline_timeout` trên sync HTTP path. *(luồng 5)*
7. Không giữ semaphore suốt retry/backoff; cắt retry call phụ khi 500. *(luồng 4)*

**Nhóm C — delivery/isolation (điều kiện):**
8. Streaming: emit `error` frame khi provider chết giữa chừng. *(luồng 5)*
9. Consume-side idempotency (ON CONFLICT) + callback dead-letter (nối outbox ChatAnswered). *(luồng 5)*
10. Bật `DATABASE_URL_APP=ragbot_app` nếu cần cô lập đa-tenant thật. *(luồng 6)*

**Nhóm D — data-loss & re-upload:**
11. Re-upload 1 phần: giữ stats-index của entity không đổi (đừng wipe-all). *(luồng 3)*

**Không đụng (đã chuẩn):** hybrid retrieve/rerank/mmr, parser registry, chunk/embed core, 4-key resolve, RLS *thiết kế*, cache-scope, config precedence resolver.

---

## 6. Phương pháp
6 agent flow-audit (read-only) + verify-S1 (0 S1 → 0 verify chạy) + completeness-critic. 7 agent, 0 lỗi, ~1.07M token. Nguồn: branch `fix-260623-ingest-expert` + log load-test 13/07. Journal: `subagents/workflows/wf_78a848c2-1c1/journal.jsonl`.
