# [T1-Smartness] Phase A — Fix BUG-1 CONFLATE giá (price-of-named-entity routing)

> Status: **DRAFT — chờ user approve trước khi đụng `src/`** (CLAUDE.md /plan + bug-investigation mandate).
> Tier: T1 (RAGBOT trả lời thông minh — faithfulness). Layer fix: **routing/retrieval** (đúng tầng, KHÔNG sysprompt).
> Evidence-grounded: mọi điểm dưới đã verify `file:line` trực tiếp trong phiên 2026-06-18.

---

## BUG INVESTIGATION (5-step, bắt buộc)

### 1. Bug gì — reproduce
- **Câu hỏi**: "<dịch-vụ-X> giá bao nhiêu?" (factoid giá, named-entity, KHÔNG có code) — đo bằng load-test §0.
- **Đáp án đúng**: `price_primary` của X trong `document_service_index` (1 row = 1 giá có nhãn).
- **Bot trả**: giá của **entity KHÁC** (conflate) hoặc refuse sai "chưa có giá".
- **Brittleness**: cùng câu viết 6 cách → 6 đáp án khác nhau (1 đúng / 3 refuse / 2 conflate).
- **Evidence**: `reports/PROJECT_ALL_FLOWS_20260618.md` §0 BUG-1 (load-test thật, bypass_cache).

### 2. Nguyên nhân trực tiếp
- **Layer fail**: routing (query understanding → retrieve).
- **Số liệu**: query "giá X bao nhiêu" → `parse_list_query` **return None** → `parse_range_query` None (không có range token) → **rơi vector path** → chunk co-occur đa-dịch-vụ → LLM gán nhầm.
- **Vị trí**: [query_range_parser.py:374-377](../../src/ragbot/shared/query_range_parser.py#L374-L377) — `if "gia bao nhieu" in folded or "bao nhieu tien" in folded: return None`. Docstring tự ghi "Let parse_range_query / vector handle it" — **nhưng KHÔNG có route nào nhận price-of-entity** → vector mặc định.
- **Evidence**: đã đọc trực tiếp file 2026-06-18; agent A DUMB-1 xác nhận độc lập.

### 3. Gốc rễ (chain — immutable cause)
```
bot trả giá sai (conflate)
 ← LLM thấy chunk chứa NHIỀU dịch vụ + giá co-occur                 (Noise Problem)
 ← vector path kéo chunk đa-entity (centroid embedding lẫn)
 ← query "giá X" KHÔNG sản xuất RangeFilter nào → fallthrough vector
 ← parse_list_query:376 cố tình loại price-factoid, KHÔNG có parser price-of-entity thay thế
```
- **Immutable cause**: **catalog price-of-entity Q&A đi fuzzy-vector thay vì structured-first.** Hệ thống đã có route deterministic (`operation="keyword"` → `query_by_name_keyword`, 1 row = 1 giá có nhãn — verified [stats_index_repository.py:418-494](../../src/ragbot/infrastructure/repositories/stats_index_repository.py#L418-L494)) nhưng price-of-entity KHÔNG được đẩy vào đó.

### 4. Expert solution — đúng tầng + SOTA
- **Tầng fix**: routing parser + retrieve wiring (khớp gốc rễ). KHÔNG vá sysprompt (bài học spa-07: vá retrieval bằng sysprompt = sai tầng).
- **Pattern**: **Structured-first / Self-Query routing** (LlamaIndex SQLAutoVectorQueryEngine; Langchain Self-Query). Catalog factoid → structured SQL deterministic; vector chỉ cho unstructured/knowledge.
- **2 mức**:
  - **A1 (short-term, surgical, plan này)**: thêm parser `parse_price_of_entity_query` → sản xuất `RangeFilter(operation="keyword", keyword=<entity>)`, **tái dùng nguyên `query_by_name_keyword` đã có**. Zero hạ tầng mới.
  - **A-next (mid-term, plan riêng)**: thêm `query_type`+`entity_name` vào `UnderstandOutput` schema → LLM intent+entity extractor thay regex pile (đa ngôn ngữ). Lớn hơn, cần plan + A/B riêng — KHÔNG trong Phase A.
- **Tại sao A1 đúng case này**: `query_by_name_keyword` trả per-row có nhãn (`entity_name`+`price_primary`) → 1 entity = 1 giá atomic → **conflate bất khả thi by-construction**; rỗng → fallback vector an toàn (sequential default retrieve.py:524-573).

### 5. CLAUDE.md compliance (tự audit)
| Sacred rule | Check |
|---|---|
| #1 zero-hardcode | ✅ signals/confidence → `shared/constants` + per-bot `pcfg` override (giống `stats_code_lookup_enabled`) |
| #2 Strategy+DI | ✅ tái dùng port `stats_index_repo`, không hard-code provider |
| #4 tenant isolation | ✅ `query_by_name_keyword` scoped `record_bot_id` + RLS |
| #6 4-key identity | ✅ không đụng |
| #7 tests real | ✅ TDD failing-test-first, assert giá đúng entity |
| #8 domain-neutral | ✅ key trên SHAPE "price-ask + residual keyword", KHÔNG brand/service literal |
| #9 T1/T2/T3 | ✅ T1-Smartness declared |
| #10 no app-inject/override | ✅ chỉ đổi ROUTE; LLM vẫn đọc chunk thật, không chèn/ghi đè answer |
| #11 model tier | ✅ edit ở main session Opus; subagent chỉ read-only |
| HALLU=0 | ✅ structured route giảm conflate; refusal trap honored |

---

## SCOPE & SUCCESS CRITERIA

**In-scope (A1 — core, ship đầu tiên):** route price-of-named-entity → stats `query_by_name_keyword`.
**Defense-in-depth (A2/A3 — cùng phase, ship sau A1 gate):** per-row chunk exclusive; numeric grounding cho giá.

**Success (load-test gate — PHẢI pass trước khi đóng phase):**
- Conflate rate trên 6-phrasing price trap: **0/6** (baseline 2-5/6).
- Coverage (price-factoid có trong corpus → trả đúng): **≥ 0.95**.
- HALLU fabricate: **0** (sacred).
- KHÔNG regression: full pytest pass; legal "Điều N giá" vẫn skip stats (structural guard); list/count vẫn đúng.
- Latency price-factoid: KHÔNG tăng (stats path nhanh hơn vector); đo p50/p95 trước/sau.

---

## IMPLEMENTATION PHASES

### A1 — Routing: price-of-entity → stats keyword (CORE)
**Files:**
- `src/ragbot/shared/query_range_parser.py` — thêm `parse_price_of_entity_query(query) -> RangeFilter|None`:
  - Detect price-ask signal (folded): `"gia bao nhieu" | "bao nhieu tien" | "bao nhieu mot" | "gia" + "?" | "bao tien" | "tinh tien" | "gia the nao" | "how much" | "price of"` → constants `_PRICE_ASK_SIGNALS`.
  - **Guard loại trừ**: nếu có range token (dưới/trên/từ-đến/khoảng) hoặc superlative → return None (để parse_range_query xử lý). Nếu có structural anchor (Điều/Khoản) → return None.
  - Extract entity keyword: strip `_PRICE_ASK_SIGNALS` + tái dùng `_LIST_STRIP_PHRASES` (price-ask + service-noise), word-boundary, giữ diacritics. `len(kw) >= DEFAULT_PRICE_ENTITY_MIN_KEYWORD_LEN(2)` else None.
  - Return `RangeFilter(operation="keyword", keyword=kw, confidence=DEFAULT_PRICE_OF_ENTITY_CONFIDENCE)`.
  - Sửa `parse_list_query:376`: KHÔNG `return None` mù — để price-factoid rơi xuống parser mới (đổi thứ tự ở caller, KHÔNG xóa guard list).
  - Add to `__all__`.
- `src/ragbot/shared/constants/<appropriate _NN file>.py` — `_PRICE_ASK_SIGNALS`, `DEFAULT_PRICE_OF_ENTITY_CONFIDENCE`, `DEFAULT_PRICE_ENTITY_MIN_KEYWORD_LEN`, `DEFAULT_STATS_PRICE_OF_ENTITY_ENABLED=True` (per-bot opt-out).
- `src/ragbot/orchestration/nodes/retrieve.py` — wire **sau** `_parse_code_query` (216-220), **trước** `_parse_list_query` (226-227):
  ```
  if _range_filter is None and pcfg(stats_price_of_entity_enabled): _range_filter = _parse_price_of_entity_query(_raw_query)
  ```
  (Thứ tự: range → code → **price-of-entity** → list. Price-of-entity trước list để "X bao nhiêu" không bị list trả ALL X — fix case A trong §2 báo cáo.)
- `src/ragbot/orchestration/query_graph.py` — inject `_parse_price_of_entity_query` vào DI closure cạnh `_parse_list_query`/`_parse_code_query` (verify cách 3 parser kia được inject).

**TDD (failing test FIRST):**
- `tests/unit/test_price_of_entity_routing.py`:
  - `parse_price_of_entity_query("<X> giá bao nhiêu")` → `operation="keyword", keyword~="<X>"`.
  - 6 phrasings đều ra keyword route (không None, không list-all).
  - Range/superlative/structural → None (không hijack).
  - "giá trị điều luật" (knowledge, no entity-price) → None hoặc keyword fallback an toàn.
- `tests/integration/...` (nếu có stats fixture): keyword route → `query_by_name_keyword` → chunk có nhãn đúng entity.

### A2 — Ingest per-row exclusive (defense-in-depth)
**Files:** `src/ragbot/shared/chunking/csv_chunker.py` — đảm bảo `table_csv` per-row, KHÔNG emit group-chunk co-occur; RFC-4180 cho multi-line cell. *(Chi tiết theo DEEPDIVE_CHUNKING §3 #1/#3.)* Chỉ làm sau khi A1 pass gate.

### A3 — Numeric grounding cho giá (hardening, optional)
**Files:** `src/ragbot/infrastructure/guardrails/local_guardrail.py` — numeric claim map về đúng `chunk_id` (không chỉ "có mặt corpus"). Cân nhắc; có thể tách plan riêng.

---

## RISK / ROLLBACK
- **Risk**: over-route "giá X" khi X không phải catalog entity → keyword rỗng → đã có fallback vector (an toàn). Mitigate: confidence floor + min-keyword-len + guard range/structural.
- **Risk**: "giá" xuất hiện ngữ cảnh phi-giá → guard bằng yêu cầu residual keyword đủ dài + per-bot opt-out flag.
- **Rollback**: flag `stats_price_of_entity_enabled=false` per-bot/system_config → tắt route mới, về hành vi cũ ngay, KHÔNG redeploy.

## VERIFY CHECKLIST (trước commit)
- [ ] Failing test viết trước, fail đúng lý do, rồi pass sau fix.
- [ ] `scripts/verify_fixes_loadtest.py` 6-phrasing: conflate 0/6, Coverage ≥0.95, HALLU 0.
- [ ] Full `pytest` pass, không regression (legal/list/superlative).
- [ ] grep zero-hardcode: signals/confidence ở constants, không inline.
- [ ] Latency price-factoid p50/p95 không tăng (đo trước/sau).
- [ ] Self-audit sacred 11/11 (bảng trên).

## NOTE — data local
DB local 5434 đang rỗng. Load-test gate cần: `alembic upgrade head` + nạp corpus có price entity. Nếu chưa có data → unit test A1 chạy được, nhưng **integration/load-test gate phải hoãn tới khi có data** (honest: không claim "verified" khi chưa chạy được trên data thật).
