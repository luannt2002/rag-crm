# 00 — CHARTER · Ragbot Expert Build Program

> Mục tiêu cuối: Ragbot là nền tảng RAG **multi-tenant chuẩn production** —
> 1 tenant có N workspace, 1 workspace có N bot, 1 bot có N channel_type.
> Lõi chunking theo **mindset AdapChunk**. Mọi quyết định kỹ thuật có phân tích trade-off.

## 6 trục mục tiêu (Definition of Done)

| Trục | Mục tiêu đo được |
|---|---|
| **ĐÚNG** | HALLU_FABRICATE = 0 (sacred) · Faithfulness ≥ 0.95 |
| **ĐỦ** | Recall ≥ 0.9 · Coverage ≥ 0.95 (corpus có đáp án → bot trả đúng) |
| **AN TOÀN** | RLS leak test 2-tenant pass trong CI · 0 cross-tenant row |
| **NHANH** | p95: Tier-1 < 1s · Tier-2 < 3s · Tier-3 < 15s |
| **RẺ** | cost/query đo được **per-tenant** · cache hit ≥ 30% |
| **KIỂM SOÁT** | mọi quyết định pipeline có log + lý do (request_steps + structlog) |

## Quy tắc vận hành (sacred cho cả program)

1. Mỗi phase có **GATE** — orchestrator KHÔNG sang phase sau khi gate chưa pass + user chưa approve.
2. Agent Phase 1–3 **CHỈ ĐỌC + BÁO CÁO**. Chỉ Phase 4 được sửa code, theo ADR đã duyệt.
3. Mọi phát hiện = evidence `file:line` hoặc `commit-hash`. Không evidence = không tính.
4. Tuân thủ `CLAUDE.md` sacred rules tuyệt đối: HALLU=0, app KHÔNG inject/override answer,
   4-key identity, zero-hardcode, domain-neutral, no-version-ref, no-psql-hotfix, model-tier.
5. **File là bộ nhớ**: mọi tri thức nằm trong `program/*.md`. Phiên chết → phiên mới đọc `program/` tiếp tục được.
6. Reviewer độc lập (Phase 4) ≠ builder. Không bao giờ cùng một agent/phiên.

## Nguyên tắc AdapChunk (Phase 3/4 ràng buộc)

**GIỮ MINDSET** (structure-aware · atomic protection · narrate-then-embed · rule cross-check ·
eval-by-question-type) — **được phép thay ENGINE** nếu có ADR phân tích, đúng tinh thần 4 lần
thay engine trước: Qdrant→pgvector · Mistral→Kreuzberg · LLM-selector→rule · sbert→zembed.

## Phạm vi mở rộng (chốt sau review khung 2026-06-10)

Khung gốc phủ ~85% phần **engine**. Bổ sung phần **application** để đạt "expert application":
- Phase 3 thêm decisions: **D11 SLO+DR+Nghị định 13 · D12 production feedback loop ·
  D13 human ground-truth process · D14–D17 AdapChunk engine fixes** (xem decision register).
- Thêm **Wave 6 — Application layer** (sau khi engine 6-trục xanh): bot-owner dashboard,
  sysprompt editor + preview, analytics refuse/miss, thumbs feedback → eval loop.

## STRATEGIC STANCE — EVOLVE, KHÔNG REWRITE (binding, chốt 2026-06-10)

**Không viết lại từ đầu. Đào sâu dự án cũ và tiến hóa nó (strangler fig).** Bằng chứng:
khung đã là expert (Hexagonal/DDD · Strategy+Port+Adapter+DI · 2 LangGraph graph tách rời qua
vector store + event bus · config chain 6 tầng · 9 sacred contracts). Vấn đề KHÔNG phải "khung sai"
mà là "dây chưa nối hết". Ragbot đã chứng minh tiến hóa được: 4 lần thay engine (Qdrant→pgvector ·
Mistral OCR→Kreuzberg · LLM-selector→rule · sbert→zembed) đều làm trong khung hiện tại nhờ Port/Adapter.
Code cũ chứa "vết sẹo production" tiền không mua lại được (safety-net legal under-rank · temp-0 ép vì
multi-fact flip · never-refetch source_url · cliff filter · purge gemma timeout 30s=76% p95) + trạng thái
**HALLU=0 verified 87/91** đang có. Rewrite = mất hết, mất nhiều tháng chỉ để đuổi kịp chính mình.

**Ma trận xử lý từng phần (ràng buộc Phase 3/4):**
| Phần | Cách xử lý |
|---|---|
| 2 graph · 4-key identity · sacred contracts · config chain | **GIỮ nguyên** (đập = lỗi nặng nhất) |
| RLS · semantic cache scope · worker GUC | **WIRE + HARDEN** (code/policy có sẵn, thiếu dây + test) |
| AdapChunk B1–B4 (block list · atomic · narrate) | **HOÀN THIỆN** (dead-code chờ nối) |
| Workspace slug → entity · quota cascade | **MIGRATE schema** (alembic backward-compat null→default ws) |
| Parser adapter Kreuzberg flat-text → emit block list | **REWRITE cục bộ 1 module** (chỗ duy nhất đáng viết lại; Port có sẵn) |
| Selector · reranker · embedder · judge | **SWAP qua ADR** khi Phase 3 chứng minh engine tốt hơn |

"Expert" = (1) nối hết dây đã thiết kế · (2) migrate đúng chỗ schema · (3) rewrite đúng 1–2 module nghẽn ·
(4) **bộ eval chứng minh bằng số**. Critical-path con-người (song song agent): gom 3 corpus thật +
viết ground-truth (người KHÔNG biết hệ thống — AdapChunk §9.3). Wave 1 (RLS+cache scope) = code sửa ĐẦU TIÊN.

## Trạng thái repo (anchor Phase 0)

- Branch: `fix-260604-action-slotmachine-dead-key` · git commits: **1639** · alembic head: **0195**
- src: **557 .py / ~109.8k LOC** · tests: **674** · plans: 27 · reports: 110 · docs/master: 16 (A–P)
- File khổng lồ cần biết: `query_graph.py` 8087 dòng (P1-A trọng tâm), `document_service.py` 4104,
  `chunking.py` 3015 (P1-B trọng tâm AdapChunk).
