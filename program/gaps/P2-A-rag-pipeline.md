# P2-A — RAG QUERY-GRAPH ORCHESTRATION AUDIT (Phase 2 · adversarial · rev 2)

> Auditor: P2-A (was P1-A). Date 2026-06-10 · branch `fix-260604-action-slotmachine-dead-key` · anchor `7dd1f84`.
> READ-ONLY: nothing in `src/` / `alembic/` / `tests/` touched. Only this file written.
> STANCE = EVOLVE not rewrite. Every claim carries `file:line` / commit / DB-row / link.
> **rev 2**: adds read-only `psql` ground-truth of production flags (`ragbot_v2_dev.system_config` + `bots.plan_limits`)
> — this CORRECTS rev-1's 🕰-1 (structured_subanswer "no flip evidence" was WRONG: alembic 0192 flipped it ON)
> and settles open questions Q1/Q3/Q4/Q9/Q10 that rev 1 deferred to "Phase 3".
> Labels: ✅ ĐÃ CHUẨN · 🕰 LỖI THỜI · ↔️ LỆCH (doc≠code / plan≠code / DB≠code) · 🐛 SAI.

---

## (0) PRODUCTION FLAG TOPOLOGY — SỰ THẬT từ DB (read-only psql, ragbot_v2_dev, 2026-06-10)

`SELECT key, value FROM system_config WHERE key LIKE '%enabled%'` + `SELECT DISTINCT jsonb_object_keys(plan_limits) FROM bots`:

**ON (system_config row = true)**: `multi_query_enabled` (+ per-intent map: chỉ `aggregation`/`range_query` true),
`pipeline_parallel_cache_understand_enabled`, `pipeline_parallel_rewrite_mq_enabled`, `pipeline_parallel_output_guards_enabled`,
`pipeline_multi_query_speculative_enabled`, `speculative_retrieve_enabled`, `reranker_enabled`, `grounding_check_enabled`
(+`_async`), `structured_subanswer_enabled` (**flipped ON `20260609_0192_enable_structured_subanswer_ab.py`, DB updated_at
2026-06-08 19:16**), `metadata_extraction_enabled` + `metadata_aware_retrieval_enabled` + `metadata_fallback_relax_enabled`,
`cr_enhanced_enabled`, `contextual_retrieval_enabled`, `prompt_compression_enabled`, `lost_in_middle_reorder_enabled`,
`streaming_enabled`, `structured_output_enabled`, `docs_only_strict_enabled`, `understand_query_cache_enabled`,
`decomposer.enabled` (adaptive L3, read at `nodes/query_decomposer.py:151`).

**OFF in prod**: `speculative_streaming_enabled=false` (DB row), `autocut_enabled=false`, `parent_child_enabled=false` (DB row —
init script seeds `true`, drift), `retrieval_multistage_enabled=false`; **no DB row + constants default False** →
`reflection_enabled` (`_01:232`), `self_rag_critique_enabled` (`_20:275`), `neighbor_expand_enabled` (`_15:20`),
`cascade_routing_enabled` (`_20:263`), `hyde_enabled` global (`_00:129`; per-bot ON cho `thong-tu-09-2020-tt-nhnn` +
`lich-su-vn` qua `plan_limits.hyde_enabled`); `graph_rag_mode` **không có row + không có plan_limits key** → GraphRAG
disabled toàn platform. **`adaptive_router_l1_enabled`: không có DB row → constants default True (`_14:162`) → ON.**

**Per-bot plan_limits keys thực tế (10 distinct)**: `crag_skip_retry_above_score, cr_enhanced_enabled, custom_vocabulary,
hyde_enabled, prompt_compression_enabled, reflect_skip_if_grounded, reflect_skip_top_score_floor, rerank_cliff_min_keep,
rerank_skip_intents, sysprompt_version` — KHÔNG bot nào set `reflection_enabled`/`self_rag_critique_enabled`/
`neighbor_expand_enabled`/`cascade_routing_enabled`/`speculative_streaming_enabled`.

→ **Active node set per turn (prod)**: guard_input → cache∥understand∥spec-retrieve∥spec-MQ wrapper → understand →
query_complexity (L1 ON) → [adaptive_decompose | rewrite_and_mq_parallel | retrieve | decompose] → retrieve → rerank →
mmr_dedup → neighbor_expand(no-op) → grade ⇄ rewrite_retry → generate → critique_parse(no-op) → guard_output →
reflect(no-op) → persist. GraphRAG + reflect + critique + cascade + speculative-streaming = inert.

---

## (1) Labeled component table

| Component | Label | Evidence (file:line / commit / DB) | Note |
|---|---|---|---|
| Graph topology 21 nodes + bounded loop | ✅ ĐÃ CHUẨN | `query_graph.py:7908-8044`; tests `test_query_graph_build.py`, `test_build_graph_singleton.py` | Entry `guard_input` `:7952`→`persist`→END `:8042`; reflect loop capped `_reflect_route` `:8027`; grade retries capped `:7543`. |
| `get_graph()` singleton + state-lift | ✅ ĐÃ CHUẨN | `:8059-8081` (lock `:8060`, `_reset_graph_singleton_for_test` `:8081`); commit `65b2c10` | One compiled graph, multi-tenant-safe — per-request data on `GraphState`. Keystone, đừng đụng. **Does NOT block split plan — see Q10 §4.** |
| guard_input | ✅ ĐÃ CHUẨN | `:1794`; `_input_blocked` `:7453`; tests `test_guardrail_rules.py`, `test_guardrail_e2e_runner.py` | Language-pack preload; blocked → persist (audit trail kept). |
| cache_check_and_understand_parallel | ✅ ĐÃ CHUẨN | `:2426-2560`: cancel-on-hit + **awaited** `suppress(CancelledError)` for und/spec/spec-MQ tasks (`:2516-2530`); flag ON in DB; tests `test_cache_hit_path.py`, `test_mq_speculative_parallel.py` | 4-way gather (cache ∥ understand ∥ spec-retrieve ∥ spec-MQ) đúng Async Rule #1/#5. Q6 race: answered §4. |
| understand_query (merged condense+router) | ✅ ĐÃ CHUẨN | `:2071`, idempotency guard `:2073-2080`; `understand_query_cache_enabled=true` DB | 1-LLM-call merge (`9c9b20d`) — cost win giữ từ 2026-04-20. |
| condense_question + router (legacy path) | ✅ ĐÃ CHUẨN (alive-by-design) | `:1995`, `:2618`; picker `_pcfg(state,"merge_condense_router", True)` `:7463` | Byte-identical fallback path. ⚠ minor: inline literal `True` default — see 🐛-3. |
| rewrite_and_mq_parallel | ✅ ĐÃ CHUẨN | `:2973`; DB `pipeline_parallel_rewrite_mq_enabled=true`; per-intent skip `rewrite_enabled_by_intent` DB row; tests `test_per_intent_rewrite_mq_skip.py`, `test_node_rewrite.py` | |
| decompose (legacy multi-hop) | ✅ ĐÃ CHUẨN | `:3016`; confidence gate `:7506-7536` (`decompose_confidence_gate` + Prometheus skip counter); tests `test_decompose_parallel_retrieve.py`, `test_decompose_prompt_multi_entity.py` | |
| query_complexity (Adaptive L1) | 🕰 LỖI THỜI (heuristic, works) | `nodes/query_complexity.py`, wrapper `:7731`; ON in prod (default True `_14:162`, no DB override) | Heuristic regex/đếm-entity; 2026 chuẩn = lightweight **learned** router. §2 🕰-1. |
| adaptive_decompose (L3) | ✅ ĐÃ CHUẨN | `:7797`, `nodes/query_decomposer.py:151`; DB `decomposer.enabled=true`; fail→pass-through | Domain-neutral; mutual-exclusive với legacy decompose by route (`_understand_query_route` `:7477-7491`: multi_hop → legacy, còn lại → L1). |
| retrieve (monolith) | ✅ functionally / ↔️ size | `:3162-4794` (~1632 dòng); hybrid RRF `:3904`, HyDE `:1698`, stats-race `:3250`, fallback ladder `:393` | Hoạt động + test dày; size = T3 debt, split plan designed (§4 Q10). Tie-order nondeterminism = 🐛-1. |
| graph_retrieve | ↔️ LỆCH (path inconsistency) + inert prod | node `:7609-7621` (`[]` nếu thiếu kg_service/session_factory); `chat_worker.py:1357-1360,:1471` builds KG service khi `graph_rag_mode != "disabled"`; **`chat_stream.py:330` hardcode `"kg_service": None`**; DB: không có `graph_rag_mode` row/plan_limits key | NOT dead code (worker wires được), nhưng (a) SSE path không bao giờ có GraphRAG kể cả bot opt-in → worker vs stream behavior LỆCH; (b) prod = disabled toàn bộ. Test `test_node_graph_retrieve.py`. |
| rerank + cliff/autocut/threshold | ✅ ĐÃ CHUẨN | `:4795-4803`; cliff `:792`, autocut `:781`, threshold `:863`; per-bot `rerank_cliff_min_keep`/`rerank_skip_intents` in plan_limits (DB); `reranker_enabled=true` | ZE zerank-2 + CB fail-soft. |
| mmr_dedup | ✅ ĐÃ CHUẨN | `:5678-5687` per-intent threshold; tests `test_mmr_lambda.py`, `test_mmr_numpy_parity.py`, `test_mmr_strip_embedding.py` | |
| neighbor_expand | ✅ ĐÃ CHUẨN (OFF prod) | `:5725`, identity-`{}` khi OFF (`:7938` comment); default False `_15:20`, no DB/plan_limits row; test `test_neighbor_expand_node.py` | Identity-OFF pattern mẫu mực. |
| grade + rewrite_retry (CRAG-lite) | ✅ ĐÃ CHUẨN | grade `:5203`, `_grade_route` `:7543`, rewrite_retry `:5786`; per-bot `crag_skip_retry_above_score` (DB); tests `test_crag_batch_grader.py`, `test_grade_parallel.py`, `test_grade_timeout_cap.py`, `test_grade_no_answer_injection.py` | Model-agnostic; thiếu web-fallback leg = charter decision (§2 🕰-3). |
| generate (incl. refuse short-circuit + action slots) | ✅ ĐÃ CHUẨN | `:5812`; refuse-SC `:5934-5960` — template = `_oos_text(state)` (bot DB) `or DEFAULT_OOS_ANSWER_TEMPLATE` = `""` (`_04_jwt_auth.py:30`); sysprompt verbatim + 9-test lock `test_generate_no_app_injection.py` | Sacred-clean: skip-LLM refuse trả bot template hoặc empty, không i18n hardcode. Action slot-machine: branch chưa merge main (memory 2026-06-04). |
| critique_parse (Self-RAG critique) | ✅ ĐÃ CHUẨN — **RULED COMPLIANT** | `:6650-6719`; OFF default (`_20:275`) + 0 bot opt-in (DB); swap target `bots.oos_answer_template` `:6697-6698`, fallback `DEFAULT_OOS_ANSWER_TEMPLATE=""`; fail-open `:6670-6672`; test `test_critique_parser.py` | Quality-Gate #10 ruling §4 Q8. Hiện inert prod. |
| guard_output | ✅ ĐÃ CHUẨN | `:6719-6731`: grounding = observability-only, comment cites MINDSET #2; async bg check `:1005-1088`; tests `test_guard_output_parallel.py`, `test_guard_output_intent_gating.py` | Sacred no-override giữ vững. Grounding ≤5-câu cap = metric hole (P1-SYNTHESIS D7, domain P2-E). |
| reflect | ✅ ĐÃ CHUẨN (gated OFF) | `:7143`; gate `reflection_enabled` `:7602` default False (`_01:232`); skip knobs `reflect_skip_if_grounded`/`_top_score_floor` `:7242,:7253` | ⚠ orphan config: nhiều bot có `reflect_skip_*` trong plan_limits nhưng KHÔNG có `reflection_enabled` → keys chết (↔️ minor, dọn khi audit plan_limits). |
| persist | ✅ ĐÃ CHUẨN | `:7276`; sync `query_completed` audit (Async Rule #8) → bg `_bg_cache_write` `:7288`; numeric-cache-skip dùng `extract_numeric_claims` CHỈ để **decide** cache (`:7355-7365`), không sửa answer; test `test_node_persist_reflect.py` | math_lockdown import `:7361` = pure-function, sacred-compliant. |
| Speculative streaming + hallu verifier | 🕰 LỖI THỜI (risky-by-design, mitigated OFF) | `:1299-1333`: Phase-2 streams draft tokens khi verifier flag OFF (comment tự nhận "Phase 2's HALLU-risk path"); DB `speculative_streaming_enabled=false`; tests `test_speculative_router.py`, `test_speculative_router_bug2_fix.py` | §2 🕰-2: Speculative RAG chuẩn = verify-BEFORE-emit. |
| structured_subanswer | ✅ ĐÃ CHUẨN (**rev-1 corrected**) | `_resolve_generate_schema` `:554-575`; **alembic `20260608_0189` seed + `20260609_0192_enable_structured_subanswer_ab` flip ON**; DB row `true` @2026-06-08 19:16 | KHÔNG còn "shipped-OFF unevaluated": flip qua tracked migration tên `_ab` (đúng no-psql-hotfix). ⚠ inline `False` fallback `:569` = zero-hardcode debt (🐛-3). |
| rrf_round_robin helper | ↔️ LỆCH (built-not-wired) | `nodes/rrf_round_robin.py:88`; 0 refs trong query_graph.py; commit `93a5483` | Minority-entity fix viết xong không nối — quyết định Phase 4: wire sau RRF fuse hoặc xóa. |
| ColBERT multi-vector scaffold | ↔️ LỆCH (scaffold-only) | `ports/multi_vector_embed_port.py:1`, `multi_vector_registry.py:29`; 0 refs query path | Giữ port (T3 OK), không tính là capability. |
| Flag-default 3-way drift | ↔️ LỆCH | `scripts/init_system_config.py:209-210` seeds parallel flags `"false"` vs constants `True` (`_11:165-166`) vs DB `true` (alembic `20260516_0104` flip) | Init script stale → DB clone mới sẽ OFF ngầm 2 parallel wrapper. Khớp P1-SYNTHESIS D9. |
| math_lockdown dead DB rows | ↔️ LỆCH (DB≠code) | DB rows `math_lockdown_enabled=true` + `default_math_lockdown_enabled=true` tồn tại; `grep -rn math_lockdown_enabled src/ scripts/` = **0 readers** (exit 1); code override removed `cad52dc`, constants removed `6e9041d` | Rows mồ côi — operator đọc DB sẽ tưởng sacred violation đang ON. Cần alembic DELETE 2 rows (KHÔNG psql tay). |
| Doc 04-D "24-step + math lockdown" | ↔️ LỆCH — **CODE LÀ SỰ THẬT** | `04-D:1` "24-step canonical", `04-D:114` "→ math lockdown" trong Q14, last-updated 2026-05-12 (`04-D:3`) vs code: 21 `add_node` `:7909-7950`, 33 step instrumented (P1-SYNTHESIS §2), override gone `cad52dc`+`6e9041d`, no-override comment `:6723-6728` | Settled (§3). Doc-only fix. |
| Retrieval tie-order nondeterminism | 🐛 SAI (OPEN, fix-direction known) | `6547fb6` add → `2f5ed41` revert cùng ngày; `7dd1f84` "85/91 revert confirmed"; P1-SYNTHESIS §1: deterministic-by-UUID chọn chunk tệ hơn (legal −13pp), variance thật ở LLM temp-0 upstream | §3 🐛-1 repro test. Hiện KHÔNG có stable tie ordering. |
| 36-flag surface vs visibility | ✅ (đã đo được) — note | rev-1 gắn 🕰 "invisible from code"; rev 2: 1 lệnh psql read-only trả lời đủ (§0) | Hạ cấp khỏi 🕰: vấn đề là *quy trình* (không ai chạy query) chứ không phải kiến trúc. Đề xuất giữ: per-turn flag-snapshot event (rẻ, structlog — đúng no-premature-observability). |

**Đếm**: ✅ = 18 · 🕰 = 2 · ↔️ = 7 · 🐛 = 1 nặng + 2 nhẹ (gộp trong §3).

---

## (2) 🕰 SOTA-2026 verdicts

### 🕰-1 Adaptive Router L1 heuristic — chuẩn 2026 là lightweight *learned* router
- **Hiện tại**: L1 = heuristic domain-neutral (`nodes/query_complexity.py`, micro-seconds), L3 = prompt-LLM decomposer. ON prod (§0).
- **Chuẩn 2026**: Adaptive-RAG (Jeong 2024) train T5-large classifier trên complexity labels; baseline study 2026 ([RAGRouter-Bench, arXiv:2604.03455](https://arxiv.org/html/2604.03455v1)) cho thấy **KNN/MLP router trên sentence-embeddings là competitive, DeBERTa router cắt 40% large-model calls không mất quality**; [RAGRouter (arXiv:2505.23052)](https://arxiv.org/html/2505.23052v2) model hoá retrieval-aware routing. LLM-prompted classifier vẫn hợp lệ ([ragaboutit query-adaptive](https://ragaboutit.com/query-adaptive-rag-routing-complex-questions-to-multi-hop-retrieval-while-keeping-simple-queries-fast/)).
- **Verdict**: kiến trúc 2-tầng L1-cheap/L3-LLM đã đúng shape; thứ lỗi thời là *cách* L1 quyết định. Fix-direction Phase 4/5: log `(query, complexity_label, outcome)` từ request_steps → train KNN/MLP nhỏ trên embedding sẵn có (đã embed query anyway) = zero thêm latency, không cần GPU. KHÔNG urgent (T1 chưa block bởi L1 misroute — chưa có số đo misroute rate → đo trước, đúng rule #0).

### 🕰-2 Speculative streaming Phase-2-without-verifier — ngược chuẩn verify-before-emit
- **Hiện tại**: `:1299-1333` khi `speculative_streaming_enabled` ON mà `speculative_hallu_verify_enabled` OFF, draft-model tokens stream THẲNG tới user; code comment tự nhận "Phase 2's HALLU-risk path for explicit per-bot accept". Mitigation thật: default OFF + DB row `false` + verifier opt-in chặn 2 lớp.
- **Chuẩn 2026**: [Speculative RAG (arXiv:2407.08223)](https://arxiv.org/html/2407.08223v1) — drafter sinh N drafts, **verifier chấm + chọn TRƯỚC khi emit**; cắt 30-50% latency "without raising hallucination risk" ([Meilisearch](https://www.meilisearch.com/blog/speculative-rag)). Không có biến thể chuẩn nào stream draft chưa verify cho user thấy.
- **Verdict**: giữ flag-OFF như hiện tại = an toàn. Fix-direction: hard-couple — `speculative_streaming_enabled` chỉ hợp lệ khi verifier bound (preflight reject thay vì cho phép Phase-2-alone), hoặc đổi semantics thành draft-prefetch (dùng draft để prefill KV/retrieval, không emit). HALLU=0 sacred > latency win.

### 🕰-3 (carry từ rev 1, giữ nguyên verdict) CRAG-lite thiếu corrective leg + reflect value unproven
- CRAG without web-search fallback = "CRAG-lite" ([CRAG, OpenReview](https://openreview.net/pdf?id=JnWJbrnaUE)); Self-RAG critique/reflect vẫn current trong agentic-RAG 2026 ([MarsDevs](https://www.marsdevs.com/guides/agentic-rag-2026-guide), [jobsbyculture](https://jobsbyculture.com/blog/agentic-rag-guide-2026)) — KHÔNG obsolete, chỉ phải opt-in đúng query class (đang đúng: gate `:7602`). Web-fallback = trust-boundary decision cho charter (xung đột docs-only sacred), defer. Reflect: prod 0 bot ON (§0) → reflect loop economics (Q7) hiện = moot; đo lại khi có bot opt-in.

---

## (3) 🐛 SAI — repro tests (PROPOSED ONLY, not created)

### 🐛-1 Retrieval tie-order nondeterminism (OPEN sau revert 2f5ed41)
Hiện trạng: không có stable ordering trên score ties; revert đúng (deterministic-by-UUID chọn chunk tệ hơn −13pp legal), nhưng "đúng để revert" ≠ "đã fix". Variance gốc ở LLM temp-0 upstream (P1-SYNTHESIS §1) — nghĩa là fix phải là **quality-neutral stable key**, không phải UUID.

```python
# tests/integration/test_retrieval_tie_order_stable.py — PROPOSED (P2-A, not created)
import pytest

@pytest.mark.asyncio
async def test_hybrid_search_stable_order_on_score_ties(vector_store, seeded_dense_corpus):
    """Two identical queries against a corpus with >=3 exact-tie scores
    MUST return identical chunk_id sequences (flip = nondeterminism bug).
    seeded_dense_corpus: fixture ingests 5 chunks engineered to equal BM25+cosine."""
    q = "câu hỏi trùng điểm"  # engineered to tie
    run1 = [c["chunk_id"] for c in await vector_store.hybrid_search(q, record_bot_id=BOT, top_k=5)]
    run2 = [c["chunk_id"] for c in await vector_store.hybrid_search(q, record_bot_id=BOT, top_k=5)]
    assert run1 == run2, f"tie-order flip: {run1} != {run2}"
    # Quality-neutral tie key requirement: ties broken by (score DESC, chunk_index ASC,
    # content_hash) — NOT by uuid (proven worse, 2f5ed41 A/B). Assert ordering matches.
```

### 🐛-2 graph_retrieve worker-vs-stream path divergence (latent)
`chat_worker.py:1357-1360` builds `KnowledgeGraphService()` khi `graph_rag_mode != "disabled"`, nhưng `chat_stream.py:330` hardcode `"kg_service": None` → bot bật GraphRAG sẽ có 2 behavior khác nhau tùy transport. Hôm nay inert (mode disabled toàn platform §0) nhưng là mìn chờ flip.

```python
# tests/unit/test_kg_service_transport_parity.py — PROPOSED (P2-A, not created)
def test_chat_stream_state_builds_kg_service_when_graph_rag_enabled():
    """chat_stream must mirror chat_worker: graph_rag_mode != 'disabled'
    => kg_service is not None on initial GraphState."""
    state = build_stream_initial_state(pipeline_config={"graph_rag_mode": "adaptive"})
    assert state["kg_service"] is not None, (
        "SSE path drops GraphRAG that the worker path honors (chat_stream.py:330)"
    )
```

### 🐛-3 Zero-hardcode debt: inline behavior-toggle defaults trong _pcfg
`_pcfg(state, "merge_condense_router", True)` `:7463`, `_pcfg(state, "decompose_enabled", True)` `:7507`, `_pcfg(state, "structured_subanswer_enabled", False)` `:569` — behavior-toggle default phải là named constant trong `shared/constants` (CLAUDE.md zero-hardcode: "KHÔNG cho behavior toggle default"). Severity LOW (config-driven override vẫn hoạt động), fix = lift 3 constants khi split file (cùng PR 260609-split, tránh drive-by riêng).

```python
# tests/unit/test_no_inline_toggle_defaults.py — PROPOSED (P2-A, not created)
import re, pathlib
def test_pcfg_boolean_defaults_are_named_constants():
    src = pathlib.Path("src/ragbot/orchestration/query_graph.py").read_text()
    hits = re.findall(r'_pcfg\(\s*state,\s*"[a-z_0-9]+",\s*(True|False)\s*\)', src)
    assert not hits, f"{len(hits)} inline boolean toggle defaults — lift to shared/constants"
```

(rev-1 🐛 "Doc 04-D drift" và "grounding ≤5 câu" giữ nguyên giá trị: 04-D chuyển sang ↔️ bảng trên — doc-only; grounding-cap thuộc domain P2-E, đã note P1-SYNTHESIS D7, không double-count ở đây.)

---

## (4) Open questions — ANSWERED (synthesis §4 + P1-A §e, domain này)

- **Q1 flags ON prod**: **ANSWERED bằng psql read-only** — toàn bộ §0. Không còn "cần query DB".
- **Q2 tie-break**: revert đúng; root variance = LLM temp-0 upstream; OPEN item = quality-neutral stable tie key (🐛-1). Evidence `7dd1f84` + P1-SYNTHESIS §1.
- **Q3 GraphRAG dead?**: **CHẾT trong prod hôm nay** — không `graph_rag_mode` row trong system_config, không plan_limits key (DB query §0), và SSE path hardcode None (`chat_stream.py:330`). Worker path sẵn sàng nếu flip, nhưng có 🐛-2 transport divergence.
- **Q4 speculative streaming**: DB `speculative_streaming_enabled=false`, 0 bot plan_limits → **chưa từng ON prod qua config hiện hành**; verifier chưa từng validate dưới load. Giữ OFF (🕰-2).
- **Q5 double-decompose triple-cost**: **KHÔNG xảy ra — by topology.** (a) `_understand_query_route` `:7477-7491`: intent multi_hop → legacy decompose path, còn lại → L1; hai decomposer mutually exclusive per turn. (b) `decompose → retrieve` và `adaptive_decompose → retrieve` là edge thẳng (`:7987,:7995`) — không qua rewrite_and_mq_parallel. (c) MQ expansion tự bypass khi `len(sub_queries) >= 2` (`:2766-2768` decompose-precedence). Tối đa 1 decomposer + 0 MQ, hoặc 0 decomposer + 1 MQ.
- **Q6 cache-HIT race**: cancel + **await** với `suppress(CancelledError, Exception)` cho cả 3 speculative tasks (`:2516-2530`) → không orphan task; node trả về CHỈ `cache_out` — LangGraph channel-merge chỉ nhận dict được return, in-place writes của branch bị hủy không vào persisted state. Mức confidence: code-read; integration test hit+spec đồng thời vẫn đáng thêm ở Phase 3.
- **Q7 reflect economics**: moot prod (reflection OFF mọi bot, §0). Đo khi nào có bot opt-in.
- **Q8 critique_parse vs Quality Gate #10**: **RULED COMPLIANT, 3 điều kiện**: (1) opt-in per-bot, default OFF + 0 bot ON; (2) swap text = `bots.oos_answer_template` hoặc `""` (`DEFAULT_OOS_ANSWER_TEMPLATE=""` `_04_jwt_auth.py:30`) — bot data, không phải app text, đúng sacred #3; (3) trigger = LLM TỰ chấm ([Unsupported] tokens nó tự sinh), app chỉ thi hành ngưỡng do bot cấu hình — khác bản chất math_lockdown (app regex tự phán). Cùng precedent guardrail `response_message`. Fail-open `:6670` bảo toàn answer khi parse lỗi.
- **Q9 structured_subanswer**: **ĐÃ FLIP ON** — alembic `20260609_0192_enable_structured_subanswer_ab.py`, DB row true @2026-06-08 19:16. rev-1 + P1-A claim "no flip evidence" = SAI, corrected. Migration tên `_ab` → có chủ đích A/B; kết quả A/B nằm trong `reports/GRADED_*` đợt 2026-06-09/10 (13 file đang modified working-tree) — Phase 3 đọc verdict.
- **Q10 split plan blocked bởi closures + singleton?**: **KHÔNG BLOCK — plan đã giải.** `plans/260609-query-graph-split/plan.md` thiết kế dep-container: `GraphDeps` frozen dataclass (24 DI handles) + `LLMRuntime` + node classes, bound method = `state->dict` callable LangGraph chấp nhận; `get_graph()` singleton ở lại `query_graph.py` (~250 dòng), `ignore-kwargs-after-first-call` semantics giữ nguyên vì build vẫn 1 entry. Import 1 chiều chống cycle. Risk thật = mechanical extraction 2 vùng 1.6-1.7K dòng (retrieve, generate-cluster) — plan đã yêu cầu pytest sau MỖI phase.

---

## (5) "ĐÃ CHUẨN — đừng đụng" shortlist (đập = lỗi nặng nhất)

1. **Graph singleton + state-lift** (`:8059-8081`, `65b2c10`) — multi-tenant-safe compiled-graph reuse. Split plan giữ nguyên nó.
2. **No-app-inject / no-app-override** (`generate` sysprompt verbatim; `guard_output` warn-only `:6723-6728`) — locked bởi `test_generate_no_app_injection.py`. Sacred.
3. **Double-decompose topology guard** — mutual-exclusive routes (`:7477-7491`) + MQ decompose-precedence bypass (`:2766`). Cost discipline bằng cấu trúc graph, không bằng if-rừng.
4. **Cancel-on-cache-hit với awaited suppress** (`:2500-2530`) — đúng asyncio hygiene, không orphan task.
5. **Identity-OFF node pattern** (neighbor_expand, critique_parse, reflect: flag OFF → `{}`) — opt-in feature không nhiễm default path.
6. **Refuse-text origin chain** — `bots.oos_answer_template` → `""` (`_04_jwt_auth.py:30`), không i18n hardcode, ở cả refuse short-circuit `:5946` lẫn critique swap `:6698`.
7. **Flag-flip qua alembic có tên `_ab`** (`0192`, `0104`, `0190/0191` disable-failed-AB) — đúng no-psql-hotfix + có dấu vết A/B revert được. Quy trình này là điểm sáng governance.
8. **CRAG-lite + per-bot skip knobs** (`crag_skip_retry_above_score` đang dùng thật trong plan_limits DB) — model-agnostic, enterprise-practical.

---
*P2-A rev 2 complete. Chỉ file này được ghi. Sources: code/commit/DB như trích dẫn; web — [RAGRouter-Bench arXiv:2604.03455](https://arxiv.org/html/2604.03455v1) · [RAGRouter arXiv:2505.23052](https://arxiv.org/html/2505.23052v2) · [Speculative RAG arXiv:2407.08223](https://arxiv.org/html/2407.08223v1) · [Meilisearch Speculative RAG](https://www.meilisearch.com/blog/speculative-rag) · [CRAG OpenReview](https://openreview.net/pdf?id=JnWJbrnaUE) · [MarsDevs Agentic RAG 2026](https://www.marsdevs.com/guides/agentic-rag-2026-guide) · [jobsbyculture Agentic RAG 2026](https://jobsbyculture.com/blog/agentic-rag-guide-2026) · [ragaboutit query-adaptive](https://ragaboutit.com/query-adaptive-rag-routing-complex-questions-to-multi-hop-retrieval-while-keeping-simple-queries-fast/).*
