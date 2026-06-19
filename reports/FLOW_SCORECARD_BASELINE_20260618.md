# Flow Scorecard — BASELINE (trước khi code+test) 2026-06-18

> **Phương pháp**: điểm TĨNH theo code-evidence (tồn tại · nối dây đúng · unit-test · review logic). **KHÔNG phải** quality runtime — DB local rỗng, phiên này CHƯA chạy load-test. Điểm "smartness" = trần code-readiness (có cap vì chưa verify số).
> **Mục đích**: điểm-MỐC. Sau khi seed+eval+fix, đo lại để chứng minh cải thiện bằng số.
> **Rubric**: 90-100 verified · 70-89 nối đúng+unit-test chưa runtime · 50-69 gated/thiếu · 30-49 chết-dây/sai-tầng/vứt-data · 0-29 thiếu.

---

## FLOW 1 — RAG INGEST (U0→U7)
| Step | Điểm | Lý do (evidence) |
|---|---:|---|
| U0 IDENTITY_VALIDATE | 85 | pydantic 4-key + WorkspaceIdValidator, test cover |
| U0.5 BOT_RESOLVE_4KEY | 85 | Redis cache `ragbot:bot:{4key}` + DB lookup |
| U1 VALIDATE (size+dedup) | 80 | 500k guard + content_hash + source_url dedup |
| U2 PARSE | 55 | Kreuzberg trả flat-text → **block structure mất** (cần emit block list) |
| U3 CLEAN | 80 | NFC + hyphenation + injection strip (ZWS/control) |
| **U4 CHUNK** | **35** | 🔴 **block-pipeline chết dây** `parsed_blocks=[]` → `smart_chunk_atomic` không chạy → chunk co-occur đa-entity (gốc conflate) |
| U5 CONTEXTUAL ENRICH | 55 | Haiku contextual, gated cờ DEFAULT off |
| U6 VN SEGMENT | 60 | optional, có nhưng chưa đo lift |
| U7 EMBED+STORE | 70 | ✅ chạy; 🔴 **cost embed KHÔNG log** (vứt usage) |
| **TB Ingest** | **67/100** | nền ok, lõi chunk hỏng kéo xuống |

## FLOW 2 — RAG QUERY (graph node thật, verified query_graph.py:3767-3900)
| Node | Điểm | Lý do |
|---|---:|---|
| guard_input | 75 | input guardrail, có |
| understand_query | 70 | intent/entity, regex-pile (chưa LLM extractor) |
| condense_question | 70 | history condense |
| router | 55 | nhiều nhánh **DEFAULT=False** (cascade/intent gate tắt) |
| rewrite_and_mq_parallel | 50 | gated off |
| decompose | 55 | gated |
| query_complexity | 45 | gated off (built, không chạy) |
| adaptive_decompose | 45 | gated off |
| **retrieve** | **45** | 🔴 **BUG-1 conflate** — price-of-entity rơi vector (`query_range_parser.py:374-377`) |
| graph_retrieve | 55 | có, conditional; GraphRAG cost cao |
| rerank | 70 | ✅ chạy; 🔴 cost rerank KHÔNG log |
| mmr_dedup | 75 | có, ổn |
| neighbor_expand | 70 | context expand |
| grade (CRAG) | 55 | retry có nhưng **grounding chỉ warn**, không enforce |
| rewrite_retry | 60 | loop về retrieve |
| generate | 80 | ✅ solid; **token LLM log đầy đủ** |
| critique_parse (self-RAG) | 55 | gated cờ |
| guard_output | 70 | output guardrail |
| reflect | 50 | gated off |
| persist | 85 | ✅ điểm hội tụ mọi path → ghi log |
| **TB Query** | **62/100** | trí tuệ built nhưng KHÓA + routing bug |

## FLOW 3 — THỐNG KÊ SỬ DỤNG TOKEN
| Tầng | Điểm | Lý do |
|---|---:|---|
| Capture LLM | 90 | `dynamic_litellm_router.py:715` extract+emit đầy đủ |
| **Capture rerank/embed** | **20** | 🔴 VỨT `usage` (`jina_reranker.py:275`, `jina_embedder.py:289`) → cost vô hình |
| Store `token_ledger` | 90 | schema xuất sắc: action·provider·model·in/out/cached·cost·time·4-key·request_id |
| Store mirror (model_invocations/monitoring_log/bot_token_usage_log) | 85 | 3 bảng forensic+rollup |
| **Aggregate (query)** | **25** | 🔴 0 query đọc token_ledger (index date_trunc sẵn nhưng không dùng) |
| **Dashboard API** | **20** | 🔴 chưa có endpoint timeseries per-scope |
| Per-request cumulative | 35 | `state.tokens` last-writer-wins (chỉ call cuối) |
| **TB Token-stats** | **52/100** | 80% bảng, ~0% surface |

## FLOW 4 — TRACE LOG
| Thành phần | Điểm | Lý do |
|---|---:|---|
| trace_id/tenant propagation | 90 | middleware + ContextVar bind |
| request_logs (per-request) | 85 | create→finalize lifecycle |
| request_steps (per-node) | 85 | timing+token/step |
| monitoring_log (durable mirror) | 85 | FK-free survive delete |
| audit_log (hash chain) | 90 | tamper-evident + `GET /audit/verify` |
| **TB Trace-log** | **87/100** | **mạnh nhất** |

## FLOW 5 — RAG-CRM / MULTI-TENANT
| Thành phần | Điểm | Lý do |
|---|---:|---|
| tenants/workspaces/bots 4-key | 90 | first-class, unique constraint |
| RLS definition | 85 | GUC policy 0069/0141/0187 đủ bảng |
| **RLS runtime enforce** | **50** | 🟡 đang bypass superuser DSN (`RAGBOT_ALLOW_SUPERUSER_RUNTIME`) |
| analytics endpoints | 60 | có nhưng query `request_logs`, chưa đọc ledger |
| quota/budget infra | 80 | quotas/token_budgets/tokens_used |
| **TB CRM** | **73/100** | khung chuẩn, runtime+dashboard yếu |

## FLOW 0 — NỀN TẢNG (chặn verify)
| Hạng mục | Điểm | Lý do |
|---|---:|---|
| Migration chain (fresh DB) | 30 | 🔴 `alembic upgrade head` fail rev 0006 (phải dùng runbook bypass) |
| DB local seeded | 10 | 🔴 rỗng — 0 bot/corpus → chặn MỌI verify |
| eval-CI harness | 15 | 🔴 thiếu → "đụng test lòi bug" |
| Code quality (ruff/mypy strict/6158 test/~0 broad-except) | 75 | ✅ nền code tốt, vài god-file |
| **TB Nền** | **32/100** | điểm thấp nhất — phải sửa trước |

---

## 📊 TỔNG HỢP BASELINE

| Flow | Điểm TB | Trạng thái |
|---|---:|---|
| Trace log | **87** | ✅ mạnh nhất |
| RAG-CRM | **73** | ✅ khung chuẩn |
| RAG Ingest | **67** | 🟡 lõi chunk hỏng |
| RAG Query | **62** | 🟡 trí tuệ khóa + bug |
| Token-stats | **52** | 🟡 bảng có, API thiếu |
| Nền tảng | **32** | 🔴 chặn verify |
| **OVERALL (flat avg)** | **~62/100** | kiến trúc ~78, wiring/verify ~45 kéo xuống |

### Đọc điểm cho đúng
- **Kiến trúc/khung ≈ 78/100** — đã expert (Hexagonal, Port+DI, 4-key, RLS, token_ledger, audit chain). → **EVOLVE không REWRITE**.
- **Nối-dây + bật + verify ≈ 45/100** — cờ DEFAULT=False, chết-dây chunk, vứt cost rerank/embed, DB rỗng, không eval. → **đây là việc thật phải làm**.
- **Trần bị cap**: không hạng mục "smartness" nào lên >85 được vì **chưa runtime-verify phiên này**. Muốn vượt trần → phải seed+eval+đo số thật.

### Mốc để đo cải thiện (sau khi code+test)
| Sau bước | Kỳ vọng điểm tăng |
|---|---|
| 0 seed + 1 eval-CI | Nền 32→65; mở khóa verify toàn bộ |
| 2 fix conflate | Query retrieve 45→75 (cần load-test chứng minh) |
| 3 bật trí tuệ (A/B từng cờ) | Query 62→75+ (chỉ bật cờ +lift) |
| 4 D1+D2 token | Token-stats 52→85 |

> ⚠️ Các con số "kỳ vọng" trên là MỤC TIÊU, **chưa phải kết quả** — sẽ chỉ tuyên bố đạt khi có load-test/eval output thật (rule #0).
