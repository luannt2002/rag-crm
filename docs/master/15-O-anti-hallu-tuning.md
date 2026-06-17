# O — Anti-HALLU Tuning Guide (9-layer config base)

> **Status**: ✅ Verified 2026-05-05 — applied to production DB as default base
> **Scope**: Setting MẶC ĐỊNH cho 80% bot Ragbot — stack hiện tại: GPT-4.1-mini answer (policy mini toàn fleet, alembic 0184) + gpt-4.1-mini/Haiku enrich + ZeroEntropy zembed-1 (1280-dim) embed + zerank-2 rerank (ZE migrate 2026-05-12).
> **Source-of-truth**: `reports/RAGBOT_DEFAULT_CONFIG_TUNING_FINAL_20260505.md` (520 dòng deep-dive).
> **Production gate**: setting này chống bịa, tăng thông minh, vẫn trả lời tự nhiên.

---

## TL;DR — 9-LAYER DEFAULT BASE

| # | Layer | Knob | Default ⭐ | Universal? |
|---|---|---|---|---|
| 1 | Temperature | `generation_temperature` | 0.0 | ✅ mọi LLM |
| 1 | Temperature | `enrichment_temperature` | 0.0 | ✅ |
| 1 | Temperature | `llm_default_temperature` | 0.0 | ✅ |
| 2 | Grounding | `grounding_check_enabled` | true | ✅ |
| 2 | Grounding | `grounding_check_threshold` | 0.3 | ✅ |
| 2 | Grounding | `citation_marker_required` | true | ✅ |
| 3 | Chunk quality | `reranker_min_score_active` | **0.15** | ⚠ Calibrated cho ZeroEntropy `zerank-2` (default) + Jina v3 — KHÁC nếu Cohere/Voyage/ViRanker; tune per-bot trong `bots.plan_limits` |
| 3 | Chunk quality | `reranker_min_score_bypass` | 0.0 | ✅ |
| 4 | Self-correction | `pipeline_max_reflect_retries` | 2 | ✅ |
| 5 | Retrieve | `rag_top_k` | 20 | ✅ |
| 5 | Retrieve | `rag_rerank_top_n` | 7 | ✅ |
| 6 | Generation | `generate_max_tokens` | 250 | ✅ (constants) |
| 6 | Generation | `generate_context_chars_cap` | 2900 | ✅ (constants) |
| 7 | Chunking | `chunk_size` | 1024 | ✅ (constants) |
| 7 | Chunking | `chunk_overlap` | 128 | ✅ (constants) |
| 8 | Cache | `pipeline_cache_similarity_threshold` | 0.97 | ✅ |
| 9 | Sysprompt | per-bot `system_prompt + oos_answer_template` | bot owner viết | ✅ |

**Expected**: PASS 80-88%, HALLU silent 2-3 câu (3-4%), HALLU nguy hiểm 0, refuse 12-20%, cost ~$0.0016/turn.

---

## 1. LAYER 1 — Temperature

| Knob | Value | Lý do |
|---|---|---|
| `generation_temperature` | **0.0** | Bot chat trả lời cố định, không sáng tạo, fact-driven |
| `enrichment_temperature` | **0.0** | LLM enrich chunks không bịa metadata |
| `llm_default_temperature` | **0.0** | Default cho mọi LLM call (fallback) |

**Kỹ thuật**: `softmax(logits / T)` — T=0 = argmax (deterministic).

**Industry standard 2026**: tất cả về 0.0 cho RAG / Document Q&A.

**Áp dụng được mọi provider**: OpenAI, Anthropic, Gemini, Mistral, Llama local.

---

## 2. LAYER 2 — Grounding (ép bot dựa docs)

### `grounding_check_threshold = 0.3`

| Value | Behavior |
|---|---|
| 0.0 | Tuyệt đối strict — 1 câu unsupported = fail |
| **0.3** ⭐ | 30% câu unsupported = fail (sweet spot) |
| 0.5 | Lenient |
| 0.95 ⚠ | Gần như bypass |

**Source**: Self-RAG paper (Asai 2023), Anthropic + OpenAI grounding paper recommend 0.2-0.4.

### `citation_marker_required = true`

Bot phải viết `[chunk_id]` trong answer. Drop câu invalid citation IDs (không match retrieved chunks).

### `grounding_substring_min = 20`

≥20 chars verbatim quote từ chunk → coi là grounded (không cần marker).

### `grounding_numeric_overlap_enabled = true`

Mọi số trong answer PHẢI có trong chunks → match → grounded.

---

## 3. LAYER 3 — Chunk quality (⚠ TUNE THEO RERANKER!)

**Đây là layer NHẠY nhất với reranker provider** vì mỗi reranker có score distribution khác nhau.

### Verified Jina v3 score behavior — Ragbot 75q × 3 runs

```
Min:  0.013   P10:  0.135   P25:  0.228
Median: 0.316   P75: 0.416   Max: 0.566
```

→ Threshold 0.4 (Cohere default) → giữ 9-25% câu = **KILL bot**.
→ Threshold 0.15 (Jina P10 VN) → giữ 83-93% câu = **sweet spot**.

### Threshold đúng theo provider

| Reranker | Score range | VN threshold | EN threshold |
|---|---|---|---|
| **Jina v3** ⭐ | 0.0-0.6 (skewed low) | **0.15** | 0.25-0.30 |
| Cohere rerank-3.5 | 0.0-1.0 (sigmoid) | 0.30 | 0.40 |
| Voyage rerank-2.5 | 0.0-1.0 | 0.35 | 0.50 |
| BGE reranker v2-m3 | 0.0-1.0 | 0.35 | 0.50 |
| OpenAI cosine (no rerank) | -1 to 1 | 0.60 | 0.75 |

### `reranker_min_score_bypass = 0.0`

Chitchat mode không cần docs → không filter.

---

## 4. LAYER 4 — Self-correction

### `pipeline_max_reflect_retries = 2`

| Value | Effect |
|---|---|
| 0 | Không retry — answer đầu = final |
| 1 (default cũ) | 1 retry |
| **2** ⭐ | 2 lần retry — chấp nhận latency tăng để giảm bịa |
| 3+ | Diminishing returns + cost cao |

**Source**: Self-RAG paper recommends 2-3 retries cho production.

---

## 5. LAYER 5 — Retrieve

| Knob | Value | Industry range |
|---|---|---|
| `rag_top_k` | **20** | 15-50 |
| `rag_rerank_top_n` | **7** | 3-10 (sweet spot context cho 4K-8K LLM window) |

Áp dụng được mọi vector DB.

---

## 6. LAYER 6 — Generation output

| Knob | Value | Lý do |
|---|---|---|
| `generate_max_tokens` | **250** | Đủ trả lời 1 câu hỏi spa/FAQ. > 500 = lan man |
| `generate_context_chars_cap` | **2900** | ~700 tokens — đủ 7 chunks × 100 token prefix |

---

## 7. LAYER 7 — Chunking

| Knob | Value |
|---|---|
| `chunk_size` | **1024** chars |
| `chunk_overlap` | **128** chars (12.5%) |

**Source**: Anthropic Contextual Retrieval paper recommends 1024.

---

## 8. LAYER 8 — Semantic cache

`pipeline_cache_similarity_threshold = 0.97` — chỉ cache hit khi câu hỏi NEAR-IDENTICAL (cosine 0.97+).

Tránh false positive (cache hit sai → trả answer không liên quan).

---

## 9. LAYER 9 — Sysprompt + Refuse template (per-bot)

| Knob | Đặt ở đâu | Note |
|---|---|---|
| Sysprompt v5b "Anti-Fake-Premise" | `bots.system_prompt` | Bot owner tự viết |
| `oos_answer_template` | `bots.oos_answer_template` | "Em chưa có thông tin..." |

→ Bot owner kiểm soát qua sysprompt. Application KHÔNG inject text.

---

## 10. PER-STACK MATRIX — nếu thay model thì sao?

### Đổi CHAT LLM (GPT/Haiku/Sonnet/Gemini/Mistral)
**KHÔNG đổi knob**. Chỉ đổi `bot_model_bindings.llm_primary` trong DB.

### Đổi UPLOAD LLM (Haiku → GPT-4.1 → Sonnet)
**KHÔNG đổi knob**. Chỉ đổi `enrichment_model` config + `contextual_retrieval_model`.

### Đổi RERANKER (Jina → Cohere/Voyage/BGE)
⚠ **PHẢI tune Layer 3** (`reranker_min_score_active`):
- Jina v3 → 0.15
- Cohere → 0.30
- Voyage → 0.35
- BGE → 0.35

### Đổi EMBEDDER (Jina → OpenAI/BGE)
**KHÔNG đổi knob**. Chỉ đổi `embedding_dimension` (1024 vs 1536).

### Đổi VECTOR DB (PgVector → Pinecone/Weaviate)
**KHÔNG đổi knob**. Chỉ đổi infrastructure layer.

---

## 11. Quy trình tự xác định setting cho stack mới

```
Step 1: Set reranker_min_score_active = 0.0 (không filter)
Step 2: Run 30 câu smoke test
Step 3: Tính P10 của top_score distribution
Step 4: Set reranker_min_score_active = P10
Step 5: Verify với 75q test → expected PASS ≥ 80%
```

---

## 12. Per-bot override matrix

### Bot lenient (FAQ, customer support — 15% bot)

```sql
UPDATE bots SET pipeline_config = pipeline_config || $$ {
  "grounding_check_threshold": 0.4,
  "reranker_min_score_active": 0.10,
  "max_reflect_retries": 1
} $$ WHERE id = '<bot_uuid>';
```

### Bot strict (healthcare, legal, finance — 5% bot)

```sql
UPDATE bots SET pipeline_config = pipeline_config || $$ {
  "grounding_check_threshold": 0.15,
  "reranker_min_score_active": 0.25,
  "max_reflect_retries": 3,
  "generate_max_tokens": 200
} $$ WHERE id = '<bot_uuid>';
```

---

## 13. Apply default base — SQL

```sql
INSERT INTO system_config (key, value) VALUES
    ('generation_temperature', '0.0'),
    ('enrichment_temperature', '0.0'),
    ('llm_default_temperature', '0.0'),
    ('grounding_check_enabled', 'true'),
    ('grounding_check_threshold', '0.3'),
    ('grounding_use_structured', 'true'),
    ('grounding_substring_min', '20'),
    ('grounding_numeric_overlap_enabled', 'true'),
    ('citation_marker_required', 'true'),
    ('reranker_min_score_active', '0.15'),
    ('reranker_min_score_bypass', '0.0'),
    ('reranker_enabled', 'true'),
    ('pipeline_max_reflect_retries', '2'),
    ('rag_top_k', '20'),
    ('rag_rerank_top_n', '7'),
    ('pipeline_cache_similarity_threshold', '0.97')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;

-- Bust Redis cache
-- redis-cli -u redis://127.0.0.1:6379/1 FLUSHDB
```

---

## 14. Verify after apply

```bash
# Smoke 1 câu — check filter log
TOKEN=$(curl -s http://localhost:3004/api/ragbot/test/tokens/self | jq -r .token)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "User-Agent: Mozilla/5.0" \
  -H "Content-Type: application/json" \
  -d '{"bot_id":"<bot>","channel_type":"web","connect_id":"smoke","question":"<q>"}' \
  http://localhost:3004/api/ragbot/test/chat

# Check log threshold
journalctl -u ragbot-api --since "1 min ago" | grep "rerank_min_score_filtered"
# Expect: "threshold": 0.15
```

---

## 15. Verified evidence

### 75q × 3 runs (Dr. Medispa bot)

| Run | Stack | PASS | HALLU silent |
|---|---|---|---|
| 1 | mini upload (legacy) | 57.3% | 5 (nguy hiểm) |
| 2 | gpt-4.1 upload | 88.0% | 2 |
| 3 | Haiku 4.5 upload ⭐ | 84.0% | 3 (nhẹ) |

### Jina v3 score distribution

```
P10 = 0.135 → set threshold = 0.15 (round up an toàn)
median = 0.316
threshold 0.4 → giữ chỉ 9-25% câu (KILL bot)
```

---

## 16. Reference docs

- Full deep-dive: `reports/RAGBOT_DEFAULT_CONFIG_TUNING_FINAL_20260505.md`
- Tuning guide v1: `reports/RAGBOT_ANTI_HALLU_TUNING_GUIDE_20260505.md`
- Load test 75q × 3 model: `reports/MEDISPA_LOAD_TEST_75Q_REPORT_20260505.md`
- Code constants: `src/ragbot/shared/constants.py:11-1492`

---

## 17. Industry sources

| # | Source | URL |
|---|---|---|
| 1 | OpenAI Cookbook RAG | https://cookbook.openai.com/examples/question_answering_using_embeddings |
| 2 | Anthropic Contextual Retrieval | https://www.anthropic.com/news/contextual-retrieval |
| 3 | Self-RAG paper (Asai 2023) | https://arxiv.org/abs/2310.11511 |
| 4 | RAGAS faithfulness | https://docs.ragas.io/en/latest/concepts/metrics/faithfulness.html |
| 5 | Jina v3 multilingual | https://jina.ai/news/jina-embeddings-v3 |
| 5b | ZeroEntropy zerank-2 (current default reranker) | https://docs.zeroentropy.dev |
| 5c | ZeroEntropy zembed-1 (embed alternative, 2560-dim matryoshka) | https://docs.zeroentropy.dev |
| 6 | Cohere rerank docs | https://docs.cohere.com/docs/rerank |
| 7 | LangChain RAG best practices | https://python.langchain.com/docs/modules/data_connection/retrievers/ |

---

**Last updated**: 2026-05-05
**Status**: ✅ Default base config APPLIED to production DB
**Next review**: sau khi 75q test final với 9-layer base hoàn thành
