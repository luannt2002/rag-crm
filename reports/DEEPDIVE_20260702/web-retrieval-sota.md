# DEEPDIVE — Web research: 2025–2026 State of the Art in RAG RETRIEVAL

**Slug**: `web-retrieval-sota` · **Date**: 2026-07-02 · **Mode**: READ-ONLY web research + codebase grounding
**Rule #0 compliance**: every claim below carries a URL (web) or `file:line` (code). Claims are labeled **FACT** (source states it) vs **HYPOTHESIS** (my inference, unverified against ragbot runtime). Vendor self-benchmarks are labeled **VENDOR-CLAIM**.

---

## 0. Where ragbot stands today (code FACTs, grounding for the recommendations)

| Component | Current implementation | Evidence |
|---|---|---|
| Lexical branch | Postgres FTS `ts_rank_cd(...)` over `websearch_to_tsquery('simple', :query)` — **cover-density ranking, NOT true BM25** (no corpus-level IDF, no doc-length saturation) | `src/ragbot/infrastructure/retrieval/pg_bm25_retrieval.py:112-119`; `src/ragbot/infrastructure/vector/pgvector_store.py:436-449` |
| Dense branch | pgvector, ZeroEntropy `zembed-1` at 1280 dims (server-side `dimensions` param passed on every call) | `src/ragbot/shared/constants/_02_per_intent_rerank_skip_gate_.py:65-66`; `src/ragbot/infrastructure/embedding/zeroentropy_embedder.py:144-148` |
| Fusion | RRF round-robin node | `src/ragbot/orchestration/nodes/rrf_round_robin.py` |
| Reranker | `zerank-2` default (`DEFAULT_ZEROENTROPY_RERANKER_MODEL = "zerank-2"`), plus jina/voyage/ViRanker-local adapters behind registry | `src/ragbot/shared/constants/_01_http_db_client_construction_.py:46`; `src/ragbot/infrastructure/reranker/registry.py` |
| Query expansion | HyDE adapter (`infrastructure/hyde/llm_hyde.py`) + multi-query fanout nodes | `src/ragbot/infrastructure/hyde/llm_hyde.py`; `src/ragbot/orchestration/nodes/retrieve.py` |
| Multi-vector (late interaction) | Adapter exists but is **entirely commented out** (dead scaffold) | `src/ragbot/infrastructure/embedding/sentence_split_multi_vector.py:70-130` (all `def`s commented) |
| Vietnamese tokenization | underthesea word segmentation + code-token masking (`A1/B2C3`, `91H` kept as ONE BM25 token), fallback lowercase | `src/ragbot/shared/vi_tokenizer.py:1-60` |
| Lexical Strategy/DI | `lexical_registry.py` + `null_lexical_retrieval.py` already exist → swap-ready | `src/ragbot/infrastructure/retrieval/lexical_registry.py` |

**Summary of the gap**: the platform is architecturally at 2025 SOTA (hybrid + RRF + cross-encoder rerank + per-bot DI), but the lexical branch is *pseudo-BM25* (`ts_rank_cd` has no IDF), the learned-sparse and late-interaction axes are absent/dead, and query-expansion value is unproven per-bot.

---

## 1. Hybrid search (BM25 + dense + RRF) — still the baseline that wins

- **FACT**: On the WANDS e-commerce benchmark a tuned hybrid (BM25+vector) reaches NDCG 0.7497 vs 0.6983 (BM25-only) and 0.6953 (vector-only) — a ~7.4% lift over either branch alone. Source: [Digital Applied — Hybrid Search: BM25, Vector & Reranking Reference 2026](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026).
- **FACT**: A 2026 arXiv benchmark on text-and-table documents finds BM25+dense fused via RRF "improves over both constituent methods across all metrics and all dataset subsets"; two-stage hybrid + neural rerank reaches Recall@5 = 0.816 on financial docs. Source: [From BM25 to Corrective RAG: Benchmarking Retrieval Strategies (arXiv 2604.01733)](https://arxiv.org/html/2604.01733v1).
- **FACT**: RRF (Cormack/Clarke/Buettcher 2009) is score-scale-free — it fuses on ranks, standard `k=60`; ParadeDB's manual recommends `LIMIT 20` per search branch and optional weighted RRF (e.g. 0.7 lexical / 0.3 semantic for technical corpora). Source: [ParadeDB — Hybrid Search in PostgreSQL: The Missing Manual](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual).
- **FACT**: Dense and sparse fail in *orthogonal* ways (exact codes/IDs vs paraphrase), which is why fusion wins. Source: [mbrenndoerfer — Hybrid Search: BM25 and Dense Retrieval Combined](https://mbrenndoerfer.com/writing/hybrid-search-bm25-dense-retrieval-fusion).

**Ragbot implication (HYPOTHESIS)**: the platform already fuses via RRF, so the marginal win is not "add hybrid" but "make the lexical branch a *real* BM25" (§2) — the WANDS/arXiv numbers above were all measured with true-BM25 lexical branches, not `ts_rank_cd`.

---

## 2. True BM25 inside Postgres — the ecosystem caught up in late 2025

The single most quotable line for ragbot: **Postgres `ts_rank` "lacks global context because it only considers individual documents in isolation. True BM25 accounts for inverse document frequency… which `ts_rank` cannot do."** — [ParadeDB, Hybrid Search Missing Manual](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual).

Options, all verified live in 2025–2026:

| Extension | What it is | Evidence |
|---|---|---|
| **pg_textsearch** (TigerData) | Native BM25 index (`CREATE INDEX … USING bm25(content)`), TF saturation + IDF + length norm; **fully open source under the PostgreSQL license**; released ~2025-12-23; demonstrated in same post fusing with pgvector via RRF; benchmarked at 138M docs vs ParadeDB | [TigerData — BM25 is now in Postgres](https://www.tigerdata.com/blog/you-dont-need-elasticsearch-bm25-is-now-in-postgres) |
| **VectorChord-bm25** | BM25 ranking with Block-WeakAnd algorithm, designed to pair with VectorChord/pgvector hybrid | [VectorChord — Hybrid search with Postgres Native BM25](https://blog.vectorchord.ai/hybrid-search-with-postgres-native-bm25-and-vectorchord); [docs](https://docs.vectorchord.ai/vectorchord/use-case/hybrid-search.html) |
| **ParadeDB pg_search** | Tantivy-backed BM25, production FTS | [ParadeDB blog](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual) |

- **FACT (relevance delta)**: On a labeled dataset, VectorChord measured Postgres's built-in ranker at **0.07**, BM25 at **0.69**, pgvector at 0.66, fused at **0.70** — i.e. native ranking was ~10× worse than BM25 on their labels (metric per their post; queries all <12 ms in one Postgres). Source: [VectorChord — PostgreSQL full-text search fast when done right](https://blog.vectorchord.ai/postgresql-full-text-search-fast-when-done-right-debunking-the-slow-myth). (Note: their 0.07 baseline is `ts_rank` without cover density/normalization tuning; ragbot's tuned `ts_rank_cd` + `websearch_to_tsquery` + vi segmentation will be far above that floor — but the IDF gap is structural.)
- **FACT (prior art in repo)**: memory/roadmap already flagged `pg_textsearch` as deferred P15-1 (MEMORY.md "P15 roadmap progress: P15-1 deferred (pg_textsearch)").

**Recommendation** (see §10 R1): this is the highest-leverage retrieval upgrade available to ragbot, and it drops into the existing `lexical_registry` as one new strategy file — exactly the Port+Registry pattern CLAUDE.md mandates.

---

## 3. Late interaction (ColBERT / PLAID / multi-vector)

- **FACT**: Late interaction (ColBERTv2) holds SOTA on many benchmarks; **PLAID** cuts its search latency up to **7× GPU / 45× CPU** vs vanilla ColBERTv2 with no quality loss. Source: [PLAID (arXiv 2205.09707)](https://arxiv.org/abs/2205.09707).
- **FACT**: 2025's **GTE-ModernColBERT-v1** (LightOn, 149M params, trained with PyLate) reached BEIR avg NDCG@10 ≈ **54.7** — SOTA-class for its size; LightOn's **LateOn** then pushed to **57.22** BEIR avg. Sources: [LightOn GTE-ModernColBERT release](https://lighton.ai/lighton-blogs/lighton-releases-gte-moderncolbert-first-state-of-the-art-late-interaction-model-trained-on-pylate), [lightonai/GTE-ModernColBERT-v1 (HF)](https://huggingface.co/lightonai/GTE-ModernColBERT-v1), [lightonai/LateOn (HF)](https://huggingface.co/lightonai/LateOn), [PyLate (arXiv 2508.03555)](https://arxiv.org/html/2508.03555v1).
- **FACT**: Multi-vector shows its edge specifically on **out-of-domain, long-context and reasoning-intensive** retrieval. Source: [PyLate paper](https://arxiv.org/html/2508.03555v1).
- **FACT (multilingual late interaction exists)**: **jina-colbert-v2** — 560M XLM-RoBERTa backbone, **89 languages**, matryoshka token dims 128/96/64, +6.5% over ColBERTv2, beats BM25 on all MIRACL languages tested. Sources: [Jina blog](https://jina.ai/news/jina-colbert-v2-multilingual-late-interaction-retriever-for-embedding-and-reranking/), [arXiv 2408.16672](https://arxiv.org/html/2408.16672v4).
- **FACT (the practicality unlock)**: **MUVERA** (Google, NeurIPS 2024) reduces multi-vector retrieval to single-vector MIPS via Fixed Dimensional Encodings — avg **+10% recall at 90% lower latency** vs PLAID across BEIR, and works with *off-the-shelf* single-vector indexes (i.e. pgvector). Sources: [Google Research blog](https://research.google/blog/muvera-making-multi-vector-retrieval-as-fast-as-single-vector-search/), [arXiv 2405.19504](https://arxiv.org/abs/2405.19504).

**Ragbot implication**: pgvector has no native MaxSim; a ColBERT branch today means either (a) MUVERA-style FDE vectors stored as ordinary pgvector rows, or (b) an external PLAID engine (new infra, violates "one Postgres" simplicity). **HYPOTHESIS**: with zerank-2 already reranking top-50, a late-interaction *first-stage* would mostly duplicate the reranker's win; defer until eval shows first-stage recall (not precision) is the binding constraint. The commented-out `sentence_split_multi_vector.py` scaffold (`src/ragbot/infrastructure/embedding/sentence_split_multi_vector.py:70-130`) should be revived-as-MUVERA or deleted — dead code is the worst of both.

---

## 4. Learned sparse (SPLADE family)

- **FACT**: SPLADE learns query/document term expansions via BERT MLM head; sparse representations generalize better out-of-domain (BEIR), and **SPLADE-v3** improves BEIR OOD by ~2%. Sources: [naver/splade (GitHub)](https://github.com/naver/splade), [SPLADE-v3 (arXiv 2403.06789)](https://www.emergentmind.com/papers/2403.06789).
- **FACT**: **Echo-Mistral-SPLADE** (LLM-based learned sparse) beats SPLADE-v3 by 3–5 BEIR nDCG@10 points — learned sparse is now LLM-backbone territory. Source: [Mistral-SPLADE (arXiv 2408.11119)](https://arxiv.org/html/2408.11119v2).
- **FACT**: BM25 still wins several BEIR OOD cases outright — sparse lexical signal remains unbeaten on exact-match-heavy domains. Source: [same paper](https://arxiv.org/html/2408.11119v2).
- **FACT (multilingual learned sparse = BGE-M3, not SPLADE)**: SPLADE models are English-trained; the practical multilingual learned-sparse option is **BGE-M3**, which emits dense (1024d) + **sparse lexical weights** + ColBERT token vectors from one encoder, 100+ languages, 8192 tokens. Sources: [BAAI/bge-m3 (HF)](https://huggingface.co/BAAI/bge-m3), [BGE-M3 paper (arXiv 2402.03216)](https://arxiv.org/html/2402.03216v3).
- **FACT (storage exists in pgvector)**: pgvector ≥0.7.0 has a **`sparsevec`** type storing only non-zero elements — SPLADE/BGE-M3 sparse weights are directly storable/queryable in Postgres. Sources: [pgvector (GitHub)](https://github.com/pgvector/pgvector), [VectorChord — sparse vector docs](https://docs.vectorchord.ai/use-case/sparse-vector.html), [ParadeDB — SPLADE inside Postgres](https://www.paradedb.com/blog/introducing-sparse).

**Ragbot implication (HYPOTHESIS)**: a third RRF branch (BGE-M3 sparse over `sparsevec`) is the cheapest way to buy SPLADE-style OOD robustness for Vietnamese without leaving Postgres — but it adds an ingest-time model dependency; gate per-bot, measure with the existing eval harness before default-on (CLAUDE.md T2 discipline).

---

## 5. Reranker landscape — ragbot's default is the current leaderboard #1

- **FACT**: Agentset reranker leaderboard (updated 2026-02-15; GPT-5 pairwise ELO over top-50 FAISS candidates, 3 domains): **zerank-2 ELO 1638 (#1)**, Cohere Rerank 4 Pro 1629 (#2), zerank-1 1573, Voyage rerank-2.5 1544; latency zerank-2 ≈ **265 ms** vs Cohere 4 Pro ≈ 614 ms; price zerank-2 **$0.025/1M** vs $0.050/1M. Source: [Agentset reranker leaderboard](https://agentset.ai/rerankers), [zerank-2 vs cohere-3.5](https://agentset.ai/rerankers/compare/zerank-2-vs-cohere-rerank-35).
- **FACT**: Rerankers as a stage deliver "15–40% higher retrieval accuracy" vs semantic-only per the same leaderboard methodology page ([Agentset](https://agentset.ai/rerankers)).
- **FACT**: zerank-2 (released 2025-11-18) is a cross-encoder trained with zELO (pairwise→Elo), **instruction-following** (append abbreviations/business context to steer ranking), **calibrated scores** (0.8 ≈ 80% relevance), 100+ languages incl. code-switching; on HuggingFace + API. Source: [ZeroEntropy — Introducing zerank-2](https://zeroentropy.dev/articles/zerank-2-advanced-instruction-following-multilingual-reranker/). Vietnamese not explicitly named in the post (**gap to verify locally**).
- **FACT**: Cohere Rerank 4 shipped December 2025 (post-3.5); Voyage rerank-2.5 claims +7.94% over Cohere 3.5. Source: [bestaiweb comparison](https://www.bestaiweb.ai/how-to-add-reranking-to-your-rag-pipeline-with-cohere-rerank-4-pro-voyage-rerank-2-5-and-zerank-2-in-2026/), [Agentset blog](https://agentset.ai/blog/best-reranker).
- **FACT (open-source)**: **Qwen3-Reranker** 0.6B/4B/8B, Apache-2.0, 100+ languages, SOTA-class open rerankers (June 2025). Source: [Qwen3-Embedding blog](https://qwenlm.github.io/blog/qwen3-embedding/), [HF Qwen3-Reranker-8B](https://huggingface.co/Qwen/Qwen3-Reranker-8B).
- **FACT (Vietnamese-specific)**: **ViRanker** (PhoBERT-based cross-encoder, `namdp-ptit/ViRanker` on HF) outperforms PhoRanker and BGE-reranker-v2-m3 on Vietnamese reranking NDCG@10. Source: [ViRanker paper (arXiv 2509.09131)](https://arxiv.org/pdf/2509.09131). Ragbot already carries an adapter: `src/ragbot/infrastructure/reranker/viranker_local_reranker.py`.

**Ragbot verdict (FACT + HYPOTHESIS)**: FACT — the default reranker (`zerank-2`, constants `_01:46`) is the Feb-2026 leaderboard #1 at half the price and ~2.3× lower latency than Cohere 4 Pro; no change needed. HYPOTHESIS — zerank-2's *instruction* input could carry per-bot `custom_vocabulary`/abbreviations (bot-owner config, not app-injected answer text — Quality-Gate-#10-compatible since it steers *ranking*, not the answer); needs an A/B with the eval harness before adoption. Known local caveat already in constants: zerank-2 buried a legal clause BM25 ranked #1 (`_01_http_db_client_construction_.py:181`) — rank-fusion guardrails must stay.

---

## 6. Embedding models — SOTA and how to read the leaderboards

- **FACT**: **Qwen3-Embedding-8B** = 70.58 on MMTEB (multilingual), surpassing **gemini-embedding-001** (68.32, English MTEB lead at publication); Qwen3-Embedding 0.6B/4B/8B are Apache-2.0, 100+ languages. Sources: [Qwen blog](https://qwenlm.github.io/blog/qwen3-embedding/), [arXiv 2506.05176](https://arxiv.org/html/2506.05176v1), [Modal MTEB overview](https://modal.com/blog/mteb-leaderboard-article).
- **FACT**: OpenAI text-embedding-3-large ≈ 64.6 MTEB — no longer competitive at the top. Source: [Ailog — Best Embedding Models 2025](https://app.ailog.fr/en/blog/guides/choosing-embedding-models).
- **FACT**: **voyage-4** family (Jan 15, 2026) is the first production **MoE** embedding model, shared embedding space across large/standard/lite/nano, ~40% lower serving cost claims — but its wins are on Voyage's own RTEB runs, "not yet independently verified on the public MTEB leaderboard". Sources: [Vercel model page](https://vercel.com/ai-gateway/models/voyage-4-large), [buildmvpfast comparison](https://www.buildmvpfast.com/blog/best-embedding-model-comparison-voyage-openai-cohere-2026).
- **FACT**: **RTEB** (Hugging Face + Voyage + mixedbread, Oct 2025) is the new retrieval-focused benchmark with **private held-out datasets** specifically to kill benchmark overfitting ("teaching to the test"); it now powers the MTEB leaderboard Retrieval section. Sources: [HF blog — Introducing RTEB](https://huggingface.co/blog/rteb), [InfoQ](https://www.infoq.com/news/2025/10/rteb-benchmark/), [The New Stack](https://thenewstack.io/exploring-rteb-a-new-benchmark-to-evaluate-embedding-models/).
- **VENDOR-CLAIM**: ragbot's current **zembed-1** (ZeroEntropy, 4B open-weight): ZE claims best-in-class across finance/health/legal/code/STEM and top of MS MARCO, beating text-embedding-3-large, Cohere Embed v4, gemini-embedding-001, voyage-4-nano. Sources: [ZE — Introducing zembed-1](https://zeroentropy.dev/articles/introducing-zembed-1-the-worlds-best-multilingual-text-embedding-model/), [ZE — Best embedding model of 2026](https://zeroentropy.dev/articles/best-embedding-model-overall-2026/), [HF zeroentropy/zembed-1](https://huggingface.co/zeroentropy/zembed-1). Not yet on public MTEB (open issue: [mteb#4195](https://github.com/embeddings-benchmark/mteb/issues/4195)) → treat as unverified vs Qwen3/Gemini until RTEB lists it.

### 6b. Vietnamese specifically — VN-MTEB (2025) is the reference

- **FACT**: **VN-MTEB** (arXiv 2507.21500): 41 datasets / 6 tasks, 15 retrieval datasets. Retrieval top-3: **gte-Qwen2-7B-instruct 46.05%**, e5-Mistral-7B-instruct 41.73%, m-e5-large-instruct 40.88%. **Vietnamese-specific models UNDERPERFORM big multilingual**: AITeamVN Vietnamese_Embedding 34.18% retrieval; vietnamese-bi-encoder 54.89% overall avg vs m-e5-base 62.42%. RoPE-based + instruct-tuned models win. Source: [VN-MTEB (arXiv 2507.21500)](https://arxiv.org/html/2507.21500v1).
- **FACT**: Vietnamese legal retrieval work confirms the recipe = word-segmented lexical + dense hybrid, with synthetic-data fine-tuning for domain lift. Sources: [Multi-stage IR for Vietnamese Legal Texts (arXiv 2209.14494)](https://arxiv.org/pdf/2209.14494), [Improving Vietnamese Legal Document Retrieval using Synthetic Data (arXiv 2412.00657)](https://arxiv.org/html/2412.00657v1).

**Ragbot implication (HYPOTHESIS)**: the "fine-tuned-for-Vietnamese beats multilingual" intuition is *wrong* per VN-MTEB — big multilingual instruct models win. So the platform's multilingual-first embedding strategy is correct; the open question is only *which* multilingual model, answerable with a VN-MTEB-subset run of zembed-1 vs Qwen3-Embedding (never done publicly — **CHƯA verify, cần chạy eval**).

---

## 7. Matryoshka / dimension truncation

- **FACT**: MRL trains multi-checkpoint losses so leading dims carry most signal; at ~50% dims typical loss is 1–4 nDCG points; at 512d models retain 94–98%, at 256d ≥88% of full-dim nDCG@10. Sources: [HF — Matryoshka intro](https://huggingface.co/blog/matryoshka), [MindStudio — MRL in Gemini Embedding 2](https://www.mindstudio.ai/blog/matryoshka-representation-learning-gemini-embedding-2), [Patent embeddings benchmark (arXiv 2605.24297)](https://arxiv.org/pdf/2605.24297).
- **FACT (caution)**: benchmark-metric retention ≠ result-set overlap — at 256d only 57% of top-10 matched the 768d baseline in one study. If you truncate, your *retrieved set changes* even when nDCG barely moves. Source: [JoeSack — Matryoshka: Benchmark Quality vs Retrieval Overlap](https://joesack.substack.com/p/matryoshka-embeddings-benchmark-quality).
- **FACT (vendor position shift)**: ZeroEntropy published "**Matryoshka Is Dead**" — zembed-1's reduced dims (2560→1280→…→40) come from a **learned post-hoc linear projection**, which they measured to dominate an MRL-trained variant at every dim count; plus binary quantization to <128 bytes/vector. Source: [ZE — Matryoshka is dead](https://zeroentropy.dev/articles/matryoshka-is-dead/).
- **Code cross-check (FACT)**: ragbot requests `dimensions: 1280` server-side on every embed call (`zeroentropy_embedder.py:144-148`) — so it automatically gets ZE's projection, NOT naive client truncation. Correct usage. The comment at `constants/_02_per_intent_rerank_skip_gate_.py:66` calls it "matryoshka truncation" — terminologically stale per ZE's own article, mechanically harmless (**doc-only nit**).

---

## 8. Query expansion — HyDE / multi-query / RAG-fusion: evidence is MIXED, gate it

- **FACT**: RAGSmith (arXiv 2511.01386, systematic composition search across datasets): query-expansion methods show **inconsistent performance across datasets — no single RAG composition dominates uniformly**; reranking helps but magnitude varies. Source: [RAGSmith](https://arxiv.org/pdf/2511.01386).
- **FACT**: HyDE helps when docs are long/narrative and queries short/underspecified; it is **prone to hallucination in fact-precision domains** and "should be replaced by direct retrieval" there; query-expansion gives "limited benefit for precise numerical queries" where BM25 outperforms dense. Sources: [Medium — Retrieval Is the Bottleneck](https://medium.com/@mudassar.hakim/retrieval-is-the-bottleneck-hyde-query-expansion-and-multi-query-rag-explained-for-production-c1842bed7f8a), [HyDE topic overview](https://www.emergentmind.com/topics/hypothetical-document-embeddings-hyde), [ARAGOG (arXiv 2404.01037)](https://arxiv.org/pdf/2404.01037).
- **FACT**: Combining multiple rewrite strategies (MDP-selected) beats any single one: ~+7% vs HyDE, +2.55% vs RAG-Fusion on multi-hop. Source: [MQRF-RAG (ACM 2025)](https://dl.acm.org/doi/10.1145/3728199.3728221).

**Ragbot implication (HYPOTHESIS)**: ragbot's tenants are price-list / legal / spec-code heavy (exactly the "factual precision" profile where HyDE mis-retrieves). HyDE must stay per-bot opt-in with an eval gate, never platform-default-on. Multi-query fanout (already shipped) is the safer default of the two per MQRF/RAGSmith evidence.

---

## 9. Vietnamese lexical specifics — segmentation is the price of admission

- **FACT**: Vietnamese multi-syllable words ("sản phẩm") must be segmented (VnCoreNLP / underthesea) before BM25 or terms shatter into meaningless syllables; standard recipe in Vietnamese legal IR is segmentation + stopwords + lexical/dense hybrid. Sources: [Multi-stage IR for Vietnamese Legal Texts (arXiv 2209.14494)](https://arxiv.org/pdf/2209.14494), [underthesea via same literature](https://arxiv.org/html/2412.00657v1).
- **Code cross-check (FACT)**: ragbot already does this *with* code-token protection (masks `A1/B2C3`, `2-X17`, `91H` so underthesea can't shatter SKUs, then restores) — `src/ragbot/shared/vi_tokenizer.py:26-60`. This is ahead of what the papers describe (they don't handle spec-codes). Keep it; any BM25-extension migration (§2) MUST reuse the same segmentation on both index and query sides or scores desync (the `'simple'` config assumption is baked in at `pg_bm25_retrieval.py:9` and `vi_tokenizer.py:527`).

---

## 10. Concrete recommendations for ragbot (pgvector + BM25, Vietnamese-heavy, multi-tenant)

Ranked by expected T1 impact ÷ effort. Every item respects CLAUDE.md: Strategy+DI (new strategy file, no orchestrator edits), zero-hardcode (thresholds → `system_config`), per-bot gating, domain-neutral, measure-before-claim.

| # | Tier | Recommendation | Why / evidence | Effort |
|---|---|---|---|---|
| **R1** | T1 | **Swap `ts_rank_cd` → true BM25** via `pg_textsearch` (PostgreSQL-license) or VectorChord-bm25, as ONE new strategy in `lexical_registry` (`pg_bm25_retrieval.py` sibling), per-bot config-string flip, A/B with eval harness | `ts_rank` has no IDF ([ParadeDB](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)); native ranker scored 0.07 vs BM25 0.69 on labeled data ([VectorChord](https://blog.vectorchord.ai/postgresql-full-text-search-fast-when-done-right-debunking-the-slow-myth)); already deferred as P15-1 | Medium (extension install + 1 adapter + reuse `vi_tokenizer`) |
| **R2** | T1 | **Keep zerank-2** — it is leaderboard #1 (ELO 1638, 265 ms, $0.025/1M, [Agentset 2026-02-15](https://agentset.ai/rerankers)); pilot its **instruction-following** input fed from per-bot `custom_vocabulary` (bot-owner data, steers ranking not answers → QG#10-safe) | [ZE zerank-2 post](https://zeroentropy.dev/articles/zerank-2-advanced-instruction-following-multilingual-reranker/) | Low (adapter param + eval A/B) |
| **R3** | T1 | **Benchmark zembed-1 vs Qwen3-Embedding-8B/4B on a VN-MTEB retrieval subset + own golden sets** before believing either vendor. VN-MTEB says big multilingual instruct > Vietnamese-specific; zembed-1 has zero public VN numbers | [VN-MTEB](https://arxiv.org/html/2507.21500v1); [Qwen3 70.58 MMTEB](https://qwenlm.github.io/blog/qwen3-embedding/); [zembed-1 not on MTEB yet](https://github.com/embeddings-benchmark/mteb/issues/4195) | Medium (eval-only, no code) |
| **R4** | T1/T2 | **Optional third RRF branch: BGE-M3 sparse lexical weights in pgvector `sparsevec`** — multilingual learned-sparse (SPLADE itself is English-only), per-bot opt-in | [BGE-M3](https://huggingface.co/BAAI/bge-m3); [pgvector 0.7 sparsevec](https://github.com/pgvector/pgvector); SPLADE OOD wins ([arXiv 2408.11119](https://arxiv.org/html/2408.11119v2)) | High (ingest-side model + column + branch) — only after R1 measured |
| **R5** | T2 | **Do NOT build ColBERT/PLAID infra now.** If first-stage recall (not precision) is proven binding, use **MUVERA FDEs** stored as plain pgvector rows (single-vector proxy, +10% recall/−90% latency vs PLAID) or jina-colbert-v2 @128d. **Delete or revive-as-MUVERA the dead `sentence_split_multi_vector.py`** | [MUVERA](https://research.google/blog/muvera-making-multi-vector-retrieval-as-fast-as-single-vector-search/); dead code at `sentence_split_multi_vector.py:70-130` | Decision + cleanup |
| **R6** | T1 | **Keep HyDE per-bot opt-in, default OFF** for fact-precision tenants (prices/legal/SKU) — literature says HyDE hallucination-retrieves there; prefer multi-query as default expansion | [ARAGOG](https://arxiv.org/pdf/2404.01037); [RAGSmith](https://arxiv.org/pdf/2511.01386); [production guide](https://medium.com/@mudassar.hakim/retrieval-is-the-bottleneck-hyde-query-expansion-and-multi-query-rag-explained-for-production-c1842bed7f8a) | None (policy) |
| **R7** | T2 | **RRF hygiene**: k=60 standard, per-branch LIMIT ~20, expose weighted-RRF (lexical-heavy for spec/legal bots) as per-bot config | [ParadeDB manual](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual) | Low |
| **R8** | T2 | **Dims**: stay at 1280 via server-side `dimensions` param (correct today, `zeroentropy_embedder.py:148`); if storage pressure → 640 is likely ≤4pt loss *but* re-measure result-set overlap, not just nDCG | [HF matryoshka](https://huggingface.co/blog/matryoshka); [overlap caveat](https://joesack.substack.com/p/matryoshka-embeddings-benchmark-quality); [ZE learned projection](https://zeroentropy.dev/articles/matryoshka-is-dead/) | None now |
| **R9** | T1 | **Preserve `vi_tokenizer` segmentation symmetry** through any R1 migration (index-side and query-side must share segmentation), keep code-token masking | `vi_tokenizer.py:26-60`, `:527`; [arXiv 2209.14494](https://arxiv.org/pdf/2209.14494) | Guard test |
| **R10** | T2 | **Eval trust policy**: prefer RTEB-style held-out internal golden sets over vendor MTEB claims (voyage-4 & zembed-1 both currently vendor-only); track Coverage + Faithfulness per CLAUDE.md | [RTEB](https://huggingface.co/blog/rteb); [voyage-4 unverified note](https://www.buildmvpfast.com/blog/best-embedding-model-comparison-voyage-openai-cohere-2026) | Policy |
| **R11** | T2 | **ViRanker-local stays the offline/failover reranker** for Vietnamese (adapter already exists), Qwen3-Reranker-0.6B/4B is the modern self-hosted alternative if ViRanker ages | [ViRanker](https://arxiv.org/pdf/2509.09131); [Qwen3-Reranker](https://huggingface.co/Qwen/Qwen3-Reranker-8B); `infrastructure/reranker/viranker_local_reranker.py` | None now |

### What NOT to do (anti-recommendations, evidence-backed)

1. **Don't chase a Vietnamese-monolingual embedder** — VN-MTEB shows they lose to large multilingual instruct models ([arXiv 2507.21500](https://arxiv.org/html/2507.21500v1)).
2. **Don't default-on HyDE platform-wide** — fact-precision domains regress ([ARAGOG](https://arxiv.org/pdf/2404.01037)).
3. **Don't add Elasticsearch** — 2025-12 Postgres-native BM25 options close the gap in-database ([TigerData](https://www.tigerdata.com/blog/you-dont-need-elasticsearch-bm25-is-now-in-postgres)).
4. **Don't build PLAID serving infra** — MUVERA makes multi-vector a data-shape problem, not an engine problem ([Google Research](https://research.google/blog/muvera-making-multi-vector-retrieval-as-fast-as-single-vector-search/)).
5. **Don't trust any vendor's own leaderboard** (zembed-1, voyage-4 both unverified publicly) — RTEB private-split or in-house goldens only ([HF RTEB](https://huggingface.co/blog/rteb)).

---

## 11. Full source list

**Hybrid / fusion**: [Digital Applied 2026 reference](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026) · [arXiv 2604.01733 BM25→CRAG benchmark](https://arxiv.org/html/2604.01733v1) · [ParadeDB hybrid manual](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual) · [mbrenndoerfer hybrid](https://mbrenndoerfer.com/writing/hybrid-search-bm25-dense-retrieval-fusion) · [denser.ai guide](https://denser.ai/blog/hybrid-search-for-rag/)

**Postgres BM25**: [TigerData pg_textsearch](https://www.tigerdata.com/blog/you-dont-need-elasticsearch-bm25-is-now-in-postgres) · [VectorChord BM25 perf](https://blog.vectorchord.ai/postgresql-full-text-search-fast-when-done-right-debunking-the-slow-myth) · [VectorChord hybrid](https://blog.vectorchord.ai/hybrid-search-with-postgres-native-bm25-and-vectorchord) · [VectorChord docs](https://docs.vectorchord.ai/vectorchord/use-case/hybrid-search.html) · [Pedro Alonso pg BM25](https://www.pedroalonso.net/blog/postgres-bm25-search/)

**Late interaction**: [PLAID arXiv 2205.09707](https://arxiv.org/abs/2205.09707) · [PyLate arXiv 2508.03555](https://arxiv.org/html/2508.03555v1) · [LightOn GTE-ModernColBERT](https://lighton.ai/lighton-blogs/lighton-releases-gte-moderncolbert-first-state-of-the-art-late-interaction-model-trained-on-pylate) · [HF GTE-ModernColBERT-v1](https://huggingface.co/lightonai/GTE-ModernColBERT-v1) · [HF LateOn](https://huggingface.co/lightonai/LateOn) · [jina-colbert-v2](https://jina.ai/news/jina-colbert-v2-multilingual-late-interaction-retriever-for-embedding-and-reranking/) · [arXiv 2408.16672](https://arxiv.org/html/2408.16672v4) · [MUVERA blog](https://research.google/blog/muvera-making-multi-vector-retrieval-as-fast-as-single-vector-search/) · [MUVERA arXiv 2405.19504](https://arxiv.org/abs/2405.19504)

**Learned sparse**: [naver/splade](https://github.com/naver/splade) · [SPLADE-v3](https://www.emergentmind.com/papers/2403.06789) · [Mistral-SPLADE arXiv 2408.11119](https://arxiv.org/html/2408.11119v2) · [BGE-M3 HF](https://huggingface.co/BAAI/bge-m3) · [BGE-M3 arXiv 2402.03216](https://arxiv.org/html/2402.03216v3) · [pgvector GitHub](https://github.com/pgvector/pgvector) · [ParadeDB SPLADE](https://www.paradedb.com/blog/introducing-sparse) · [VectorChord sparse](https://docs.vectorchord.ai/use-case/sparse-vector.html)

**Rerankers**: [Agentset leaderboard](https://agentset.ai/rerankers) · [Agentset best-reranker](https://agentset.ai/blog/best-reranker) · [zerank-2 vs cohere-3.5](https://agentset.ai/rerankers/compare/zerank-2-vs-cohere-rerank-35) · [ZE zerank-2](https://zeroentropy.dev/articles/zerank-2-advanced-instruction-following-multilingual-reranker/) · [ZE choosing guide](https://zeroentropy.dev/articles/ultimate-guide-to-choosing-the-best-reranking-model-in-2025/) · [bestaiweb 2026](https://www.bestaiweb.ai/how-to-add-reranking-to-your-rag-pipeline-with-cohere-rerank-4-pro-voyage-rerank-2-5-and-zerank-2-in-2026/) · [Qwen3-Reranker-8B](https://huggingface.co/Qwen/Qwen3-Reranker-8B) · [ViRanker arXiv 2509.09131](https://arxiv.org/pdf/2509.09131)

**Embeddings**: [Qwen3-Embedding blog](https://qwenlm.github.io/blog/qwen3-embedding/) · [arXiv 2506.05176](https://arxiv.org/html/2506.05176v1) · [Modal MTEB](https://modal.com/blog/mteb-leaderboard-article) · [Ailog 2025 guide](https://app.ailog.fr/en/blog/guides/choosing-embedding-models) · [Gemini Embedding arXiv 2503.07891](https://arxiv.org/pdf/2503.07891) · [voyage-4-large Vercel](https://vercel.com/ai-gateway/models/voyage-4-large) · [buildmvpfast comparison](https://www.buildmvpfast.com/blog/best-embedding-model-comparison-voyage-openai-cohere-2026) · [ZE zembed-1](https://zeroentropy.dev/articles/introducing-zembed-1-the-worlds-best-multilingual-text-embedding-model/) · [ZE Matryoshka-is-dead](https://zeroentropy.dev/articles/matryoshka-is-dead/) · [mteb#4195](https://github.com/embeddings-benchmark/mteb/issues/4195) · [HF RTEB](https://huggingface.co/blog/rteb) · [InfoQ RTEB](https://www.infoq.com/news/2025/10/rteb-benchmark/) · [The New Stack RTEB](https://thenewstack.io/exploring-rteb-a-new-benchmark-to-evaluate-embedding-models/)

**Vietnamese**: [VN-MTEB arXiv 2507.21500](https://arxiv.org/html/2507.21500v1) · [arXiv 2209.14494 VN legal IR](https://arxiv.org/pdf/2209.14494) · [arXiv 2412.00657 VN legal synthetic](https://arxiv.org/html/2412.00657v1) · [AITeamVN Vietnamese_Embedding](https://huggingface.co/AITeamVN/Vietnamese_Embedding)

**Matryoshka**: [HF matryoshka blog](https://huggingface.co/blog/matryoshka) · [MindStudio MRL](https://www.mindstudio.ai/blog/matryoshka-representation-learning-gemini-embedding-2) · [JoeSack overlap](https://joesack.substack.com/p/matryoshka-embeddings-benchmark-quality) · [arXiv 2605.24297 patent bench](https://arxiv.org/pdf/2605.24297)

**Query expansion**: [ARAGOG arXiv 2404.01037](https://arxiv.org/pdf/2404.01037) · [RAGSmith arXiv 2511.01386](https://arxiv.org/pdf/2511.01386) · [MQRF-RAG ACM](https://dl.acm.org/doi/10.1145/3728199.3728221) · [HyDE overview](https://www.emergentmind.com/topics/hypothetical-document-embeddings-hyde) · [production bottleneck guide](https://medium.com/@mudassar.hakim/retrieval-is-the-bottleneck-hyde-query-expansion-and-multi-query-rag-explained-for-production-c1842bed7f8a)
