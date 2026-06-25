# CHUẨN-AUDIT — Ragbot vs tldw_server: cái nào "có nhưng chưa chuẩn = RÁC"

> Mandate (user): "có mà không chuẩn = rác; chỉ cái CHUẨN mới là expert-có." Đối chiếu từng capability
> với tldw_server (`_external_refs/tldw_server`), chấm CHUẨN / RÁC / MISSING bằng evidence `file:line` +
> live-trace, KHÔNG credit feature chỉ vì code có mặt. Audit 2026-06-25 (2 agent Opus + verify).

## 🔴 P0 — KEYSTONE: Reranker CHẾT toàn hệ thống (đã FIX `cf7f09b`)
- **Bug**: `system_config` drift — `reranker_provider="jina"` (không phải provider code hợp lệ) +
  `reranker_model="zerank-2"` (thuộc `zeroentropy`). `reranker_resolver._lookup_platform_default`
  (`reranker_resolver.py:248`) JOIN `name='zerank-2' AND code='jina'` → **0 rows → None → NullReranker**
  cho MỌI bot không có binding `purpose='rerank'`.
- **Triệu chứng**: factoid/policy query collapse còn 1 chunk sai (cliff giữ top-1 vì không có score rerank
  thật). Câu bảo hành trả "3 tháng" thay vì "5 năm".
- **Fix**: alembic align `jina_ai` + `jina-reranker-v3` (key verified 200) + WARN khi JOIN rỗng mà
  `reranker_enabled=true` (chặn silent-NullReranker tái diễn). **Verified**: `jina_rerank_done`
  input=20→output=7, mode=rerank, "5 năm" đúng.
- Đây là `[feedback_resolver_must_fallback_system_config]` tái diễn qua vector mới (provider/model drift).

## Bảng CHUẨN-AUDIT (retrieval + chunking)

| Capability | tldw (ref) | OURS | VERDICT | Defect + fix |
|---|---|---|---|---|
| Hybrid vector+BM25 | `database_retrievers.py:1881` | `pgvector_store.hybrid_search:373` RRF in-SQL | **CHUẨN** | BM25 reaches answer; wired |
| RRF fusion | `_reciprocal_rank_fusion:1908` | `rrf_merge_chunks` (Cormack k=60) wired | **CHUẨN** | formula correct |
| `rrf_round_robin` node (entity-quota) | — | `nodes/rrf_round_robin.py` | **RÁC orphan** | KHÔNG import vào graph; wire hoặc xóa |
| **Reranker** | 7-strategy `advanced_reranking.py` | Jina + cliff + cap | **RÁC→FIXED (P0)** | was NullReranker (config drift) — fixed cf7f09b |
| Parent/neighbor expand | `parent_retrieval.py:192` (5 strat) | `neighbor_expand` default **OFF** | **RÁC (gap)** | atomic-section doc → 1-chunk blind; A/B `neighbor_expand_enabled=True` window=1 |
| HyDE | `hyde.py:56` | `DEFAULT_HYDE_ENABLED=False` | **RÁC (off)** | bare "bao lâu" embeds kém; A/B HyDE-on factoid |
| Multi-query / decompose | `query_expansion.py:546` | wired + ON | **CHUẨN** | fan-out + RRF live |
| Grade/CRAG | `document_grader.py:454` no-hard-drop | scale-aware relative gate `grade.py:490` | **CHUẨN** | không over-strict; stats-skip đúng |
| Grounding/faithfulness | `faithfulness.py:156` post-hoc | `local_guardrail.py:417` per-sentence | **CHUẨN-judge** | đúng cơ chế; nhưng grounding≠correctness (không cứu retrieval miss) |
| **AdapChunk LLM selector** | n/a | `llm_resolver.py:139` built, **bootstrap 0 provider** | **RÁC orphan (B-1)** | U4 chạy rule `select_strategy()`; wire `build_chunking_resolver` vào bootstrap |
| Rule selector + L5 cross-check | — | `analyze.py:551` ON | **CHUẨN** | selector thật đang chạy |
| **Block pipeline / typed blocks** | typed `DocumentElement` | parser `-> list[dict]`; blocks chỉ set ở OCR branch `document_worker.py:448` | **RÁC no-op (B-2)** | DOCX/XLSX/CSV → `parsed_blocks=None` → text-flatten; flag ON mà chạy rỗng. Fix: parser emit typed Block → cascade unblock atomic-protect + narrate-by-type |
| Structure-aware breadcrumb | `structure_aware.py:696` folder>doc>H1>H2 | `_chunk_hdt:296` section-path; VN-legal `vn_structural.py:241` | **CHUẨN (partial)** | breadcrumb có (xe `[I.]/[II.]` verified); thiếu doc/workspace prefix |
| **Narrate-then-embed** | — | `narrate_dispatch.py:107`, default **OFF** | **RÁC passthrough** | log `narrate_then_embed_applied` là PASSTHROUGH (service non-None nhưng enabled=False); table embed raw. Fix: log phân biệt applied/passthrough + carry block_type từ #B-2 |
| Atomic-protect (L6) | element types | gate OFF `_00:95`; `smart_chunk_atomic` 0 callers | **RÁC (off+orphan)** | TABLE/FORMULA cắt giữa; route qua smart_chunk_atomic sau #B-2 |
| Semantic chunk (cosine) | `semantic.py` embedding | live `_chunk_semantic` = **lexical SequenceMatcher**; `_chunk_semantic_embed` (cosine) **0 callers** | **RÁC (cosine orphan)** | "semantic" runtime = lexical topic-shift; wire cosine variant sau A/B |
| Propositions | `propositions.py` | `_chunk_proposition` rule-based wired | **CHUẨN (rule) / RÁC (LLM variant chưa wire)** | clause-split chạy; LLM proposition chưa |

## Kết luận
- **CHUẨN (expert-thật)**: hybrid BM25, RRF formula, rule-selector + L5, VN-legal breadcrumb, multi-query,
  CRAG scale-aware, grounding-judge mechanism, proposition rule-split.
- **RÁC (có nhưng chưa chuẩn)**: ⭐ reranker (config-dead → FIXED), B-1 LLM-selector orphan, B-2 block
  pipeline no-op, narrate passthrough-default, atomic-protect off, semantic-cosine orphan, rrf_round_robin
  orphan, neighbor/HyDE off.
- **Đòn bẩy nhất đã fix**: P0 reranker (1 config align → rerank sống toàn hệ thống).
- **Đòn bẩy tiếp**: B-2 block-pipeline (1 parser contract → cascade unblock atomic-protect + narrate-by-type).
- **Mình HƠN tldw**: RLS single-DB multi-tenant, pipeline node + config-chain, sacred HALLU=0, 1 canonical
  ingest endpoint. **Đừng đập cái đã chuẩn** — chỉ "nối dây" (EVOLVE).

*(Clone: `_external_refs/tldw_server`. Audit agents: retrieval a939f57, chunking a8cf97a.)*
