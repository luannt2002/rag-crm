# DEEPDIVE — Luồng Hiểu Câu Hỏi & Retrieval: Vấn đề & Hướng Expert

> **Loại:** READ + REPORT (Phase 1-3, KHÔNG sửa code). Mọi claim kèm evidence `file:line` / trace / SQL.
> **Ngày:** 2026-06-18 · **Anchor:** sau alembic 0240 + re-ingest 9 docs fresh
> **Trigger:** owner báo "hỏi 1 dạng ra, hỏi dạng khác không ra"; "hỏi về da không liệt kê da chết"; "rerank chết rồi à?"; "sao fix code ở tầng SQL query?"
> **Tier:** T1-Smartness (bot trả lời đúng + đủ) — cao nhất.

---

## 0. TL;DR — Verdict

1. **Reranker KHÔNG chết.** `jina-reranker-v3` chạy mỗi query, strategy `cliff` dùng điểm rerank cắt topK (trace thật, mục 2).
2. **`embedding_model_mismatch` là false-alarm cosmetic** — `jina_ai/jina-embeddings-v3` vs `jina-embeddings-v3` = cùng model, khác prefix. Không phải bug, chỉ là code so sánh string thô (`query_graph.py:769`).
3. **Hệ thống có 2 đường retrieval song song** (Path A vector+rerank, Path B SQL structured). Đường SQL tồn tại **đúng nguyên tắc** cho câu "liệt kê/đếm/giá" — vì rerank+topK tối ưu *relevance* và *cắt* topK, KHÔNG đảm bảo *completeness*. SOTA xác nhận (mục 5).
4. **Cái YẾU thật** (owner đúng): đường SQL match keyword bằng **`ILIKE '%substring%'` thô** → over-match ("da"→"đầu/dầu"), không semantic grouping; `parse_list_query` strip stopword **hardcode tiếng Việt**, fragile. **Vá string mãi = băng dán.**
5. **`bots.custom_vocabulary` (synonym map per-bot) đang CHẾT** ở đường keyword — chỉ làm hint cho LLM, không expand keyword vào SQL. Owner không thể dạy "da = {da chết, chăm sóc da}".
6. **Multilingual: CHƯA sẵn sàng** ở fast-path — query parsers VN-locked.
7. **Expert solution KHÔNG phải vá string** mà là: **category metadata + intent router + adaptive-k + wire custom_vocabulary + word-segmentation** (mục 6, 7).

---

## 1. Luồng đầy đủ Query → Retrieval (map thực tế)

Graph wiring: `orchestration/query_graph.py:3719-3854`.

| # | Node | File | Vai trò |
|---|------|------|--------|
| 1 | `guard_input` | `query_graph.py:3719` | Input guardrail |
| 2 | `cache_check_and_understand_parallel` | `query_graph.py:3724` | 2-tier cache (exact hash + semantic pgvector) ‖ understand |
| 3 | `understand_query` | `nodes/understand.py:55` | Heuristic intent (L1) → LLM understand (condense + intent + rewrite) |
| 4 | `query_complexity` | `nodes/query_complexity.py` | Adaptive Router L1 (đơn/phức) |
| 5 | `adaptive_decompose` | `query_graph.py:3736` | LLM tách sub-query (multi-entity) |
| 6 | `rewrite_and_mq_parallel` | `query_graph.py:3735` | Query rewrite + multi-query paraphrase |
| **7** | **`retrieve`** | `nodes/retrieve.py:147` | **Stats-route gating → vector/BM25 hybrid → RRF** |
| 8 | `graph_retrieve` | `query_graph.py:3744` | KG retrieval (optional) |
| 9 | `rerank → mmr_dedup → neighbor_expand` | `query_graph.py:3745-3750` | **Rerank (jina/cliff)** → MMR dedup → small-to-big |
| 10 | `grade (CRAG) → rewrite_retry` | `query_graph.py:3751` | CRAG grade; inadequate → loop lại retrieve |
| 11 | `generate → guard_output → persist` | `query_graph.py:3753+` | LLM trả lời → output guard → lưu |

### Bên trong `retrieve` (gating quyết định đường nào):
`nodes/retrieve.py:204-227`, chạy trên `state["original_query"]` (raw, KHÔNG phải condensed):
1. `parse_range_query` — bound giá (`:205`)
2. `parse_code_query` — mã/spec sản phẩm (`:216`) ← *em vừa đảo lên trước list*
3. `parse_list_query` — keyword/list (`:226`)
4. Nếu có filter + confidence ≥ `RANGE_QUERY_MIN_CONFIDENCE` → `_do_stats_lookup` (`query_graph.py:2759`)
5. Nếu KHÔNG → vector/BM25 hybrid + RRF (`retrieve.py:977+`)

---

## 2. Trạng thái Reranker (evidence)

**Config** (`system_config`):
```
reranker_provider = "jina"      reranker_model = "zerank-2"     reranker_enabled = true
rerank_filter_strategy = "cliff"  rerank_cliff_gap_ratio = 0.5  reranker_min_score_active = 0.30
rerank_weights_by_intent default = {bm25:0.5, vector:0.5, reranker:0.0}   ← LƯU Ý
```
**Keys (.env):** Jina SET (65 ch) · ZeroEntropy SET (19 ch) · Cohere/Voyage EMPTY.

**Trace thật** (query legal "quy định sao lưu dự phòng"):
```
jina_rerank_done  model=jina-reranker-v3
rerank_threshold_gate_skipped  strategy=cliff reason=cliff_strategy_owns_filtering
```
→ **Rerank chạy, cliff dùng điểm rerank để cắt.** KHÔNG chết.

**✅ Đã resolve `reranker:0.0`:** có 2 cơ chế TÁCH BIỆT, không xung đột:
- (a) **RRF fusion weight** (`adaptive_rerank_weight.py`, dùng ở `retrieve.py:1058`): trộn điểm bm25/vector/reranker-as-*retrieval-signal* TRƯỚC khi vào rerank node. `reranker:0.0` ở đây ĐÚNG — vì reranker là node riêng CHẠY SAU, không double-count làm pre-signal.
- (b) **Rerank node** (`nodes/rerank.py`, mode="rerank"): jina **reorder lại `out`** + cliff cắt (`:255-288`). Đây mới là rerank thật — **ĐANG ACTIVE, có đổi thứ tự cuối.**
→ **Kết luận: rerank sống VÀ ảnh hưởng thứ tự.** Weight 0.0 không vô hiệu nó.

---

## 3. Insight kiến trúc cốt lõi: COMPLETENESS ≠ RELEVANCE

Câu hỏi của owner: *"lấy danh sách → rerank → topK → LLM, sao phải fix SQL?"*

**Trả lời:** vì 2 loại câu hỏi cần 2 cơ chế retrieval khác bản chất:

| Loại câu | Cần gì | Công cụ đúng |
|---|---|---|
| **Factoid/ngữ nghĩa** ("quy định sao lưu thế nào") | chunk *liên quan nhất* | vector+rerank+topK (Path A) |
| **List/Count/Aggregation** ("liệt kê hết dịch vụ da", "có bao nhiêu", "đắt nhất") | *TẤT CẢ* record khớp | structured/set retrieval (Path B) |

> **Rerank+topK tối ưu PRECISION rồi TRUNCATE → không bao giờ đảm bảo recall=100%.**
> Hỏi "liệt kê 4 dịch vụ da" mà topK=5 nhưng vector chỉ rank cao 2 cái → mất 2. Reranker không cứu được vì nó chỉ *xếp lại* cái đã lấy, không *bổ sung* cái thiếu.

**SOTA xác nhận** (research agent, mục 5): *"similarity-ranked top-K is the WRONG tool for completeness — use structured-index/metadata routing. Treat 'list all X' as a set/aggregation problem, NOT a top-K similarity problem."* (LangChain SelfQueryRetriever; arxiv 2510.02388 Learning-to-Route).

→ **Path B (SQL structured) đúng về nguyên tắc.** Đó là lý do nó tồn tại và em fix nó.

---

## 4. Ranked Root Weaknesses (evidence `file:line`)

### W1 — Keyword match là SUBSTRING ILIKE thuần, ZERO semantic (CRITICAL)
`infrastructure/repositories/stats_index_repository.py:443-452`:
```sql
WHERE unaccent(entity_name) ILIKE unaccent(:kw)   -- kw = '%keyword%'
```
- **Over-match đo thật:** `'%da%'` (unaccent) → 18 dịch vụ, gồm "Gội **đầu**", "Thải độc **đầu**", "Gội **dầu**" (vì `unaccent('đầu')='dau'⊃'da'`). SAI.
- **Under-match:** không expand "da"→{tẩy da chết, chăm sóc da, trẻ hóa da}; chỉ khớp khi tên *chứa literal* "da".
- Không embedding, không synonym, không trigram, không fuzzy.

### W2 — `parse_list_query` strip stopword = blocklist VN hardcode, fragile (HIGH)
`shared/query_range_parser.py:339-400`. Keyword = phần dư sau khi xoá ~40 cụm VN. Failure:
- **Over-strip:** keyword thật trùng stopword ("các", "loại", "về") bị xoá.
- **Filler sót:** "có dịch vụ **vào về** da chết" → kw="vào về da chết" → ILIKE miss → rớt vector top-1 → **chỉ 1 service** (đúng bug owner báo). *Em vừa vá vào/về/nào — nhưng đây là vá string, không giải gốc.*

### W3 — TopK per-intent TĨNH, không query-adaptive (HIGH)
- `DEFAULT_TOP_K=20` (`_00_app_env_taxonomy.py:28`) — **không phải 5** như tưởng.
- Per-intent: `_16_prompt_token_squeeze_phase_b.py:84` → aggregation=40, multi_hop=30, comparison=25, factoid=15, light=5.
- Promote nếu `has_aggregation_keyword` (substring match `_24_structural_markers_by_lang.py:53`). Nhưng "tư vấn về da" / "có làm X" KHÔNG chứa token agg → nếu LLM gán factoid → topK=15, list bị giới hạn.
- **Verdict: dynamic-by-intent, fragile-by-phrasing.**

### W4 — 3 bộ phân loại intent rời rạc, không chung vocabulary (HIGH)
1. `heuristic_intent_classifier.py:62-106` (regex) — agg = `có mấy|bao nhiêu|liệt kê|tất cả...` — **thiếu** `tư vấn về`, `có làm`, `có dịch vụ`.
2. `parse_list_query` signals (`query_range_parser.py:378`) — bắt `tu van ve|co dich vu` — **thiếu** `có làm X`, `tên gì`.
3. LLM `understand_query` — semantic nhưng non-deterministic.
→ "có làm tẩy da chết không" lọt cả 3 → vector top-15 → có thể chỉ 1 variant.

### W5 — Stats route phụ thuộc `document_service_index` đã populate; bot nào chưa index → MỌI câu list rớt vector (HIGH)
`retrieve.py:198` gate toàn bộ stats route trên `stats_index_repo is not None` + có rows. Bot chưa extract stats → list query rớt vector top-k. → coverage **không đồng nhất giữa các bot**.

### W6 — `custom_vocabulary` synonym map WIRED nhưng CHẾT ở keyword SQL (MEDIUM, gốc của "no semantic grouping")
- Schema `bots.custom_vocabulary` JSONB tồn tại; đọc vào state (`retrieve.py:671`).
- Dùng cho: (a) expand viết tắt VN của *query text*, (b) `vocabulary_expander.enrich_state`.
- **NHƯNG `enrich_state` chỉ ghi `state["context_base"]["vocabulary"]`** = hint cho LLM (`vocabulary_expander.py:489-493` ghi rõ "not an instruction"). **KHÔNG expand keyword đưa vào `query_by_name_keyword`.** Synonym map không bao giờ tới ILIKE.
- → Owner KHÔNG thể dạy "da"→{da chết, chăm sóc da}. **Đây đúng là "code chưa control được" owner nói.**

### W7 — Coverage do SYSPROMPT ép, LLM chỉ liệt kê được cái retrieval đưa lên (MEDIUM-HIGH)
Sysprompt spa (alembic 0236/0237/0128) CÓ rule mạnh "TƯ VẤN NHÓM → LIỆT KÊ ĐỦ", GATE-2 list-exception, ENUMERATION STRICT. **Nhưng LLM chỉ enumerate cái đã vào context.** Nếu retrieval (ILIKE miss / vector truncate) đưa 1/4 → "liệt kê đủ" ra 1. **Đúng pattern wrong-layer (bài học spa-07 2026-06-03 trong CLAUDE.md): vá coverage ở sysprompt khi gốc ở retrieval.**

### W8 — Multilingual: parsers VN-locked (MEDIUM)
`parse_list_query` signals (`query_range_parser.py:376`), `_LIST_SIGNALS` (`:119`), stopwords (`:339`) toàn token VN. `_LIST_SIGNALS` có "list" nhưng thiếu "how many/all/do you have/services". → "do you have skin services" (EN) → no list signal → vector top-k. LLM understand path đa ngữ OK; **fast structured route VN-locked.**

---

## 5. SOTA — Cách Expert giải (research, có cite)

| Kỹ thuật | Giải gì | Khi nào | Nguồn |
|---|---|---|---|
| **Self-query → metadata filter** trên cột category controlled | semantic grouping + complete list (đảm bảo đủ, không over-match) | "list all X" | LangChain SelfQueryRetriever; Elastic self-query |
| **Intent router** (LLM nhẹ) phân list/factoid/yes-no → dispatch path | phrasing loạn → vài intent chuẩn | mọi query | RAG Survey 2312.10997; arxiv 2510.02388 |
| **Adaptive-k** (cắt theo phân phối điểm, 1 pass) | topK động: list lấy rộng, factoid hẹp | mọi query | arxiv 2506.08479 (Adaptive-k); 2403.14403 (Adaptive-RAG) |
| **Multi-query + RAG-Fusion (RRF)** | recall trên đa cách hỏi | phrasing variety | arxiv 2312.10997 |
| **Hybrid BM25 + dense + curated synonym** | grouping có kiểm soát, tránh drift | structured corpus | Hybrid Search 2026; arxiv 2604.01733 |
| **VN word-segmentation (VnCoreNLP/RDRSegmenter)** đối xứng query+corpus | match "sân_bay" nguyên cụm, hết substring bẩn + VN compound | VN corpus | z-luannt-new-feature.txt §213-232 |
| ~~HyDE / step-back~~ | — | **DE-PRIORITIZE** — HyDE thêm noise trên factoid corpus | benchmark 2604.01733 |

**Lưu ý nguồn:** mọi % lift là từ benchmark paper, **chưa phải corpus của mình** → theo rule#0 phải load-test đo trước khi tin.

**Về AdapChunk & 2 external refs:**
- `_external_refs/adaptive-chunking` = framework eval Ekimetrics (LREC 2026, arxiv 2603.25333) — chọn chunking bằng **5 metric argmax**, KHÔNG có LLM selector. **KHÁC** spec 7-tầng owner paste (LLM Strategy Selector + Rule Cross-check + HDT/SEMANTIC/PROPOSITION/HYBRID).
- `_external_refs/RAG-Anything` = multimodal RAG (HKUDS, arxiv 2510.12323) — query-side chỉ có 4-mode retrieval (naive/local/global/hybrid) + graph fusion.
- **Cả 2 ref + AdapChunk đều INGEST-SIDE (chunking), KHÔNG giải query-understanding.** Cái cần cho query nằm ở bảng SOTA trên. Giá trị tái dùng: 5 metric chunk-quality (SC/ICC/DCC/BI/RC) làm harness đánh giá chunk; idea per-doc strategy.

---

## 6. Strategic Plan — theo Impact (KHÔNG vá string nữa)

> Nguyên tắc CLAUDE.md: EVOLVE không REWRITE; config-driven per-bot; domain-neutral; đo trước claim.

### PHASE Q1 — Intent Router thống nhất (Low-Med effort, High impact)
- Gộp 3 detector rời (W4) thành 1 **intent taxonomy** có nhóm `list/coverage/existence` (gồm "có X không", "có làm X", "bao nhiêu X", "tư vấn về X", "có những X nào").
- Router quyết định: list/coverage → Path B (structured/set); factoid → Path A (vector+rerank).
- Config-driven: pattern theo `language_pack`, KHÔNG hardcode VN trong core.
- **Đo:** load-test 3 bot, đối chiếu intent gán vs expected.

### PHASE Q2 — Adaptive-k (Low effort, High impact)
- Thay topK tĩnh bằng cắt theo phân phối điểm (cliff đã có cho rerank — mở rộng sang retrieve stage). List intent → ceiling cao; factoid → hẹp.
- Reuse `rerank_cliff_*` constants; thêm `retrieve_adaptive_k_enabled` per-bot.

### PHASE Q3 — Category metadata + Self-query filter (Med effort, **Highest impact**)
- Thêm cột `entity_category` *sạch* (controlled vocab: skin/hair/body/...) vào `document_service_index` — owner định nghĩa per-bot, seed qua alembic/admin (KHÔNG hardcode).
- Câu list → filter `WHERE category = :cat` (deterministic, đủ 100%, không lẫn "đầu").
- Intent router (Q1) compile NL → filter. Đây là cú "Self-query" — giải W1+W6 tận gốc.

### PHASE Q4 — Wire `custom_vocabulary` vào keyword match (Med effort, Med-High)
- `query_by_name_keyword` nhận synonym map → expand keyword thành OR-set ILIKE (hoặc tốt hơn: map về category Q3).
- Owner dạy "da"→{da chết, chăm sóc da} qua `bots.custom_vocabulary` → thật sự có hiệu lực.

### PHASE Q5 — VN word-segmentation đối xứng + multilingual fast-path (Med effort, Med-High)
- Segment query + corpus bằng VnCoreNLP trước match (W8 + substring bẩn).
- Đưa list/agg signals vào `language_pack` per-locale (EN/zh), gỡ VN-hardcode khỏi `parse_list_query`.

### Dọn nợ kỹ thuật (Low)
- Fix `embedding_model_mismatch` so sánh prefix (`query_graph.py:769`) — strip `provider/` trước khi so.
- Verify `rerank_weights.default.reranker=0.0` có vô hiệu rerank ở fusion không (mục 2).
- Lift `flags = 5` inline (`retrieve.py:1051`) ra constant.

---

## 7. Rủi ro & Đo lường (rule#0)

- **Mọi phase PHẢI load-test đo trước/sau** (RAGAS faithfulness + **Coverage rate** = % câu corpus-có-đáp-án mà bot trả đủ). Coverage < 0.95 = blocker.
- **HALLU=0 sacred** — category filter + adaptive-k KHÔNG được làm bot bịa; trap (Michelin/phun xăm/Điều 78) phải vẫn refuse.
- **Không wrong-layer**: list coverage fix ở **retrieval** (Q1-Q4), KHÔNG thêm rule sysprompt (bài học spa-07).
- **EVOLVE**: giữ khung 2-path; category là cột mới + backward-compat null; KHÔNG đập Path A/B.

---

## 8. Phụ lục — Evidence files
- `nodes/retrieve.py` (gating, topK, vocab read) · `shared/query_range_parser.py` (parsers, VN stopwords)
- `infrastructure/repositories/stats_index_repository.py:418` (`query_by_name_keyword` substring ILIKE)
- `application/services/heuristic_intent_classifier.py` (L1 intent) · `nodes/understand.py` (LLM understand)
- `application/services/vocabulary_expander.py:489` (vocab chết cho keyword) · `query_graph.py:2740` (`_do_stats_lookup`), `:769` (embedding mismatch log)
- `shared/constants/_16_prompt_token_squeeze_phase_b.py:84` (topK by intent), `_24_structural_markers_by_lang.py:53` (agg keywords by lang)
- Sysprompt coverage: alembic `0236`, `0237`, `0128`
- Refs: `_external_refs/adaptive-chunking` (Ekimetrics), `_external_refs/RAG-Anything` (HKUDS), `z-luannt-new-feature.txt` (chunking survey)
