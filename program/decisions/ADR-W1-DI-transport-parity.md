# ADR-W1-DI — Transport parity cho get_graph DI + initial_state (chat_worker ≡ chat_stream ≡ test_chat)

> Phase 3 ADR · Program "Ragbot Expert Build" · Wave W1
> Status: **DRAFT — chờ user approve tại Gate** · Author: ADR-author Phase 3 · Date: 2026-06-10
> Nguồn gap: P2-K 🐛-K1 + ↔️-K2 (`program/gaps/P2-K-api-channel-schema.md` §3) · P2-A 🐛-2 + Q10 (`program/gaps/P2-A-rag-pipeline.md` §4, §5.1)
> Tier: **[T1-Smartness]** (4 deps None = HyDE/uq-cache/stats/parent-child silent-off → retrieval kém đi) + T2 phụ (KeyError crash path SSE)

---

## 1. RÀNG BUỘC BINDING (đọc trước khi implement)

- **STANCE = EVOLVE.** Graph singleton + state-lift (`src/ragbot/orchestration/query_graph.py:8058-8084`, commit `65b2c10`) nằm trong shortlist **"ĐÃ CHUẨN — đừng đụng"** (P2-A §5.1 item 1). ADR này **KHÔNG bỏ singleton, KHÔNG đổi ignore-kwargs-after-first-build semantics, KHÔNG đổi `build_graph` signature**. Fix duy nhất = cách các transport **CUNG CẤP** deps (kwargs lần build đầu) + state (per-request) — tức là sửa **callsite assembly**, không sửa engine.
- Split plan `plans/260609-query-graph-split/plan.md` (GraphDeps frozen-dataclass, 24 DI handles) là T3 riêng, anchor `bf5b77f`. ADR này phải **forward-compatible** với plan đó (xem §8).

---

## 2. CONTEXT — bug chain (evidence từng mắt xích)

### 2.1 Singleton ignore-kwargs (by design, ĐÚNG — không phải bug)

`query_graph.py:8062-8078`:

```python
async def get_graph(**di_kwargs: Any) -> Any:
    global _GRAPH_SINGLETON
    if _GRAPH_SINGLETON is not None:
        return _GRAPH_SINGLETON            # :8073-8074 — kwargs các lần sau bị ignore
    async with _GRAPH_SINGLETON_LOCK:      # :8075 (lock khai báo :8059)
        if _GRAPH_SINGLETON is None:
            _GRAPH_SINGLETON = build_graph(**di_kwargs)   # :8077 — first caller wins
    return _GRAPH_SINGLETON
```

Docstring `:8065-8070` nói rõ tiền đề an toàn: *"The DI singletons (llm, model_resolver, vector_store, ...) are themselves process-wide so reusing the compiled graph across calls is correct: **it always closes over the same handles**."* Tiền đề này **chỉ đúng khi mọi callsite truyền CÙNG một bộ kwargs**. Hôm nay thì không:

### 2.2 SỰ THẬT — 4 production callsite, mỗi cái tự hand-roll kwargs, 3 bộ khác nhau

| Callsite | File:line | Số kwargs | Thiếu so với full 24 |
|---|---|---|---|
| chat_worker (async 202) | `src/ragbot/interfaces/workers/chat_worker.py:1376-1401` | 24 | — (full; `understand_query_cache` `:1394`, `hyde_generator` `:1395`, `stats_index_repo` `:1397`, `doc_repo` `:1398`) |
| chat_stream (SSE prod) | `src/ragbot/interfaces/http/routes/chat_stream.py:243-264` | 20 | **`understand_query_cache`, `hyde_generator`, `stats_index_repo`, `doc_repo`** |
| test_chat sync (demo) | `src/ragbot/interfaces/http/routes/test_chat.py:3031-3055` | 23 | **`understand_query_cache`** |
| test_chat stream (demo) | `src/ragbot/interfaces/http/routes/test_chat.py:3555-3579` | 23 | **`understand_query_cache`** |

`build_graph` default tất cả 4 tham số này về `None` (`query_graph.py:1158-1162`) và **THẬT SỰ dùng** chúng:
- `hyde_generator` → `:1698-1711` (HyDE hypothetical-answer retrieval)
- `understand_query_cache` → `:2087` (`_uq_cache = understand_query_cache`)
- `stats_index_repo` → `:3095` (price-range query), `:3188`
- `doc_repo` → `:3123-3142` (parent-child chunk link), `:3520-3524` (corpus summaries)

Provider cho cả 4 **tồn tại sẵn trong container** (`src/ragbot/bootstrap.py:205` understand_query_cache, `:405` document_repo, `:413` stats_index_repo, `:503` hyde_generator) — đây là bug **"dây chưa nối"** đúng nghĩa strangler-fig, không thiếu hạ tầng.

### 2.3 SỰ THẬT — blast radius refined theo process topology (phát hiện mới so với P2-K)

Singleton là **per-process**. Topology host hiện tại (đo 2026-06-10):
- API = uvicorn 1 worker (pid 95207, `--workers 1`); embedded workers trong API process **chỉ có** document_consumer + outbox_publisher + recovery (`src/ragbot/interfaces/http/embedded_workers.py:164-180`) — **KHÔNG có chat consumer embedded**.
- `ragbot-chat-worker.service` = process riêng (`python -m ragbot.interfaces.workers.chat_worker`, `chat_worker.py:1760` `main()`), hiện **inactive/disabled** trên host này (`systemctl status` 2026-06-10).

Hệ quả refine P2-K 🐛-K1:
1. **Worker process** (khi chạy): singleton riêng, luôn build full 24 kwargs → worker KHÔNG BAO GIỜ bị stream làm degrade. Câu "None cho TOÀN BỘ request kể cả worker path" trong P2-K chỉ đúng nếu chat consumer chạy embedded cùng process API — hôm nay không.
2. **API process**: singleton build bởi callsite ĐẦU TIÊN trong {chat_stream, test_chat sync, test_chat stream}. Vì **KHÔNG callsite API nào truyền `understand_query_cache`** → trong API process, `understand_query_cache = None` **DETERMINISTIC dưới MỌI warm-up order** (không phải race — là chắc chắn tắt). 3 deps còn lại (hyde/stats/doc_repo) = race: test_chat build trước thì có, chat_stream build trước thì None.

**GIẢ THUYẾT (CHƯA verify bằng runtime test)**: mức độ ảnh hưởng thực tế lên answer quality của SSE/demo path phụ thuộc flag (`hyde_enabled`, uq-cache enabled, stats race...). Cần bước VERIFY §5-V trước khi claim % lift.

### 2.4 SỰ THẬT — initial_state divergence (per-request, KHÔNG bị singleton che)

Worker `chat_worker.py:1441-1473` vs stream `chat_stream.py:304-332`:

| State key | Worker | Stream | Consumer trong graph | Hệ quả trên SSE |
|---|---|---|---|---|
| `workspace_id` | `:1448` ✅ | **THIẾU** (đã resolve ở `:128` nhưng không đặt lên state) | `query_graph.py:7417` — **direct subscript** `state["workspace_id"]` trong arg của `_bg_cache_write` (persist node) | `GraphState` là `TypedDict, total=False` (`src/ragbot/orchestration/state.py:9`) → **KeyError trong persist** khi đủ điều kiện cache-write (`:7365-7370`: semantic_cache wired ∧ answer non-empty ∧ không cache-hit ∧ không refuse ∧ không numeric-skip). Latent crash, không chỉ "drift". |
| `user_groups` | `:1449` ✅ | **THIẾU** | `:4659` `state.get("user_groups") or []` trong permission pre-filter | Khi `permission_filtering_enabled=true`: user trên SSE bị coi như không thuộc group nào → mất docs group-scoped (degrade-closed, không leak — nhưng SAI behavior). |
| `bot_extra_output_tokens_per_response` | `:1466-1468` ✅ | **THIẾU** | `:6327` `state.get(..., 0)` | Bot trả phí extra output budget **không được áp** trên SSE (silent revenue-feature off). |
| `kg_service` | `:1357-1360` build `KnowledgeGraphService()` khi `pipeline_config["graph_rag_mode"] != "disabled"`, đặt `:1471` | `:330` **hardcode `"kg_service": None`** | `graph_retrieve_node` `:7609-7614` return `[]` khi kg None | = P2-A 🐛-2. Hôm nay inert (mode disabled toàn platform) — mìn chờ flip. test_chat cũng hardcode None (`test_chat.py:3125,:3642`). |

Ghi nhận thêm (minor, cùng sửa qua shared builder, không mở scope): stream tokens dict có key `"cached": 0` (`chat_stream.py:319`) còn worker không (`chat_worker.py:1458`); stream `conversation_id: None` (`:308`) — phần conversation_id ĐANG được sửa trên branch hiện tại `fix-260604-action-slotmachine-dead-key` (memory 2026-06-04), ADR này KHÔNG đụng để tránh double-fix.

### 2.5 Gốc rễ (immutable cause)

```
4 deps None / state thiếu key trên SSE
  ← mỗi transport tự hand-roll kwargs list + initial_state dict riêng (4 chỗ copy-paste lệch nhau)
    ← KHÔNG có shared assembly function — drift là tất yếu khi thêm DI param mới
      (bằng chứng lịch sử: `conversation_state`/`slot_extractor` được thêm đủ 4 chỗ,
       nhưng `understand_query_cache` chỉ được thêm ở worker)
```

Fix đúng tầng = tầng **assembly** (callsite), KHÔNG phải tầng engine (get_graph/build_graph — đã chuẩn), KHÔNG phải tầng sysprompt/LLM.

---

## 3. DECISION

**Chọn hướng (a): shared assembly helpers** — 1 module mới `src/ragbot/orchestration/graph_assembly.py`, cả 4 callsite gọi chung:

```python
# graph_assembly.py — module MỚI (~150 dòng), KHÔNG đụng query_graph.py engine

GRAPH_DI_REQUIRED: Final[frozenset[str]] = frozenset(
    {"llm", "model_resolver", "invocation_logger", "guardrail", "vector_store", "embedder"}
)
# llm/model_resolver/invocation_logger/guardrail = required theo build_graph signature
# (query_graph.py:1141-1142,:1149-1150 không có default);
# vector_store/embedder = required theo precedent Y3-P1 (chat_stream.py:225-241 đã 503 khi thiếu).

def build_graph_di_kwargs(container: Any) -> dict[str, Any]:
    """Canonical 24-key kwargs cho build_graph — SSoT duy nhất.

    - Key set == build_graph signature (query_graph.py:1139-1165) == GraphDeps
      fields của split plan 260609 (forward-compat, xem §8).
    - Required dep resolve fail → raise GraphAssemblyError (fail-loud,
      thay cho silent-None first-build).
    - Optional dep resolve qua narrow-catch (KeyError/AttributeError/TypeError
      — giữ nguyên pattern `_opt` chat_stream.py:207-223) → None + structured
      warning `graph_di_optional_dep_unavailable`.
    - Emit 1 structured event `graph_di_assembled` với `none_deps=[...]`
      → warm-up nào thiếu gì là THẤY được trong journal, hết silent.
    """

def resolve_kg_service(pipeline_config: dict) -> Any | None:
    """KnowledgeGraphService() khi graph_rag_mode != 'disabled' — logic
    NHẤC NGUYÊN VĂN từ chat_worker.py:1357-1360, dùng chung mọi transport."""

def build_chat_initial_state(*, record_tenant_id, request_id, message_id,
        conversation_id, bot_cfg, channel_type, workspace_id, user_groups,
        query, conversation_history, pipeline_config, tracker,
        assembled_sysprompt, oos_template_resolved, bot_language,
        kg_service, session_factory) -> GraphState:
    """Canonical GraphState — đủ keys worker đang set (chat_worker.py:1441-1473)
    gồm workspace_id / user_groups / bot_extra_output_tokens_per_response.
    tokens dict thống nhất {"prompt":0,"completion":0,"cached":0}.
    Transport-specific key (_stream_sink) do caller bổ sung SAU khi nhận dict."""
```

Bốn callsite đổi thành:

```python
graph = await get_graph(**build_graph_di_kwargs(container))      # cả 4 chỗ
initial_state = build_chat_initial_state(...)                     # worker + stream + test_chat×2
initial_state["_stream_sink"] = sink                              # riêng stream (chat_stream.py:324)
```

**Fail-loud lần build đầu**: đặt trong `build_graph_di_kwargs` (raise `GraphAssemblyError` — narrow class mới trong `src/ragbot/shared/errors.py` cạnh `RetrievalError`...), **KHÔNG đặt trong `get_graph`/`build_graph`** vì: (i) giữ nguyên engine "đừng đụng"; (ii) test-mode — hàng trăm unit test build graph minimal-kwargs hợp lệ (`tests/unit/test_build_graph_singleton.py:30-32`, `test_query_graph_build.py`...) sẽ vỡ nếu raise ở engine. Prod path duy nhất đi qua builder → builder chính là chốt chặn. Route handler map `GraphAssemblyError` → 503 (giữ behavior Y3-P1 hiện có).

**Bổ sung observability tối thiểu vào `get_graph` (additive, ~8 dòng, KHÔNG đổi semantics)**: lần build đầu lưu `frozenset(di_kwargs)` vào module-level `_GRAPH_BUILD_KWARG_NAMES`; các call sau nếu `frozenset(di_kwargs) != _GRAPH_BUILD_KWARG_NAMES` → `logger.warning("graph_singleton_kwargs_divergence", missing=..., extra=...)`. First-caller-wins vẫn nguyên — chỉ chuyển từ *silent* sang *observable*. (Nếu user xét đây là "đụng singleton" quá ranh giới → bỏ item này, các phần khác của ADR vẫn đứng độc lập; shared builder đã làm divergence = 0 nên warning chỉ là defence-in-depth cho callsite tương lai.)

**Cùng ADR fix luôn** (vì cùng gốc rễ "assembly drift", cùng builder):
1. **kg_service parity** — stream + test_chat dùng `resolve_kg_service(pipeline_config)` thay hardcode None (`chat_stream.py:330`, `test_chat.py:3125,:3642`). Worker giữ behavior y nguyên (logic nhấc từ chính worker). `session_factory` đi kèm theo điều kiện như worker `:1403-1407`.
2. **initial_state missing keys** — `workspace_id` (stream đã resolve sẵn ở `chat_stream.py:128`, chỉ chưa đặt lên state), `user_groups` (stream: `ChatRequest` có field này không? — coder Phase 4 verify `chat_schema.py`; nếu request không mang → `[]` explicit, ghi comment WHY), `bot_extra_output_tokens_per_response` (đọc từ `bot_cfg` y như worker `:1466-1468`).

---

## 4. ALTERNATIVES REJECTED

| Alternative | Vì sao reject |
|---|---|
| **(b) Đẩy GraphDeps frozen-dataclass (split plan 260609) lên sớm** — build container object thay dict | Trùng hướng dài hạn NHƯNG: (i) GraphDeps thuộc Phase 1 của split plan T3, anchor `bf5b77f`, có trình tự pytest-per-phase riêng — kéo lên trước = thực hiện một phần plan T3 ngoài trình tự, blast radius vào `query_graph.py` 8087 dòng (đổi build_graph signature) trong khi bug này KHÔNG cần; (ii) W1 là wave T1-fix, không gánh refactor T3 (CORE MVP ordering). Hướng (a) cho kết quả tương đương về parity với diff nhỏ hơn nhiều, và §8 đảm bảo (a) là bước đệm đúng hướng của (b). |
| **Per-transport graph instance** (mỗi transport build graph riêng) | **CẤM** — phá singleton ĐÃ CHUẨN (P2-A §5.1 item 1, commit `65b2c10`); nhân đôi compile cost + nhân đôi chỗ drift; vi phạm binding constraint §1. |
| **Lazy re-bind deps** (call sau merge kwargs mới vào graph đã build) | Race + vô nghĩa kỹ thuật: node closures capture deps lúc `build_graph` chạy (`:1698`, `:2087`, `:3095`...), graph đã compile **không re-bind được** closure mà không rebuild; mutate sau build = data race giữa request đang bay. |
| **Bỏ kwargs của get_graph hoàn toàn** (get_graph tự đọc container global) | Breaking với toàn bộ test suite hiện có (`test_build_graph_singleton.py` + mọi test inject mock qua kwargs); tạo global-container coupling ngược chiều DI mindset (bootstrap inject xuống, không phải orchestration với lên). |
| **Raise trong get_graph khi thiếu deps** | Vỡ test-mode minimal-kwargs hợp lệ; đụng engine "đừng đụng". Fail-loud chuyển lên builder (§3). |

---

## 5. IMPLEMENTATION PLAN — Phase 4 (failing test FIRST, theo /tdd)

### §5-V — VERIFY trước fix (bắt buộc, rule #0: GIẢ THUYẾT → SỰ THẬT)

- **V1. Test reproduce build-order (RED trước khi sửa)** — `tests/unit/test_get_graph_di_parity.py`:
  ```python
  async def test_stream_first_build_drops_worker_only_deps(monkeypatch):
      qg._reset_graph_singleton_for_test()           # query_graph.py:8081
      captured = {}
      monkeypatch.setattr(qg, "build_graph", lambda **kw: captured.update(kw) or object())
      await qg.get_graph(**STREAM_SHAPE_KWARGS)      # đúng 20 key như chat_stream.py:243-264
      assert "hyde_generator" in captured            # RED hôm nay (KeyError/absent)
      assert "understand_query_cache" in captured    # RED
      g2 = await qg.get_graph(**WORKER_SHAPE_KWARGS) # 24 key — bị ignore (semantics giữ nguyên)
      # singleton vẫn là instance đầu — assert g2 is singleton đầu (semantics lock-in)
  ```
- **V2. AST kwarg-set parity test (RED hôm nay)** — parse 4 callsite (`chat_worker.py`, `chat_stream.py`, `test_chat.py` ×2) bằng `ast`, assert kwarg-name-set của mỗi `get_graph(...)` == set tham số `build_graph` (trừ khi callsite đã chuyển sang `**build_graph_di_kwargs(...)` thì pass). Test này là regression-guard vĩnh viễn chống drift tái phát.
- **V3. Runtime evidence (prod-like, ghi vào ADR khi chạy)** — restart API service → request đầu tiên là `/chat/stream` → xác nhận journal có `graph_di_assembled`/`graph_singleton_kwargs_divergence` (sau fix) hoặc đo trước fix bằng cách thêm tạm log? KHÔNG — không sửa code để đo trước fix; evidence trước fix = V1/V2 ở mức unit (đủ, vì code path tĩnh đã chứng minh). Load-test graded so sánh trước/sau trên SSE path là bước đo lift (§7), gắn nhãn số thật khi có.

### Bước fix (sau khi V1/V2 RED được commit)

1. **Test state-parity worker ≡ stream** — `tests/unit/test_chat_state_transport_parity.py`: gọi `build_chat_initial_state` với cùng input → assert key-set == key-set worker đang set hôm nay (snapshot từ `chat_worker.py:1441-1473`), gồm 3 key thiếu; assert stream-only key `_stream_sink` KHÔNG nằm trong builder output (caller bổ sung). Thêm assert chống KeyError: `state["workspace_id"]` luôn truy cập được.
2. **Test kg_service parity** (repro sketch P2-A §3 🐛-2) — `tests/unit/test_kg_service_transport_parity.py`: `resolve_kg_service({"graph_rag_mode": "entity"})` is not None; `resolve_kg_service({"graph_rag_mode": "disabled"})` is None; và state builder nhét đúng giá trị.
3. **Ship `graph_assembly.py`** + `GraphAssemblyError` trong `shared/errors.py` → wire 4 callsite (`chat_worker.py:1376-1401` + `:1441-1473`; `chat_stream.py:243-264` + `:304-332`; `test_chat.py:3031-...` + `:3555-...` + 2 chỗ initial_state `:3100`, `:3617`). Diff mỗi callsite = XÓA block hand-rolled, THAY 1-2 dòng gọi builder. `_opt` helper của chat_stream/test_chat chuyển vào builder (1 bản duy nhất).
4. **(optional, tách commit riêng)** divergence-warning trong `get_graph` (§3) — additive ~8 dòng + 2 unit test; nếu user reject phạm vi này, revert riêng commit này được.
5. **Regression**: full `pytest tests/unit/` (baseline 5785+) + V2 AST test GREEN + graded load-test (`scripts/loadtest_graded.py`) trên CẢ async path lẫn SSE path: **HALLU=0 + pass-rate ≥ 85/91 baseline hiện tại** (rule: fix T1 không được hạ T1).

Thứ tự commit: (V1+V2 RED) → (builder + wire, tests GREEN) → (optional warning) → (docs). Mỗi commit chạy full unit suite.

---

## 6. RISKS & MITIGATION

| Risk | Mitigation |
|---|---|
| Đụng `query_graph.py` 8087 dòng hot-path | Diff vào file này = **0 dòng bắt buộc** (engine không đổi); duy nhất optional-warning §3 (~8 dòng additive cạnh `:8062-8078`, không đổi control flow, tách commit revert-được). KHÔNG drive-by refactor — surgical rule. |
| SSE path bật thêm 4 deps → behavior SSE đổi (HyDE/uq-cache/stats/doc_repo active) | Đây là **chủ đích** của fix (parity với worker — worker đã chạy full deps từ lâu). Gate = graded load-test SSE trước/sau, HALLU=0 bắt buộc; flags per-bot vẫn là công tắc cuối (hyde_enabled... đọc từ pipeline_config, không đổi). |
| `workspace_id` lên state SSE → `_bg_cache_write` bắt đầu ghi cache với workspace scope mới trên SSE | Trước fix path này **KeyError** (chưa từng ghi thành công khi reach) — sau fix hành vi = worker path đã chạy lâu nay. Verify bằng test §5.1 + check `semantic_cache` row sau load-test SSE. |
| Conflict với split plan 260609 (GraphDeps) | §8 — key-set contract chung; `graph_assembly.py` không import gì plan sẽ move; khi GraphDeps lands, builder đổi return-type dict→GraphDeps tại ĐÚNG 1 chỗ. |
| Conflict với branch hiện tại `fix-260604-action-slotmachine-dead-key` (đang sửa `conversation_id=None` ở stream) | ADR này KHÔNG đụng `conversation_id`; builder nhận `conversation_id` làm param pass-through. Coder Phase 4 rebase sau khi branch đó merge. |
| `_opt` narrow-catch nuốt lỗi container thật | Giữ nguyên contract hiện có (`chat_stream.py:207-223` đã ruled hợp lý); builder thêm event `graph_di_assembled none_deps=[...]` → None không còn invisible. Required set thì raise. |

---

## 7. GATE METRIC (đo trước khi đóng W1-DI — số thật, không đoán)

1. **Parity**: AST test V2 GREEN — 4 callsite cùng 1 builder; kwarg-set divergence = 0.
2. **Order-independence**: V1 test GREEN dưới cả 2 thứ tự build (stream-first / worker-first) — deps đủ 24 key mọi order.
3. **State parity**: key-set stream ≡ worker (trừ `_stream_sink` + `conversation_id` documented) — test §5.1 GREEN; không còn code path tới KeyError `:7417` từ SSE.
4. **HALLU = 0** (sacred) trên graded load-test SSE + async sau fix; pass-rate **≥ 85/91** baseline (không tụt).
5. **Observability**: journal sau restart có đúng 1 `graph_di_assembled` per process với `none_deps=[]` trên prod config; `graph_singleton_kwargs_divergence` = 0 lần (nếu item optional được ship).
6. **Regression**: full unit suite (5785+ baseline) 0 fail; p95 SSE không tăng >10% (4 deps bật thêm có cost — đo, nếu vượt thì gate per-bot flag, không revert parity).

---

## 8. COMPATIBILITY NOTE — split plan 260609 (GraphDeps)

- **Contract chung**: key-set của `build_graph_di_kwargs()` == tham số `build_graph` (`query_graph.py:1139-1165`) == 24 field GraphDeps (`plans/260609-query-graph-split/plan.md` dòng 7, 56-58). Một SSoT, ba hình thái.
- Khi split plan Phase 1 lands (`_graph_deps.py`): `build_graph_di_kwargs` đổi return `dict` → `GraphDeps(**kwargs)` tại đúng 1 chỗ; 4 callsite KHÔNG đổi nữa. `get_graph` singleton + ignore-kwargs semantics giữ nguyên cả hai phía (plan dòng 49: "get_graph() singleton ở lại query_graph.py ... semantics giữ nguyên").
- `graph_assembly.py` KHÔNG import node nội bộ nào của `query_graph.py` (chỉ import `GraphState`, `KnowledgeGraphService`, errors) → không cản trở việc plan move node ra `nodes/*`.

## 9. CLAUDE.md COMPLIANCE (tự audit)

- **Rule #0 no-guess**: mọi claim §2 có `file:line` đã đọc thật phiên này; phần chưa đo runtime gắn nhãn GIẢ THUYẾT + bước VERIFY §5-V. ✅
- **Sacred #10 no app-inject/override**: ADR không đụng answer path / sysprompt / template. ✅
- **Zero-hardcode**: `GRAPH_DI_REQUIRED` = structural constant (`Final`, frozenset) trong module assembly — không phải behavior toggle; không magic number mới. ✅
- **Strategy + DI**: builder đọc provider từ container, không `if provider ==`; Null/None semantics của optional deps giữ nguyên engine. ✅
- **4-key identity / tenant isolation**: không đổi resolve boundary; fix BỔ SUNG `workspace_id` lên state SSE (tăng đúng scope forensic/cache). ✅
- **No version-ref**: tên module theo purpose (`graph_assembly`), không `_v2`. ✅
- **Broad-except**: builder dùng narrow-catch theo pattern `_opt` hiện có; `GraphAssemblyError` narrow class. ✅
- **T-tier declared**: [T1-Smartness] chính + T2 phụ. ✅
- **EVOLVE stance**: singleton + state-lift giữ nguyên 100%; chỉ wire callsite. ✅
