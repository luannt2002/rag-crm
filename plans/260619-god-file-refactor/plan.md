# [T3-Refactor] Tách god-file về chuẩn ≤1000 dòng

**Audit 2026-06-19:** 632 file / 119,995 dòng. **95% (599 file) ≤600 dòng = sạch.**
Chưa đạt: 🔴 2 file >1500 · 🟠 4 file 1000–1500 · 🟡 27 file 600–1000.
Tier **T3 (ưu tiên thấp nhất)**. Mỗi file: **behavior-preserving + test 5926 xanh + re-export shim** giữ import path.

## Thứ tự ưu tiên
1. `nodes/retrieve.py` 1888 — **god-FUNCTION** (1 hàm ~1740 dòng), khó nhất
2. `query_graph.py` 2828 — tiếp Phase 6-E (đang dở)
3. `model_resolver/__init__.py` 1103 — **god-CLASS** (~30 method)
4. `llm/dynamic_litellm_router.py` 1180
5. `chat_routes.py` 1202 + `ingest_stages_store.py` 1002

---

## 1. orchestration/nodes/retrieve.py (1888) — 1 hàm `retrieve()` ~1740 dòng
**Hiện tại:** 1 hàm `retrieve()` (dòng 148→cuối) gộp các block: stats_index race/lookup · structural_ref
fallback · hybrid search · multi-query fan-out + RRF · metadata filter · vector fallback · ordering.

**Đề xuất (sub-file — tách block → helper module, KIỂU: function không class):**
```
nodes/retrieve.py                        -> giữ retrieve() THIN (chỉ điều phối, gọi helper) ~300-400 dòng
nodes/_retrieve/stats_route.py           -> stats_index race + lookup
nodes/_retrieve/hybrid_exec.py           -> _run_hybrid_for_query, _race_vector, gọi hybrid_search
nodes/_retrieve/mq_fanout.py             -> multi-query expand + RRF merge
nodes/_retrieve/structural_fallback.py   -> structural_ref fallback
nodes/_retrieve/embed_prep.py            -> _embed_query / _embed_batch_queries / _prewarm_embedding_cache
```

## 2. orchestration/query_graph.py (2828) — tiếp Phase 6-E, mục tiêu <1200
**Hiện tại:** import 22 node đã tách + helpers state-resolver + `build_graph` + wiring di_kwargs.

**Đề xuất (sub-file):**
```
query_graph.py            -> giữ build_graph + re-export (THIN)
graph_state_resolvers.py  -> _lang, _resolve_xml_wrap_enabled, _resolve_generate_schema,
                             _understand_greeting_short_circuit, _required_channel_type,
                             _resolved_oos_template, _oos_text   (pure state->value)
graph_builder.py          -> build_graph() + add_node/add_edge + di_kwargs threading
nodes/retry_hybrid.py     -> retry_hybrid_with_original
```

## 3. services/model_resolver/__init__.py (1103) — 1 class ~1000 dòng, ~30 method
**Hiện tại:** `ModelResolverService` gộp 3 concern:
- resolve_* (public API): llm/reranker/embedding/prompt/runtime/cascade/fallback_chain/preview
- cache: invalidate*/bootstrap_cache/cache_status/_l1_*/_get_cached/_serialize/_deserialize
- binding/spec: _binding_to_spec/_build_runtime/_select_primary_with_ab/_first_spec_from_cached/_cost_tier

**Đề xuất (KIỂU: class + MIXIN — vì là god-CLASS có state `self._cache/_l1`, giữ 1 class public):**
```
model_resolver/__init__.py    -> re-export ModelResolverService (THIN)
model_resolver/service.py     -> class ModelResolverService(ResolveMixin, CacheMixin, BindingMixin)
model_resolver/_cache_mixin.py    -> CacheMixin (toàn bộ method cache)
model_resolver/_binding_mixin.py  -> BindingMixin (binding/spec)
```
→ Mixin giữ **1 interface** ModelResolverService duy nhất → caller KHÔNG đổi.

## 4. infrastructure/llm/dynamic_litellm_router.py (1180)
**Hiện tại:** helpers thuần (compute_cost_usd, _apply_anthropic_cache_control, _resolve_effective_temperature,
_is_rate_limit, _is_anthropic_model, _safe_uuid) + class `DynamicLiteLLMRouter(LLMPort)` ~870 dòng.

**Đề xuất (tách helper ra file, class mỏng lại — đã đúng Port/Adapter, KHÔNG cần thêm Strategy):**
```
llm/_litellm_helpers.py        -> 6 helper thuần ở trên
llm/dynamic_litellm_router.py  -> class DynamicLiteLLMRouter (import helper) -> còn ~700-800
```

## 5+6. chat_routes.py (1202) + ingest_stages_store.py (1002)
```
chat_routes.py        -> chat_routes.py (chat) + chat_stream_routes.py (stream) + chat_debug_routes.py
ingest_stages_store.py-> embed_store.py (embed+bulk-insert) + narrate_store.py (narrate-then-embed) + late_chunk.py
```

---

## Quy tắc an toàn (BẮT BUỘC mọi file)
1. **Re-export shim**: file cũ `from .new_module import *` → import path caller KHÔNG đổi.
2. **Behavior-preserving**: KHÔNG đổi logic, CHỈ di chuyển code. Pin test trước.
3. **Từng file = 1 commit**. Chạy `pytest tests/unit` (5926) sau mỗi file. Rollback dễ.
4. **Underscore helper dùng ngoài** → re-export explicit (`from .x import _foo`) vì `import *` bỏ qua `_name`.
5. **T3**: KHÔNG ưu tiên hơn T1 (smartness). Làm khi không có việc T1/T2.
6. Không drive-by: chỉ đụng file trong scope, không sửa logic kèm theo.

## Chốt nhanh: class hay sub-file?
| God-file | Kiểu | Vì sao |
|---|---|---|
| retrieve.py, query_graph.py, ingest_stages_store.py | **sub-file (function)** | node/stage = function thuần, không state |
| model_resolver | **class + mixin** | god-CLASS có state, cần giữ 1 interface |
| dynamic_litellm_router | **tách helper + class** | đã là Port adapter, chỉ cần lôi helper ra |
| chat_routes | **sub-router** | FastAPI router tách theo concern |
