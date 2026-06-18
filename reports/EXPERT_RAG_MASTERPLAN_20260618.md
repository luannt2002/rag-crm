# EXPERT RAG — MASTER PLAN (5 tiêu chí) + Toàn bộ luồng

> **Mục tiêu:** Nhanh (latency↓) · Đúng (Faithfulness≈100% + Coverage≥0.95) · UX cao · Performance cao · Cost thấp.
> **Loại:** READ + REPORT + đo thật (Phase 1-3, chưa sửa code production). Mọi claim kèm evidence `file:line` / trace / SQL / load-test.
> **Nguồn:** 5 flow-audit agent (opus) + 3 research agent (web, cited) + 3 deep-debug đo trên hệ thống thật. Bổ trợ: [QUERY_UNDERSTANDING_DEEPDIVE_20260618.md](QUERY_UNDERSTANDING_DEEPDIVE_20260618.md).
> **Ngày:** 2026-06-18.

---

## 0. SCORECARD 5 tiêu chí (đo + verdict)

| Tiêu chí | Hiện trạng (evidence) | Điểm | Lever chính |
|---|---|---|---|
| **Đúng/Đủ** | Faithfulness OK (HALLU=0 giữ qua trap). **Coverage YẾU** — list mơ hồ thiếu (matching, không phải cap/topK) | 6/10 | Self-query + intent router + full-menu route |
| **Cost** | prompt ~3.2-3.6k tok; **caching chạy 96% prefix** (warm ≈52% full). Thủ phạm = sysprompt 2400 tok cố định | 7/10 | Nén sysprompt + giữ cache warm |
| **Nhanh** | generate ~2.6s; pipeline nặng (decompose/MQ/rerank/grade) thêm latency corpus nhỏ không bù | 6/10 | Ablate node thừa + adaptive-k |
| **UX** | refuse duyên dáng OK; list thiếu hại UX; multi-turn booking SSE chết slot | 6/10 | Fix list + fix SSE action |
| **Performance** | rerank sống; RLS **CHẾT** ở deploy (superuser); SSE action chết | 5/10 | RLS ops fix + SSE conversation_id |

**Tổng: ~6/10 — khung expert, "dây chưa nối hết".** Đúng tinh thần EVOLVE-not-REWRITE.

---

## 1. DEEP-DEBUG — 3 phát hiện đo thật (đính chính giả định team)

### DD1 — "topK=5" là HIỂU LẦM
`DEFAULT_TOP_K=20`; per-intent aggregation=40/factoid=15. Chỉ greeting/chitchat=5. **topK KHÔNG phải nghẽn list.** (`_00_app_env_taxonomy.py:28`, `_16:84`)

### DD2 — List thiếu = MATCHING, KHÔNG phải cap/topK
Đo: keyword "chăm sóc da"→SQL 7 dòng→chunk 244 chars (cap-agg 5500)→**FIT**. massage/tẩy-da-chết/gội-đầu đều fit. Stats route trả TẤT CẢ row khớp (limit 1000), **không bị topK/cap giới hạn**. Thiếu vì:
- Câu mơ hồ ("có những dịch vụ gì") → keyword extraction sai → match nhầm subset (gội đầu 3 cái).
- "Liệt kê TOÀN BỘ menu" (127) → không keyword nào match hết → rớt vector (score 0.22, 18 cái).
→ Gốc = **W1 substring match + W2 keyword extraction + thiếu "full-menu" route**.

### DD3 — Prompt caching ĐANG CHẠY (không vỡ)
Đo 3 call lặp: xe cached 0→3328/3454 (**96% prefix**); spa cached 3072-3584. Warm cost ≈52% full. `{captured_slots}` chỉ vỡ cache GIỮA booking (slot đổi/turn). → Cost lever = **nén sysprompt 2400→~1000 tok** (cold-call + 4% chưa cache), KHÔNG phải "fix caching".

---

## 2. TOÀN BỘ LUỒNG — weakness tổng hợp (5 audit, ranked)

### 🔴 CRITICAL
| # | Luồng | Phát hiện | Evidence |
|---|---|---|---|
| C1 | **Multi-tenancy/RLS** | **RLS chết ở deployment** — `.env` chạy superuser DSN + `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` → 23 policy RLS = no-op. Cross-tenant chỉ chặn bởi WHERE `record_bot_id` app-level. 1 chỗ thiếu filter = leak. | `.env`; `engine.py:67-81`; alembic 0069/0073 |
| C2 | **Action/booking** | SSE path `conversation_id=None` hardcode → **slot booking KHÔNG persist** qua turn trên streaming production (silent). Worker path OK. | `chat_stream.py:291` |

### 🟠 HIGH
| # | Luồng | Phát hiện |
|---|---|---|
| H1 | Retrieval | Keyword match **substring ILIKE thô** — over-match ("da"→"đầu/dầu" qua unaccent), no semantic group. `query_by_name_keyword` (`stats_index_repository.py:443`) |
| H2 | Retrieval | `custom_vocabulary` synonym map **CHẾT** ở keyword SQL (chỉ hint LLM). Owner không dạy được "da={da chết,chăm sóc da}". `vocabulary_expander.py:489` |
| H3 | Intent | 3 detector intent rời rạc, vocab không chung → phrasing loạn ("có làm X" lọt). `heuristic_intent_classifier.py`, `parse_list_query` |
| H4 | Ingestion | Chunking **fixed rule-based**; AdapChunk 7-tầng = scaffold/telemetry default OFF; `parsed_blocks=[]` hardcode rỗng. `ingest_stages.py:501` |
| H5 | Ingestion | stats-extraction + money + header **hardcode VN/VND**; `entity_category` **gần như luôn NULL** → chưa dùng được cho category-filter. `document_stats.py:58,292`; `number_format.py:49` |
| H6 | Guardrails | Grounding judge **KHÔNG block** (chỉ observability) → HALLU=0 do SYSPROMPT giữ, không phải runtime guard. Structured judge = **dead code**. `local_guardrail.py:541`; `guard_output.py:62` |
| H7 | Multilingual | Fast-path (parser/intent/slot-extract/money) **VN-locked** → EN/zh vỡ. `slot_extractor.py:39`, `query_range_parser.py` |

### 🟡 MED/LOW
- Cache key thiếu `workspace_id`/`channel_type`; `bot_version` không gồm vocab/model/action → đổi config không bust cache (`query_graph.py:841`).
- 2 bảng history không sync (chat_histories vs messages).
- Worker re-fetch GET thẳng Google `edit?gid=` → HTML (latent, chỉ khi raw_content rỗng). `document_worker.py:301`.
- Duplicate SSoT constant `DEFAULT_PRICE_BUCKETS_VND` (2 file).
- Slot-extractor/range-parser hardcode VN trong core = vi phạm domain-neutral.

### ✅ ĐẠT (không cần đụng)
- Sacred-#10: generate KHÔNG inject/override answer (math_lockdown chỉ detect; refusal từ template per-bot). SysPromptAssembler governed, pin tests pass.
- Model resolver fallback chain đúng (per-bot→system_config→fail-loud).
- Rerank sống (jina-reranker-v3 + cliff). Prompt caching chạy.

---

## 3. RESEARCH — SOTA techniques (cited, đủ để build)

### Nguyên lý nền (đổi tư duy)
- **Top-K similarity SAI cho "list-all"** — tối ưu precision per-passage, cắt K, không đảm bảo completeness. Set-selection (SetR arxiv 2507.06838): coverage 19→36%, dùng ÍT passage hơn. → **"list all" = SQL/set retrieval, không phải top-K.** (Red Hat "Boring RAG": "similarity is not correctness".)
- **Nghịch lý n8n rẻ+chuẩn = ĐÚNG cho corpus nhỏ.** Advanced/agentic pipeline +3-10× token, +2-10s latency, accuracy gain *phụ thuộc corpus* (ARAGOG: reranker no advantage trên 1 số corpus). MiniRAG/Naive-RAG khuyến nghị cho data nhỏ + low latency. → **ABLATE node thừa.**
- **Sysprompt 2400 tok = bloat** (nên 5-10% context). Lost-in-the-middle (arxiv 2307.03172): >30% drop khi info ở giữa. Bloat hại CẢ accuracy. → **nén 2400→~1000 tok.**
- **CAG (Cache-Augmented Generation)** cho catalog nhỏ: nhồi nguyên catalog làm cached-prefix → fix completeness (không drop) + cost (cache) + latency (bỏ retrieve). Match/vượt RAG corpus nhỏ. (arxiv "Don't Do RAG".)

### Techniques + implementation (agent #2, có thuật toán)
| Kỹ thuật | Giải | Thuật toán/integration | Pitfall |
|---|---|---|---|
| **Self-Query** | list-all đủ + semantic group | LLM→StructuredQuery(filter IR: EQ/GT/IN/AND...) → compile SQL `WHERE category=X` tenant-scoped. Schema từ DB. | filter hallucination→validate enum; whitelist column; degrade semantic nếu 0 row |
| **Adaptive-RAG routing** | phrasing loạn | classifier intent {list/factoid/existence} → route. Heuristic trước, LLM temp-0 fallback. EXISTENCE phải retrieve (không A). | misroute list→factoid; normalize+pin few-shot |
| **Adaptive-k** | topK động + completeness | sort score desc → cut tại argmax(gap), buffer=5, boundary 90%, clip k_min/k_max. Trên điểm RERANK (sắc hơn cosine). | flat distribution→dùng rerank score; LIST bypass→full SQL set |
| **RAG-Fusion (RRF)** | fuse BM25+vector, đa cách hỏi | `RRF=Σ 1/(k+rank)`, k=60, original-query weight 2×. Parallel gather. | gate cho list/ambiguous, skip factoid; paraphrase xấu→noise |
| **CRAG 3-way** | grade an toàn | CORRECT/AMBIGUOUS/INCORRECT, threshold calibrate trên data MÌNH. **Grader KHÔNG bao giờ rỗng→floor top-3**; **LIST bypass drop**. | over-filter (lịch sử chunks=0); threshold paper KHÔNG transfer |
| **VN word-seg** | match cụm + de-substring | underthesea/pyvi **CHỈ leg BM25** (jina-v3 dense = raw text!). Segment query+corpus đối xứng. | segment 1 phía → recall sập |
| **Prompt caching** | cost↓ | sysprompt prefix byte-identical; động (slots/date/chunks) ra SAU; `prompt_cache_key=record_bot_id` | dynamic chen trước prefix→0% hit; cold bot→0 benefit |
| **Babel/CLDR** | de-VN money parse | `parse_decimal(s,locale)` thay regex VND | không parse chữ ("một triệu")→giữ number_words map |

---

## 4. BUILD PLAN — ưu tiên impact/risk (ship từng cái, mỗi bước load-test gate)

> **Gate mọi bước:** Coverage ≥0.95 + HALLU=0 (load-test) TRƯỚC merge. Constant → `system_config`/`pipeline_config` (zero-hardcode). Per-bot flag, default OFF cho thứ rủi ro.

### 🚨 P-CRIT (ops/bảo mật — làm ngay, không cần code lớn)
- **C1 RLS**: ops set `DATABASE_URL_APP` = role `ragbot_app` NOBYPASSRLS + gỡ `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1`. Verify policy active. *(Không phải em code — cần ops + verify.)*
- **C2 SSE action**: fix `conversation_id=None` (`chat_stream.py:291`) → resolve UUID như worker path. Booking trên streaming mới persist.

### P0 — Cost + UX, Pareto (rủi ro thấp, lợi nhiều trục)
1. **Nén sysprompt 2400→~1000-1200 tok** (distill rule, externalize rule hiếm theo intent, gỡ few-shot verbatim — cũng fix bẫy leak). Alembic-tracked. *Đo: token↓, false-refuse, HALLU=0, Coverage.* → Cost↓ + Accuracy↑ + Nhanh↑.
2. **Giữ cache warm**: `prompt_cache_key=record_bot_id`; đảm bảo không có gì động trước sysprompt prefix (chú ý `{captured_slots}` mid-booking). *Đo: cached_tokens hit-rate.*

### P1 — Đúng/Đủ (fix list-complete — cái team kẹt)
3. **Intent router** (Adaptive-RAG heuristic): {list/factoid/existence} thống nhất, gộp 3 detector. *Đo: routing accuracy, paraphrase-cluster route giống nhau.*
4. **Self-query SQL cho LIST** + **"full-menu" route** (câu "toàn bộ" → trả all rows bot, không keyword). Thay substring bằng category-filter. *Đo: 10-item query → ra đủ 10; Coverage→1.0.*
5. **Wire `custom_vocabulary`** vào keyword/category match (owner dạy synonym). *Đo: "da"→đủ da-family, không gội đầu.*

### P2 — Nhanh + Cost (ablate + adaptive)
6. **Ablate decompose/multi-query/CRAG** per-bot, default OFF. *Đo: Coverage+Faith+p95+token ON vs OFF — giữ chỉ khi lift Coverage.*
7. **Adaptive-k** (cut theo gap điểm rerank) thay topK tĩnh; LIST bypass→full set. *Đo: Coverage giữ + token↓.*
8. **RRF fuse BM25+vector** đúng chuẩn (k=60, original 2×), gate theo route.

### P3 — Đa ngữ + chunking (nền dài hạn)
9. **VN word-seg** (underthesea) cho leg BM25 (query+corpus đối xứng); dense giữ raw.
10. **De-VN fast-path**: money→Babel, stopword/intent/slot-prompt→`language_pack[locale]` (alembic), language detection. Gỡ `if locale=="vi"`.
11. **entity_category** map từ cột header "danh mục/category" → cột sạch (cho self-query P1). Chunking adaptive (wire AdapChunk hoặc bỏ scaffold chết).

### P4 — Kiến trúc (validate trước commit)
12. **Pilot CAG-lite**: catalog nhỏ (vài trăm dòng) → nhồi cached-prefix thay retrieve cho intent list/catalog. *Đo: Coverage→100%, p95, token.* Chỉ corpus < context ceiling + ít đổi.

---

## 5. RỦI RO & nguyên tắc (rule#0)
- **GIẢ THUYẾT chưa verify trên corpus mình** (phải load-test trước claim): ablation không mất accuracy; CAG-lite win; threshold CRAG/Self-RAG paper (KHÔNG transfer); cache ROI (phụ thuộc traffic warmth per-bot); mọi % lift từ paper.
- **HALLU=0 sacred** — Self-query/adaptive-k/CRAG KHÔNG được làm bịa; trap phải vẫn refuse.
- **KHÔNG wrong-layer** — list coverage fix ở RETRIEVAL (P1), KHÔNG thêm rule sysprompt (bài học spa-07).
- **EVOLVE** — giữ 2-path + 4-key + sacred; thêm cột/route backward-compat null; KHÔNG đập cái đã chuẩn.
- **Đừng thêm phức tạp mù** — corpus nhỏ: pipeline nặng = net negative. Lead bằng Pareto set (cache, prompt diet, SQL-for-list, ít chunk). Decompose/MQ/CRAG = opt-in đo trước.

---

## 6. Evidence index
- Flow audits: query/retrieval (QUERY_UNDERSTANDING_DEEPDIVE), ingestion, generation, guardrails, cross-cutting (RLS/cache/state/obs) — full file:line trong từng audit.
- Research cited: SetR 2507.06838 · Adaptive-RAG 2403.14403 · Adaptive-k 2506.08479 · CRAG 2401.15884 · Self-RAG 2310.11511 · RAG-Fusion(RRF Cormack 2009) · CAG "Don't Do RAG" · Lost-in-Middle 2307.03172 · jina-v3 2409.10173 · BGE-M3 · LLMLingua-2 · OpenAI/Anthropic prompt-caching docs · Babel/CLDR.
- Deep-debug đo: DD1 topK, DD2 list=matching, DD3 caching 96% — trace + SQL trong report này §1.
