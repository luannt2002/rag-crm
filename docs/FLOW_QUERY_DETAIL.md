# Luồng TRẢ LỜI câu hỏi — chi tiết từng step

> Verified từ `src/ragbot/orchestration/query_graph.py::build_graph()` (2026-06-15).
> Model: answer/grade/grounding/transforms = `gpt-4.1-mini` · embed/rerank = ZeroEntropy (zembed-1/zerank-2).
> Adaptive Graph: intent quyết định đường đi — factoid đi tắt, aggregation/comparison fan-out đầy đủ.

---

## STEP 0 — Nhận request + resolve bot (HTTP layer)

| | |
|---|---|
| **Input** | `POST /api/ragbot/test/chat` body `{bot_id, channel_type, question, connect_id, workspace_id?, debug?, bypass_cache?}` + JWT bearer (record_tenant_id) |
| **Việc** | (1) lift `record_tenant_id` từ JWT · (2) resolve bot: có `workspace_id` → `find_by_4key`; không có → `find_by_3key_unique` (unique match tenant+bot+channel) · (3) load `pipeline_config` per-bot (plan_limits/threshold_overrides/action_config/setting_options) |
| **Output** | `bot_cfg` (record_bot_id, system_prompt, workspace_slug) + `pipeline_config` |
| **Lỗi** | bot không thấy → 404 · tenant thiếu → 422 |
| **Code** | `interfaces/http/routes/test_chat.py::test_chat()` |

→ Sau khi có `bot_cfg`, request vào **LangGraph state machine** (các node dưới).

---

## STEP 1 — guard_input (guardrail đầu vào)
| | |
|---|---|
| **Việc** | Quét câu hỏi: prompt-injection, PII, nội dung chặn theo guardrail config. KHÔNG gọi LLM (rule/pattern). |
| **Output** | pass → tiếp · block → trả refusal sớm |
| **Code** | node `guard_input` |

## STEP 2 — cache_check + understand_query (chạy song song) 🤖
| | |
|---|---|
| **cache_check** | L1 Redis exact-hash (key versioned theo system_prompt + oos_template + corpus_version) · L2 pgvector semantic @0.97. **HIT → nhảy thẳng STEP 17 persist** (0 LLM, trả lời ~ms). |
| **understand_query** 🤖 | 1 LLM call: phân loại **intent** (factoid / aggregation / comparison / multi_hop / greeting / chitchat / out_of_scope) + chuẩn hoá query. **Intent này điều khiển toàn bộ nhánh sau.** |
| **Output** | `intent`, `rewritten_query`, cache verdict |
| **Code** | `cache_check_and_understand_parallel` |

## STEP 3 — query_complexity (heuristic, $0)
| | |
|---|---|
| **Việc** | `_classify_query_complexity()` — rule-based (đếm token, pattern), KHÔNG LLM. Ghi `complexity_label` + `complexity_score` để route. |
| **Output** | simple / complex |

## STEP 4 — condense / router (gộp lịch sử) 🤖 (có điều kiện)
| | |
|---|---|
| **Việc** | Nếu có history ≥ ngưỡng: 1 LLM call gộp các lượt cũ thành ngữ cảnh gọn (condense). Router chọn nhánh tiếp theo. |
| **Gate** | chỉ khi có history đủ dài (`condense_history_limit`, default 6) |

## STEP 5 — rewrite + multi_query fanout 🤖 (GATED by intent)
| | |
|---|---|
| **rewrite** 🤖 | viết lại query cho dễ retrieve |
| **multi_query** 🤖 | sinh 2-5 paraphrase/sub-query để tăng recall, mỗi cái retrieve riêng rồi RRF merge |
| **Gate** | **CHỈ chạy cho `aggregation` / `comparison` / `multi_hop`.** `factoid` / `greeting` / `chitchat` → **SKIP hoàn toàn** (`DEFAULT_REWRITE_ENABLED_BY_INTENT` + `DEFAULT_MULTI_QUERY_ENABLED_BY_INTENT`, factoid=False) |
| **Vì sao** | factoid chỉ cần 1 retrieve; fan-out cho factoid = đốt tiền vô ích |

## STEP 5b — decompose / adaptive_decompose 🤖 (multi-hop)
| | |
|---|---|
| **Việc** | Tách câu phức nhiều ý thành sub-question độc lập (vd "Điều X và Y nói gì") |
| **Gate** | intent multi_hop / compound |

## STEP 6 — retrieve (HYBRID — KHÔNG LLM)
| | |
|---|---|
| **Việc** | Truy hồi lai 3 thành phần: (1) **dense** — embed query (ZE zembed-1) rồi cosine top-K trên pgvector HNSW · (2) **BM25** — `tsvector` keyword search · (3) **RRF fuse** — Reciprocal Rank Fusion gộp 2 danh sách. Multi-query: mỗi sub-query retrieve rồi RRF gộp tất cả. |
| **Output** | danh sách chunk ứng viên (top-K) |
| **Refuse short-circuit** | 0 chunk → trả `oos_answer_template`, KHÔNG gọi generate (tiết kiệm) |
| **Code** | node `retrieve` + `hybrid_search` |

## STEP 7 — graph_retrieve (optional, default OFF)
| | |
|---|---|
| **Việc** | Knowledge-graph synthesis (entity/relation). Bật per-bot. |

## STEP 8 — rerank (ZeroEntropy zerank-2) + cliff filter
| | |
|---|---|
| **Việc** | Cross-encoder zerank-2 chấm lại độ liên quan (query, chunk) chính xác hơn embedding. Sau đó **cliff filter**: cắt theo gap (khoảng hụt điểm) + sàn tuyệt đối 0.05. |
| **Output** | chunk đã rerank + lọc (giảm nhiễu) |
| **Lưu ý** | rerank dùng API ZeroEntropy riêng (KHÔNG OpenAI) |

## STEP 9 — mmr_dedup
| | |
|---|---|
| **Việc** | Maximal Marginal Relevance — bỏ chunk trùng nội dung, giữ đa dạng. KHÔNG LLM. |

## STEP 10 — neighbor_expand (optional)
| | |
|---|---|
| **Việc** | Mở rộng chunk lân cận (parent/child, câu kề) để đủ ngữ cảnh. Bật per-bot. |

## STEP 11 — grade (CRAG) 🤖
| | |
|---|---|
| **Việc** | 1 LLM call (structured output): chấm mỗi/nhóm chunk **relevant / không**. Quyết định chunk nào thực sự vào generate. 3-state: correct / incorrect / ambiguous. Có leniency cho compound intent. |
| **Output** | `graded_chunks` (chunk được duyệt) |
| **Skip** | `DEFAULT_CRAG_SKIP_RETRY_ABOVE_SCORE` — top_score rất cao thì bớt retry |
| **Vai trò chống bịa** | lọc chunk rác trước khi LLM thấy → giảm HALLU |

## STEP 12 — rewrite_retry 🤖 (khi grade fail)
| | |
|---|---|
| **Việc** | grade thấy 0 chunk tốt → viết lại query + quay lại STEP 6 retrieve (loop, max retries). |
| **Gate** | chỉ khi grade fail + còn lượt retry |

## STEP 13 — generate (SINH CÂU TRẢ LỜI) 🤖 ⭐
| | |
|---|---|
| **Việc** | 1 LLM call chính: ghép `system_prompt` (bot owner) + platform-default rules (append, ADR-W1-S10) + `<documents>` (graded_chunks) + history + câu hỏi → sinh câu trả lời. Có thể dùng structured output (answer + citations). |
| **Action/booking** | nếu `action_config` bật: load conversation_state + slot_extractor (1 LLM call phụ) + render `{captured_slots}` |
| **Sacred** | application KHÔNG inject text/override answer — owner sysprompt là single source of truth |
| **Output** | `answer` + citations |

## STEP 14 — critique_parse (Self-RAG, optional) 🤖
| | |
|---|---|
| **Việc** | Bot tự phê bình câu trả lời (đủ/đúng?). Bật per-bot, default OFF. |

## STEP 15 — guard_output + grounding judge 🤖 ⭐
| | |
|---|---|
| **guard_output** | guardrail ra: shingle leak (chống lộ verbatim sysprompt) + PII. KHÔNG LLM. |
| **grounding judge** 🤖 | 1 LLM call: đối chiếu từng câu trong answer vs `<documents>` → SUPPORTED / NOT. Phát hiện bịa. **Chạy SYNC (chặn, chờ kết quả) HOẶC ASYNC (background lane riêng, không chặn response)** — KHÔNG bao giờ chạy cả hai. |
| **Vai trò** | tầng cuối bảo vệ HALLU=0 |

## STEP 16 — reflect (optional) 🤖
| | |
|---|---|
| **Việc** | answer rỗng + còn lượt → sinh lại. Bật per-bot. |

## STEP 17 — persist (lưu + kết thúc)
| | |
|---|---|
| **Việc** | Lưu cache (L1+L2), audit_log, chat_histories (history room theo connect_id), request_logs (cost/token), outbox event. KHÔNG LLM. |
| **Output** | response trả về client: `{answer, citations, debug?}` |

---

## TỔNG KẾT — bao nhiêu step tùy intent

### 🟢 FACTOID (giá/ngày/điều khoản — ~70% traffic) — đường TẮT
```
guard_input → understand → complexity → retrieve → rerank → mmr → grade → generate → grounding → persist
```
**~10 node · 4 LLM call** (understand + grade + generate + grounding). Bỏ rewrite + multi_query.
→ ~$0.004/câu · ~3-5s warm.

### 🟠 AGGREGATION / COMPARISON (liệt kê/so sánh — ~30%) — đường ĐẦY ĐỦ
```
guard_input → understand → complexity → rewrite → multi_query(2-5) → retrieve×N → rerank → mmr → grade → generate → grounding → persist
```
**~14 node · 10-15 LLM call**. → ~$0.012/câu.

## 3 tầng chống bịa (HALLU=0)
1. **grade (CRAG)** — lọc chunk rác trước khi LLM thấy
2. **cliff filter** — cắt chunk điểm thấp (sàn 0.05)
3. **grounding judge** — verify answer vs documents sau khi sinh

## Model dùng ở từng step
| Step LLM | Model |
|---|---|
| understand, condense, rewrite, multi_query, decompose, grade, generate, grounding, slot_extract | `gpt-4.1-mini` |
| embed query (retrieve) | ZeroEntropy `zembed-1` |
| rerank | ZeroEntropy `zerank-2` |
| query_complexity | heuristic (KHÔNG LLM) |
