# EXTERNAL MODEL-API FLOWS — toàn bộ luồng dự án gọi model ngoài

> Cập nhật 2026-06-27. Evidence: `bot_model_bindings.purpose` (DB) + `system_config` + grep call-site.
> Mục đích: biết CÁI NÀO gọi API external, LÀ GÌ, KHI NÀO, ACTIVE hay OFF, hỏng thì sao.

## 0. TÓM TẮT — 3 provider external
| Loại | Provider sống | Key (.env) | Endpoint |
|---|---|---|---|
| **Embedding** | ZeroEntropy `zembed-1` @1280 | `ZEROENTROPY_API_KEY` | `api.zeroentropy.dev/v1/models/embed` |
| **Rerank** | ZeroEntropy `zerank-2` | `ZEROENTROPY_API_KEY` (chung) | `api.zeroentropy.dev` |
| **LLM** (mọi purpose) | innocom `qwen3` (OpenAI-compat) | `INNOCOM_API_KEY` (+`OPENAI_API_BASE` redirect) | `ai.innocom.co/v1/chat/completions` |

OpenAI + Jina = **DEAD** (bỏ). Đổi provider: chỉ qua alembic (`system_config` provider/model + `bot_model_bindings`) — KHÔNG psql (sacred#7).

---

## 1. EMBEDDING — ZeroEntropy zembed-1 (5 call-site)
| Call-site | Khi nào | Trên hot-path? | Status |
|---|---|---|---|
| **Query embed** | mỗi câu chat (embed câu hỏi → vector search) | ✅ per-turn | ACTIVE |
| **Semantic-cache embed** | mỗi câu (embed query → tra cache cosine) — **call RIÊNG** | ✅ per-turn | cache_enabled (default) |
| **Multi-query fanout embed** | mỗi sub-query (N câu) → N× embed | ✅ per-turn | **multi_query=ON** → nhân N |
| **HyDE embed** | embed câu-trả-lời-giả | per-turn nếu bật | hyde=OFF (default) |
| **Ingest embed** | mỗi chunk lúc upload | ingest-time | ACTIVE |
| **Warmup embed** | boot app (1 lần) | boot | ACTIVE |

→ **1 câu chat = ít nhất 2 embed** (query + cache); multi-query ON thì +N.

## 2. RERANK — ZeroEntropy zerank-2
| Call-site | Khi nào | Status |
|---|---|---|
| **Rerank node** | mỗi câu có retrieve (rerank top-K) | **reranker_enabled=ON** |
| Multi-query → rerank mỗi nhánh | multi_query ON | ACTIVE |

## 3. LLM QUERY-PATH — innocom qwen3 (nhiều call/turn)
| Purpose | Vai trò | Status (flag) |
|---|---|---|
| `understand_query`/`intent` | phân loại intent | ACTIVE |
| `decompose`+`multi_query` | tách câu + fanout | **multi_query=ON** |
| `condense`/`condensing` | gộp lịch sử multi-turn | active khi có history |
| `rewrite`/`rewriting` | viết lại query | code-default |
| `generation`/`chat`/`llm_primary` + per-intent (`llm_factoid/chitchat/greeting/oos/multi_hop/comparison/aggregation/feedback/vu_vo`) | **LLM trả lời chính** | ACTIVE |
| `grade`/`grading` | CRAG chấm chunk | crag flag |
| `grounding` | verify grounded (anti-HALLU) | **grounding_check=ON** + fail-closed (S1-B) |
| `reflect`/`reflection` | self-RAG | OFF (default) |
| `routing` | query router | `query_router_provider=null` → OFF |
| `cascade_high/low_model` | cascade routing | cascade=OFF (default) |
| `slot_extractor` | trích slot đặt-lịch/action | active khi action bot |
| `guard` | guardrail LLM | `guardrail_provider=local` → KHÔNG gọi external |

→ **1 câu chat = nhiều LLM call** (intent + condense + generate + grounding + [decompose×N nếu multi-query]). Đây là lý do innocom **503 dưới tải sustained**.

## 4. LLM INGEST-PATH — innocom
| Purpose | Vai trò | Status |
|---|---|---|
| `enrichment`/`contextual_retrieval_model` | context per-chunk (LLM mỗi chunk!) | **OFF** (`contextual_retrieval_enabled=false`, `enrichment_enabled=false`) — đã tắt vì là thủ phạm kẹt xe-3 (213 call) |
| `metadata_extraction_model` | trích metadata/điều-khoản | **metadata_extraction=ON** |
| `graph_rag_entity_extraction` | entity graph | OFF |
| **`vlm_caption`** (vision LLM) | caption ảnh upload | `vlm_provider=null` → OFF (FMT-3 fixed: prompt từ config) |

## 5. LLM OFF-PATH (eval/admin)
| Purpose | Khi nào |
|---|---|
| `deepeval_judge_model` | chạy eval LLM-judge (thủ công) |
| `admin_refuse_suggestions` | admin gợi ý FAQ |
| `golden_dataset_model` | sinh golden (thủ công) |
| `/health/models` probe | health-check gọi thử mỗi provider |
| **eval-agent-judge** (mới) | Stage B chấm điểm — dùng Claude agent, KHÔNG dùng bot-provider |

---

## 6. Dễ QUÊN (nhân-bội call)
- **Semantic-cache embed** = embed thứ 2 mỗi turn (ngoài query-embed).
- **Multi-query** (ON) = N sub-query × (embed + retrieve + rerank) → nhân tải lớn nhất.
- **HyDE** = LLM-gen + embed (2 call) — OFF.
- **Grounding async judge** = LLM background sau khi trả lời.
- **Warmup** = embed + LLM probe lúc boot.

## 7. Hỏng thì sao (failure mode)
| Provider down | Hệ quả |
|---|---|
| **ZE embed** (CB open) | query-embed fail → vector retrieval chết; còn BM25 lexical. CB open từng xảy ra do load-test hammer. |
| **ZE rerank** | rerank skip → dùng RRF/cliff; degrade nhẹ |
| **innocom LLM** | generate fail → **empty answer**; 503 dưới concurrency (sustained). Retry InternalServerError (S0-B). |

## 8. Ước lượng call/turn (factoid, multi-query ON)
```
1 câu chat ≈
  embed:  query(1) + cache(1) + multi-query sub(N×1)          = 2+N
  rerank: main(1) + sub(N)                                     = 1+N
  LLM:    intent(1) + condense(0-1) + decompose(1) + generate(1) + grounding(1)  ≈ 4-5
→ ~10-15 external call / 1 câu khi multi-query ON
```
→ Tải external **nhân lên theo multi-query**; innocom (~1 req/s heavy) là nút thắt.

## 9. Cách đổi/kiểm provider (sacred#7)
- `system_config`: `embedding_provider/model`, `reranker_provider/model`, `llm_default_model`, `*_model` keys.
- `bot_model_bindings`: per-bot per-purpose override.
- Resolver 3-tier: binding > system_config + `_lookup_platform_default` > NullObject.
- Mọi thay đổi qua **alembic** (tracked), KHÔNG psql.
- Kiểm: `/health/models`, `python scripts/cost_audit.py`, grep `system_config` model keys.
