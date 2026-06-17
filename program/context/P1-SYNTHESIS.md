# P1-SYNTHESIS · Hấp thụ context (Phase 1 → GATE 1)

> Tổng hợp 7 report P1-A…G (đều có file:line/commit evidence). Đây là bản đồ "Ragbot đang sở hữu gì",
> vì sao thành ra như hôm nay, plan mồ côi, và 25 câu hỏi mở quan trọng nhất cho Phase 2.
> Nguồn: program/context/P1-A…G + P1-C-PRESEED. STANCE = EVOLVE, không rewrite.

## 1. Timeline tiến hóa (bước ngoặt từ git — lý do + bài học)

| Mốc | Thay đổi | Lý do (evidence) | Bài học |
|---|---|---|---|
| 0001→0034 | `tenant_id` INT → `record_tenant_id` UUID | bridge legacy → UUID PK | naming convention EXTERNAL vs record_ |
| 0062 | thêm `workspace_id` VARCHAR lên 16 bảng (4-key) | multi-workspace identity | **workspace = slug, chưa entity** |
| 0069 / 0141 / 0186-0187 | RLS policy → workspace-aware → re-assert + app-role | tenant isolation | **policy có, enforcement chưa bật** (0 callsite) |
| 0050 / 0085 | embedding dim 1024→1536→**1280** (zembed-1 matryoshka) | ZE migration, fit HNSW 2000-dim | column fixed-dim, đổi model = ALTER+wipe |
| 0193-0195 | rewrite sysprompt best-practice + **purge LMStudio/gemma** → grounding+grading sang OpenAI nano | gemma 30s timeout = **76% p95** | engine swap qua Port, giữ mindset |
| 6547fb6 → 2f5ed41 | tie-break determinism ADD → **REVERT cùng ngày** | A/B: legal -13pp (87→73-75) | deterministic-by-UUID chọn chunk tệ hơn; variance thật ở **LLM temp-0 upstream**, không phải SQL order |
| 1446fef | tách `constants.py` → **22 module** | maintainability | `validate_constants.sh` còn trỏ file cũ (drift) |
| c94bac9 / 93a5483 / f845fd7 | structured_subanswer flag-OFF · rrf_round_robin 0-ref · graph_retrieve short-circuit `[]` | ship-flag-off pattern | **"built-but-not-wired" = meta-pattern của dự án** |

## 2. AI ĐANG SỞ HỮU GÌ — ma trận component × trạng thái

### ✅ LIVE & tốt (KHÔNG đụng — đập = lỗi nặng nhất)
- Query graph 21 node wired, entry guard_input → persist→END (query_graph.py:7909-7950); loop bounded (2 counter ≤8 / ≤max).
- **Sacred compliance CLEAN**: 0 app-inject (sysprompt verbatim, query_graph.py:6258-6279), 0 app-override trên always-on path (grounding = warn-only :6723-6728).
- 4-key identity + unique constraint + Redis registry key đủ 4 thành phần.
- RLS **policy** (23, đủ bảng) + role ragbot_app NOBYPASSRLS + FK ON DELETE CASCADE (hard-delete).
- L2 semantic cache **scoped đúng** record_bot_id + record_tenant_id TRƯỚC cosine (semantic_cache.py:474-496).
- Reranker ZE zerank-2 có **circuit-breaker** + fail-soft. HNSW m=32/ef_construction=200/ef_search=64.
- corpus_version + bot_version passive bust hoạt động đúng (đổi sysprompt / xóa doc → flip key).
- Observability cải thiện: **33 step instrumented** trong request_steps (cũ 12/27).
- Haiku **KHÔNG vi phạm**: pipeline dùng haiku cho partial-task token nhỏ; answer = gpt-4.1-mini (2 scope governance khác nhau).

### 🟡 PARTIAL (code có, chưa wired / default OFF)
- AdapChunk L1 (Kreuzberg parse) LIVE nhưng emit `list[dict]`, không Block. L3 profile computed nhưng block-path flag-gated.
- structured_subanswer (c94bac9), self-RAG critique, cascade — ship flag-OFF.
- Cost attribution: emit Prometheus label OK nhưng **purpose KHÔNG persist DB**; request_logs per-turn aggregate.

### ❌ DEAD-CODE (define, 0 prod callsite — chờ nối)
- **AdapChunk L6**: `smart_chunk_atomic` (chunking.py:2737) + `_smart_chunk_with_atomic_protect` (:2425, flag default False). Ingest gọi legacy `smart_chunk(text:str)` (document_service.py:2091). **Thiếu parser→Block adapter** = gap lớn nhất.
- `_narrate_service` narrate-then-embed: thực ra wired ON (document_service.py:2891) — cần verify lại (P1-B nói dead, charter pre-seed nói chờ; **mâu thuẫn → Phase 2 chốt**).
- RLS: `attach_rls_session_hook` (session.py:154) 0 callsite; `app.workspace_id` GUC **không SET ở đâu** → workspace-aware policy degrade tenant-only.
- rrf_round_robin (93a5483), ColBERT port scaffold, graph_retrieve `[]` khi thiếu kg_service.

### 🐛 SAI / HOLE (cần fix)
- RLS 100% inert runtime (superuser DSN + hook off) → isolation chỉ dựa app-WHERE. **D3 P0.**
- Workspace GUC chưa set kể cả sau wire hook. **D2/D3.**
- 8 invalidation hole: bot/tenant soft-delete purge nothing; semantic_cache **no FK to bots**; stuck-document reaper thiếu (doc state=active trước embed → worker crash = active doc 0 chunk). **D4.**
- Ingest fairness = 0: 1 stream global + Semaphore(5) chia chung mọi tenant. **D8.**
- **Exactly-once = NO** (P1-F): dedup SET NX *trước* handler (redis_streams_bus.py:196-215) → handler fail + XCLAIM redeliver = dedup-skip + ACK = **message DROPPED** (at-most-once). Ledger global theo outbox UUID (đúng), nhưng "DLQ" = log+ACK. **D8-adjacent / new.**
- Embedding model change: 0 guard khi có chunks (chỉ Prometheus counter no-op). **D10.**
- config drift: init_system_config.py ≠ alembic 0020 (max_tokens 1024 vs 450, rerank_top_n 10 vs 5); config-lint chỉ check 2 cặp. **D9-adjacent.**
- Grounding judge ≤5 câu (local_guardrail.py:413/445): câu 6+ unchecked = HALLU hole tiềm năng. **D7.**
- Tie-break: post-revert KHÔNG có stable tie ordering; variance thật ở temp-0 upstream. **D5 reframe.**

## 3. Plan mồ côi (orphan plans — nguồn P1-G)
- **ABANDONED/superseded**: `260604-bm25-vietnamese-aware` (DRAFT, vài mitigation đã live rời rạc), `260604-metadata-aware-v4`, `260604-multi-domain-metadata-aware` (DRAFT, blocker legal-only article_ref).
- **DRAFT/never-started**: `260609-query-graph-split` (file vẫn 8087 dòng), `260608-path-to-9.5-expert`, `260609-file-size-reduction` (body).
- **DOING/partial-ship (trạng thái áp đảo)**: ~11 plan. Meta-pattern tự đặt tên: **"built-but-not-wired"** (CR-dead, Layer-3 NameError, action_config DTO-drift, RLS hook 0-callsite, math_lockdown wired-but-OFF).
- `260608-multitenant-hardening:72` đã flag "cascade → 0 orphan" = chưa implement.

## 4. 25 câu hỏi mở quan trọng nhất (→ Phase 2 điều tra)

**An toàn / multi-tenant**
1. `DATABASE_URL_APP` có set trong prod .env không? (unset → superuser → RLS chết toàn bộ).
2. Wire `attach_rls_session_hook` cách nào ít xâm lấn nhất (mọi session, kể cả worker + script)?
3. `app.workspace_id` GUC: contextvar nào mang slug (hiện chưa có), set ở đâu?
4. Leak test 2-tenant: integration thật (connect ragbot_app, assert 0 row) hay mock?
5. HNSW filter record_bot_id pre/post vector scan? (recall-cliff cho small-corpus bot trong bảng multi-tenant).
6. Workspace nâng entity: schema + migration backward-compat (null→default ws) thế nào?
7. RBAC workspace-scope: claim `workspace_roles` map hay bảng `workspace_members`?
8. Quota cascade tenant→workspace→bot: enforce ở đâu (guard_input)?

**AdapChunk / ingest**
9. Ai viết parser→Block adapter, schema dict canonical (pdf/excel/markdown đang lệch)?
10. `_narrate_service` thật sự LIVE hay passthrough? (P1-B vs pre-seed mâu thuẫn) — đo 3 chunk bảng trong DB: embedded text là narration hay raw?
11. Vì sao atomic_protect ship default-OFF (62a1a05) — có A/B đo regression không?
12. 2 impl atomic song song (text-string vs Block) — cái nào sống khi consolidate?
13. Proposition chunking có verify ngược source không (HALLU risk tại ingest)?
14. SEMANTIC chunking cost (zembed-1 per-sentence) có đáng so recursive+atomic? (ablation Phase 5).
15. Large-table: atomic tuyệt đối vs table_csv row-as-chunk — hòa giải sao?

**Retrieval / answer**
16. Per-intent context cap áp ở đâu trong query_graph (P1-D không định vị được)? safety-net có overflow nó không?
17. Vì sao deterministic-by-id làm legal *tệ hơn* không phải neutral (cần per-flip chunk_id diff)?
18. Grounding ≤5 câu: answer multi-fact dài có để tail claim unchecked → HALLU?
19. nano judge đủ mạnh tránh false-PASS silent HALLU? 0195 có chạy A/B HALLU=0 thật?
20. Numeric aggregation: disclaimer vs extract-then-compute (ranh giới sacred #5)?

**Cost / config / vận hành**
21. purpose persist DB (child table request_llm_calls) hay chấp nhận per-turn aggregate cho GA?
22. Reconcile init_system_config.py vs alembic 0020 + 4-way config-lint CI?
23. BotLifecycleService: orchestrated purge (chunks + cache + registry) — thiết kế?
24. Stuck-document reaper: sweep doc active-but-0-chunk sau worker crash?
25. Ground-truth process (D13): ai gán nhãn đáp án chuẩn (người không biết hệ thống — AdapChunk §9.3)? Đây là đường găng Phase 5.

## 5. Mâu thuẫn cần Phase 2 chốt (doc≠code≠report)
- `_narrate_service` LIVE (P1-E/document_service.py:2891) vs DEAD (P1-B) — **phải đo DB thật**.
- Resolve chain: charter "6 tầng" vs P1-G "5 tầng documented + tầng workspace thin/referenced-only".
- doc drift: 04-D nói "24-step canonical" + còn "math lockdown" (last-updated 2026-05-12) vs code 33-step + sacred no-override.
