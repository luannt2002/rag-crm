# GAP-FIX ACCEPTANCE — "làm hết cái chưa handle/chưa control" + test đầy đủ

> Branch `fix-260623-ingest-expert` · base HEAD `6caeb9c` · **ALL changes UNCOMMITTED** (per owner rule "cấm commit trước khi cho phép").
> Method: mỗi fix = read source → surgical edit → import/AST check → unit test (TDD regression guard) → full-suite regression gate. Mọi claim có `file:line` + test output. FACT = ran it; không đoán.

---

## 0. OBJECTIVE NUMBER — full unit suite (before vs after this batch)

```
cd /var/www/html/ragbot; set -a; source .env; set +a
python -m pytest tests/unit/ -q --continue-on-collection-errors -p no:cacheprovider
```

| Metric | Trước batch (gap-recheck baseline) | Sau batch | Delta |
|---|---|---|---|
| **failed** | 18 | **18** | **0 net-new** |
| **passed** | 6581 | **6592** | **+11** (new regression tests) |
| collection-errors | 0 | 0 | — |
| skipped / xfailed / xpassed | 33 / 37 / 33 | 33 / 37 / 33 | — |

**18 failures = 100% pre-existing** (verified in gap-L3 against `da37778~1`): 14 callback DNS/SSRF env + `test_no_new_price_domain_coupling` (ceiling) + `test_broad_except_count_decreases` (ceiling) + `test_no_version_ref_grep` (ceiling) + `test_generate_intent_max_tokens` (source-regex pin). **Mid-batch tôi tạo 3 net-new fail** (`test_retrieval_stages` — mock rows thiếu `parent_chunk_id` sau Q7) → **đã fix ngay** (mock rows += `parent_chunk_id: None`) → về lại 18.

**Self-audit diffs (FACT):** 0 dòng `+except Exception` mới · 0 version-ref (`_v[0-9]`/`Sprint`/semver) · 0 comment mang commit-hash/date/audit-ID (WHY-only compliant).

---

## 1. HANDLED trong batch này — 8 fix code + 1 phân tích-không-fix

| ID | Sev | Vấn đề (root) | Fix (file:line) | Test |
|---|---|---|---|---|
| **Q7** | HIGH | `parent_chunk_id` không bao giờ được SELECT lại → 3 feature chết (parent-child expansion, stage-4 parent-expand, auto-merge) — mọi consumer `.get("parent_chunk_id")` = None | Thêm cột vào SELECT + lift lên dict ở **cả 4 path**: dense `pgvector_store.search:331/351`, hybrid CTE `:523/533/546/553/589`, `bm25_only_stage2:73/117`, `keyword_stage3:100/142` | `test_retrieval_stages` (12) + pgvector (51) green |
| **O3** | HIGH | Redis recovery XCLAIM message rồi **không re-dispatch** → job transient-fail (embed 429 + owner crash) rục rịch tới DLQ mà chưa từng chạy handler | `redis_streams_bus.py:571` thêm param `dispatch`, re-drive claimed qua `_dispatch_one` (own XACK+inbox-dedup); `_loop:505` truyền `dispatch=_dispatch_one` | `test_redis_streams_recovery` +2 (10 total) |
| **I12** | MED | Bulk chunk INSERT gộp 1 `VALUES(...)` cho mọi row × ~12-13 bind/row → **crash asyncpg 32767 bind ceiling** ở >~2900 chunk (doc lớn multi-format) | `ingest_helpers._bulk_insert_chunks` batch theo `POSTGRES_MAX_BIND_PARAMS` (new constant); mỗi batch 1 execute, share session/tx (không half-ingest) | `test_bulk_insert_bind_batching` +3 (NEW file) |
| **Q11** | HIGH | `SPECULATIVE_REDO_SENTINEL` ("__SPECULATIVE_REDO__") **leak vào answer text** — consumer `async for delta` append thẳng vào buffer+SSE, không strip | `query_graph.py:1063` import sentinel + `:1129` strip control-marker (`buffer.clear(); continue`), short-circuit guard off-path | `test_invoke_llm_node_max_tokens` +2 (5 total) |
| **Q12** | HIGH | Reranker build lại **mỗi turn** (kể cả Redis cache-hit) → CircuitBreaker mới mỗi lần → outage không bao giờ trip breaker (mỗi turn CLOSED → live-call fail → RRF từng cái) | `reranker_resolver.py` thêm `_instance_cache` per-bot keyed by config-signature; `_get_or_build` reuse instance khi config bất biến | `test_reranker_resolver` +2 (11 total) |
| **I20** | LOW | Stats index `record_tenant_id=record_tenant_id or uuid.uuid4()` — **fabricate random tenant** khi None → orphan rows, tenant-isolation breach | `ingest_stages_final.py:559` gate `record_tenant_id is not None`, else fail-loud log skip; gỡ `import uuid` (unused) | full-suite green |
| **UNCTRL-A** | HIGH | AST pin chỉ soi node-RETURN dict, **mù với in-place `state[k]=`** → 6 key undeclared (`resolved_answer_model`=Q6, `action_state`, `grounding_async_task`, `multi_query_skipped_simple`, `_uq_cache_hit`, `_generate_empty_answer`) reducer drop im lặng | `state.py:227` declare 6 key + `test_graphstate_key_pin.py` thêm walk `state[k]=` Subscript-assign | `test_graphstate_key_pin` +1 (2 total) |
| **UNCTRL-C** | HIGH | Parity test chỉ soi **test_chat** dict + tuple-keys, mù với **worker dict body** → mirage-knob (11 knob chỉ tới test harness). Phát hiện thêm drift thật: `embedding_provider` build ở test_chat, **thiếu ở worker** → cache-key namespaced sai trên prod | `chat_worker/pipeline_config.py:284` thêm `embedding_provider` + `test_pipeline_cfg_keys_parity.py` thêm AST-extractor worker dict + test | `test_pipeline_cfg_keys_parity` +1 (4 total) |
| **I7** | MED | (re-ingest wipe stats) — **PHÂN TÍCH → KHÔNG FIX** | Case catastrophe (re-parse → EMPTY) đã được guard `if _stats_entities:` (ingest_stages_final.py:533) chặn delete. Case "smaller non-empty replace larger" = **đúng idempotent** (stats phải phản ánh parse hiện tại; giữ max(old,new) = bug stale-entity). Per rule#0 + Simplicity-First: không thêm code sai. | N/A |

**Đã có sẵn từ gap-fix trước (uncommitted, verify persisted):** O4 NameError (`invocation_logger.py:43/64` import structlog+logger), Q17-completion (`retrieve.py:1784` + `neighbor_expand.py:354` soft-delete filter), worker 11 mirage-knobs, I4 comment, I17 unused import.

---

## 2. OWNER-GATED — KHÔNG thể tự hoàn tất (đã đào root, cần quyết định/ops/measure)

| ID | Sev | Vì sao blocked (evidence) | Cần gì |
|---|---|---|---|
| **Q6 full-wire** | HIGH | `resolved_answer_model` viết ở `generate.py:408` nhưng `_invoke_llm_node:980` resolve model 100% qua `resolve_runtime(purpose=)`. `apply_cascade_routing` trả **bare model-name**, không phải full binding. Wire cần **method mới** `resolve_runtime_by_model` (provider/key/params) + đây là feature **T2 cost** → per rule#0 no-guess-must-measure phải **load-test** đo cost/quality trade-off (cheap model trên "simple" query có thể tụt faithfulness). Không blind-wire feature cost. | Owner chốt wire-vs-delete + load-test. (Key đã declare xong = hết silent-drop.) |
| **I13** | HIGH | `tool_name = title.lower()...` collision → 2 doc cùng title ghi đè nhau qua `uq_doc_tool` ON CONFLICT. NHƯNG `tool_name` là **external contract**: DELETE API định danh doc bằng nó (`delete_document.py:53`), domain-invariant (`document.py:108`), citation, unique-constraint key. Fix đúng = đổi uniqueness key sang `source_url` (**alembic ALTER uq_doc_tool**) hoặc đổi semantics DELETE. | Owner chốt contract + alembic migration (sacred: schema chỉ qua alembic). |
| **I16** | MED | `page_number` tính ở parser (kreuzberg block) nhưng **flatten mất** khi block→`content` markdown trước `smart_chunk` (`ingest_core.py:228-232` xác nhận block chỉ threaded cho observability). Persist page đúng cần **block-native chunking flip (S2/S3 = I1)**. Ship page bừa = **wrong-page = citation fabrication** (vi phạm HALLU=0). | Blocked-on-I1. |
| **I1** | CRIT | Worker Path A/B flatten `full_text` + local:// bypass structured parse (`document_worker.py:463/501/613`). Refactor LARGE (block-native pipeline). | Owner + design (evolve-not-rewrite). |
| **I3** | HIGH | Không có OLE2 parser (.doc/.xls/.ppt), không có `\xd0\xcf\x11\xe0` sniff. | New parser adapter + dependency. |
| **I8** | HIGH | AdapChunk chưa evaluate-then-select (`analyze.py:407` chọn TRƯỚC khi chunk). Phase 4. | Owner Phase 4. |
| **O2** | HIGH | Verification tier (numeric-fidelity/citation-validate/completeness) **chưa tồn tại** (grep 0 hit). New subsystem. | Owner Phase 3. |
| **Q4/O6** | HIGH | Grounding gate NGƯỢC: judge-confirms-ungrounded → ship + flag; judge-dead → refuse (`guard_output.py:503-519`). = quyết định sacred-#10 (block vs observe). | Owner chốt. |
| **Q10** | HIGH | Per-bot embed-dim + vector(1280) locked. Cần schema/migration + owner. | Owner + alembic. |
| **S-1** | HIGH | Middleware order: CORS+3 RL chạy TRƯỚC tenant-bind (`app.py:490-563`). Cần integration test boot app. | Ops + integration test. |
| **S-2 / Q16** | HIGH | RLS dead qua superuser-DSN fallback (`engine.py:72`). Ops provision `ragbot_app` NOBYPASSRLS role. | Ops. |
| **Eval harness** | — | Agent-Grader RAGAS-parallel + ground-truth 6-type. Chưa build. | Owner + infra. |
| **Load-test** | — | Mọi tuyên bố coverage/quality runtime cần load-test bypass_cache. | Infra. |

---

## 3. VERDICT

- **8 fix code + 1 phân tích** landed trong working tree, **UNCOMMITTED**. Full suite **18 fail (100% pre-existing, 0 net-new)** / **6592 pass (+11 test mới)**. 3 net-new fail tôi tự gây (Q7 mock) đã fix ngay.
- Mọi fix **surgical, đúng tầng, có test regression-guard**, CLAUDE.md-compliant (0 broad-except mới, 0 version-ref, 0 app-inject/override, WHY-only comment, zero-hardcode qua constant).
- Phần còn lại (Q6-wire, I13, I16, I1, I3, I8, O2, Q4, Q10, S-1, S-2, Q16, eval, load-test) = **owner-gated thật** (schema/contract/ops/measure/new-subsystem) — đã đào tới root + nêu chính xác cần gì, KHÔNG blind-ship.
- **Chưa commit** — chờ owner cho phép review code rồi mới commit.
