# [T1-Smartness] LLM Strategy Selector — wire AdapChunk Tầng 3/4 (spec → code)

> User chốt 2026-06-23: build LLM-based chunking strategy selector đúng spec AdapChunk §4
> (LLM nhận profile → JSON {strategy, confidence, reasoning, detected_type, risk_factors}).
> Hiện code = rule-based `select_strategy` (sync, no LLM). Port `ChunkingStrategyResolverPort`
> + DTO `ChunkingDecision` ĐÃ CÓ scaffold (chưa wire) → "dây chưa nối hết" → WIRE, không rewrite.

## Nguyên tắc (binding)
- **Strategy+DI** (CLAUDE.md mandate): LLM adapter implement port + **rule-based làm Null/fallback** (KHÔNG comment-dead-code). Config `chunking_strategy_provider` ∈ {rule | llm}. Flip 1 dòng = đổi.
- **Hybrid (an toàn + rẻ)**: fast-path rule (CSV→table_csv, legal→hdt) chạy TRƯỚC trong flow → LLM chỉ cho **ambiguous prose** → tiết kiệm cost + không regress catalog.
- **Graceful degradation**: LLM fail (timeout/breaker/bad-JSON) → fallback rule. Ingest KHÔNG vỡ.
- **Cross-check giữ nguyên** (`apply_cross_check` L5) — override khi LLM vô lý (đúng spec Tầng 5).
- **Default OFF** (`provider="rule"`) → 0 regression. Bật `"llm"` per-bot/global khi đo xong.
- TDD · domain-neutral prompt · model rẻ (Haiku) · zero-hardcode.

## Files
| File | Vai trò | Status |
|---|---|---|
| `application/ports/strategy_ports.py` | Port `ChunkingStrategyResolverPort` + `ChunkingDecision` | ✅ CÓ sẵn |
| `infrastructure/chunking_strategy/llm_resolver.py` | **LLM selector** (profile→LLM→JSON→decision) | 🔨 NEW |
| `infrastructure/chunking_strategy/rule_resolver.py` | rule-based (wrap `select_strategy`+cross-check) = fallback/default | 🔨 NEW |
| `infrastructure/chunking_strategy/registry.py` | `build_chunking_resolver(provider)` | 🔨 NEW |
| `tests/unit/test_llm_chunking_strategy_resolver.py` | TDD (mock LLM→JSON; fallback on fail) | 🔨 NEW |
| `bootstrap.py` + `_stage_u4_chunk` wiring + config key | DI + flow wire (default rule) | ⏳ Phase 2 |

## Phase
- **P1 (turn này)**: adapters (llm + rule) + registry + TDD. Pure infra, KHÔNG đụng hot-path.
- **P2**: wire vào `_stage_u4_chunk` (sau fast-path) + DI bootstrap + config key `chunking_strategy_provider` (default "rule").
- **P3 (GATE)**: A/B rule vs llm trên corpus thật (Strategy Selection Accuracy + Coverage). KHÔNG default "llm" khi chưa đo.

## ⚠️ Lưu ý integration (honest)
- Port nhận `DocumentProfile` (entity); `select_strategy` cần dict richer (`is_csv_format`, `vn_hierarchical_markers`). Rule-resolver map entity→dict (CSV/legal fast-path đã chạy TRƯỚC resolver nên set False/0 — chỉ còn ambiguous-prose case).
- Spec muốn LLM nhận cả "block list" — port hiện chỉ profile. P2 cân nhắc extend port thêm `blocks` nếu profile chưa đủ.

## Status: P1 🔨 · P2 ⏳ · P3 ⏳
